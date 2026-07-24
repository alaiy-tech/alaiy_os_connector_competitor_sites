"""
Category-listing pagination scraper.

Pages through a listing/category URL by incrementing a `?p=N` query param
(the convention this app's non-Shopify sites use), extracting products from
each page via Firecrawl's v2 scrape endpoint, and stops once a page returns
no new products (past the last page) or the requested limit is reached.
"""

import time
import uuid
from urllib.parse import urlencode, urlparse, urlunparse

import frappe
import requests

SCRAPE_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"
MAX_PAGES = 30  # safety cap

_PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "product_name":       {"type": "string",  "description": "Product name or title"},
        "images":             {"type": "array", "items": {"type": "string"}, "description": "List of product image URLs"},
        "product_source_url": {"type": "string",  "description": "URL of this product page"},
        "price":              {"type": "string",  "description": "Current price including $ symbol"},
        "description":        {"type": "string",  "description": "Product description"},
        "category":           {"type": "string",  "description": "Product category or type"},
        "sku":                {"type": "string",  "description": "Product SKU or item number"},
    },
    "required": ["product_name"],
}

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": _PRODUCT_SCHEMA,
            "description": "Every product listed on the page",
        }
    },
    "required": ["products"],
}


class FirecrawlCreditsError(Exception):
    pass


def _get_firecrawl_api_key():
    api_key = frappe.conf.get("firecrawl_api_key")
    if not api_key:
        settings = frappe.get_single("Stellar Brands Connector Settings")
        api_key = settings.get_password("sb_firecrawl_api_key")
    if not api_key:
        frappe.throw("Firecrawl API key not set. Add firecrawl_api_key to site_config.json.")
    return api_key


def _is_credit_error(e):
    msg = str(e)
    return "402" in msg or "Payment Required" in msg or "Insufficient credits" in msg or ("401" in msg and "Token missing" in msg)


def _is_rate_limit_error(e):
    return "429" in str(e) or "rate limit" in str(e).lower()


def _is_timeout_error(e):
    return "timeout" in str(e).lower() or "timed out" in str(e).lower()


def _clean_url(url):
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))[:140]


def _already_in_db(urls):
    if not urls:
        return set()
    placeholders = ",".join(["%s"] * len(urls))
    rows = frappe.db.sql(
        f"SELECT source_product_url FROM `tabScraped Product` WHERE source_product_url IN ({placeholders})",
        urls,
    )
    return {r[0] for r in rows}


def _with_retry(fn, *args, max_attempts=3, **kwargs):
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if _is_credit_error(e):
                raise FirecrawlCreditsError("Firecrawl credits exhausted") from e
            if _is_rate_limit_error(e) or _is_timeout_error(e):
                wait = 10 * (attempt + 1)
                frappe.logger().info(f"Retrying after {type(e).__name__}, waiting {wait}s")
                time.sleep(wait)
                last_exc = e
                continue
            last_exc = e
            break
    if last_exc:
        frappe.log_error(f"Call failed after retries: {last_exc}", "Scraper")
    return None


def _page_url(base, page):
    parts = urlparse(base)
    query = urlencode({"p": page})
    return urlunparse(parts._replace(query=query))


def _scrape_page(api_key, url):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "onlyMainContent": True,
        # Give the client-side grid time to hydrate before extraction.
        "actions": [{"type": "wait", "milliseconds": 3500}],
        "formats": [
            {
                "type": "json",
                "prompt": "Extract every product shown on this listing page.",
                "schema": _EXTRACT_SCHEMA,
            }
        ],
    }

    resp = requests.post(SCRAPE_ENDPOINT, headers=headers, json=payload, timeout=300)
    if not resp.ok:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:300]}")

    result = resp.json()
    if not result.get("success"):
        raise Exception(f"Scrape failed: {result}")

    return result.get("data", {}).get("json", {}).get("products", []) or []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _scrape_firecrawl(site_url):
    api_key = _get_firecrawl_api_key()

    products = []
    seen = set()

    for page in range(1, MAX_PAGES + 1):
        url = _page_url(site_url, page)
        print(f"Scraping page {page}: {url}")
        batch = _with_retry(_scrape_page, api_key, url) or []

        new = [p for p in batch if p.get("product_source_url") not in seen]
        for p in new:
            if p.get("product_source_url"):
                seen.add(p["product_source_url"])
        products.extend(new)

        print(f"  +{len(new)} new (total {len(products)})")
        frappe.logger().info(f"{url}: +{len(new)} new (total {len(products)})")

        # Stop when a page adds nothing new (past the last page).
        if not new:
            print("  no new products on this page, stopping pagination")
            break

    if not products:
        print("Done. No products found.")
        return [], 0, 0

    urls_found = len(products)

    clean_urls = [_clean_url(p["product_source_url"]) for p in products if p.get("product_source_url")]
    existing = _already_in_db(clean_urls)
    new_products = [
        p for p in products
        if p.get("product_source_url") and _clean_url(p["product_source_url"]) not in existing
    ]
    already_in_db = len(existing)

    print(f"Done. {urls_found} found, {already_in_db} already in DB, {len(new_products)} new to save")
    frappe.logger().info(
        f"Scrape: {urls_found} products found, {already_in_db} already in DB, {len(new_products)} new to save"
    )

    return _normalise(new_products), urls_found, already_in_db


