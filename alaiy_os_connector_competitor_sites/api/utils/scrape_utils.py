"""
Category-listing pagination scraper.

Pages through a listing/category URL by incrementing a `?p=N` query param
(the convention this app's non-Shopify sites use), extracting products from
each page via Firecrawl's v2 scrape endpoint, and stops once a page returns
no new products (past the last page) or the requested limit is reached.
"""

import hashlib
import time
import uuid
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

import frappe
import requests

SCRAPE_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"
MAX_PAGES = 30  # safety cap

# Query params that identify a *different* product/variant and must survive
# canonicalisation (dropping them would merge distinct products into one row).
_KEEP_QUERY_PARAMS = {
    "id", "productid", "product_id", "pid", "sku", "style", "styleid",
    "stylecode", "colorcode", "color", "colour", "itemid", "prod", "p_id",
    "variant", "variant_id",
}
# Everything else in the query string is tracking/session noise and is dropped.


def _log_error(title, message):
    """frappe.log_error's signature is (title, message) — call sites in this
    file used to pass them the other way round, which throws a MySQL 1406
    (title overflow) and masks the real error. Always go through this."""
    try:
        frappe.log_error(title=str(title)[:100], message=message)
    except Exception:
        # Logging must never be the thing that crashes a scrape run.
        frappe.logger().warning(f"_log_error failed for title={title!r}: {message!r}")

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
        settings = frappe.get_single("Competitor Sites Connector Settings")
        api_key = settings.get_password("cs_firecrawl_api_key")
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


