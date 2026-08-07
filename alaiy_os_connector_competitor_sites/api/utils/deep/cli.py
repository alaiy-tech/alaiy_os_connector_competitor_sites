"""Local-dev entry point — runs the exact same code path as production
(same runner, same validation, same DB writes) so testing locally actually
proves something about the server behavior, just without the RQ queue
wrapper.

Usage (test scenario 1 — one site, no product cap, unlimited time budget):

    bench --site <site> execute alaiy_os_connector_competitor_sites.api.utils.deep.cli.run_local \
        --kwargs "{'site_name': 'Penningtons', 'limit': 0}"

Creates a real Scrape Log row (so you're also exercising the log-format
code), prints a live tail of the transcript to stdout, and returns the
final counts.
"""

import frappe

from alaiy_os_connector_competitor_sites.api.utils.deep.runner import scrape_deep


def run_local(site_name, limit=0):
    site = frappe.get_doc("Competitor Site", site_name)

    log_doc = frappe.get_doc({
        "doctype": "Scrape Log",
        "site_name": site_name,
        "site_url": site.site_url,
        "scrape_id": frappe.generate_hash(length=12),
        "status": "Running",
        "started_at": frappe.utils.now_datetime(),
        "method_used": "Deep",
    })
    log_doc.insert(ignore_permissions=True)
    frappe.db.commit()

    print(f"Scrape Log: {log_doc.name}")
    print(f"Scraping {site.site_url} ...")

    products, urls_found, already_in_db, saved = scrape_deep(
        site_url=site.site_url,
        site_name=site_name,
        scrape_id=log_doc.scrape_id,
        log_name=log_doc.name,
        listing_urls=site.get("listing_urls"),
        limit=limit or (site.get("deep_max_products") or 0),
        filter_jewelry=bool(site.get("filter_jewelry", 1)),
        categories=site.get("categories"),
    )

    log_doc.reload()
    print("\n" + "=" * 60)
    print(log_doc.log or "(no log)")
    print("=" * 60)
    print(f"status={log_doc.status}  urls_found={urls_found}  already_in_db={already_in_db}  saved={saved}")
    return {"log_name": log_doc.name, "urls_found": urls_found, "already_in_db": already_in_db, "saved": saved}


def diag_page(url, wait_ms=2500):
    """Diagnostic-only helper: loads a URL and prints what Playwright
    actually sees (title, HTML length, anchor/img counts, bot-block
    markers). Not used by the scraper itself — for debugging a site that
    Deep isn't extracting from."""
    from alaiy_os_connector_competitor_sites.api.utils.deep import browser as browser_mod
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = browser_mod.launch(pw, headless=True)
    context = browser_mod.new_context(browser)
    page = context.new_page()
    try:
        response = page.goto(url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
        html = page.content()
        anchors = page.evaluate("document.querySelectorAll('a[href]').length")
        imgs = page.evaluate("document.querySelectorAll('img').length")
        anchors_with_img = page.evaluate(
            "Array.from(document.querySelectorAll('a[href]')).filter(a => a.querySelector('img')).length"
        )
        result = {
            "title": page.title(),
            "final_url": page.url,
            "html_len": len(html),
            "anchors": anchors,
            "imgs": imgs,
            "anchors_with_img": anchors_with_img,
            "looks_blocked": any(
                marker in html.lower()
                for marker in ("just a moment", "cf-chl", "captcha", "access denied", "are you human")
            ),
            "html_snippet": html[:1000],
            # Raw wire-level facts -- status/headers, not the parsed DOM --
            # for telling "0 candidates because the extractor missed
            # something real" apart from "0 candidates because the edge
            # server rejected the request before any real page loaded".
            "response_status": response.status if response else None,
            "response_status_text": response.status_text if response else None,
            "response_headers": response.all_headers() if response else None,
            "request_headers": response.request.all_headers() if response else None,
        }
        print(frappe.as_json(result))
        return result
    finally:
        browser_mod.safe_close(browser=browser, context=context, playwright=pw)


def diag_apis(url, wait_ms=4000):
    """Diagnostic-only: lists EVERY JSON XHR/fetch response the page made
    that scored as a possible product API (not just the one Tier 1 auto-
    picked). For a site where the auto-picked candidate is wrong -- shows
    what else was on the table, with a sample row from each, so the real
    product endpoint can be identified by eye instead of guessed at."""
    from alaiy_os_connector_competitor_sites.api.utils.deep import browser as browser_mod
    from alaiy_os_connector_competitor_sites.api.utils.deep.discovery import list_all_json_candidates
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = browser_mod.launch(pw, headless=True)
    context = browser_mod.new_context(browser)
    page = context.new_page()
    try:
        candidates = list_all_json_candidates(page, url, wait_ms=wait_ms)
        print(f"{len(candidates)} JSON API candidate(s) found:\n")
        for c in candidates:
            print(f"score={c['score']}  rows={c['row_count']}  kind={c['kind']}  path={c['path']!r}")
            print(f"  url={c['url']}")
            print(f"  sample_row={frappe.as_json(c['sample_row'])[:400]}")
            print()
        return candidates
    finally:
        browser_mod.safe_close(browser=browser, context=context, playwright=pw)
