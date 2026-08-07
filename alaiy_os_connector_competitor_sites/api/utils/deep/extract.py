"""Product extraction, cheapest-first:

  tier 0   — products.json probe (no browser at all): reuses the existing
             Shopify scraper, since a meaningful fraction of "unknown" sites
             turn out to be Shopify stores under a different domain.
  tier 0.5 — sitemap.xml product-URL discovery (no browser, platform-
             agnostic): most storefronts of any platform publish a
             sitemap listing every product page directly -- cheaper and
             more complete than any listing-grid tier, when present.
  tier 1   — discover and replay the page's own JSON API (deep/discovery.py) --
             handled by runner.py before this module's tiers run at all.
  tier 2a  — embedded SSR state: many frameworks embed the whole initial
             payload as a window global (Next.js's __NEXT_DATA__, Nuxt's
             __NUXT__, or a bespoke __INITIAL_STATE__/__PRELOADED_STATE__/
             Apollo/Redux store) rather than fetching it via a client-visible
             XHR/fetch call at all -- confirmed live: tier 1's network
             intercept found nothing on a real Next.js listing page because
             the product grid was server-rendered directly into the initial
             payload, never fetched client-side.
  tier 2b  — JSON-LD embedded in the page HTML (Product / ItemList schema.org)
  tier 3   — generic DOM card extraction: every <a href> that wraps an <img>
             and has price-like text nearby, which is what a product grid
             tile looks like on virtually every storefront regardless of
             framework (Salesforce Commerce Cloud, Magento, bespoke themes).
"""

import json
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from alaiy_os_connector_competitor_sites.api.utils.deep import api_signatures
from alaiy_os_connector_competitor_sites.api.utils.shopify_scraper import _scrape_shopify

_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def try_products_json(site_url, skip_urls=None, filter_jewelry=True, categories=None):
    """Tier 0. Returns (rows, already_skipped) same shape as _scrape_shopify,
    or ([], 0) if this doesn't look like a Shopify store at all."""
    try:
        rows, skipped = _scrape_shopify(
            site_url, skip_urls=skip_urls or set(), filter_jewelry=filter_jewelry, categories=categories)
        return rows, skipped
    except Exception:
        return [], 0


_SITEMAP_FETCH_TIMEOUT = 12
_MAX_CHILD_SITEMAPS = 6  # safety cap when no child sitemap is obviously product-named
_MAX_SITEMAP_URLS = 5000  # a catalog sitemap can legitimately be huge; cap the raw pull, not just the returned list
_PRODUCT_URL_RE = re.compile(r"/(product|products|p)(/|$)", re.IGNORECASE)


def _local_tag(elem):
    """ElementTree keeps the sitemap XML namespace in every tag
    ('{http://www.sitemaps.org/schemas/sitemap/0.9}urlset') -- strip it so
    callers can match on the bare tag name regardless of which namespace
    URI (or none) a given site's sitemap declares."""
    tag = elem.tag
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _fetch_xml(session, url):
    try:
        resp = session.get(url, timeout=_SITEMAP_FETCH_TIMEOUT)
        if resp.status_code != 200 or not resp.content:
            return None
        return ElementTree.fromstring(resp.content)
    except Exception:
        return None


def _robots_sitemap_urls(session, base):
    try:
        resp = session.get(urljoin(base, "/robots.txt"), timeout=_SITEMAP_FETCH_TIMEOUT)
        if resp.status_code != 200:
            return []
        return [
            line.split(":", 1)[1].strip()
            for line in resp.text.splitlines()
            if line.lower().startswith("sitemap:")
        ]
    except Exception:
        return []


