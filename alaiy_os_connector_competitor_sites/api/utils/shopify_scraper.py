import re
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import frappe

from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import _log_error

# Keyword list lives in api_signatures.py alongside every other known-
# signature list this connector uses (validate.py's is_jewelry_extended
# extends this same list further for general fashion-site categories).
# Built lazily, not at import time: deep/api_signatures.py lives under the
# `deep` package, and importing it here at module level triggers
# deep/__init__.py -> deep.runner -> deep.extract, both of which import
# names from this same module -- a real circular import, confirmed live
# on stellar's first deploy ("cannot import name '_strip_html'/
# '_scrape_shopify' from partially initialized module").
_JEWELRY_RE = None


def _jewelry_re():
    global _JEWELRY_RE
    if _JEWELRY_RE is None:
        from alaiy_os_connector_competitor_sites.api.utils.deep import api_signatures
        _JEWELRY_RE = re.compile(r"\b(" + "|".join(api_signatures.JEWELRY_KEYWORDS) + r")\b", re.IGNORECASE)
    return _JEWELRY_RE


def _normalize_tags(tags):
    if isinstance(tags, list):
        raw = tags
    elif isinstance(tags, str):
        raw = tags.split(",")
    else:
        raw = []
    # store-specific tags like "LC_Jewelry" pack the real word behind an
    # underscore, so swap separators for spaces before word-boundary matching
    return [re.sub(r"[_-]", " ", t).strip() for t in raw if t and t.strip()]


def _is_jewelry(product_type, tags, title):
    haystack = " ".join([product_type or "", title or ""] + _normalize_tags(tags))
    return bool(_jewelry_re().search(haystack))


def _strip_html(html):
    if not html:
        return ""
    return re.sub(r"<[^>]+>", " ", html).strip()


def _base_url(site_url):
    p = urlparse(site_url)
    return f"{p.scheme}://{p.netloc}"


def _collection_handle(site_url):
    path = urlparse(site_url).path
    m = re.search(r"/collections/([^/?#]+)", path)
    return m.group(1) if m else None


def _parse_link_header(header):
    if not header:
        return None
    for part in header.split(","):
        url_match = re.search(r'<([^>]+)>', part)
        rel_match = re.search(r'rel="([^"]+)"', part)
        if url_match and rel_match and rel_match.group(1) == "next":
            return url_match.group(1)
    return None


_MAX_FETCH_ATTEMPTS = 4


def _get_with_retry(session, url):
    """GET with real 429/5xx handling -- previously a single transient error
    (or a legitimate Shopify rate-limit) silently ended pagination early with
    no distinction from "genuinely reached the last page", quietly losing
    the rest of the catalog, AND every failure path returned None with no
    log at all -- a real 4xx (bad request, moved store, auth wall) left no
    trace anywhere to diagnose why a scrape came back empty. Every terminal
    failure now logs the request (method, URL) and response (status,
    headers, body snippet) so a failed scrape is debuggable from the Error
    Log alone, without needing to reproduce it live. Honors Retry-After
    when Shopify sends one; otherwise a short fixed backoff. Returns the
    Response, or None if every attempt failed (caller treats that as "stop,
    nothing more to get")."""
    last_exc = None
    last_response = None
    for attempt in range(_MAX_FETCH_ATTEMPTS):
        try:
            r = session.get(url, timeout=20)
        except Exception as e:
            last_exc = e
            time.sleep(2 * (attempt + 1))
            continue

        last_response = r
        if r.status_code == 200:
            return r
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "0")) or (2 * (attempt + 1))
            time.sleep(min(wait, 30))
            continue
        if r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        # A real 4xx (other than 429) won't fix itself on retry -- give up
        # immediately rather than burning the remaining attempts. A plain
        # 404 is deliberately NOT logged here: this same probe IS how Tier 0
        # detects "this isn't a Shopify store" in the first place -- most
        # sites a general-purpose scraper touches will 404 here, every
        # single run, by design. Logging that as an Error Log entry would
        # spam the log on the routine, expected case. Anything else (403,
        # 401, etc) is a genuinely unusual response worth a trace.
        if r.status_code != 404:
            _log_error(
                "Scraper: Shopify fetch got a non-retryable error",
                f"GET {url}\nstatus={r.status_code}\nheaders={dict(r.headers)}\nbody={r.text[:1000]}",
            )
        return None

    if last_exc:
        _log_error("Scraper: Shopify fetch failed after retries (network error)", f"GET {url}\n{last_exc}")
    elif last_response is not None:
        _log_error(
            "Scraper: Shopify fetch failed after retries (rate-limited or server error)",
            f"GET {url}\nstatus={last_response.status_code}\nheaders={dict(last_response.headers)}\n"
            f"body={last_response.text[:1000]}",
        )
    return None


