"""Product extraction, cheapest-first:

  tier 0  — products.json probe (no browser at all): reuses the existing
            Shopify scraper, since a meaningful fraction of "unknown" sites
            turn out to be Shopify stores under a different domain.
  tier 1  — discover and replay the page's own JSON API (deep/discovery.py) --
            handled by runner.py before this module's tiers run at all.
  tier 2a — embedded SSR state: many frameworks embed the whole initial
            payload as a window global (Next.js's __NEXT_DATA__, Nuxt's
            __NUXT__, or a bespoke __INITIAL_STATE__/__PRELOADED_STATE__/
            Apollo/Redux store) rather than fetching it via a client-visible
            XHR/fetch call at all -- confirmed live: tier 1's network
            intercept found nothing on a real Next.js listing page because
            the product grid was server-rendered directly into the initial
            payload, never fetched client-side.
  tier 2b — JSON-LD embedded in the page HTML (Product / ItemList schema.org)
  tier 3  — generic DOM card extraction: every <a href> that wraps an <img>
            and has price-like text nearby, which is what a product grid
            tile looks like on virtually every storefront regardless of
            framework (Salesforce Commerce Cloud, Magento, bespoke themes).
"""

import json
import re

from alaiy_os_connector_competitor_sites.api.utils.deep import api_signatures
from alaiy_os_connector_competitor_sites.api.utils.shopify_scraper import _scrape_shopify

_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def try_products_json(site_url, skip_urls=None):
    """Tier 0. Returns (rows, already_skipped) same shape as _scrape_shopify,
    or ([], 0) if this doesn't look like a Shopify store at all."""
    try:
        rows, skipped = _scrape_shopify(site_url, skip_urls=skip_urls or set())
        return rows, skipped
    except Exception:
        return [], 0


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
    other non-JSON-LD JSON <script> tag on the page, and for whichever one
    exists, reuses discovery.py's generic array-scoring and row-mapping to
    find and map the product list inside it -- same "find the product
    array in a JSON blob" problem whether that blob came from a network
    response (tier 1) or an embedded payload (here), so no separate scoring
    logic needed. Returns [] if nothing found scores as containing a
    product list. Returns on the FIRST source that yields real rows --
    doesn't keep scanning once one candidate works."""
    from alaiy_os_connector_competitor_sites.api.utils.deep import discovery

    def _try(data):
        if not data:
            return []
        found = discovery.best_product_array(data)
        if not found:
            return []
        _path, unwrap_key, arr, _score = found
        rows = [discovery.map_generic_row(item, base_url, unwrap_key) for item in arr]
        return [r for r in rows if r]

    for name in api_signatures.EMBEDDED_JSON_GLOBALS:
        try:
            data = page.evaluate(f"() => window.{name} || null")
        except Exception:
            data = None
        rows = _try(data)
        if rows:
            return rows

    try:
        script_texts = page.evaluate(_OTHER_JSON_SCRIPT_TAGS_JS) or []
    except Exception:
        script_texts = []
    for text in script_texts:
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        rows = _try(data)
        if rows:
            return rows

    return []


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
