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

from alaiy_os_connector_competitor_sites.api.utils.deep import blocking
from alaiy_os_connector_competitor_sites.api.utils.deep import browser as browser_mod
from alaiy_os_connector_competitor_sites.api.utils.deep import category_finder
from alaiy_os_connector_competitor_sites.api.utils.deep import discovery
from alaiy_os_connector_competitor_sites.api.utils.deep import extract
from alaiy_os_connector_competitor_sites.api.utils.deep import paginate
from alaiy_os_connector_competitor_sites.api.utils.deep.budget import Budget, ResourceGuard
from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import _log_error, _save_products
from alaiy_os_connector_competitor_sites.api.utils.shopify_scraper import _strip_html
from alaiy_os_connector_competitor_sites.api.utils.validate import is_jewelry_extended, validate_row

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


def _heartbeat(log_name, last_beat_at, transcript=None, **extra_fields):
    """Also persists the live transcript text when one is passed -- before
    this, the Scrape Log's `log` field only got written once at the very
    end (_finalize), so a still-running or killed-mid-run scrape showed
    nothing at all until it finished. Piggybacks on the existing throttle
    (every _HEARTBEAT_MIN_INTERVAL seconds) rather than writing on every
    single transcript.add() call, which would be a DB write per page/
    per-product during PDP enrichment."""
    now = time.monotonic()
    if log_name and (now - last_beat_at[0]) >= _HEARTBEAT_MIN_INTERVAL:
        fields = {"last_heartbeat": frappe.utils.now_datetime()}
        if transcript is not None:
            fields["log"] = transcript.render()
        fields.update(extra_fields)
        try:
            frappe.db.set_value("Scrape Log", log_name, fields, update_modified=False)
            frappe.db.commit()
        except Exception:
            pass
        last_beat_at[0] = now


_ENRICH_PAGE_TIMEOUT_MS = 15_000