def discover_sitemap_product_urls(site_url, limit=200):
    """Tier 0.5. Platform-agnostic: sitemap.xml is a generic SEO convention,
    not a Shopify-specific one -- most storefronts on any platform publish
    one, often broken into per-content-type files (sitemap_products_1.xml,
    products-sitemap.xml, etc). Cheaper than any listing-grid tier (no
    browser, no pagination guessing) and often more complete, since it's
    the site's own authoritative list of every product page. Returns a
    deduped list of product-page URLs, or [] if no usable sitemap exists.
    Callers are responsible for actually visiting each URL to extract the
    row -- this only discovers WHERE the products are, not what's on them."""
    import requests

    base = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    root = _fetch_xml(session, urljoin(base, "/sitemap.xml"))
    if root is None:
        for candidate in _robots_sitemap_urls(session, base):
            root = _fetch_xml(session, candidate)
            if root is not None:
                break
    if root is None:
        return []

    def _urls_from_urlset(node):
        out = []
        for url_el in node:
            if _local_tag(url_el) != "url":
                continue
            for child in url_el:
                if _local_tag(child) == "loc" and child.text:
                    out.append(child.text.strip())
                    break
        return out

    if _local_tag(root) == "urlset":
        all_urls = _urls_from_urlset(root)[:_MAX_SITEMAP_URLS]
    elif _local_tag(root) == "sitemapindex":
        child_locs = []
        for sm_el in root:
            if _local_tag(sm_el) != "sitemap":
                continue
            for child in sm_el:
                if _local_tag(child) == "loc" and child.text:
                    child_locs.append(child.text.strip())
                    break
        # Prefer child sitemaps whose own filename says "product" -- pulling
        # every child (page/category/blog/etc sitemaps included) on a large
        # catalog is real wasted work for no better a result.
        product_named = [u for u in child_locs if "product" in u.lower()]
        to_fetch = product_named or child_locs[:_MAX_CHILD_SITEMAPS]

        all_urls = []
        for child_url in to_fetch:
            child_root = _fetch_xml(session, child_url)
            if child_root is not None and _local_tag(child_root) == "urlset":
                all_urls.extend(_urls_from_urlset(child_root))
            if len(all_urls) >= _MAX_SITEMAP_URLS:
                break
        all_urls = all_urls[:_MAX_SITEMAP_URLS]
    else:
        return []

    # A sitemap fetched from a child file whose NAME already says "product"
    # is trusted as-is (some platforms use handles with no "/product" path
    # segment at all, e.g. /p/12345 or a bare slug) -- only path-filter when
    # pulling from a mixed/unlabelled sitemap, so page/category/blog URLs
    # that snuck in via the generic fallback don't get treated as products.
    product_like = [u for u in all_urls if _PRODUCT_URL_RE.search(urlparse(u).path)]
    candidates = product_like or all_urls

    seen = set()
    deduped = []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
        if len(deduped) >= limit:
            break
    return deduped


# Signature lists (window-global names, script MIME types) live in
# api_signatures.py alongside every other known-signature list this
# connector uses -- see EMBEDDED_JSON_GLOBALS / JSON_LD_SCRIPT_TYPE there.

# CSS selector for every JSON <script> tag that ISN'T JSON-LD (that one has
# its own dedicated extractor, extract_json_ld, since it has a known
# Product/ItemList shape). Catches frameworks that embed state via a JSON
# script tag under a bespoke id/type rather than a documented window
# global -- Nuxt 3's __NUXT_DATA__ payload, SvelteKit's fetched-data
# blocks, Qwik's state block, and any other bespoke convention the global
# list doesn't happen to name.
_OTHER_JSON_SCRIPT_TAGS_JS = (
    r"""
() => {
  const out = [];
  for (const el of document.querySelectorAll('script[type="application/json"]')) {
    if ((el.type || '').toLowerCase() === '"""
    + api_signatures.JSON_LD_SCRIPT_TYPE
    + r"""') continue;
    const text = el.textContent;
    if (text && text.trim()) out.push(text);
  }
  return out;
}
"""
)


