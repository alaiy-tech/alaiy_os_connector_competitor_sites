import uuid

import frappe
from firecrawl import FirecrawlApp

from stellar_brands.api.models.scrape_schemas import FirecrawlPageSchema, SingleProductSchema


def _get_firecrawl():
    api_key = frappe.conf.get("firecrawl_api_key")
    if not api_key:
        frappe.throw("firecrawl_api_key not set in site config")
    return FirecrawlApp(api_key=api_key)


def _parse_price(price_str):
    if not price_str:
        return None
    cleaned = price_str.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _save_products(raw_products, site_name, scrape_id):
    saved = 0
    for item in raw_products:
        source_url = item.get("product_source_url")
        if not source_url:
            continue
        if frappe.db.exists("Scraped Product", {"source_product_url": source_url}):
            continue

        try:
            frappe.get_doc({
                "doctype": "Scraped Product",
                "id": str(uuid.uuid4()),
                "scrape_id": scrape_id,
                "product_name": item.get("product_name"),
                "product_image_url": item.get("product_image_url"),
                "source_product_url": source_url,
                "source_site": site_name,
                "sku": item.get("sku"),
                "categories": item.get("category"),
                "source_price": _parse_price(item.get("price")),
                "description": item.get("description"),
                "scraped_at": frappe.utils.now(),
            }).insert(ignore_permissions=True)
            saved += 1
        except Exception as e:
            frappe.log_error(f"Failed to save product {source_url}: {e}", "Scraper")

    frappe.db.commit()
    return saved


def _scrape_url(url):
    fc = _get_firecrawl()
    result = fc.scrape_url(
        url,
        formats=["extract"],
        extract={
            "schema": FirecrawlPageSchema.model_json_schema(),
            "prompt": "Extract all products visible on this page including name, image URL, product page URL, price, description, category, and SKU."
        }
    )
    extract = getattr(result, "extract", None) or {}
    return extract.get("products", [])


def _scrape_single_url(url):
    fc = _get_firecrawl()
    result = fc.scrape_url(
        url,
        formats=["extract"],
        extract={
            "schema": SingleProductSchema.model_json_schema(),
            "prompt": "Extract the product name, image URL, price, description, category, and SKU from this product page."
        }
    )
    extract = getattr(result, "extract", None) or {}
    extract["product_source_url"] = url
    return extract


def _bg_scrape_site(site_name, site_url, scrape_id):
    try:
        products = _scrape_url(site_url)
        saved = _save_products(products, site_name, scrape_id)
        frappe.logger().info(f"Scrape {scrape_id}: {saved} products saved from {site_name}")
    except Exception as e:
        frappe.log_error(f"Scrape failed for {site_name}: {e}", "Scraper")
