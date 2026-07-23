import json
import uuid

import frappe


def _normalize_site_names(sites):
    """Accept None, a single site name, a list of site names, or a JSON-encoded
    string of either. An empty/missing result means "all competitor sites"."""
    if isinstance(sites, str):
        try:
            sites = json.loads(sites)
        except ValueError:
            pass  # not JSON, treat as a plain site name

    if isinstance(sites, str):
        sites = [sites]

    site_names = list(sites) if sites else []

    if not site_names:
        site_names = [s["name"] for s in frappe.get_all("Competitor Site", fields=["name"])]

    return site_names


def _create_scrape_log(site_name, site_url, scrape_id, product_limit):
    log_doc = frappe.get_doc({
        "doctype": "Scrape Log",
        "site_name": site_name,
        "site_url": site_url,
        "scrape_id": scrape_id,
        "status": "Queued",
        "product_limit": product_limit,
    })
    log_doc.insert(ignore_permissions=True)
    return log_doc


def _enqueue_site_scrape(site_name, site_url, scrape_id, log_name, scrape_method, product_limit):
    frappe.enqueue(
        "alaiy_os_connector_competitor_sites.api.utils.scrape_utils._bg_scrape_site",
        site_name=site_name,
        site_url=site_url,
        scrape_id=scrape_id,
        log_name=log_name,
        scrape_method=scrape_method or "Auto",
        product_limit=product_limit,
        queue="default",
        timeout=600,
    )


@frappe.whitelist()
def scrape_all_sites(sites=None, product_limit=500):
    """Enqueue scrapes for Competitor Sites. If `sites` is omitted (or empty),
    scrapes every configured site; otherwise scrapes only the given site(s).
    `sites` may be a single site name, a list of site names, or a JSON-encoded
    string of either. Creates a Scrape Log record per site immediately —
    before the worker even starts — so the UI always has a DB row to poll."""
    site_names = _normalize_site_names(sites)

    if not site_names:
        return {"message": "No competitor sites configured", "scrape_id": None, "log_names": {}}

    product_limit = min(int(product_limit), 50)
    scrape_id = str(uuid.uuid4())
    sites_by_name = {name: frappe.get_doc("Competitor Site", name) for name in site_names}
    log_names = {}  # site_name -> log_doc.name

    for name in site_names:
        log_doc = _create_scrape_log(name, sites_by_name[name].site_url, scrape_id, product_limit)
        log_names[name] = log_doc.name

    frappe.db.commit()

    for name in site_names:
        site = sites_by_name[name]
        _enqueue_site_scrape(name, site.site_url, scrape_id, log_names[name], site.scrape_method, product_limit)

    return {
        "message": f"Scrape enqueued for {len(site_names)} site(s)",
        "scrape_id": scrape_id,
        "log_names": log_names,
    }


@frappe.whitelist()
def get_scrape_progress(log_names):
    """Return per-site status by reading Scrape Log records directly from DB.
    log_names: dict of {site_name: log_doc_name}"""
    if isinstance(log_names, str):
        log_names = json.loads(log_names)

    results = {}
    for site_name, log_name in log_names.items():
        try:
            doc = frappe.get_doc("Scrape Log", log_name)
            results[site_name] = {
                "status": doc.status,
                "products_saved": doc.products_saved or 0,
                "urls_found": doc.urls_found or 0,
                "already_in_db": doc.already_in_db or 0,
                "log": doc.log or "",
                "method_used": doc.method_used or "",
                "log_name": log_name,
            }
        except Exception as e:
            results[site_name] = {
                "status": "Failed",
                "products_saved": 0,
                "urls_found": 0,
                "already_in_db": 0,
                "log": f"Could not read log record: {e}",
                "method_used": "",
                "log_name": log_name,
            }

    return results
