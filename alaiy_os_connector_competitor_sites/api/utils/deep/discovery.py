"""Tier 1: discover the JSON API a listing page's own frontend already calls
to fetch product data, by intercepting XHR/fetch responses during the first
page load, instead of scraping rendered DOM text.

Why this tier is worth having (confirmed live, not theoretical): a Chico's
jewelry category page's DOM extraction found real product names/URLs/images
but EVERY row's price came back blank -- the price text on that page either
paints after the extraction window or isn't in a shape the generic price
regex recognises. Whatever XHR/fetch call actually populated that grid
carries the real price as a typed field, not scraped text. This tier only
needs to find that call once per listing; pagination against it reuses
paginate.py unchanged (detect_pagination/iter_pages only need a
fetch_page(url) -> rows callable -- they don't care whether that callable
hits DOM or a JSON API underneath).

No site-specific field names anywhere here -- every mapping is generic
fuzzy key-matching, same discipline as validate.py's approach.
"""

import re
from urllib.parse import urljoin

_PRODUCT_KEY_HINTS = {
    "name": ("name", "title", "productname", "displayname"),
    "url": ("url", "link", "producturl", "canonicalurl", "slug", "handle"),
    "image": ("image", "imageurl", "thumbnail", "img", "media"),
    "price": ("price", "currentprice", "saleprice", "listprice", "amount"),
    "sku": ("sku", "id", "productid", "styleid", "itemid"),
}

_MIN_SCORE = 2  # an array of dicts needs to touch >=2 product-field categories to count
_MAX_RESPONSES_SCANNED = 60  # safety cap -- a chatty page can fire far more XHR than we need to check


def _score_dict_keys(d):
    """How many distinct product-field categories this dict's keys touch."""
    if not isinstance(d, dict):
        return 0
    keys_lower = {k.lower() for k in d.keys()}
    hits = 0
    for hints in _PRODUCT_KEY_HINTS.values():
        if any(any(h in k for h in hints) for k in keys_lower):
            hits += 1
    return hits


def _find_arrays(obj, path=""):
    """Yield (path, list) for every array found anywhere in a JSON tree.
    Only descends into an array's first 3 items to look for further nested
    arrays -- real product-list arrays are homogeneous, no need to walk
    thousands of siblings looking for a shape that won't repeat."""
    if isinstance(obj, list):
        yield path, obj
        for i, item in enumerate(obj[:3]):
            yield from _find_arrays(item, f"{path}[{i}]")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _find_arrays(v, f"{path}.{k}" if path else k)


def best_product_array(json_obj):
    """Returns (path, array, score) for the array in this JSON tree that
    looks most like a product list, or None if nothing scores >= _MIN_SCORE.
    Prefers a higher score, then a longer array, on ties."""
    best = None
    for path, arr in _find_arrays(json_obj):
        if len(arr) < 2 or not isinstance(arr[0], dict):
            continue
        score = _score_dict_keys(arr[0])
        if score < _MIN_SCORE:
            continue
        if best is None or score > best[2] or (score == best[2] and len(arr) > len(best[1])):
            best = (path, arr, score)
    return best


def capture_best_api_candidate(page, listing_url, wait_ms=6000):
    """Navigates page to listing_url with a response listener attached, and
    returns (response_url, array_path, score) for the single best-scoring
    product array seen across every JSON XHR/fetch response during load --
    or None if nothing usable turned up. Caller is responsible for the page
    already being at rest afterward (this does its own goto)."""
    candidates = []
    scanned = 0

    def on_response(response):
        nonlocal scanned
        if scanned >= _MAX_RESPONSES_SCANNED:
            return
        try:
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype:
                return
            scanned += 1
            body = response.json()
        except Exception:
            return
        found = best_product_array(body)
        if found:
            path, arr, score = found
            candidates.append((response.url, path, len(arr), score))

    page.on("response", on_response)
    try:
        page.goto(listing_url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
    except Exception:
        pass
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[3], c[2]), reverse=True)
    url, path, _count, score = candidates[0]
    return url, path, score


_PATH_PART_RE = re.compile(r"[^.\[\]]+|\[\d+\]")


def walk_json_path(obj, path):
    """Resolves a path produced by _find_arrays (e.g. 'data.products.items'
    or 'items[0].nested') against a (possibly freshly re-fetched) JSON
    object. Returns None if the path no longer resolves -- a page's own API
    shape changing between requests is rare but must fail closed, not throw."""
    node = obj
    for part in _PATH_PART_RE.findall(path):
        if node is None:
            return None
        if part.startswith("["):
            idx = int(part[1:-1])
            node = node[idx] if isinstance(node, list) and idx < len(node) else None
        else:
            node = node.get(part) if isinstance(node, dict) else None
    return node


def map_generic_row(item, base_url):
    """Best-effort mapping of an arbitrary product-API dict to our canonical
    row shape via fuzzy key matching -- no site-specific field names. Returns
    None if the item doesn't even carry a name+url (not a real product row)."""
    if not isinstance(item, dict):
        return None

    def find(hints):
        for k, v in item.items():
            if any(h in k.lower() for h in hints):
                return v
        return None

    name = find(_PRODUCT_KEY_HINTS["name"])
    url = find(_PRODUCT_KEY_HINTS["url"])
    image = find(_PRODUCT_KEY_HINTS["image"])
    price = find(_PRODUCT_KEY_HINTS["price"])
    sku = find(_PRODUCT_KEY_HINTS["sku"])

    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url") or image.get("src")
    if isinstance(price, dict):
        price = price.get("amount") or price.get("value") or price.get("current")

    if not name or not url:
        return None

    return {
        "product_name": str(name),
        "product_source_url": urljoin(base_url, str(url)),
        "product_image_url": str(image) if image else "",
        "price": str(price) if price is not None else "",
        "sku": str(sku) if sku is not None else "",
        "description": "",
        "category": "",
    }


def fetch_api_page(page, api_url, array_path):
    """Fetches api_url via an IN-PAGE fetch() call (page.evaluate), not a
    separate requests.Session -- this preserves the exact cookie/TLS/header
    fingerprint the site's own browser session already has, avoiding the
    403-after-a-few-calls failure mode a bare requests session hits on
    Akamai/PerimeterX-protected sites. Returns mapped canonical rows, or []
    on any failure (network, non-JSON, path no longer resolving)."""
    try:
        data = page.evaluate(
            "async (url) => { const r = await fetch(url, {credentials: 'include'}); "
            "return await r.json(); }",
            api_url,
        )
    except Exception:
        return []

    arr = walk_json_path(data, array_path)
    if not isinstance(arr, list):
        return []

    rows = [map_generic_row(item, api_url) for item in arr]
    return [r for r in rows if r]
