"""Orchestrates the Deep scrape: tier cascade, pagination, validation,
incremental saving, and all the reliability plumbing (budget, memory guard,
browser slot, launch-failure handling, heartbeat).

Returns a 4-tuple: (products, urls_found, already_in_db, saved). `products`
is always [] — Deep saves incrementally as it goes (crash-safety: a killed
worker loses at most the current batch, never the whole run) rather than
handing back one big list for the caller to save at the end. `saved`
carries the true total so `_bg_scrape_site` can report it correctly.
"""

import time

import frappe

from alaiy_os_connector_competitor_sites.api.utils.deep import browser as browser_mod
from alaiy_os_connector_competitor_sites.api.utils.deep import extract
from alaiy_os_connector_competitor_sites.api.utils.deep import paginate
from alaiy_os_connector_competitor_sites.api.utils.deep.budget import Budget, ResourceGuard
from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import _log_error, _save_products
from alaiy_os_connector_competitor_sites.api.utils.validate import (
    is_jewelry_extended,
    parse_price,
    validate_row,
)

_SAVE_BATCH_SIZE = 40
_HEARTBEAT_MIN_INTERVAL = 15  # seconds
_MIN_ROWS_TO_SKIP_BROWSER = 20
_DOM_PAGE_TIMEOUT_MS = 25_000
_MIN_ROWS_FOR_JEWELRY_FILTER = 30  # below this run size, adaptive filter doesn't have enough signal
_JEWELRY_FILTER_MIN_MATCH_RATE = 0.30


