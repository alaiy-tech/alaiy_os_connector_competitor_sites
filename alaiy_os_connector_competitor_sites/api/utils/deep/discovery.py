"""Tier 1: discover the JSON API a listing page's own frontend already calls
to fetch product data, by intercepting XHR/fetch responses during the first
page load, instead of scraping rendered DOM text.

Why this tier is worth having (confirmed live, not theoretical): a real test
site's category page DOM extraction found real product names/URLs/images
but EVERY row's price came back blank -- the price text on that page either
paints after the extraction window or isn't in a shape the generic price
regex recognises. Whatever XHR/fetch call actually populated that grid
carries the real price as a typed field, not scraped text. This tier only
needs to find that call once per listing; pagination against it reuses
paginate.py unchanged (detect_pagination/iter_pages only need a
fetch_page(url) -> rows callable -- they don't care whether that callable
hits DOM or a JSON API underneath).

No site-specific field names anywhere here -- every mapping is generic
fuzzy key-matching (vocabulary lives in api_signatures.py, same discipline
as validate.py's approach), works whether the underlying API is REST,
GraphQL, a hosted search index, or anything else that returns JSON.
"""

import re
from urllib.parse import urljoin, urlparse

from alaiy_os_connector_competitor_sites.api.utils.deep import api_signatures

_PRODUCT_KEY_HINTS = api_signatures.PRODUCT_KEY_HINTS

_MIN_SCORE = 2  # an array of dicts needs to touch >=2 product-field categories to count
_MAX_RESPONSES_SCANNED = 60  # safety cap -- a chatty page can fire far more XHR than we need to check


def _score_dict_keys(d):
    """Returns (hit_count, hit_categories) -- how many distinct product-
    field categories this dict's keys touch, and which ones."""
    if not isinstance(d, dict):
        return 0, set()
    keys_lower = {k.lower() for k in d.keys()}
    hit_categories = {
        category for category, hints in _PRODUCT_KEY_HINTS.items()
        if any(any(h in k for h in hints) for k in keys_lower)
    }
    return len(hit_categories), hit_categories


# name+url alone is not enough signal -- confirmed live: a nav-menu tree
# and a plain footer link list ({"title": ..., "url": ...}) both hit
# exactly this pair and cleared _MIN_SCORE, getting picked ahead of the
# real product array on the same page. A real product row almost always
# carries a price or an image; a navigational link never does. Require
# at least one of those alongside the base score, not just any 2
# categories.
_REQUIRE_ONE_OF = {"price", "image"}


_MAX_TREE_DEPTH = 40  # a real product-list array never sits this deep; caps runaway walks too
_MAX_NODES_VISITED = 20_000  # hard total-work cap, independent of depth -- see docstring


def _find_arrays(obj, path=""):
    """Yield (path, list) for every array found anywhere in a JSON tree.
    Only descends into an array's first 3 items to look for further nested
    arrays -- real product-list arrays are homogeneous, no need to walk
    thousands of siblings looking for a shape that won't repeat.

    Iterative (explicit stack), not recursive -- confirmed live: a real
    __NEXT_DATA__ payload (Next.js embeds the whole router/props state,
    often deeply nested) blew past Python's default recursion limit on a
    real site.

    Depth-capped AND total-node-capped, independently -- confirmed live
    that depth alone wasn't enough: a real __NEXT_DATA__ payload can be
    megabytes of unrelated build/route-manifest data with thousands of
    dict keys at shallow depth (wide, not deep), and this walk runs on
    EVERY page fetch during pagination (page1/page2 verification, every
    subsequent page) -- an unbounded walk there is a real multiplying
    hang, not just a slow one-off. Bailing out past the node cap still
    returns whatever arrays were already found; a genuine product list is
    virtually always reachable well before this limit if it exists."""
    stack = [(obj, path, 0)]
    visited = 0
    while stack:
        if visited >= _MAX_NODES_VISITED:
            return
        node, node_path, depth = stack.pop()
        visited += 1
        if depth > _MAX_TREE_DEPTH:
            continue
        if isinstance(node, list):
            yield node_path, node
            for i, item in enumerate(node[:3]):
                stack.append((item, f"{node_path}[{i}]", depth + 1))
        elif isinstance(node, dict):
            for k, v in node.items():
                stack.append((v, f"{node_path}.{k}" if node_path else k, depth + 1))