def canonical_url(url):
    """Normalise a product URL for deduplication: lowercase host, strip
    www./default port/fragment, drop tracking params while keeping the ones
    that actually distinguish products/variants, sort what's left, and strip
    a trailing slash or /index.html. This is the ONLY normalisation used for
    both the dedup check and the save — previously they used different
    logic (_clean_url for lookup, raw url for insert), so a URL could be
    "new" on lookup and then collide on insert, or vice versa."""
    if not url:
        return ""
    p = urlparse(url.strip())
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    host = host.split(":")[0] if ":80" in host or ":443" in host else host

    path = unquote(p.path or "/")
    path = path.rstrip("/") or "/"
    if path.lower().endswith("/index.html") or path.lower().endswith("/index.php"):
        path = path.rsplit("/", 1)[0] or "/"

    kept = sorted(
        (k.lower(), v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.lower() in _KEEP_QUERY_PARAMS
    )
    query = urlencode(kept)

    scheme = (p.scheme or "https").lower()
    base = f"{scheme}://{host}{path}"
    return f"{base}?{query}" if query else base


def url_hash(url):
    """Fixed-length (64 char) dedup key, safe for a varchar(140) unique
    column regardless of how long the real URL is."""
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


def _already_in_db(urls):
    """urls: iterable of raw product URLs (not pre-cleaned) — hashing happens
    here so callers never have to remember which normalisation to apply."""
    hashes = list({url_hash(u) for u in urls if u})
    if not hashes:
        return set()
    found = set()
    # Chunk the IN(...) lookup — at Deep-scraper volumes (thousands of URLs
    # per site) a single unbounded IN() risks max_allowed_packet / a huge
    # query plan.
    chunk_size = 500
    for i in range(0, len(hashes), chunk_size):
        chunk = hashes[i : i + chunk_size]
        placeholders = ",".join(["%s"] * len(chunk))
        rows = frappe.db.sql(
            f"SELECT url_hash FROM `tabScraped Product` WHERE url_hash IN ({placeholders})",
            chunk,
        )
        found.update(r[0] for r in rows)
    return found


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
        _log_error("Scraper: call failed after retries", f"{last_exc}")
    return None


def merge_query(url, **params):
    """Preserve every existing query param; override/add only the given
    keys. Pass a value of None to drop a key. This replaces the old
    _page_url, which discarded the ENTIRE query string and replaced it with
    just `?p=N` — almost none of the target sites use that convention
    (they use ?page=, ?page_num=, ?start=&sz=, ?offset=, etc), so real
    pagination silently kept re-fetching page 1."""
    p = urlparse(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    for k, v in params.items():
        if v is None:
            q.pop(k, None)
        else:
            q[k] = str(v)
    return urlunparse(p._replace(query=urlencode(q)))


def _page_url(base, page):
    """Kept for backwards compatibility with any external caller; Firecrawl
    pagination itself now reuses whatever page-param convention the
    configured site_url already carries (falls back to "page" if the URL
    doesn't have a recognisable one), via merge_query()."""
    existing = dict(parse_qsl(urlparse(base).query))
    key = next((k for k in ("page", "p", "page_num", "pagenum") if k in existing), "page")
    return merge_query(base, **{key: page})


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

    raw_urls = [p["product_source_url"] for p in products if p.get("product_source_url")]
    existing = _already_in_db(raw_urls)  # now keyed by url_hash internally — same function used at save time
    new_products = [
        p for p in products
        if p.get("product_source_url") and url_hash(p["product_source_url"]) not in existing
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

def _fit(value, max_len):
    """Truncate a string to a column's max length so a single oversized
    field (a long CDN image URL, a long description) can never raise a
    MySQL 1406 at insert time."""
    if value is None:
        return value
    value = str(value)
    return value[:max_len] if len(value) > max_len else value


def _save_products(raw_products, site_name, scrape_id, stats=None):
    """Insert each product inside its own savepoint. Previously a single bad
    row (e.g. a >140-char URL) raised inside the loop with no rollback —
    MariaDB aborts the whole transaction at that point, so every row after
    the bad one in the same batch silently failed too. Savepoints make one
    bad row cost exactly one row.

    Returns just `saved` (int) to preserve the existing call-site contract
    (`saved = _save_products(...)`). Pass a dict as `stats` to also get
    already_in_db/save_failed counted into it in place (keys "already_in_db"
    and "save_failed", added to if already present) — used by the Deep
    runner, which needs accurate already-in-db counts for its own reporting
    since it saves incrementally in batches rather than via the single
    upstream _already_in_db() check Firecrawl/Shopify do."""
    saved = 0
    already_in_db = 0
    save_failed = 0

    for item in raw_products:
        source_url = item.get("product_source_url")
        if not source_url:
            continue

        h = url_hash(source_url)
        if frappe.db.exists("Scraped Product", {"url_hash": h}):
            already_in_db += 1
            continue

        savepoint = f"sp_{uuid.uuid4().hex[:12]}"
        frappe.db.savepoint(savepoint)
        try:
            frappe.get_doc({
                "doctype": "Scraped Product",
                "id": str(uuid.uuid4()),
                "scrape_id": scrape_id,
                "product_name": _fit(item.get("product_name"), 140),
                "product_image_url": item.get("product_image_url"),  # Small Text — no truncation needed
                "source_product_url": _fit(source_url, 140),          # display copy, truncation is fine here
                "url_hash": h,
                "source_site": site_name,
                "sku": _fit(item.get("sku"), 140),
                "categories": _fit(item.get("category"), 140),
                "source_price": _fit(item.get("price"), 140),
                "description": item.get("description"),
                "scraped_at": frappe.utils.now(),
            }).insert(ignore_permissions=True)
            saved += 1
        except frappe.DuplicateEntryError:
            frappe.db.rollback(save_point=savepoint)
            already_in_db += 1
        except Exception as e:
            frappe.db.rollback(save_point=savepoint)
            save_failed += 1
            _log_error(f"Scraper: failed to save product ({site_name})", f"{source_url}: {e}")

    frappe.db.commit()
    if stats is not None:
        stats["already_in_db"] = stats.get("already_in_db", 0) + already_in_db
        stats["save_failed"] = stats.get("save_failed", 0) + save_failed
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

        site_doc = frappe.get_doc("Competitor Site", site_name)
        filter_jewelry = bool(getattr(site_doc, "filter_jewelry", 1))
        categories = getattr(site_doc, "categories", None)

        deep_presaved = 0  # Deep commits incrementally as it goes (crash-safety); this
                            # is what it already saved before returning, on top of `saved` below.

        if scrape_method == "Shopify":
            products, already_in_db = _scrape_shopify(
                site_url, skip_urls=shopify_skip_urls, filter_jewelry=filter_jewelry, categories=categories)
            urls_found = len(products) + already_in_db
            method_used = "Shopify"
        elif scrape_method == "Firecrawl":
            products, urls_found, already_in_db = _scrape_firecrawl(site_url)
            method_used = "Firecrawl"
        elif scrape_method == "Deep":
            from alaiy_os_connector_competitor_sites.api.utils.deep import scrape_deep

            products, urls_found, already_in_db, deep_presaved = scrape_deep(
                site_url=site_url,
                site_name=site_name,
                scrape_id=scrape_id,
                log_name=log_name,
                listing_urls=getattr(site_doc, "listing_urls", None),
                limit=getattr(site_doc, "deep_max_products", None) or 0,
                filter_jewelry=filter_jewelry,
                categories=categories,
            )
            method_used = "Deep"
        else:
            try:
                products, already_in_db = _scrape_shopify(
                    site_url, skip_urls=shopify_skip_urls, filter_jewelry=filter_jewelry, categories=categories)
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

        saved = deep_presaved + _save_products(products, site_name, scrape_id)
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
        _log_error("Scraper: Firecrawl credits exhausted", msg)
        _update_log(log_name, status="Failed", log=msg, completed_at=frappe.utils.now_datetime())
    except Exception as e:
        # RQ's own job timeout raises rq.timeouts.JobTimeoutException, which is a
        # plain Exception subclass — catch it by name (rather than importing rq,
        # an optional dependency at import time) so a Deep run that runs out of
        # its RQ-level timeout still ends as a clean status instead of vanishing.
        if type(e).__name__ == "JobTimeoutException":
            msg = "Ran out of time — partial results (if any) were saved as we went. Run again to continue."
            _update_log(log_name, status="Partial", log=msg, completed_at=frappe.utils.now_datetime())
            return
        msg = _friendly_error(e)
        _log_error(f"Scraper: scrape failed for {site_name}", str(e))
        _update_log(log_name, status="Failed", log=msg, completed_at=frappe.utils.now_datetime())
