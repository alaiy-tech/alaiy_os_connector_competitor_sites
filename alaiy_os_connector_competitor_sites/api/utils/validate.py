"""Product-row validation shared by the Deep scraper (and usable by any
future method). Fixes the class of bug seen on Anthropologie, where the
extractor returned nav links (/new-clothes, /bottoms, /30-off-summer...) as
"products" because nothing checked whether a scraped row actually looked
like a product before saving it.

Nothing here is destroyed silently — every rejection carries a reason so the
caller can log it, tally it, and (if the reject rate is too high) surface an
honest "this listing URL is probably wrong" message instead of quietly
polluting the DB.
"""

import re
from urllib.parse import urlparse

from alaiy_os_connector_competitor_sites.api.utils.deep import api_signatures
from alaiy_os_connector_competitor_sites.api.utils.shopify_scraper import _is_jewelry

# Paths that are almost always navigation/category/utility pages, not
# products, unless a product marker (below) also appears in the path.
# Word list lives in api_signatures.py alongside every other known-
# signature list this connector uses.
_NAV_BLOCKLIST_RE = re.compile(
    r"/(" + "|".join(api_signatures.NAV_BLOCKLIST_WORDS) + r")(/|$|\?)",
    re.IGNORECASE,
)

# Any of these appearing in the path is a strong positive signal this IS a
# product page, even if a nav-blocklist word also appears earlier in it
# (e.g. /shop/jewelry/products/gold-hoop-earrings).
_PRODUCT_MARKER_RE = re.compile(
    r"/(" + "|".join(api_signatures.PRODUCT_MARKER_PATTERNS) + r")(/|$|\?)"
    r"|" + api_signatures.PRODUCT_MARKER_NUMERIC_ID_PATTERN,
    re.IGNORECASE,
)

_NAV_WORD_NAMES = set(api_signatures.NAV_WORD_NAMES)

_PRICE_RE = re.compile(r"[-+]?\d[\d,.\s']*\d|\d")

_JEWELRY_KEYWORD_EXTRA = api_signatures.JEWELRY_KEYWORDS_EXTENDED


def _strip_www(host):
    return host[4:] if host.startswith("www.") else host


def _same_registrable_domain(url, listing_url):
    try:
        a = _strip_www(urlparse(url).netloc.lower())
        b = _strip_www(urlparse(listing_url).netloc.lower())
    except Exception:
        return False
    return bool(a) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def parse_price(raw):
    """Best-effort numeric price from a raw scraped string. Returns
    (amount: float|None, currency: str|None). Never raises; on ambiguity
    returns None rather than guessing wrong."""
    if not raw:
        return None, None
    s = str(raw).strip()

    currency = None
    if "$" in s:
        currency = "USD"
    elif "£" in s:
        currency = "GBP"
    elif "€" in s:
        currency = "EUR"
    elif re.search(r"\bUSD\b", s, re.IGNORECASE):
        currency = "USD"
    elif re.search(r"\bGBP\b", s, re.IGNORECASE):
        currency = "GBP"
    elif re.search(r"\bEUR\b", s, re.IGNORECASE):
        currency = "EUR"

    # A range or sale/was pair — take the first (usually lowest / current) number.
    matches = _PRICE_RE.findall(s)
    if not matches:
        return None, currency

    first = matches[0]
    # Decide decimal vs thousands separator: last separator followed by
    # exactly 1-2 digits is the decimal point.
    cleaned = re.sub(r"[^\d.,]", "", first)
    m = re.search(r"[.,](\d{1,2})$", cleaned)
    if m:
        decimal_part = m.group(1)
        int_part = re.sub(r"[.,]", "", cleaned[: -len(decimal_part) - 1])
        amount_str = f"{int_part}.{decimal_part}"
    else:
        amount_str = re.sub(r"[.,]", "", cleaned)

    try:
        amount = float(amount_str)
    except ValueError:
        return None, currency

    if amount < 0.5 or amount > 100000:
        return None, currency  # implausible — don't guess
    return amount, currency


def is_jewelry_extended(product_type, tags, title):
    """_is_jewelry plus a handful of terms missing from the original keyword
    list that under-collect on general fashion sites' jewelry categories."""
    if _is_jewelry(product_type, tags, title):
        return True
    haystack = " ".join([product_type or "", title or ""]).lower()
    return any(kw in haystack for kw in _JEWELRY_KEYWORD_EXTRA)


def validate_row(row, listing_url):
    """row: dict with product_name, product_source_url, product_image_url,
    price (raw string), sku (optional).
    Returns (accepted: bool, reason: str). reason is "" when accepted,
    otherwise one of: off_site, is_listing_or_home, nav_url_shape,
    low_substance, bad_name."""
    url = (row.get("product_source_url") or "").strip()
    name = (row.get("product_name") or "").strip()

    if not url:
        return False, "missing_url"

    if not _same_registrable_domain(url, listing_url):
        return False, "off_site"

    path = urlparse(url).path.rstrip("/") or "/"
    listing_path = urlparse(listing_url).path.rstrip("/") or "/"
    if path == listing_path or path == "" or path == "/":
        return False, "is_listing_or_home"

    has_product_marker = bool(_PRODUCT_MARKER_RE.search(path))
    if _NAV_BLOCKLIST_RE.search(path) and not has_product_marker:
        return False, "nav_url_shape"

    if not name or len(name) < 3 or len(name) > 200:
        return False, "bad_name"
    if name.lower() in _NAV_WORD_NAMES:
        return False, "bad_name"
    if re.match(r"^\d{1,3}%\s*off", name, re.IGNORECASE):
        return False, "bad_name"

    # Substance test: a real product-grid tile carries at least 2 of these 3
    # signals. A nav link (the Anthropologie failure) carries none.
    price_amount, _ = parse_price(row.get("price"))
    has_price = price_amount is not None
    has_image = bool((row.get("product_image_url") or "").strip())
    has_sku = bool((row.get("sku") or "").strip())
    signals = sum([has_price, has_image, has_sku or has_product_marker])
    if signals < 2:
        return False, "low_substance"

    return True, ""
