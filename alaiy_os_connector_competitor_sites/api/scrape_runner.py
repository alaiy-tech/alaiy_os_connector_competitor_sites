import uuid

import frappe

from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import _save_products, _scrape_single_url


@frappe.whitelist()
def scrape_from_site(site_name):
    """Scrape all products from a Competitor Site's URL."""
    site = frappe.get_doc("Competitor Site", site_name)
    scrape_id = str(uuid.uuid4())

    frappe.enqueue(
        "alaiy_os_connector_competitor_sites.api.utils.scrape_utils._bg_scrape_site",
        site_name=site_name,
        site_url=site.site_url,
        scrape_id=scrape_id,
        scrape_method=site.scrape_method or "Auto",
        queue="long",
        timeout=600,
    )

    return {"scrape_id": scrape_id, "site": site_name, "url": site.site_url}


@frappe.whitelist()
def scrape_single_product(product_url, site_name=None):
    """Scrape a single product page directly."""
    extract = _scrape_single_url(product_url)
    scrape_id = str(uuid.uuid4())
    saved = _save_products([extract], site_name, scrape_id)
    return {"scrape_id": scrape_id, "saved": saved, "data": extract}


@frappe.whitelist()
def scrape_selected_sites(sites=None, product_limit=500):
    """Enqueue scrapes for Competitor Sites. If sites is empty/None, scrapes all configured sites."""
    import json as _json
    if sites:
        site_names = _json.loads(sites) if isinstance(sites, str) else sites
    else:
        site_names = [s["name"] for s in frappe.get_all("Competitor Site", fields=["name"])]

    if not site_names:
        return {"message": "No competitor sites configured", "scrape_id": None}

    scrape_id = str(uuid.uuid4())
    for name in site_names:
        site = frappe.get_doc("Competitor Site", name)
        frappe.enqueue(
            "alaiy_os_connector_competitor_sites.api.utils.scrape_utils._bg_scrape_site",
            site_name=name,
            site_url=site.site_url,
            scrape_id=scrape_id,
            scrape_method=site.scrape_method or "Auto",
            product_limit=int(product_limit),
            queue="long",
            timeout=600,
        )
    return {"message": f"Scrape enqueued for {len(site_names)} site(s)", "scrape_id": scrape_id}


@frappe.whitelist()
def get_scrape_progress(scrape_id):
    """Poll progress for a running scrape — returns per-site product counts, completion status, and errors."""
    rows = frappe.db.sql(
        """
        SELECT source_site, COUNT(*) AS count, MAX(scraped_at) AS last_at
        FROM `tabScraped Product`
        WHERE scrape_id = %s
        GROUP BY source_site
        """,
        scrape_id,
        as_dict=True,
    )

    done_sites = frappe.cache().get_value(f"scrape_done:{scrape_id}", use_local_cache=False) or {}

    errors = frappe.db.sql(
        """
        SELECT error, creation
        FROM `tabError Log`
        WHERE method = 'Scraper'
          AND creation >= NOW() - INTERVAL 10 MINUTE
        ORDER BY creation DESC
        LIMIT 20
        """,
        as_dict=True,
    )
    return {
        "sites": rows,
        "done_sites": done_sites,
        "errors": [e["error"] for e in errors],
    }