def extract_embedded_json(page, base_url):
    """Tier 2a. Reads every known SSR-framework window global, then every
    other non-JSON-LD JSON <script> tag on the page, and reuses
    discovery.py's generic array-scoring and row-mapping on EACH one --
    same "find the product array in a JSON blob" problem whether that
    blob came from a network response (tier 1) or an embedded payload
    (here), so no separate scoring logic needed. Scans every source and
    keeps the best-scoring one, rather than returning on the first source
    that yields any rows at all -- confirmed live: a page can carry a
    real product array in one global (say __NEXT_DATA__) alongside a
    smaller, higher-scoring-by-coincidence footer/nav-link array in
    another (say a global site-header/-footer settings payload); the
    first-hit version returned the nav links and never even looked at the
    real one."""
    from alaiy_os_connector_competitor_sites.api.utils.deep import discovery

    def _try(data):
        if not data:
            return None
        found = discovery.best_product_array(data)
        if not found:
            return None
        _path, unwrap_key, arr, score = found
        rows = [discovery.map_generic_row(item, base_url, unwrap_key) for item in arr]
        rows = [r for r in rows if r]
        if not rows:
            return None
        return score, rows

    best = None

    for name in api_signatures.EMBEDDED_JSON_GLOBALS:
        try:
            data = page.evaluate(f"() => window.{name} || null")
        except Exception:
            data = None
        result = _try(data)
        if result and (best is None or result[0] > best[0] or (result[0] == best[0] and len(result[1]) > len(best[1]))):
            best = result

    try:
        script_texts = page.evaluate(_OTHER_JSON_SCRIPT_TAGS_JS) or []
    except Exception:
        script_texts = []
    for text in script_texts:
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        result = _try(data)
        if result and (best is None or result[0] > best[0] or (result[0] == best[0] and len(result[1]) > len(best[1]))):
            best = result

    return best[1] if best else []


def _flatten_ld_json(obj):
    """A page can have one <script> with a single object, an array, or a
    @graph — normalise to a flat list of dicts."""
    if isinstance(obj, list):
        out = []
        for item in obj:
            out.extend(_flatten_ld_json(item))
        return out
    if isinstance(obj, dict):
        if "@graph" in obj and isinstance(obj["@graph"], list):
            out = []
            for item in obj["@graph"]:
                out.extend(_flatten_ld_json(item))
            return out
        return [obj]
    return []


def extract_json_ld(html):
    """Returns a list of candidate product-row dicts found via JSON-LD
    Product or ItemList entries embedded in the page HTML."""
    rows = []
    for match in _JSON_LD_RE.finditer(html or ""):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue

        for node in _flatten_ld_json(data):
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]

            if "ItemList" in types:
                for element in node.get("itemListElement", []) or []:
                    item = element.get("item", element) if isinstance(element, dict) else None
                    if isinstance(item, dict):
                        row = _row_from_product_ld(item)
                        if row:
                            rows.append(row)
                continue

            if "Product" in types:
                row = _row_from_product_ld(node)
                if row:
                    rows.append(row)

    return rows


def extract_breadcrumb_category(html):
    """A page's `Product` JSON-LD block often has no `category` field at
    all (confirmed live: real fields present were name/image/description/
    color/sku/brand/offers/aggregateRating -- no category), but the SAME
    page frequently carries a separate `BreadcrumbList` JSON-LD block with
    the real category path (confirmed live: "Jewelry & Accessories" >
    "Jewelry"). Returns the deepest (most specific) breadcrumb entry's name
    as a fallback category, or "" if no BreadcrumbList is present. The
    top-level "home" crumb is skipped -- it's never a real category."""
    for match in _JSON_LD_RE.finditer(html or ""):
        try:
            data = json.loads(match.group(1).strip())
        except (ValueError, TypeError):
            continue
        for node in _flatten_ld_json(data):
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if "BreadcrumbList" not in types:
                continue
            items = node.get("itemListElement") or []
            named = [i.get("name") for i in items if isinstance(i, dict) and i.get("name")]
            named = [n for n in named if n.strip().lower() != "home"]
            if named:
                return named[-1]
    return ""


def _row_from_product_ld(node):
    if not isinstance(node, dict):
        return None
    name = node.get("name")
    url = node.get("url") or node.get("@id")
    if not name or not url:
        return None

    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")

    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    price = None
    if isinstance(offers, dict):
        price = offers.get("price") or offers.get("lowPrice")

    return {
        "product_name": str(name),
        "product_source_url": str(url),
        "product_image_url": str(image) if image else "",
        "price": str(price) if price is not None else "",
        "sku": str(node.get("sku") or ""),
        "description": str(node.get("description") or ""),
        "category": str(node.get("category") or ""),
    }


