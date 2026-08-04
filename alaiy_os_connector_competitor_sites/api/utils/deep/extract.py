"""Product extraction, cheapest-first:

  tier 0 — products.json probe (no browser at all): reuses the existing
           Shopify scraper, since a meaningful fraction of "unknown" sites
           turn out to be Shopify stores under a different domain.
  tier 2 — JSON-LD embedded in the page HTML (Product / ItemList schema.org)
  tier 3 — generic DOM card extraction: every <a href> that wraps an <img>
           and has price-like text nearby, which is what a product grid
           tile looks like on virtually every storefront regardless of
           framework (Salesforce Commerce Cloud, Magento, bespoke themes).

(Tier 1 — discovering and replaying a site's own XHR/JSON API — is the
highest-value tier for JS-heavy SPAs like ASOS/Anthropologie, but is not
implemented in this pass; sites that need it fall through to tier 3's DOM
extraction, which still works on anything that renders, just less
precisely. This is a known, deliberate scope cut — see pradyun-scraper-plan.md.)
"""

import json
import re

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
_DOM_CARD_JS = r"""
() => {
  const priceRe = /[$£€]\s?\d[\d,.\s]*\d|\d[\d,.\s]*\d\s?(USD|GBP|EUR)/i;
  const results = [];
  const seen = new Set();

  const anchors = Array.from(document.querySelectorAll('a[href]'));
  for (const a of anchors) {
    const img = a.querySelector('img') || (a.closest('[class]') || a).querySelector('img');
    if (!img) continue;

    const href = a.href;
    if (!href || seen.has(href)) continue;

    // price: look in the anchor itself, then walk up a couple of ancestors
    let priceText = '';
    let node = a;
    for (let i = 0; i < 3 && node; i++) {
      const m = (node.innerText || '').match(priceRe);
      if (m) { priceText = m[0]; break; }
      node = node.parentElement;
    }

    // name: alt text, then aria-label, then anchor text, then title attr
    let name = img.getAttribute('alt') || a.getAttribute('aria-label') ||
               (a.innerText || '').trim().split('\n')[0] || img.getAttribute('title') || '';
    name = name.trim().slice(0, 200);

    // image: prefer lazy-load attributes since real <img src> is often a
    // placeholder until the image actually scrolls into view
    const imageUrl = img.getAttribute('data-src') || img.getAttribute('data-original') ||
                      (img.getAttribute('srcset') || '').split(',').pop().trim().split(' ')[0] ||
                      img.getAttribute('src') || '';

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
