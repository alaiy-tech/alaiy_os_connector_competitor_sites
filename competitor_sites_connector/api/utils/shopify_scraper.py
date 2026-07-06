import re
import frappe

JEWELRY_KEYWORDS = {
    "jewelry", "jewellery", "jewel", "accessory", "accessories",
    "earring", "necklace", "bracelet", "bangle", "ring", "pendant",
    "anklet", "brooch", "chain", "choker", "cuff", "stud", "hoop",
}


def _strip_html(html):
    if not html:
        return ""
    return re.sub(r"<[^>]+>", " ", html).strip()


def _base_url(site_url):
    from urllib.parse import urlparse
    p = urlparse(site_url)
    return f"{p.scheme}://{p.netloc}"


def _collection_handle(site_url):
    """Extract /collections/{handle} from the site URL if present."""
    from urllib.parse import urlparse
    path = urlparse(site_url).path
    m = re.search(r"/collections/([^/?#]+)", path)
    return m.group(1) if m else None


def _is_jewelry(product):
    """Return True if product_type or tags contain any jewelry keyword."""
    text = " ".join([
        (product.get("product_type") or ""),
        " ".join(product.get("tags") or []),
        (product.get("title") or ""),
    ]).lower()
    return any(kw in text for kw in JEWELRY_KEYWORDS)



def _parse_link_header(header):
    """Extract the 'next' cursor URL from Shopify's Link response header."""
    if not header:
        return None
    import re
    for part in header.split(","):
        url_match = re.search(r'<([^>]+)>', part)
        rel_match = re.search(r'rel="([^"]+)"', part)
        if url_match and rel_match and rel_match.group(1) == "next":
            return url_match.group(1)
    return None


def _scrape_shopify(site_url, product_limit=500):
    import requests
    base = _base_url(site_url)
    handle = _collection_handle(site_url)

    if handle:
        endpoint = f"{base}/collections/{handle}/products.json?limit=250"
        filter_by_keyword = False
    else:
        endpoint = f"{base}/products.json?limit=250"
        filter_by_keyword = True

    products = []
    next_url = endpoint
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    while next_url:
        try:
            r = session.get(next_url, timeout=20)
            if r.status_code != 200:
                break
            batch = r.json().get("products", [])
        except Exception as e:
            frappe.log_error(f"Shopify fetch failed: {e}", "Scraper")
            break

        if not batch:
            break

        for p in batch:
            if filter_by_keyword and not _is_jewelry(p):
                continue

            image = (p.get("images") or [{}])[0].get("src", "")
            if not image:
                continue

            variant = (p.get("variants") or [{}])[0]
            handle_val = p.get("handle", "")
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
