import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urlunparse

import frappe
from firecrawl import V1FirecrawlApp as FirecrawlApp

_DEFINITELY_NOT_PRODUCT = re.compile(
    r'/(search|cart|checkout|account|login|register|blog|about|contact|'
    r'faq|policy|sitemap|wishlist|compare|gift-card|stores?)(/|$|\?)',
    re.I,
)

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

_IMAGE_RE = re.compile(
    r'https?://[^\s\)\]"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\)\]"\']*)?',
    re.IGNORECASE,
)
# img src / data-src / data-lazy-src attributes in raw HTML
_IMG_TAG_RE = re.compile(
    r'<img[^>]+(?:src|data-src|data-lazy-src|data-original)=["\']'
    r'(https?://[^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)["\']',
    re.IGNORECASE,
)
# tiny images — tracking pixels, icons, thumbnails with small dimensions in URL
_TINY_IMAGE_RE = re.compile(
    r'[_\-x](?:[1-9]|[1-5]\d)x(?:[1-9]|[1-5]\d)[_\-\.]|'   # _40x40. or -20x30-
    r'[?&](?:w|width|h|height|size)=(?:[1-9]|[1-9]\d)\b|'    # ?w=60 or &width=80
    r'/(?:icon|logo|sprite|pixel|avatar|badge)[s]?[/_\-.]',   # /icons/ /logo- etc
    re.IGNORECASE,
)


def _extract_images(extract_images, markdown, html):
    """Collect all product images from every source, dedup, filter tiny ones."""
    candidates = list(extract_images or [])

    # from markdown ![...](...) and bare URLs
    if markdown:
        candidates += _IMAGE_RE.findall(markdown)

    # from raw HTML img tags (catches lazy-loaded images)
    if html:
        candidates += _IMG_TAG_RE.findall(html)

    seen = set()
    result = []
    for url in candidates:
        url = url.strip().rstrip(")")
        if not url or url in seen:
            continue
        seen.add(url)
        if _TINY_IMAGE_RE.search(url):
            continue
        result.append(url)

    return result[:8]  # keep up to 8 images per product

WORKERS = 3


class FirecrawlCreditsError(Exception):
    pass


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


def _clean_url(url):
    """Strip query params/fragments and truncate to 140 chars."""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))[:140]


def _is_product_url(url):
    return not bool(_DEFINITELY_NOT_PRODUCT.search(url))


def _already_in_db(urls):
    """Return set of URLs already saved in tabScraped Product."""
    if not urls:
        return set()
    placeholders = ",".join(["%s"] * len(urls))
    rows = frappe.db.sql(
        f"SELECT source_product_url FROM `tabScraped Product` WHERE source_product_url IN ({placeholders})",
        urls,
    )
    return {r[0] for r in rows}


def _map_with_retry(fc, domain_url, keyword, limit):
    for attempt in range(3):
        try:
            kwargs = {"limit": limit}
            if keyword:
                kwargs["search"] = keyword
            result = fc.map_url(domain_url, **kwargs)
            return getattr(result, "links", None) or []
        except Exception as e:
            if _is_credit_error(e):
                raise FirecrawlCreditsError("Firecrawl credits exhausted") from e
            if "429" in str(e) or "rate limit" in str(e).lower():
                wait = 60 * (attempt + 1)
                frappe.logger().info(f"Firecrawl rate limit — waiting {wait}s")
                time.sleep(wait)
            else:
                frappe.log_error(f"map_url failed for {domain_url}: {e}", "Scraper")
                return []
    return []


def _scrape_product_url(fc, url):
    try:
        result = fc.scrape_url(
            url,
            formats=["extract", "markdown", "html"],
            extract={
                "schema": _PRODUCT_SCHEMA,
                "prompt": "Extract the product name, ALL product image URLs (full-size, not thumbnails), price, description, category, and SKU from this product page.",
            },
        )
        data = getattr(result, "extract", None) or {}
        if not isinstance(data, dict) or not data.get("product_name"):
            return None

        markdown = getattr(result, "markdown", "") or ""
        html = getattr(result, "html", "") or ""
        data["images"] = _extract_images(data.get("images"), markdown, html)
        data["product_source_url"] = _clean_url(data.get("product_source_url") or url)
        return data
    except Exception as e:
        if _is_credit_error(e):
            raise FirecrawlCreditsError("Firecrawl credits exhausted") from e
        print(f"scrape failed for {url}: {e}")
        return None


def _scrape_firecrawl(site_url, product_limit=100):
    fc = _get_firecrawl()
    parsed = urlparse(site_url)
    domain_url = f"{parsed.scheme}://{parsed.netloc}"

    # Step 1: discover URLs
    # always discover broadly — "products to fetch" controls saving, not discovery
    all_urls = _map_with_retry(fc, domain_url, None, 500)
    product_urls = [u for u in all_urls if isinstance(u, str) and _is_product_url(u)]
    product_urls = list(dict.fromkeys(product_urls))

    # Step 2: skip URLs already in DB (save credits)
    clean_urls = [_clean_url(u) for u in product_urls]
    existing = _already_in_db(clean_urls)
    new_urls = [u for u in product_urls if _clean_url(u) not in existing]

    urls_found = len(product_urls)
    already_saved = len(existing)

    frappe.logger().info(
        f"map_url: {urls_found} product URLs found, {already_saved} already in DB, {len(new_urls)} to scrape"
    )

    if not new_urls and not product_urls:
        # No URLs at all — fallback to direct page scrape
        result = fc.scrape_url(
            site_url,
            formats=["extract"],
            extract={"schema": _PRODUCT_SCHEMA, "prompt": "Extract all products on this page."},
        )
        extract = getattr(result, "extract", None) or {}
        items = extract.get("products") or ([extract] if extract.get("product_name") else [])
        return _normalise(items), 0, 0

    if not new_urls:
        return [], urls_found, already_saved

    # Step 3: scrape only new URLs in parallel
    products = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_scrape_product_url, fc, url): url for url in new_urls[:product_limit]}
        for future in as_completed(futures):
            data = future.result()
            if data:
                products.append(data)

    return _normalise(products), urls_found, already_saved


def _normalise(items):
    out = []
    for item in items:
        if not item:
            continue
        images = item.get("images") or []
        image_url = images[0] if images else item.get("product_image_url") or ""
        out.append({
            "product_name":       item.get("product_name") or item.get("title") or "",
            "product_image_url":  image_url,
            "product_source_url": item.get("product_source_url") or "",
            "price":              item.get("price") or "",
            "sku":                item.get("sku") or "",
            "description":        item.get("description") or "",
            "category":           item.get("category") or "",
        })
    return out


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
    # fallback: keep it but trim to something readable
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

        if scrape_method == "Shopify":
            products = _scrape_shopify(site_url, product_limit=product_limit)
            method_used = "Shopify"
        elif scrape_method == "Firecrawl":
            products, urls_found, already_in_db = _scrape_firecrawl(site_url, product_limit=product_limit)
            method_used = "Firecrawl"
        else:
            try:
                products = _scrape_shopify(site_url, product_limit=product_limit)
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
