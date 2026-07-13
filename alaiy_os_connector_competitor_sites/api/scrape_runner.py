import json
import uuid

import frappe

from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import _save_products, _scrape_firecrawl


@frappe.whitelist()
def scrape_from_site(site_name):
    """Scrape all products from a Competitor Site's URL."""
    site = frappe.get_doc("Competitor Site", site_name)
    scrape_id = str(uuid.uuid4())

    log_doc = frappe.get_doc({
        "doctype": "Scrape Log",
        "site_name": site_name,
        "site_url": site.site_url,
        "scrape_id": scrape_id,
        "status": "Queued",
        "product_limit": 100,
    })
    log_doc.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        "alaiy_os_connector_competitor_sites.api.utils.scrape_utils._bg_scrape_site",
        site_name=site_name,
        site_url=site.site_url,
        scrape_id=scrape_id,
        log_name=log_doc.name,
        scrape_method=site.scrape_method or "Auto",
        queue="default",
        timeout=600,
    )

    return {"scrape_id": scrape_id, "log_name": log_doc.name, "site": site_name}


@frappe.whitelist()
def scrape_single_product(product_url, site_name=None):
    """Scrape a single product page directly."""
    products = _scrape_firecrawl(product_url, product_limit=1)
    scrape_id = str(uuid.uuid4())
    saved = _save_products(products, site_name, scrape_id)
    return {"scrape_id": scrape_id, "saved": saved, "data": products[0] if products else {}}


@frappe.whitelist()
def scrape_selected_sites(sites=None, product_limit=500):
    """Enqueue scrapes for the selected Competitor Sites. Creates a Scrape Log
    record per site immediately — before the worker even starts — so the UI
    always has a DB row to poll."""
    if sites:
        site_names = json.loads(sites) if isinstance(sites, str) else sites
    else:
        site_names = [s["name"] for s in frappe.get_all("Competitor Site", fields=["name"])]

    if not site_names:
        return {"message": "No competitor sites configured", "scrape_id": None, "log_names": {}}

    scrape_id = str(uuid.uuid4())
    log_names = {}  # site_name -> log_doc.name

    for name in site_names:
        site = frappe.get_doc("Competitor Site", name)

        log_doc = frappe.get_doc({
            "doctype": "Scrape Log",
            "site_name": name,
            "site_url": site.site_url,
            "scrape_id": scrape_id,
            "status": "Queued",
            "product_limit": int(product_limit),
        })
        log_doc.insert(ignore_permissions=True)
        log_names[name] = log_doc.name

    frappe.db.commit()

    for name in site_names:
        site = frappe.get_doc("Competitor Site", name)
        frappe.enqueue(
            "alaiy_os_connector_competitor_sites.api.utils.scrape_utils._bg_scrape_site",
            site_name=name,
            site_url=site.site_url,
            scrape_id=scrape_id,
            log_name=log_names[name],
            scrape_method=site.scrape_method or "Auto",
            product_limit=int(product_limit),
            queue="default",
            timeout=600,
        )

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