_MAX_UNWRAP_KEYS = 3  # only unwrap a thin wrapper dict (edges/node-shaped), not a rich object
                       # that happens to have a nested dict field


def _best_row_shape(item):
    """A GraphQL "edges" array's items look like {"node": {...}, "cursor": ...}
    -- the real product fields sit one level inside "node", not on the edge
    dict itself, so scoring the edge dict directly finds nothing even though
    real data is right there. Returns (unwrap_key, score) for whichever shape
    (the item itself, or a nested dict inside a thin wrapper) scores best."""
    best_score, best_categories = _score_dict_keys(item)
    best_key = None
    if len(item) <= _MAX_UNWRAP_KEYS:
        for k, v in item.items():
            if isinstance(v, dict):
                score, categories = _score_dict_keys(v)
                if score > best_score:
                    best_key, best_score, best_categories = k, score, categories
    if best_score >= _MIN_SCORE and not (best_categories & _REQUIRE_ONE_OF):
        best_score = 0
    return best_key, best_score


def best_product_array(json_obj):
    """Returns (path, unwrap_key, array, score) for the array in this JSON
    tree that looks most like a product list, or None if nothing scores
    >= _MIN_SCORE. unwrap_key is set when the real fields sit one level
    inside each array item (GraphQL edges/node and similar thin wrappers),
    None when the item itself is the row shape. Prefers a higher score, then
    a longer array, on ties."""
    best = None
    for path, arr in _find_arrays(json_obj):
        if len(arr) < 2 or not isinstance(arr[0], dict):
            continue
        unwrap_key, score = _best_row_shape(arr[0])
        if score < _MIN_SCORE:
            continue
        if best is None or score > best[3] or (score == best[3] and len(arr) > len(best[2])):
            best = (path, unwrap_key, arr, score)
    return best


def classify_api_kind(request):
    """Best-effort request classification against known signatures in
    api_signatures.py -- purely observational (surfaced in the Scrape Log so
    an operator can see what each site is actually running), never gates
    extraction: best_product_array walks the response tree the same way
    regardless of what kind of API produced it. Add a new vendor/framework
    by extending api_signatures.py, not this function."""
    try:
        url = (request.url or "").lower()
        headers = {k.lower(): v for k, v in (request.headers or {}).items()}

        if any(marker in url for marker in api_signatures.SEARCH_INDEX_HOST_MARKERS):
            return "Search-index"
        if any(m in headers for m in api_signatures.FRAMEWORK_ACTION_HEADER_MARKERS):
            return "Framework Server Action"
        if any(m in headers.get("content-type", "") for m in api_signatures.FRAMEWORK_ACTION_CONTENT_TYPE_MARKERS):
            return "Framework Server Action"
        if any(marker in url for marker in api_signatures.GRAPHQL_URL_MARKERS):
            return "GraphQL"
        if request.method == "POST":
            body = (request.post_data or "")[:500].lower()
            if any(marker in body for marker in api_signatures.GRAPHQL_BODY_MARKERS):
                return "GraphQL"
        return "REST"
    except Exception:
        return "unknown"


def capture_best_api_candidate(page, listing_url, wait_ms=6000):
    """Navigates page to listing_url with a response listener attached, and
    returns (response_url, array_path, unwrap_key, score, api_kind) for the
    single best-scoring product array seen across every JSON XHR/fetch
    response during load -- or None if nothing usable turned up. Caller is
    responsible for the page already being at rest afterward (this does its
    own goto)."""
    candidates = []
    scanned = 0

    def on_response(response):
        nonlocal scanned
        if scanned >= _MAX_RESPONSES_SCANNED:
            return
        try:
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            url_lower = response.url.lower()
            host = urlparse(url_lower).netloc
            if any(m in url_lower for m in api_signatures.ANALYTICS_TRACKER_HOST_MARKERS):
                # Never even score these -- confirmed live: Adobe's
                # demdex.net analytics beacon scored 2 on a real test
                # site's traffic and got picked over the real catalog API.
                # browser.py's request-blocking should already stop most
                # of these before a response even completes; this is
                # defense in depth against anything that slips through.
                return
            if any(host.startswith(p) for p in api_signatures.ANALYTICS_SUBDOMAIN_PREFIXES):
                # First-party analytics/CDP proxy convention (Segment,
                # RudderStack, etc routed through the site's own subdomain)
                # -- confirmed live: analytics.mejuri.com's settings
                # endpoint scored 2 and got picked as a candidate.
                return
            if any(m in url_lower for m in api_signatures.ANALYTICS_PATH_MARKERS):
                # Same proxy problem via URL PATH instead of subdomain --
                # confirmed live: a real test site's own
                # /assets/optimizely/datafiles/....json (an A/B-test
                # config file) got picked as the Tier 1 candidate ahead
                # of the real catalog API.
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
            path, unwrap_key, arr, score = found
            kind = classify_api_kind(response.request)
            candidates.append((response.url, path, unwrap_key, len(arr), score, kind))

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
    candidates.sort(key=lambda c: (c[4], c[3]), reverse=True)
    url, path, unwrap_key, _count, score, kind = candidates[0]
    return url, path, unwrap_key, score, kind


