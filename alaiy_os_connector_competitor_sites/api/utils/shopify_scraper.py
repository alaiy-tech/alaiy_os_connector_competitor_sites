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


def _fetch_products(session, endpoint, skip_urls=None):
    """Paginate a Shopify products.json endpoint and return every jewelry product,
    plus a count of how many were skipped because they're already scraped previously
    (in skip_urls), so a repeat scrape finds new ones instead of re-saving old ones."""
    skip_urls = skip_urls or set()
    products = []
    skipped = 0
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
            handle_val = p.get("handle", "")
            base = endpoint.split("/collections/")[0].split("/products")[0]
            source_url = f"{base}/products/{handle_val}" if handle_val else ""
            if not source_url:
                continue
            if source_url in skip_urls:
                skipped += 1
                continue
            if not _is_jewelry(p.get("product_type"), p.get("tags"), p.get("title")):
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

        next_url = _parse_link_header(r.headers.get("Link"))
    return products, skipped


def _scrape_shopify(site_url, skip_urls=None):
    import requests
    base = _base_url(site_url)
    handle = _collection_handle(site_url)

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    skipped_total = 0

    if handle:
        # Try the specific collection first
        products, skipped = _fetch_products(session, f"{base}/collections/{handle}/products.json?limit=250", skip_urls)
        skipped_total += skipped
        # If the collection returned very few, also pull from root to get more
        if len(products) < 20:
            frappe.logger().info(f"Collection '{handle}' only has {len(products)} products, pulling from root products.json too")
            root_products, root_skipped = _fetch_products(session, f"{base}/products.json?limit=250", skip_urls)
            skipped_total += root_skipped
            # merge, dedupe by product_source_url
            seen = {p["product_source_url"] for p in products}
            for p in root_products:
                if p["product_source_url"] not in seen:
                    products.append(p)
                    seen.add(p["product_source_url"])
    else:
        products, skipped_total = _fetch_products(session, f"{base}/products.json?limit=250", skip_urls)

    return products, skipped_total