def _enrich_missing_fields(context, transcript, budget, resource_guard, scrape_id, log_name=None, last_beat_at=None):
    """PDP enrichment: sku/description/category are never present on a
    listing-grid card (confirmed against real markup -- a card only ever
    shows name/image/price), so the only way to fill them in is visiting
    each product's own page. Runs after the listing pagination for this
    scrape has already flushed its rows to the DB -- queries this run's own
    Scraped Product rows still missing all three fields, and updates
    whichever ones a PDP visit's JSON-LD (Product schema.org, the standard
    and most reliable source of this on a real product page) can fill in.
    Budget/memory-aware, same guards as the rest of the run -- a huge
    catalog stops enrichment cleanly rather than running unbounded."""
    rows = frappe.get_all(
        "Scraped Product",
        filters={
            "scrape_id": scrape_id,
            "sku": ["in", ["", None]],
            "description": ["in", ["", None]],
            "categories": ["in", ["", None]],
        },
        fields=["name", "source_product_url"],
    )
    if not rows:
        return

    transcript.add(f"ENRICH  visiting {len(rows)} product page(s) for sku/description/category")
    page = context.new_page()
    enriched = 0
    try:
        for row in rows:
            if budget.expired():
                transcript.add("ENRICH  time budget exhausted -- stopping enrichment early.")
                break
            if resource_guard.should_stop():
                transcript.add("ENRICH  low memory -- stopping enrichment early.")
                break
            url = row.source_product_url
            if not url:
                continue
            try:
                page.goto(url, timeout=_ENRICH_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
                page.wait_for_timeout(600)
                html = page.content()
            except Exception:
                continue

            ld_rows = extract.extract_json_ld(html)
            best = ld_rows[0] if ld_rows else {}
            updates = {}
            if best.get("sku"):
                updates["sku"] = best["sku"]
            if best.get("description"):
                updates["description"] = _strip_html(best["description"])
            if best.get("category"):
                updates["categories"] = best["category"]
            elif not best.get("category"):
                # Product JSON-LD frequently has no category field at all
                # (confirmed live) -- a page's separate BreadcrumbList block
                # usually carries the real category path instead.
                breadcrumb_category = extract.extract_breadcrumb_category(html)
                if breadcrumb_category:
                    updates["categories"] = breadcrumb_category
            if not ld_rows and not updates:
                continue
            if updates:
                frappe.db.set_value("Scraped Product", row.name, updates)
                enriched += 1
            if log_name and last_beat_at is not None:
                _heartbeat(log_name, last_beat_at, transcript=transcript)
    finally:
        try:
            page.close()
        except Exception:
            pass
        frappe.db.commit()

    transcript.add(f"ENRICH  filled sku/description/category for {enriched}/{len(rows)} product(s)")


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


_MIN_ROWS_TO_VERIFY_CATEGORY = 3  # a real category page should show at least this many product-shaped rows


def scrape_deep(site_url, site_name, scrape_id, log_name=None, listing_urls=None, limit=0,
                 filter_jewelry=True, categories=None):
    transcript = _Transcript()
    transcript.add(f"DEEP SCRAPE  {site_url}")

    explicit_listings = [u.strip() for u in (listing_urls or "").splitlines() if u.strip()]
    listings = explicit_listings or [site_url]
    category_names = [c.strip() for c in (categories or "").split(",") if c.strip()]

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
        nonlocal total_saved, total_already_in_db, pending_save_buffer
        if not pending_save_buffer:
            return
        stats = {}
        saved_now = _save_products(pending_save_buffer, site_name, scrape_id, stats=stats)
        total_saved += saved_now
        total_already_in_db += stats.get("already_in_db", 0)
        pending_save_buffer = []
        _heartbeat(log_name, last_beat_at, transcript=transcript, products_saved=total_saved, already_in_db=total_already_in_db)

    def handle_candidate(row, listing_url_for_validation):
        nonlocal total_urls_found
        if limit and len(all_candidate_rows) >= limit:
            # Already reached the configured cap -- stop accepting more, even
            # mid-page. Without this, a page-boundary-only limit check let a
            # limit=5 run save all 99 rows found on page 1 before the check
            # ever fired (confirmed live on a real test-site run).
            return
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
    known_urls = set(frappe.get_all(
        "Scraped Product", filters={"source_site": site_name}, pluck="source_product_url"
    ))
    try:
        rows0, skipped0 = extract.try_products_json(
            site_url, skip_urls=known_urls, filter_jewelry=filter_jewelry, categories=categories)
    except Exception:
        rows0, skipped0 = [], 0
        transcript.add("TIER 0  http probe failed")

    total_already_in_db += skipped0
    if rows0:
        transcript.add(f"TIER 0  products.json probe: {len(rows0)} candidates, {skipped0} already in db")
        for row in rows0:
            handle_candidate(row, site_url)
        # Real gap confirmed live: handle_candidate silently stops accepting
        # once `limit` is reached, with zero trace of how many more were
        # actually available -- an operator watching a Scrape Log has no
        # way to tell "that's everything" from "there's 89 more you're not
        # seeing because of your own limit setting" without this line.
        if limit and len(rows0) > limit:
            transcript.add(
                f"LIMIT  {len(rows0)} product(s) available from Tier 0, kept only {limit} "
                f"(Competitor Site's configured max) — raise or clear it to get the rest."
            )
    else:
        transcript.add("TIER 0  products.json probe: no results (not a Shopify store, or empty)")

    # Skip the browser entirely once Tier 0 alone already satisfies either
    # the fixed "clearly enough" threshold, OR the caller's own configured
    # limit -- confirmed live: Tier 0 found 139 real candidates against a
    # limit=10 run, but the fixed threshold (20) alone didn't trigger since
    # limit had already capped acceptance at 10, so the browser launched
    # and did 3 more page loads for rows that could only ever be dropped.
    tier0_satisfies_limit = limit and len(all_candidate_rows) >= limit
    if (len(all_candidate_rows) >= _MIN_ROWS_TO_SKIP_BROWSER or tier0_satisfies_limit) and budget.expired() is False:
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

            # --- Tier 0.5: sitemap.xml product-URL discovery -------------------
            # Platform-agnostic, unlike Tier 0 -- most storefronts of any
            # platform publish a sitemap that's the site's own authoritative
            # list of every product page, often more complete than anything a
            # listing-grid tier can find via pagination guessing.
            try:
                sitemap_urls = extract.discover_sitemap_product_urls(site_url, limit=limit or 200)
            except Exception as e:
                sitemap_urls = []
                transcript.add(f"TIER 0.5  sitemap discovery failed: {e}")

            if sitemap_urls:
                sitemap_urls = [u for u in sitemap_urls if u not in known_urls]
                transcript.add(f"TIER 0.5  sitemap: {len(sitemap_urls)} new product URL(s) discovered, visiting each")
                sitemap_page = context.new_page()
                visited = 0
                try:
                    for product_url in sitemap_urls:
                        if budget.expired():
                            transcript.add("TIER 0.5  time budget exhausted -- stopping sitemap visits.")
                            break
                        if resource_guard.should_stop():
                            transcript.add("TIER 0.5  low memory -- stopping sitemap visits.")
                            break
                        if limit and len(all_candidate_rows) >= limit:
                            break
                        t0 = time.monotonic()
                        try:
                            sitemap_page.goto(product_url, timeout=_DOM_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
                            sitemap_page.wait_for_timeout(600)
                            html = sitemap_page.content()
                        except Exception:
                            continue
                        visited += 1
                        budget.record_page_duration(time.monotonic() - t0)

                        # A product page's own JSON-LD is the cheapest and most
                        # reliable single-page extraction (same block PDP
                        # enrichment already relies on) -- fall back to embedded
                        # SSR state only if this page doesn't carry one.
                        ld_rows = extract.extract_json_ld(html)
                        row = ld_rows[0] if ld_rows else None
                        if not row:
                            # allow_js_asset_scan=False -- this runs once per
                            # PDP in the sitemap loop, potentially hundreds
                            # of times; the JS-asset tier fetches up to 8
                            # files per call and is only worth that cost
                            # once per LISTING page, not once per product.
                            embedded_rows = extract.extract_embedded_json(
                                sitemap_page, product_url, allow_js_asset_scan=False)
                            row = embedded_rows[0] if embedded_rows else None
                        if row:
                            row.setdefault("product_source_url", product_url)
                            handle_candidate(row, site_url)
                        if visited % 25 == 0:
                            _heartbeat(log_name, last_beat_at, transcript=transcript, urls_found=total_urls_found)
                finally:
                    try:
                        sitemap_page.close()
                    except Exception:
                        pass
                transcript.add(f"TIER 0.5  sitemap: visited {visited}/{len(sitemap_urls)} page(s), "
                                f"{len(all_candidate_rows)} accepted so far")

            # No explicit Listing URLs configured, but category names were --
            # find the real listing page for each one instead of requiring
            # the operator to hand-discover and paste it. Never trusted
            # blind: each candidate is verified by actually attempting a
            # DOM extraction and requiring real product-shaped rows before
            # it's accepted.
            if not explicit_listings and category_names:
                def _verify_listing_url(url):
                    probe_page = context.new_page()
                    try:
                        probe_page.goto(url, timeout=_DOM_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
                        probe_page.wait_for_timeout(1200)
                        rows = extract.extract_dom_cards(probe_page)
                        valid = [r for r in rows if validate_row(r, url)[0]]
                        return len(valid) >= _MIN_ROWS_TO_VERIFY_CATEGORY
                    except Exception:
                        return False
                    finally:
                        try:
                            probe_page.close()
                        except Exception:
                            pass

                resolved = []
                finder_page = context.new_page()
                try:
                    for category_name in category_names:
                        url = category_finder.find_category_listing_url(
                            finder_page, site_url, category_name, _verify_listing_url)
                        if url:
                            transcript.add(f"CATEGORY  '{category_name}' -> {url}")
                            resolved.append(url)
                        else:
                            transcript.add(f"CATEGORY  '{category_name}' -> no matching listing page found")
                finally:
                    try:
                        finder_page.close()
                    except Exception:
                        pass

                if resolved:
                    listings = resolved
                else:
                    transcript.add("CATEGORY  none resolved -- falling back to the bare site URL.")

            pages_fetched = 0
            for listing_url in listings:
                if budget.expired():
                    transcript.add("BUDGET  time budget exhausted before this listing — stopping.")
                    break
                if resource_guard.should_stop():
                    transcript.add("BUDGET  low memory mid-run — stopping.")
                    break

                def _pagination_break_reason():
                    """Shared between the API and DOM pagination loops --
                    same four stop conditions either way."""
                    if budget.expired():
                        return "time budget exhausted mid-pagination — stopping."
                    if resource_guard.should_stop():
                        return "low memory mid-pagination — stopping."
                    if not budget.afford_one_more_page():
                        return "next page likely won't fit in remaining time — stopping."
                    if limit and total_urls_found >= limit:
                        return f"reached configured max ({limit}) — stopping."
                    return None

                page = context.new_page()
                try:
                    # --- Tier 1: discover the page's own JSON API, if any --
                    # Far more reliable than DOM scraping when it applies --
                    # price/sku/category come back as real typed fields
                    # instead of regex-scraped text. Confirmed necessary
                    # live: a real test-site listing found correct
                    # names/URLs/images via DOM but price was blank on every
                    # single row.
                    api_candidate = None
                    try:
                        api_candidate = discovery.capture_best_api_candidate(page, listing_url)
                    except Exception as e:
                        transcript.add(f"  TIER 1  API discovery failed: {e}")

                    # Confirmed live: Tier 1 can find A JSON API and commit
                    # to it even when that API isn't the product one (e.g.
                    # a nav-menu tree scoring just high enough to win with
                    # nothing better on the page) -- it then returns 0 rows
                    # and, because this branch always `continue`d, the DOM
                    # fallback tiers below never got a chance to run at
                    # all. Only commit to Tier 1 (and skip everything else)
                    # once it has actually produced at least one real row --
                    # a wrong pick with 0 output now falls through to the
                    # embedded-json/JSON-LD/DOM chain instead of ending the
                    # page with nothing.
                    tier1_found_any = False
                    if api_candidate:
                        api_url, array_path, unwrap_key, score, api_kind = api_candidate
                        transcript.add(
                            f"TIER 1  API discovered ({api_kind})  {api_url}  "
                            f"(path={array_path!r}, unwrap={unwrap_key!r}, score={score})"
                        )

                        def fetch_api(url):
                            nonlocal pages_fetched
                            t0 = time.monotonic()
                            rows = discovery.fetch_api_page(page, url, array_path, unwrap_key)
                            pages_fetched += 1
                            budget.record_page_duration(time.monotonic() - t0)
                            transcript.add(f"  API {url}  {len(rows)} candidates")
                            return rows

                        scheme, first_page_rows = paginate.detect_pagination(api_url, fetch_api)
                        if scheme:
                            transcript.add(
                                f"PAGINATION  (api) detected key={scheme['key']} start={scheme['start']} step={scheme['step']}"
                            )
                            for _u, rows in paginate.iter_pages(api_url, scheme, fetch_api):
                                for row in rows:
                                    tier1_found_any = True
                                    handle_candidate(row, listing_url)
                                _heartbeat(log_name, last_beat_at, transcript=transcript, urls_found=total_urls_found)
                                reason = _pagination_break_reason()
                                if reason:
                                    transcript.add(f"BUDGET  {reason}")
                                    break
                        else:
                            transcript.add(
                                f"PAGINATION  (api) no verified scheme — using the {len(first_page_rows)} row(s) already found."
                            )
                            for row in first_page_rows:
                                tier1_found_any = True
                                handle_candidate(row, listing_url)

                    if tier1_found_any:
                        continue
                    if api_candidate:
                        transcript.add(
                            "TIER 1  candidate produced 0 real rows — falling through to "
                            "embedded-json/JSON-LD/DOM extraction."
                        )

                    def _load_and_extract(url):
                        response = None
                        try:
                            response = page.goto(url, timeout=_DOM_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
                        except Exception as e:
                            transcript.add(f"  GET {url}  nav error: {e}")
                            return [], "nav_error", None

                        status_code = response.status if response else 200
                        headers = response.headers if response else {}

                        # Client-rendered grids often paint products a few hundred ms
                        # after domcontentloaded (React/Vue hydration, lazy image
                        # observers) — extraction can race the render and see an
                        # empty grid on a page that genuinely has products.
                        try:
                            page.wait_for_timeout(1500)
                            # Many grids lazy-load product images/cards only once
                            # they're within (or near) the viewport.
                            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                            page.wait_for_timeout(800)
                        except Exception:
                            pass

                        html = ""
                        try:
                            html = page.content()
                        except Exception:
                            pass

                        title = ""
                        try:
                            title = page.title()
                        except Exception:
                            pass

                        diag = blocking.analyze_response(
                            html=html,
                            status_code=status_code,
                            headers=headers,
                            title=title,
                            url=url,
                        )

                        if diag.is_blocked():
                            # Detailed diagnostic log
                            reasons_str = " · ".join(diag.reasons[:3])
                            transcript.add(
                                f"  DIAGNOSTICS ({diag.status})  vendor={diag.vendor}  status={diag.status_code}  "
                                f"action={diag.recovery_action}  [{reasons_str}]"
                            )
                            return [], diag.status, html

                        # Tier 2a first -- an SSR framework's own embedded
                        # state (Next.js __NEXT_DATA__ and similar) is
                        # usually more complete/structured than JSON-LD, and
                        # is the ONLY tier that can find a product list that
                        # was server-rendered directly into the page, never
                        # fetched via a client-visible XHR at all (which is
                        # exactly why tier 1's network intercept can come up
                        # empty on some real SSR sites).
                        rows = extract.extract_embedded_json(page, url)
                        source = "embedded-json"
                        if len(rows) < 3:
                            ld_rows = extract.extract_json_ld(html)
                            if len(ld_rows) > len(rows):
                                rows = ld_rows
                                source = "json-ld"
                        if len(rows) < 3:
                            dom_rows = extract.extract_dom_cards(page)
                            if len(dom_rows) > len(rows):
                                rows = dom_rows
                                source = "dom"
                        return rows, source, html

                    def fetch_page(url):
                        nonlocal pages_fetched
                        t0 = time.monotonic()
                        rows, source, html = _load_and_extract(url)
                        pages_fetched += 1

                        if source == blocking.RATE_LIMITED:
                            wait = blocking.backoff_seconds(html, attempt=0)
                            transcript.add(f"  GET {url}  RATE LIMITED — backing off {wait}s")
                            page.wait_for_timeout(wait * 1000)
                            rows, source, html = _load_and_extract(url)
                            pages_fetched += 1
                            if source == blocking.RATE_LIMITED:
                                transcript.add(f"  GET {url}  still rate limited after backoff — giving up on this page")
                                return []
                        elif source in (blocking.CHALLENGE, blocking.CAPTCHA, blocking.GEOBLOCK, blocking.HARD_BLOCK, blocking.AUTH_REQUIRED):
                            # Log plainly with diagnostic context
                            transcript.add(f"  GET {url}  RESTRICTED ({source}) — diagnostic logged, skipping page")
                            return []
                        elif not rows:
                            # Ambiguous 0-result page (not a detected block) — one
                            # retry with a fresh reload before accepting "empty".
                            page.wait_for_timeout(1000)
                            rows, source, html = _load_and_extract(url)
                            pages_fetched += 1
                            if source in (blocking.RATE_LIMITED, blocking.CHALLENGE, blocking.CAPTCHA, blocking.GEOBLOCK, blocking.HARD_BLOCK, blocking.AUTH_REQUIRED):
                                transcript.add(f"  GET {url}  {source} on retry — giving up on this page")
                                return []
                            if rows:
                                source = f"{source}, retry"

                        budget.record_page_duration(time.monotonic() - t0)
                        transcript.add(f"  GET {url}  {len(rows)} candidates via {source}")
                        return rows

                    scheme, first_page_rows = paginate.detect_pagination(listing_url, fetch_page)
                    if scheme:
                        transcript.add(f"PAGINATION  detected key={scheme['key']} start={scheme['start']} step={scheme['step']}")
                        for _page_url, rows in paginate.iter_pages(listing_url, scheme, fetch_page):
                            for row in rows:
                                handle_candidate(row, listing_url)
                            _heartbeat(log_name, last_beat_at, transcript=transcript, urls_found=total_urls_found)
                            reason = _pagination_break_reason()
                            if reason:
                                transcript.add(f"BUDGET  {reason}")
                                break
                    else:
                        # Use the rows already found while probing candidate keys —
                        # do NOT re-fetch the bare listing_url. Confirmed on this
                        # exact codebase: a fresh ?page=1 fetch found 109 real
                        # product cards, but a second plain fetch of the un-paramed
                        # URL moments later found 0 (the site behaves differently on
                        # a repeat navigation within the same page/session). Reusing
                        # what was already verified to render is strictly safer.
                        transcript.add(f"PAGINATION  no verified scheme — using the {len(first_page_rows)} row(s) already found on page 1.")
                        for row in first_page_rows:
                            handle_candidate(row, listing_url)
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass

            transcript.add(f"BROWSER  {pages_fetched} page(s) fetched")

            # PDP enrichment: sku/description/category genuinely don't exist
            # anywhere on a listing-grid card (confirmed against real
            # markup -- title/price only), the only way to get them is
            # visiting each product's own page. Flush first so every row
            # collected so far is actually in the DB for this pass to find
            # and update.
            flush_buffer()
            _enrich_missing_fields(context, transcript, budget, resource_guard, scrape_id, log_name, last_beat_at)

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