def list_all_json_candidates(page, listing_url, wait_ms=4000):
    """Diagnostic-only: same response listener as find_best_api_source, but
    returns every scored candidate (sorted best-first) instead of just the
    winner. For a site where the auto-picked Tier 1 API is wrong (nav menu,
    A/B config, etc) -- shows what else was on the table so a human can
    pick the real product endpoint instead of guessing."""
    candidates = []
    scanned = 0

    def on_response(response):
        nonlocal scanned
        if scanned >= _MAX_RESPONSES_SCANNED:
            return
        try:
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            url_lower = response.url.lower()
            host = urlparse(url_lower).netloc
            if any(m in url_lower for m in api_signatures.ANALYTICS_TRACKER_HOST_MARKERS):
                return
            if any(host.startswith(p) for p in api_signatures.ANALYTICS_SUBDOMAIN_PREFIXES):
                return
            if any(m in url_lower for m in api_signatures.ANALYTICS_PATH_MARKERS):
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
            path, unwrap_key, arr, score = found
            kind = classify_api_kind(response.request)
            candidates.append({
                "url": response.url, "path": path, "unwrap_key": unwrap_key,
                "row_count": len(arr), "score": score, "kind": kind,
                "sample_row": arr[0] if arr else None,
            })

    page.on("response", on_response)
    try:
        page.goto(listing_url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
        # Diagnostic-only probe: some sites (hypothesis on a real test
        # site, not yet proven) only fire the real product-grid fetch once
        # the grid scrolls into view, not on initial load. Scroll and give
        # it a second window to see if a NEW candidate shows up.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(wait_ms)
    except Exception:
        pass
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

    candidates.sort(key=lambda c: (c["score"], c["row_count"]), reverse=True)
    return candidates


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


def map_generic_row(item, base_url, unwrap_key=None):
    """Best-effort mapping of an arbitrary product-API dict to our canonical
    row shape via fuzzy key matching -- no site-specific field names. Returns
    None if the item doesn't even carry a name+url (not a real product row).
    unwrap_key: for GraphQL edges/node-shaped arrays, the key (usually "node")
    whose value is the actual product dict -- resolved once in discovery and
    reused here so every row unwraps consistently."""
    if not isinstance(item, dict):
        return None
    if unwrap_key:
        item = item.get(unwrap_key)
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
    description = find(_PRODUCT_KEY_HINTS["description"])
    category = find(_PRODUCT_KEY_HINTS["category"])

    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url") or image.get("src")
    if isinstance(price, dict):
        price = price.get("amount") or price.get("value") or price.get("current")
    if isinstance(category, list):
        # breadcrumb-style category paths (["Women", "Jewelry", "Necklaces"])
        category = " / ".join(str(c) for c in category if c)

    if not name or not url:
        return None

    return {
        "product_name": str(name),
        "product_source_url": urljoin(base_url, str(url)),
        "product_image_url": str(image) if image else "",
        "price": str(price) if price is not None else "",
        "sku": str(sku) if sku is not None else "",
        "description": str(description) if description else "",
        "category": str(category) if category else "",
    }


def fetch_api_page(page, api_url, array_path, unwrap_key=None):
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

    rows = [map_generic_row(item, api_url, unwrap_key) for item in arr]
    return [r for r in rows if r]
