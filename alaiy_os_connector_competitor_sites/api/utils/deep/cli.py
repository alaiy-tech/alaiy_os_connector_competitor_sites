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
    )

    log_doc.reload()
    print("\n" + "=" * 60)
    print(log_doc.log or "(no log)")
    print("=" * 60)
    print(f"status={log_doc.status}  urls_found={urls_found}  already_in_db={already_in_db}  saved={saved}")
    return {"log_name": log_doc.name, "urls_found": urls_found, "already_in_db": already_in_db, "saved": saved}