# JS run in-page via page.evaluate() to harvest generic product-grid cards.
# Kept deliberately simple: an <a href> that wraps (or is immediately
# adjacent to) an <img>, with some text nearby that looks like a price.
# This does not depend on any site-specific class names.
#
# Two real gaps confirmed live on a real test site's category listing
# (fixed below generically, not by hardcoding anything site-specific):
#   1. Price text can sit in a SIBLING container, not an ancestor of the
#      anchor -- the real markup had <a><h2>title</h2></a> and the price
#      span as siblings, both nested a few levels below a shared parent.
#      Walking 3 levels up from the anchor never reached that parent.
#   2. The VISIBLE price can be geo-localized to a currency our regex never
#      recognised (₹5,200 shown, not $35.50) while the real USD figure sits
#      in a data-* attribute on the same element (confirmed:
#      data-bp-lti="$35.50"). Not hardcoding that attribute name (site-
#      specific) -- instead scanning every element's data-* attribute VALUES
#      in the same scope for a $-prefixed figure, since a $-tagged value is
#      unambiguous regardless of what attribute carries it or what currency
#      the page chose to display.
_DOM_CARD_JS = r"""
() => {
  const usdRe = /\$\s?\d[\d,.\s]*\d/;
  const priceRe = /[$£€¥₹₩₺₽]\s?\d[\d,.\s]*\d|\d[\d,.\s]*\d\s?(USD|GBP|EUR|JPY|INR|KRW|TRY|RUB)/i;
  const results = [];
  const seen = new Set();

  function findPriceInScope(node) {
    // Prefer an unambiguous $-tagged data-* attribute anywhere in scope --
    // catches a USD figure hiding behind a geo-localized display currency.
    const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
    for (const el of all) {
      for (const attr of el.attributes || []) {
        const m = usdRe.exec(attr.value || '');
        if (m) return m[0];
      }
    }
    const m = (node.innerText || '').match(priceRe);
    return m ? m[0] : '';
  }

  const placeholderRe = /no-?preview|placeholder|blank\.(gif|png)|data:image\/gif;base64/i;

  function resolveImageUrl(img) {
    return img.getAttribute('data-src') || img.getAttribute('data-original') ||
           (img.getAttribute('srcset') || '').split(',').pop().trim().split(' ')[0] ||
           img.getAttribute('src') || '';
  }

  const anchors = Array.from(document.querySelectorAll('a[href]'));
  for (const a of anchors) {
    const imgs = Array.from(a.querySelectorAll('img'));
    const allImgs = imgs.length ? imgs : Array.from((a.closest('[class]') || a).querySelectorAll('img'));
    if (!allImgs.length) continue;
    const img = allImgs[0]; // for alt/title lookups below -- same across responsive variants

    const href = a.href;
    if (!href || seen.has(href)) continue;

    // Walk up to the shared per-card container -- real card content
    // (title, price) is often a sibling of the anchor's own ancestor
    // chain, only reachable from a shared parent a level or two up.
    // Bounded at 4: on the real test site's own markup, level 4 from the
    // anchor was already the multi-card grid wrapper (confirmed live) -- climbing
    // further would start pulling a DIFFERENT product's price/attributes
    // into scope via querySelectorAll('*'), which is worse than missing
    // the price entirely.
    let priceText = '';
    let node = a;
    for (let i = 0; i < 4 && node; i++) {
      priceText = findPriceInScope(node);
      if (priceText) break;
      node = node.parentElement;
    }

    // name: alt text, then aria-label, then anchor text, then title attr
    let name = img.getAttribute('alt') || a.getAttribute('aria-label') ||
               (a.innerText || '').trim().split('\n')[0] || img.getAttribute('title') || '';
    name = name.trim().slice(0, 200);

    // image: a card can carry more than one <img> (responsive/desktop vs
    // mobile variants) -- confirmed live: the FIRST <img> in DOM order was
    // a Next.js lazy-load failure placeholder (a "no-preview" fallback
    // src), the real CDN srcset was on a second <img>. Check every
    // candidate, skip anything that resolves to an obvious placeholder,
    // take the first real one.
    let imageUrl = '';
    for (const candidate of allImgs) {
      const url = resolveImageUrl(candidate);
      if (url && !placeholderRe.test(url)) {
        imageUrl = url;
        break;
      }
    }
    if (!imageUrl) imageUrl = resolveImageUrl(allImgs[0]);

    seen.add(href);
    results.push({
      product_name: name,
      product_source_url: href,
      product_image_url: imageUrl,
      price: priceText,
      sku: '',
      description: '',
      category: '',
    });
  }
  return results;
}
"""


def extract_dom_cards(page):
    try:
        return page.evaluate(_DOM_CARD_JS) or []
    except Exception:
        return []
