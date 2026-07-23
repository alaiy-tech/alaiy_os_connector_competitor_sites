import re
import frappe

_JEWELRY_KEYWORDS = [
    "jewelry", "jewellery", "ring", "rings", "necklace", "necklaces",
    "earring", "earrings", "bracelet", "bracelets", "bangle", "bangles",
    "anklet", "anklets", "pendant", "pendants", "brooch", "brooches",
    "chain", "chains", "charm", "charms", "cufflink", "cufflinks",
]
_JEWELRY_RE = re.compile(r"\b(" + "|".join(_JEWELRY_KEYWORDS) + r")\b", re.IGNORECASE)


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
    return bool(_JEWELRY_RE.search(haystack))


def _strip_html(html):
    if not html:
        return ""
    return re.sub(r"<[^>]+>", " ", html).strip()


def _base_url(site_url):
    from urllib.parse import urlparse
    p = urlparse(site_url)
    return f"{p.scheme}://{p.netloc}"


def _collection_handle(site_url):
    from urllib.parse import urlparse
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


def _fetch_products(session, endpoint, product_limit):
    """Paginate a Shopify products.json endpoint and return all products up to limit."""
    products = []
    next_url = endpoint
    while next_url:
        try:
            r = session.get(next_url, timeout=20)
            if r.status_code != 200:
                break
            batch = r.json().get("products", [])
        except ValueError:
            break
        except Exception as e:
            frappe.log_error(f"Shopify fetch failed: {e}", "Scraper")
            break

        if not batch:
            break

        for p in batch:
            if not _is_jewelry(p.get("product_type"), p.get("tags"), p.get("title")):
                continue
            image = (p.get("images") or [{}])[0].get("src", "")
            if not image:
                continue
            variant = (p.get("variants") or [{}])[0]
            handle_val = p.get("handle", "")
            base = endpoint.split("/collections/")[0].split("/products")[0]
            products.append({
                "product_name": p.get("title", ""),
                "product_image_url": image,
                "product_source_url": f"{base}/products/{handle_val}" if handle_val else "",
                "price": variant.get("price", ""),
                "sku": variant.get("sku", ""),
                "description": _strip_html(p.get("body_html", "")),
                "category": p.get("product_type", ""),
            })
            if product_limit and len(products) >= product_limit:
                return products

        next_url = _parse_link_header(r.headers.get("Link"))
    return products


def _scrape_shopify(site_url, product_limit=500):
    import requests
    base = _base_url(site_url)
    handle = _collection_handle(site_url)

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    if handle:
        # Try the specific collection first
        products = _fetch_products(session, f"{base}/collections/{handle}/products.json?limit=250", product_limit)
        # If the collection returned very few, also pull from root to get more
        if len(products) < min(product_limit, 20):
            frappe.logger().info(f"Collection '{handle}' only has {len(products)} products, pulling from root products.json too")
            root_products = _fetch_products(session, f"{base}/products.json?limit=250", product_limit)
            # merge, dedupe by product_source_url
            seen = {p["product_source_url"] for p in products}
            for p in root_products:
                if p["product_source_url"] not in seen:
                    products.append(p)
                    seen.add(p["product_source_url"])
                    if product_limit and len(products) >= product_limit:
                        break
    else:
        products = _fetch_products(session, f"{base}/products.json?limit=250", product_limit)

    return products