class _Transcript:
    """Accumulates the structured log written to Scrape Log.log. Capped so
    a very long run doesn't produce an unreadable multi-MB text field."""

    MAX_CHARS = 60_000

    def __init__(self):
        self.lines = []

    def add(self, line):
        self.lines.append(line)

    def render(self):
        text = "\n".join(self.lines)
        if len(text) > self.MAX_CHARS:
            head = text[: self.MAX_CHARS // 2]
            tail = text[-self.MAX_CHARS // 2 :]
            text = head + "\n... [middle elided] ...\n" + tail
        return text


def _heartbeat(log_name, last_beat_at, **extra_fields):
    now = time.monotonic()
    if log_name and (now - last_beat_at[0]) >= _HEARTBEAT_MIN_INTERVAL:
        fields = {"last_heartbeat": frappe.utils.now_datetime()}
        fields.update(extra_fields)
        try:
            frappe.db.set_value("Scrape Log", log_name, fields, update_modified=False)
            frappe.db.commit()
        except Exception:
            pass
        last_beat_at[0] = now


def _finalize(log_name, transcript, status, summary_line, saved, urls_found, already_in_db):
    if not log_name:
        return
    try:
        frappe.db.set_value(
            "Scrape Log",
            log_name,
            {
                "status": status,
                "summary_line": summary_line[:140],
                "log": transcript.render(),
                "products_saved": saved,
                "urls_found": urls_found,
                "already_in_db": already_in_db,
                "last_heartbeat": frappe.utils.now_datetime(),
            },
            update_modified=False,
        )
        frappe.db.commit()
    except Exception as e:
        frappe.logger().warning(f"deep runner: could not finalize {log_name}: {e}")


def scrape_deep(site_url, site_name, scrape_id, log_name=None, listing_urls=None, limit=0, filter_jewelry=True):
    transcript = _Transcript()
    transcript.add(f"DEEP SCRAPE  {site_url}")

    listings = [u.strip() for u in (listing_urls or "").splitlines() if u.strip()] or [site_url]

    resource_guard = ResourceGuard()
    budget = Budget(total_seconds=1500, reserve_seconds=90)
    last_beat_at = [0.0]

    total_saved = 0
    total_urls_found = 0
    total_already_in_db = 0
    all_candidate_rows = []  # kept in memory just for the adaptive jewelry-filter rate check
    rejected_tally = {}
    rejected_samples = []
    pending_save_buffer = []

    def flush_buffer():
        nonlocal total_saved, pending_save_buffer
        if not pending_save_buffer:
            return
        saved_now = _save_products(pending_save_buffer, site_name, scrape_id)
        total_saved += saved_now
        pending_save_buffer = []
        _heartbeat(log_name, last_beat_at, products_saved=total_saved)

    def handle_candidate(row, listing_url_for_validation):
        nonlocal total_urls_found
        total_urls_found += 1

        accepted, reason = validate_row(row, listing_url_for_validation)
        if not accepted:
            rejected_tally[reason] = rejected_tally.get(reason, 0) + 1
            if len(rejected_samples) < 5:
                rejected_samples.append((row.get("product_source_url", ""), row.get("product_name", ""), reason))
            return

        all_candidate_rows.append(row)
        pending_save_buffer.append(row)
        if len(pending_save_buffer) >= _SAVE_BATCH_SIZE:
            flush_buffer()

    # --- Tier 0: products.json probe, no browser --------------------------
    try:
        rows0, skipped0 = extract.try_products_json(site_url)
    except Exception as e:
        rows0, skipped0 = [], 0
        transcript.add(f"TIER 0  http probe failed: {e}")

    total_already_in_db += skipped0
    if rows0:
        transcript.add(f"TIER 0  products.json probe: {len(rows0)} candidates, {skipped0} already in db")
        for row in rows0:
            handle_candidate(row, site_url)
    else:
        transcript.add("TIER 0  products.json probe: no results (not a Shopify store, or empty)")

    if len(all_candidate_rows) >= _MIN_ROWS_TO_SKIP_BROWSER and budget.expired() is False:
        transcript.add(f"Skipping browser tiers — tier 0 already found {len(all_candidate_rows)} candidates.")
        flush_buffer()
        return _accept_and_finish(
            transcript, log_name, all_candidate_rows, rejected_tally, rejected_samples,
            filter_jewelry, total_saved, total_urls_found, total_already_in_db,
            site_name, scrape_id, flush_buffer, pending_save_buffer,
        )

    # --- Browser preflight --------------------------------------------------
    if browser_mod.browser_known_broken():
        msg = "Chromium failed to start on a previous run in this batch — server issue, not a site issue. Skipping browser tiers for this run too."
        transcript.add(f"BROWSER  {msg}")
        return _finish(
            transcript, log_name, "Failed" if not all_candidate_rows else "Partial", msg,
            total_saved, total_urls_found, total_already_in_db, flush_buffer,
        )

    if not resource_guard.ok_to_launch():
        mb = resource_guard.mem_available_mb()
        msg = f"Low memory ({mb:.0f}MB available) — skipped browser tiers, used only what tier 0 found."
        transcript.add(f"BUDGET  {msg}")
        flush_buffer()
        return _finish(
            transcript, log_name, "Partial" if all_candidate_rows else "Failed", msg,
            total_saved, total_urls_found, total_already_in_db, flush_buffer,
        )

    with browser_mod.browser_slot() as got_slot:
        if not got_slot:
            msg = "Another Deep scrape is using the browser — ran HTTP-only tiers, no products found there. Run again to get full depth."
            transcript.add(f"BROWSER  {msg}")
            flush_buffer()
            return _finish(
                transcript, log_name, "Partial" if all_candidate_rows else "Failed", msg,
                total_saved, total_urls_found, total_already_in_db, flush_buffer,
            )

        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            transcript.add(f"BROWSER  playwright import failed: {e}")
            browser_mod.mark_browser_broken()
            flush_buffer()
            return _finish(
                transcript, log_name, "Partial" if all_candidate_rows else "Failed",
                "Chromium failed to start — server issue, not a site issue.",
                total_saved, total_urls_found, total_already_in_db, flush_buffer,
            )

        pw = None
        browser = None
        context = None
        try:
            pw = sync_playwright().start()
            try:
                browser = browser_mod.launch(pw, headless=True)
            except browser_mod.BrowserLaunchError as e:
                browser_mod.mark_browser_broken()
                transcript.add(f"BROWSER  launch FAILED: {e}")
                flush_buffer()
                return _finish(
                    transcript, log_name, "Partial" if all_candidate_rows else "Failed",
                    f"Chromium failed to start — {str(e)[:80]}. This is a server issue, not a site issue.",
                    total_saved, total_urls_found, total_already_in_db, flush_buffer,
                )

            transcript.add(f"BROWSER  launched ok  mem_available={resource_guard.mem_available_mb()}")
            context = browser_mod.new_context(browser)

            pages_fetched = 0
            for listing_url in listings:
                if budget.expired():
                    transcript.add("BUDGET  time budget exhausted before this listing — stopping.")
                    break
                if resource_guard.should_stop():
                    transcript.add("BUDGET  low memory mid-run — stopping.")
                    break

                page = context.new_page()
                try:
                    def fetch_page(url):
                        nonlocal pages_fetched
                        t0 = time.monotonic()
                        try:
                            page.goto(url, timeout=_DOM_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
                        except Exception as e:
                            transcript.add(f"  GET {url}  nav error: {e}")
                            return []
                        pages_fetched += 1
                        budget.record_page_duration(time.monotonic() - t0)

                        html = ""
                        try:
                            html = page.content()
                        except Exception:
                            pass

                        rows = extract.extract_json_ld(html)
                        source = "json-ld"
                        if len(rows) < 3:
                            dom_rows = extract.extract_dom_cards(page)
                            if len(dom_rows) > len(rows):
                                rows = dom_rows
                                source = "dom"
                        transcript.add(f"  GET {url}  {len(rows)} candidates via {source}")
                        return rows

                    scheme = paginate.detect_pagination(listing_url, fetch_page)
                    if scheme:
                        transcript.add(f"PAGINATION  detected key={scheme['key']} start={scheme['start']} step={scheme['step']}")
                        for _page_url, rows in paginate.iter_pages(listing_url, scheme, fetch_page):
                            for row in rows:
                                handle_candidate(row, listing_url)
                            _heartbeat(log_name, last_beat_at, urls_found=total_urls_found)
                            if budget.expired():
                                transcript.add("BUDGET  time budget exhausted mid-pagination — stopping.")
                                break
                            if resource_guard.should_stop():
                                transcript.add("BUDGET  low memory mid-pagination — stopping.")
                                break
                            if not budget.afford_one_more_page():
                                transcript.add("BUDGET  next page likely won't fit in remaining time — stopping.")
                                break
                            if limit and total_urls_found >= limit:
                                transcript.add(f"LIMIT  reached configured max ({limit}) — stopping.")
                                break
                    else:
                        transcript.add("PAGINATION  no verified scheme — using page 1 only.")
                        rows = fetch_page(listing_url)
                        for row in rows:
                            handle_candidate(row, listing_url)
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass

            transcript.add(f"BROWSER  {pages_fetched} page(s) fetched")

        except Exception as e:
            _log_error(f"Deep scraper: unexpected error ({site_name})", str(e))
            transcript.add(f"ERROR  {e}")
        finally:
            browser_mod.safe_close(browser=browser, context=context, playwright=pw)

    flush_buffer()
    return _accept_and_finish(
        transcript, log_name, all_candidate_rows, rejected_tally, rejected_samples,
        filter_jewelry, total_saved, total_urls_found, total_already_in_db,
        site_name, scrape_id, flush_buffer, pending_save_buffer,
    )


def _accept_and_finish(transcript, log_name, all_candidate_rows, rejected_tally, rejected_samples,
                        filter_jewelry, total_saved, total_urls_found, total_already_in_db,
                        site_name, scrape_id, flush_buffer, pending_save_buffer):
    """Note: by this point every candidate row has ALREADY been through
    _save_products via handle_candidate/flush_buffer — this function exists
    to compute the summary/observability output, not to do a second save
    pass. Validation happens implicitly via the substance/shape checks a
    caller may want to layer in a future pass; for this build, rows are
    saved as extracted, matching Shopify/Firecrawl's existing behaviour, and
    the adaptive jewelry-filter rate is reported for visibility."""
    total_rejected = sum(rejected_tally.values())
    total_seen = len(all_candidate_rows) + total_rejected
    if total_rejected:
        breakdown = " · ".join(f"{reason} {count}" for reason, count in sorted(rejected_tally.items(), key=lambda kv: -kv[1]))
        transcript.add(f"REJECTED  {total_rejected} of {total_seen} — {breakdown}")
        for url, name, reason in rejected_samples:
            transcript.add(f"  sample: \"{name}\" {url}  ({reason})")
        if total_seen and (total_rejected / total_seen) > 0.4:
            transcript.add(
                "NOTE  Over 40% of candidates were rejected as non-products — the listing URL may be "
                "a homepage/category page rather than a product grid. Check Listing URLs on this Competitor Site."
            )

    jewelry_matches = 0
    for row in all_candidate_rows:
        if is_jewelry_extended(row.get("category"), None, row.get("product_name")):
            jewelry_matches += 1

    if filter_jewelry and len(all_candidate_rows) >= _MIN_ROWS_FOR_JEWELRY_FILTER:
        rate = jewelry_matches / len(all_candidate_rows)
        if rate < _JEWELRY_FILTER_MIN_MATCH_RATE:
            transcript.add(
                f"JEWELRY FILTER  matched only {rate:.0%} of rows — likely missing vocabulary, kept everything"
            )
        else:
            transcript.add(f"JEWELRY FILTER  matched {rate:.0%} of rows")

    summary = f"Found {total_urls_found} candidates, saved {total_saved}, {total_already_in_db} already in db."
    status = "Done"
    return _finish(transcript, log_name, status, summary, total_saved, total_urls_found, total_already_in_db, flush_buffer)


def _finish(transcript, log_name, status, summary_line, total_saved, total_urls_found, total_already_in_db, flush_buffer):
    flush_buffer()
    transcript.add(f"RESULT  {status} — {summary_line}")
    _finalize(log_name, transcript, status, summary_line, total_saved, total_urls_found, total_already_in_db)
    # 4-tuple: products=[] (already saved incrementally), urls_found, already_in_db, saved
    return [], total_urls_found, total_already_in_db, total_saved
