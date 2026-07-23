"""
Listing-page-only Firecrawl scraper, merged with save/log/orchestration
functions from the original module.

Strategy: scrape category/listing pages with a schema that extracts an
ARRAY of products per call, follow pagination, dedupe against DB. That's it.
No per-product detail scraping, no enrichment, no single-product fallback.
"""

import re
import time
import uuid
from urllib.parse import urlparse, urlunparse, urljoin

import frappe
from firecrawl import V1FirecrawlApp as FirecrawlApp

MAX_LISTING_PAGES = 15  # pagination depth cap

# ---------------------------------------------------------------------------
# Schema — extracts an array of products from one listing page in one call
# ---------------------------------------------------------------------------

_LISTING_SCHEMA = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_name":       {"type": "string"},
                    "brand":              {"type": "string"},
                    "price":              {"type": "string"},
                    "product_source_url": {"type": "string", "description": "Absolute URL to the product's own page"},
                    "product_image_url":  {"type": "string"},
                    "category":           {"type": "string"},
                    "sku":                {"type": "string"},
                },
                "required": ["product_name", "product_source_url"],
            },
        }
    },
    "required": ["products"],
}

_LISTING_PROMPT = (
    "This is a product listing / category page. Extract every jewelry product "
    "shown in the grid (necklaces, earrings, rings, bracelets, anklets, body jewelry). "
    "Ignore banners, ads, navigation, and non-jewelry items. For each product return "
    "its name, brand if shown, price, absolute product page URL, main image URL, "
    "category, and SKU if visible. Do not invent a URL — omit product_source_url "
    "if none is present."
)

_NEXT_PAGE_TEXT_RE = re.compile(r"next|page\s*\d+|›|»|>>", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


class FirecrawlCreditsError(Exception):
    pass


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _get_firecrawl():
    api_key = frappe.conf.get("firecrawl_api_key")
    if not api_key:
        settings = frappe.get_single("Stellar Brands Connector Settings")
        api_key = settings.get_password("sb_firecrawl_api_key")
    if not api_key:
        frappe.throw("Firecrawl API key not set. Add firecrawl_api_key to site_config.json.")
    return FirecrawlApp(api_key=api_key)


def _is_credit_error(e):
    msg = str(e)
    return "402" in msg or "Payment Required" in msg or "Insufficient credits" in msg or ("401" in msg and "Token missing" in msg)


def _is_rate_limit_error(e):
    msg = str(e).lower()
    return "429" in msg or "rate limit" in msg


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
            if _is_rate_limit_error(e):
                wait = 20 * (attempt + 1)
                frappe.logger().info(f"Rate limited, waiting {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
                last_exc = e
                continue
            last_exc = e
            break
    if last_exc:
        frappe.log_error(f"Call failed after retries: {last_exc}", "Scraper")
    return None


def _find_next_page_url(markdown, current_url):
    if not markdown:
        return None
    for text, href in _MD_LINK_RE.findall(markdown):
        if _NEXT_PAGE_TEXT_RE.search(text.strip()):
            absolute = urljoin(current_url, href)
            if urlparse(absolute).netloc == urlparse(current_url).netloc and absolute != current_url:
                return absolute
    return None


# ---------------------------------------------------------------------------
# Listing-page scraping — the only strategy
# ---------------------------------------------------------------------------

def _scrape_listing_page(fc, url):
    """One call, returns (products_list, next_page_url_or_None)."""
    result = _with_retry(
        fc.scrape_url,
        url,
        formats=["extract", "markdown"],
        extract={"schema": _LISTING_SCHEMA, "prompt": _LISTING_PROMPT},
    )
    if result is None:
        return [], None

    data = getattr(result, "extract", None) or {}
    products = data.get("products") or []

    markdown = getattr(result, "markdown", "") or ""
    next_url = _find_next_page_url(markdown, url)

    return products, next_url


def _scrape_listing_with_pagination(fc, start_url, max_pages=MAX_LISTING_PAGES):
    all_products = []
    seen_urls = set()
    current_url = start_url
    page_num = 1

    while current_url and page_num <= max_pages:
        products, next_url = _scrape_listing_page(fc, current_url)

        new_count = 0
        for p in products:
            src = p.get("product_source_url")
            if src and src not in seen_urls:
                seen_urls.add(src)
                all_products.append(p)
                new_count += 1

        frappe.logger().info(
            f"Listing page {page_num} ({current_url}): {len(products)} found, {new_count} new"
        )

        if next_url == current_url or (products and new_count == 0):
            break

        current_url = next_url
        page_num += 1

    return all_products


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _scrape_firecrawl(site_url, product_limit=100):
    fc = _get_firecrawl()

    listing_products = _scrape_listing_with_pagination(fc, site_url)

    if not listing_products:
        return [], 0, 0

    urls_found = len(listing_products)

    clean_urls = [_clean_url(p["product_source_url"]) for p in listing_products]
    existing = _already_in_db(clean_urls)
    new_products = [
        p for p in listing_products
        if _clean_url(p["product_source_url"]) not in existing
    ][:product_limit]

    already_in_db = len(existing)

    frappe.logger().info(
        f"Listing scrape: {urls_found} products found across pages, "
        f"{already_in_db} already in DB, {len(new_products)} new to save"
    )

    return _normalise(new_products), urls_found, already_in_db


def _normalise(items):
    out = []
    for item in items:
        if not item:
            continue
        out.append({
            "product_name":       item.get("product_name") or "",
            "product_image_url":  item.get("product_image_url") or "",
            "product_source_url": item.get("product_source_url") or "",
            "price":              item.get("price") or "",
            "sku":                item.get("sku") or "",
            "description":        item.get("description") or "",
            "category":           item.get("category") or "",
        })
    return out


# ---------------------------------------------------------------------------
# Save / log / orchestration (unchanged from original module)
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
    """Convert raw exceptions to short human-readable messages."""
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
    return msg[:300] if len(msg) > 300 else msg


def _update_log(log_name, **kwargs):
    """Write status/progress fields to the Scrape Log doc in DB."""
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


def _bg_scrape_site(site_name, site_url, scrape_id, log_name=None, scrape_method="Auto", product_limit=100):
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
            products = _scrape_shopify(site_url, product_limit=product_limit, skip_urls=shopify_skip_urls)
            method_used = "Shopify"
        elif scrape_method == "Firecrawl":
            products, urls_found, already_in_db = _scrape_firecrawl(site_url, product_limit=product_limit)
            method_used = "Firecrawl"
        else:
            try:
                products = _scrape_shopify(site_url, product_limit=product_limit, skip_urls=shopify_skip_urls)
            except Exception:
                products = []
            if products:
                method_used = "Shopify"
            else:
                products, urls_found, already_in_db = _scrape_firecrawl(site_url, product_limit=product_limit)
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