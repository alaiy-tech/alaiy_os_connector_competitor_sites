import uuid

import frappe
from firecrawl import V1FirecrawlApp as FirecrawlApp

from alaiy_os_connector_competitor_sites.api.models.scrape_schemas import FirecrawlPageSchema, SingleProductSchema


def _get_firecrawl():
    settings = frappe.get_single("Stellar Brands Connector Settings")
    api_key = settings.get_password("sb_firecrawl_api_key")
    if not api_key:
        frappe.throw("Firecrawl API key is not set in Stellar Brands Connector Settings")
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
        if not item.get("product_image_url"):
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
                "source_price": item.get("price"),
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


def _mark_site_done(scrape_id, site_name, saved, error=None):
    key = f"scrape_done:{scrape_id}"
    # use_local_cache=False forces a Redis read, bypassing the per-process in-memory cache
    done = frappe.cache().get_value(key, use_local_cache=False) or {}
    done[site_name] = {"saved": saved, "error": error}
    frappe.cache().set_value(key, done, expires_in_sec=3600)


def _bg_scrape_site(site_name, site_url, scrape_id, scrape_method="Auto", product_limit=500):
    from alaiy_os_connector_competitor_sites.api.utils.shopify_scraper import _scrape_shopify
    try:
        products = []
        method_used = None

        if scrape_method == "Shopify":
            products = _scrape_shopify(site_url, product_limit=product_limit)
            method_used = "Shopify"
        elif scrape_method == "Firecrawl":
            products = _scrape_url(site_url)
            method_used = "Firecrawl"
        else:
            # Try Shopify first; fall back to Firecrawl if it returns nothing
            products = _scrape_shopify(site_url, product_limit=product_limit)
            if products:
                method_used = "Shopify"
            else:
                products = _scrape_url(site_url)
                method_used = "Firecrawl"

        saved = _save_products(products, site_name, scrape_id)
        frappe.logger().info(f"Scrape {scrape_id}: {saved} products saved from {site_name} via {method_used}")
        _mark_site_done(scrape_id, site_name, saved)
    except Exception as e:
        frappe.log_error(f"Scrape failed for {site_name}: {e}", "Scraper")
        _mark_site_done(scrape_id, site_name, 0, error=str(e))
