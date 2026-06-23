import uuid

import frappe

from stellar_brands.api.utils.scrape_utils import _save_products, _scrape_single_url


@frappe.whitelist()
def scrape_from_site(site_name):
    """Scrape all products from a Competitor Site's URL."""
    site = frappe.get_doc("Competitor Site", site_name)
    scrape_id = str(uuid.uuid4())

    frappe.enqueue(
        "stellar_brands.api.utils.scrape_utils._bg_scrape_site",
        site_name=site_name,
        site_url=site.site_url,
        scrape_id=scrape_id,
        queue="long",
        timeout=300,
    )

    return {"scrape_id": scrape_id, "site": site_name, "url": site.site_url}


@frappe.whitelist()
def scrape_single_product(product_url, site_name=None):
    """Scrape a single product page directly."""
    extract = _scrape_single_url(product_url)
    scrape_id = str(uuid.uuid4())
    saved = _save_products([extract], site_name, scrape_id)
    return {"scrape_id": scrape_id, "saved": saved, "data": extract}