def _fetch_products(session, endpoint, skip_urls=None, filter_jewelry=True):
    """Paginate a Shopify products.json endpoint and return every matching
    product, plus a count of how many were skipped because they're already
    scraped previously (in skip_urls), so a repeat scrape finds new ones
    instead of re-saving old ones. filter_jewelry=False returns every real
    product regardless of category -- this connector's default use case is
    jewelry-only, but the filter is a per-site choice (Competitor Site.
    filter_jewelry), not something Tier 0 should hardcode past what Deep
    already makes configurable."""
    skip_urls = skip_urls or set()
    products = []
    skipped = 0
    total_raw = 0

    parsed_endpoint = urlparse(endpoint)
    endpoint_query = dict(parse_qsl(parsed_endpoint.query))
    page_limit = int(endpoint_query.get("limit", "250")) if str(endpoint_query.get("limit", "250")).isdigit() else 250
    page_num = 1

    next_url = endpoint
    while next_url:
        r = _get_with_retry(session, next_url)
        if r is None:
            break
        try:
            batch = r.json().get("products", [])
        except ValueError:
            break

        if not batch:
            break
        total_raw += len(batch)

        for p in batch:
            handle_val = p.get("handle", "")
            base = endpoint.split("/collections/")[0].split("/products")[0]
            source_url = f"{base}/products/{handle_val}" if handle_val else ""
            if not source_url:
                continue
            if source_url in skip_urls:
                skipped += 1
                continue
            if filter_jewelry and not _is_jewelry(p.get("product_type"), p.get("tags"), p.get("title")):
                continue
            image = (p.get("images") or [{}])[0].get("src", "")
            if not image:
                continue
            variant = (p.get("variants") or [{}])[0]
            products.append({
                "product_name": p.get("title", ""),
                "product_image_url": image,
                "product_source_url": source_url,
                "price": variant.get("price", ""),
                "sku": variant.get("sku", ""),
                "description": _strip_html(p.get("body_html", "")),
                "category": p.get("product_type", ""),
            })

        next_from_link = _parse_link_header(r.headers.get("Link"))
        if next_from_link:
            next_url = next_from_link
        elif len(batch) >= page_limit:
            # No Link header at all -- confirmed live: some Shopify stores'
            # CDN config never sends one on products.json, and pagination
            # silently stopped after page 1 every time (a 688-product real
            # catalog returned only ~250, confirmed by directly walking
            # ?page=N with curl). Fall back to manual ?page=N pagination,
            # but only keep going while a page came back FULL -- a
            # genuinely last, partial page (len < limit) means there's
            # nothing more regardless of pagination method, so this can't
            # loop forever guessing past the real end.
            page_num += 1
            next_url = urlunparse(parsed_endpoint._replace(
                query=urlencode({**endpoint_query, "limit": page_limit, "page": page_num})
            ))
        else:
            next_url = None

    if filter_jewelry and total_raw and not products:
        # Same signal Deep's adaptive filter surfaces -- a real catalog was
        # found but nothing matched the jewelry vocabulary, which usually
        # means this store's product_type/tags wording just isn't in the
        # keyword list, not that the store genuinely has zero jewelry.
        frappe.logger().info(
            f"Shopify products.json: {total_raw} raw product(s) found at {endpoint}, "
            "0 matched the jewelry filter -- check this store's product_type/tags vocabulary"
        )

    return products, skipped


def _fetch_all_collections(session, base):
    """Shopify's own /collections.json is structured data (title + handle),
    no nav-link scraping needed the way a bespoke site requires -- paginate
    it the same way products.json paginates (?page=N, Shopify's REST
    collections.json convention), collecting {handle, title} for matching."""
    collections = []
    page = 1
    while page <= 20:  # safety cap -- a store with 20*250 collections is not real
        r = _get_with_retry(session, f"{base}/collections.json?limit=250&page={page}")
        if r is None:
            break
        try:
            batch = r.json().get("collections", [])
        except ValueError:
            break
        if not batch:
            break
        collections.extend(batch)
        page += 1
    return collections


def _match_collection_handles(collections, category_names):
    """Fuzzy title match (exact > substring > singular/plural) against a
    real store's own collection titles -- same discipline as
    category_finder.py's nav-link matching, just against structured data
    instead of scraping link text."""
    handles = []
    for name in category_names:
        norm = name.strip().lower()
        if not norm:
            continue
        variants = {norm, norm.rstrip("s"), norm + "s"}
        best = None
        best_score = 0
        for c in collections:
            title = (c.get("title") or "").strip().lower()
            if not title or not c.get("handle"):
                continue
            score = 0
            if title == norm:
                score = 3
            elif norm in title:
                score = 2
            elif title in variants:
                score = 1
            if score > best_score:
                best_score, best = score, c["handle"]
        if best:
            handles.append(best)
    return handles


def _scrape_shopify(site_url, skip_urls=None, filter_jewelry=True, categories=None):
    import requests
    base = _base_url(site_url)
    handle = _collection_handle(site_url)

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    skipped_total = 0
    category_names = [c.strip() for c in (categories or "").split(",") if c.strip()]

    handles = [handle] if handle else []
    if not handles and category_names:
        collections = _fetch_all_collections(session, base)
        handles = _match_collection_handles(collections, category_names)
        if handles:
            frappe.logger().info(f"Shopify: matched categories {category_names} -> collection handle(s) {handles}")
        else:
            frappe.logger().info(f"Shopify: no collection title matched categories {category_names}")

    if handles:
        products = []
        seen = set()
        for h in handles:
            batch, skipped = _fetch_products(
                session, f"{base}/collections/{h}/products.json?limit=250", skip_urls, filter_jewelry)
            skipped_total += skipped
            for p in batch:
                if p["product_source_url"] not in seen:
                    products.append(p)
                    seen.add(p["product_source_url"])
        # If every matched collection combined still returned very few,
        # also pull from root to make sure real inventory isn't missed.
        if len(products) < 20:
            frappe.logger().info(f"Matched collection(s) only have {len(products)} products, pulling from root products.json too")
            root_products, root_skipped = _fetch_products(session, f"{base}/products.json?limit=250", skip_urls, filter_jewelry)
            skipped_total += root_skipped
            for p in root_products:
                if p["product_source_url"] not in seen:
                    products.append(p)
                    seen.add(p["product_source_url"])
    else:
        products, skipped_total = _fetch_products(session, f"{base}/products.json?limit=250", skip_urls, filter_jewelry)

    return products, skipped_total