def _normalise(items):
    out = []
    for item in items:
        if not item:
            continue
        images = item.get("images") or []
        image_url = images[0] if images else item.get("product_image_url") or ""
        out.append({
            "product_name":       item.get("product_name") or "",
            "product_image_url":  image_url,
            "product_source_url": item.get("product_source_url") or "",
            "price":              item.get("price") or "",
            "sku":                item.get("sku") or "",
            "description":        item.get("description") or "",
            "category":           item.get("category") or "",
        })
    return out


# ---------------------------------------------------------------------------
# Save / log / orchestration (unchanged)
# ---------------------------------------------------------------------------

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
                "source_price": item.get("price"),
                "description": item.get("description"),
                "scraped_at": frappe.utils.now(),
            }).insert(ignore_permissions=True)
            saved += 1
        except Exception as e:
            frappe.log_error(f"Failed to save product {source_url}: {e}", "Scraper")
    frappe.db.commit()
    return saved


def _friendly_error(e):
    msg = str(e)
    if "402" in msg or "Payment Required" in msg or "Insufficient credits" in msg:
        return "Out of Firecrawl credits — top up at firecrawl.dev/pricing"
    if "401" in msg or "Unauthorized" in msg or "Token missing" in msg:
        return "Firecrawl API key is invalid or missing — check FN Portal Settings"
    if "429" in msg or "rate limit" in msg.lower():
        return "Firecrawl rate limit hit — wait a few minutes and try again"
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return f"Request timed out scraping {msg[:80]}"
    if "connection" in msg.lower() or "network" in msg.lower():
        return "Network error — could not reach the site or Firecrawl"
    return "Scrape failed unexpectedly — check the Error Log for details"


def _update_log(log_name, **kwargs):
    if not log_name:
        return
    try:
        doc = frappe.get_doc("Scrape Log", log_name)
        for k, v in kwargs.items():
            setattr(doc, k, v)
        doc.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.logger().warning(f"Could not update Scrape Log {log_name}: {e}")


def _bg_scrape_site(site_name, site_url, scrape_id, log_name=None, scrape_method="Auto"):
    from alaiy_os_connector_competitor_sites.api.utils.shopify_scraper import _scrape_shopify

    _update_log(log_name, status="Running", started_at=frappe.utils.now_datetime())

    try:
        products = []
        method_used = None
        urls_found = 0
        already_in_db = 0

        shopify_skip_urls = set(frappe.get_all(
            "Scraped Product", filters={"source_site": site_name}, pluck="source_product_url"
        ))

        if scrape_method == "Shopify":
            products, already_in_db = _scrape_shopify(site_url, skip_urls=shopify_skip_urls)
            urls_found = len(products) + already_in_db
            method_used = "Shopify"
        elif scrape_method == "Firecrawl":
            products, urls_found, already_in_db = _scrape_firecrawl(site_url)
            method_used = "Firecrawl"
        else:
            try:
                products, already_in_db = _scrape_shopify(site_url, skip_urls=shopify_skip_urls)
            except Exception:
                products, already_in_db = [], 0
            if products or already_in_db:
                # a confirmed Shopify store — even if fully caught up (0 new),
                # don't burn Firecrawl credits re-scraping it
                urls_found = len(products) + already_in_db
                method_used = "Shopify"
            else:
                products, urls_found, already_in_db = _scrape_firecrawl(site_url)
                method_used = "Firecrawl"

        saved = _save_products(products, site_name, scrape_id)
        frappe.logger().info(
            f"Scrape {scrape_id}: {saved} saved, {already_in_db} already in DB, from {site_name} via {method_used}"
        )
        _update_log(
            log_name,
            status="Done",
            products_saved=saved,
            urls_found=urls_found,
            already_in_db=already_in_db,
            method_used=method_used or "",
            completed_at=frappe.utils.now_datetime(),
        )

    except FirecrawlCreditsError:
        msg = "Out of Firecrawl credits — top up at firecrawl.dev/pricing"
        frappe.log_error(msg, "Scraper")
        _update_log(log_name, status="Failed", log=msg, completed_at=frappe.utils.now_datetime())
    except Exception as e:
        msg = _friendly_error(e)
        frappe.log_error(f"Scrape failed for {site_name}: {e}", "Scraper")
        _update_log(log_name, status="Failed", log=msg, completed_at=frappe.utils.now_datetime())
