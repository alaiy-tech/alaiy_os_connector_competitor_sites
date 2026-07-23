"""
Universal actions-based listing scraper.

Since we only need 25-50 products per site (not exhaustive catalogs),
we don't need multi-page pagination logic at all. One page, scrolled a
few times to trigger lazy-load / infinite-scroll, surfaces enough
products on almost any site layout — no selectors, no per-site config.

Strategy per site:
  1. Try scroll-only actions (generic, works on infinite-scroll grids,
     never fails since it needs no selector).
  2. If that returns too few products, try scroll + a generic "load more"
     click attempt — wrapped so a missing button doesn't kill the call.
  3. Extract once at the end.
"""

import time
import uuid
from urllib.parse import urlparse, urlunparse

import frappe
from firecrawl import V1FirecrawlApp as FirecrawlApp

SCRAPE_TIMEOUT_MS = 60000
MIN_ACCEPTABLE_PRODUCTS = 20  # if scroll-only yields fewer than this, try the click fallback

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

# Generic scroll actions — no selector needed, safe on every site.
_SCROLL_ACTIONS = [
    {"type": "scroll", "direction": "down"},
    {"type": "wait", "milliseconds": 1200},
    {"type": "scroll", "direction": "down"},
    {"type": "wait", "milliseconds": 1200},
    {"type": "scroll", "direction": "down"},
    {"type": "wait", "milliseconds": 1200},
]

# Generic "load more" click attempts — common phrasings across storefronts.
_LOAD_MORE_SELECTORS = [
    "text=Load more",
    "text=Load More",
    "text=Show more",
    "text=View more",
]


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


def _scrape_with_actions(fc, url, actions):
    """Try a scrape with the given actions. Returns products list, or None on failure."""
    result = _with_retry(
        fc.scrape_url,
        url,
        formats=["extract"],
        extract={"schema": _LISTING_SCHEMA, "prompt": _LISTING_PROMPT},
        actions=actions,
        timeout=SCRAPE_TIMEOUT_MS,
    )
    if result is None:
        return None
    data = getattr(result, "extract", None) or {}
    return data.get("products") or []


def _scrape_listing_page(fc, url):
    """
    One site, one page, scrolled to surface enough products for our
    25-50 product target. No pagination loop needed.
    """
    # Attempt 1: scroll-only (safe, no selector, works for infinite-scroll grids)
    products = _scrape_with_actions(fc, url, _SCROLL_ACTIONS)

    if products is not None and len(products) >= MIN_ACCEPTABLE_PRODUCTS:
        frappe.logger().info(f"{url}: {len(products)} products via scroll-only")
        return products

    # Attempt 2: scroll + try clicking a generic "load more" button.
    # Each click is tried independently — a missing button just skips that
    # one action rather than aborting the whole sequence.
    for selector in _LOAD_MORE_SELECTORS:
        actions = _SCROLL_ACTIONS + [
            {"type": "click", "selector": selector},
            {"type": "wait", "milliseconds": 1500},
        ] + _SCROLL_ACTIONS
        try:
            result = _scrape_with_actions(fc, url, actions)
            if result and (products is None or len(result) > len(products)):
                frappe.logger().info(f"{url}: {len(result)} products via scroll+click ({selector})")
                return result
        except Exception as e:
            frappe.logger().info(f"{url}: click attempt '{selector}' failed, trying next: {e}")
            continue

    # Fall back to whatever scroll-only got us, even if below the threshold
    frappe.logger().info(f"{url}: {len(products or [])} products (scroll-only fallback)")
    return products or []


MAX_DEPTH_ROUNDS = 4  # how many times we'll scroll deeper looking for NEW products


def _scrape_listing_until_enough_new(fc, url, product_limit):
    """
    If the first batch of products is mostly stuff we already have in the
    DB (e.g. a repeat scrape catching the same top-of-grid items), scroll
    deeper into the page to reach further-down, not-yet-seen products —
    rather than stopping after one shallow pass.

    Each round scrolls further than the last and merges newly-found
    products (deduped by product_source_url) into the running set.
    """
    all_products_by_url = {}
    scroll_rounds = 1

    for attempt in range(MAX_DEPTH_ROUNDS):
        actions = _SCROLL_ACTIONS * scroll_rounds
        batch = _scrape_with_actions(fc, url, actions) or []

        for p in batch:
            src = p.get("product_source_url")
            if src:
                all_products_by_url[src] = p  # dedup, keep latest seen

        candidate_urls = [_clean_url(u) for u in all_products_by_url.keys()]
        existing = _already_in_db(candidate_urls)
        new_count = sum(1 for u in all_products_by_url if _clean_url(u) not in existing)

        frappe.logger().info(
            f"{url}: round {attempt + 1} (scroll x{scroll_rounds}) — "
            f"{len(all_products_by_url)} total seen, {new_count} new (target {product_limit})"
        )

        if new_count >= product_limit:
            break

        scroll_rounds += 1  # scroll further next round to reach deeper products

    return list(all_products_by_url.values())


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _scrape_firecrawl(site_url, product_limit=50):
    fc = _get_firecrawl()

    products = _scrape_listing_until_enough_new(fc, site_url, product_limit)

    if not products:
        return [], 0, 0

    urls_found = len(products)

    clean_urls = [_clean_url(p["product_source_url"]) for p in products if p.get("product_source_url")]
    existing = _already_in_db(clean_urls)
    new_products = [
        p for p in products
        if p.get("product_source_url") and _clean_url(p["product_source_url"]) not in existing
    ][:product_limit]

    already_in_db = len(existing)

    frappe.logger().info(
        f"Scrape: {urls_found} products found, {already_in_db} already in DB, {len(new_products)} new to save"
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
    return msg[:300] if len(msg) > 300 else msg


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


def _bg_scrape_site(site_name, site_url, scrape_id, log_name=None, scrape_method="Auto", product_limit=50):
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