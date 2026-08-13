"""Generic, verified pagination for a listing URL.

The bug this replaces (in the existing Firecrawl path): blindly assuming
`?p=N` pagination and never checking whether page 2 actually differs from
page 1. Deep instead tries a short list of candidate query-param keys, and
for each candidate fetches page 1 and page 2 and checks the two returned
row-sets actually differ before trusting it for the rest of the run. A
wrong guess is caught after 2 requests, not after wasting the whole budget.
"""

from urllib.parse import parse_qsl, urlparse

from alaiy_os_connector_competitor_sites.api.utils.deep import api_signatures
from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import merge_query, canonical_url

_OVERLAP_REJECT_THRESHOLD = 0.9  # page2 sharing >90% of page1's rows = same page, wrong key


def _row_key_set(rows):
    return {canonical_url(r.get("product_source_url", "")) for r in rows if r.get("product_source_url")}


def _overlap(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / max(len(a), len(b))


def detect_pagination(base_url, fetch_page):
    """fetch_page(url) -> list[dict] of candidate product rows for that URL.
    Returns (scheme, page1_rows). `scheme` is a dict describing the working
    pagination convention, or None if nothing could be verified — in which
    case `page1_rows` (the rows found on the FIRST page tried, which is
    genuinely page 1 of the listing) should be used directly by the caller
    rather than fetching the bare base_url a second time. On sites where
    that repeat fetch behaves differently (confirmed on this exact
    codebase against a real Shopify storefront: a fresh ?page=1 fetch found
    109 real product cards, but a second plain fetch of the un-paramed URL
    moments later found 0), re-fetching would silently throw away a good
    result.

    Scheme dict: {"key": str, "start": int, "step": int, "page_size": int|None}
    """
    existing_query = dict(parse_qsl(urlparse(base_url).query))
    first_page_rows = []

    # Special-case the "start=&sz=" (offset + page size) convention some
    # Salesforce Commerce Cloud sites use — e.g. Penningtons.
    if "start" in existing_query and "sz" in existing_query:
        page_size = int(existing_query["sz"]) if str(existing_query["sz"]).isdigit() else 48
        ok, page1_rows = _verify(base_url, fetch_page, "start", 0, page_size)
        if page1_rows:
            first_page_rows = page1_rows
        if ok:
            return {"key": "start", "start": 0, "step": page_size, "page_size": page_size}, page1_rows

    candidate_keys = api_signatures.PAGINATION_CANDIDATE_KEYS
    ordered_keys = [k for k in candidate_keys if k in existing_query] + [
        k for k in candidate_keys if k not in existing_query
    ]

    page1_rows_cache = None
    for key in ordered_keys:
        base_page = 0 if key in api_signatures.PAGINATION_ZERO_INDEXED_KEYS else 1
        step = 1
        ok, page1_rows = _verify(base_url, fetch_page, key, base_page, step, page1_rows_cache)
        if page1_rows_cache is None:
            page1_rows_cache = page1_rows
            if page1_rows:
                first_page_rows = page1_rows
        if ok:
            return {"key": key, "start": base_page, "step": step, "page_size": None}, page1_rows

    return None, first_page_rows


def _verify(base_url, fetch_page, key, base, step, page1_rows_cache=None):
    """Fetches page1/page2 for the given key+step and checks their row-sets
    actually differ — shared by both the numeric-key path and the
    start=&sz= offset special-case, which only ever differed in what values
    they passed in, not in the check itself."""
    page1_url = merge_query(base_url, **{key: base})
    page1_rows = page1_rows_cache if page1_rows_cache is not None else (fetch_page(page1_url) or [])
    page1_keys = _row_key_set(page1_rows)
    if not page1_keys:
        return False, page1_rows

    page2_url = merge_query(base_url, **{key: base + step})
    page2_rows = fetch_page(page2_url) or []
    page2_keys = _row_key_set(page2_rows)
    if not page2_keys:
        return False, page1_rows

    overlap = _overlap(page1_keys, page2_keys)
    return overlap < _OVERLAP_REJECT_THRESHOLD, page1_rows


def iter_pages(base_url, scheme, fetch_page, max_pages=200):
    """Yields (page_url, rows) for as many pages as `scheme` describes,
    stopping when 2 consecutive pages add nothing new or max_pages is hit.
    The caller (runner.py) is responsible for budget/memory checks between
    iterations — this generator just knows how to walk the pages."""
    key = scheme["key"]
    cursor = scheme["start"]
    step = scheme["step"]

    seen = set()
    consecutive_empty = 0

    for _ in range(max_pages):
        url = merge_query(base_url, **{key: cursor})
        rows = fetch_page(url) or []
        new_rows = []
        for r in rows:
            k = canonical_url(r.get("product_source_url", ""))
            if k and k not in seen:
                seen.add(k)
                new_rows.append(r)

        yield url, new_rows

        if not new_rows:
            consecutive_empty += 1
        else:
            consecutive_empty = 0
        if consecutive_empty >= 2:
            return

        cursor += step
