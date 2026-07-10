# Scraping Any E-Commerce Site with Firecrawl

A practical guide based on the Refinery Home catalog scraper. This pattern scrapes hundreds of products from any retailer — no Playwright, no Selenium, no CSS selectors.

---

## Why most scrapers stop at 8–10 products

Most connector scrapers read the category/listing page directly and paginate through it. You get whatever products are on page 1 (usually 8–12), and pagination is fragile — infinite scroll, JavaScript rendering, and bot detection all break it quickly.

The fix is to **never scrape the category page**. Use `map_url` instead.

---

## The two Firecrawl methods that matter

### 1. `map_url` — discover all product URLs upfront

```python
from firecrawl import V1FirecrawlApp

app = V1FirecrawlApp(api_key="YOUR_FIRECRAWL_API_KEY")

result = app.map_url(
    "https://www.example-retailer.com",
    search="dining chairs",   # keyword to filter the sitemap
    limit=150,                # max URLs to return
)

product_urls = result.links  # list of matching URLs
```

This crawls the site's sitemap and returns up to 150 URLs matching your keyword in one call. You get the full list of individual product page URLs without touching pagination at all.

### 2. `scrape_url` with a JSON schema — AI-powered structured extraction

```python
from firecrawl.v1.client import V1JsonConfig

PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "title":            {"type": "string", "description": "Product name"},
        "price":            {"type": "string", "description": "Current price with $ symbol"},
        "compare_at_price": {"type": "string", "description": "Original price if on sale"},
        "description":      {"type": "string", "description": "Full product description"},
        "sku":              {"type": "string", "description": "Product SKU or item number"},
        "images": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of full product image URLs (4-5 different angles)",
        },
    },
    "required": ["title", "price"],
}

result = app.scrape_url(
    "https://www.example-retailer.com/products/some-chair",
    formats=["extract", "markdown"],
    extract=V1JsonConfig(schema=PRODUCT_SCHEMA),
)

product = result.extract  # dict with title, price, description, images, sku
```

No CSS selectors. No XPath. Firecrawl's AI reads the page and fills the schema regardless of the site's HTML structure.

---

## Full working scraper

```python
"""
scrape_products.py
Scrapes all products matching a keyword from any retailer site.
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from firecrawl import V1FirecrawlApp
from firecrawl.v1.client import V1JsonConfig

FIRECRAWL_API_KEY = "YOUR_API_KEY"
SITE_ROOT         = "https://www.example-retailer.com"
SEARCH_KEYWORD    = "dining chairs"
TARGET_COUNT      = 50
WORKERS           = 4         # parallel product page requests
OUTPUT_FILE       = "products.json"

PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "title":            {"type": "string"},
        "price":            {"type": "string"},
        "compare_at_price": {"type": "string"},
        "description":      {"type": "string"},
        "sku":              {"type": "string"},
        "images":           {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "price"],
}

# URL filter — keep only individual product pages
PRODUCT_URL_RE = re.compile(
    r"(/products/|/p/[^/]+/|\.html$|/[a-z0-9-]+-by-[a-z0-9-]+)"
)
EXCLUDE_RE = re.compile(
    r"(/collections/?$|/category/?$|/search|/cart|/account|/blogs|\?|#)"
)

def is_product_url(url: str) -> bool:
    return not EXCLUDE_RE.search(url) and bool(PRODUCT_URL_RE.search(url))

def map_with_retry(app, root, keyword, limit):
    """Fetch product URLs with automatic backoff on rate limits."""
    for attempt in range(4):
        try:
            result = app.map_url(root, search=keyword, limit=limit)
            return result.links or []
        except Exception as e:
            if "429" in str(e) or "Rate limit" in str(e):
                wait = 60 * (attempt + 1)
                print(f"Rate limit — waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"map_url error: {e}")
                return []
    return []

def scrape_product(app, url):
    try:
        result = app.scrape_url(
            url,
            formats=["extract", "markdown"],
            extract=V1JsonConfig(schema=PRODUCT_SCHEMA),
        )
        data = result.extract if hasattr(result, "extract") else {}
        if not isinstance(data, dict):
            return None

        # Top up images from markdown if AI extraction missed some
        markdown = getattr(result, "markdown", "") or ""
        existing = data.get("images") or []
        if len(existing) < 5 and markdown:
            imgs = re.findall(
                r"https?://[^\s\)\"']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\)\"']*)?",
                markdown, re.IGNORECASE,
            )
            combined = list(dict.fromkeys(existing + imgs))
            data["images"] = combined[:5]

        return data if data.get("title") else None
    except Exception as e:
        print(f"  ERROR {url[:60]}: {e}")
        return None

def run():
    app = V1FirecrawlApp(api_key=FIRECRAWL_API_KEY)

    # Step 1: get all product URLs
    print(f"Mapping {SITE_ROOT} for '{SEARCH_KEYWORD}'...")
    all_urls = map_with_retry(app, SITE_ROOT, SEARCH_KEYWORD, TARGET_COUNT * 3)
    product_urls = [u for u in all_urls if isinstance(u, str) and is_product_url(u)]
    product_urls = list(dict.fromkeys(product_urls))[:TARGET_COUNT]
    print(f"Found {len(product_urls)} product URLs")

    # Step 2: scrape each product page in parallel
    products = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(scrape_product, app, url): url for url in product_urls}
        for future in as_completed(futures):
            url = futures[future]
            product = future.result()
            if product:
                products.append(product)
                print(f"  [{len(products)}/{len(product_urls)}] {product.get('title','?')[:60]}")
            else:
                print(f"  no data: {url[:60]}")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(products, f, indent=2)
    print(f"\nSaved {len(products)} products to {OUTPUT_FILE}")

if __name__ == "__main__":
    run()
```

---

## Setup

```bash
pip install firecrawl-py python-dotenv
```

Get an API key at [firecrawl.dev](https://firecrawl.dev). The free tier allows ~500 pages/month. For parallel scraping at scale, the Hobby plan ($16/mo) gives 3,000 pages/month.

---

## Key decisions explained

### Why `map_url` beats pagination

| Approach | Products reachable | Fragility |
|---|---|---|
| Scrape category page | 8–12 per page | Breaks on infinite scroll, JS rendering |
| Paginate category pages | ~100 if lucky | Breaks on bot detection, layout changes |
| `map_url` + sitemap | 150+ in one call | Stable — uses the site's own sitemap |

### Why parallel workers matter

Firecrawl scrapes one page in ~2–5 seconds. With `WORKERS=4`, 50 products take ~30 seconds instead of ~4 minutes.

### Why the image top-up step exists

The AI extraction sometimes returns only 1–2 images when a product has 5+ photos. The markdown fallback grabs all image URLs from the raw page content and fills the gap.

---

## Handling different site structures

The `PRODUCT_URL_RE` and `EXCLUDE_RE` patterns cover most common URL formats, but you may need to tune them per site:

```python
# Shopify sites
PRODUCT_URL_RE = re.compile(r"/products/[a-z0-9-]+$")

# Sites with /p/ pattern (Lumens, DWR)
PRODUCT_URL_RE = re.compile(r"/p/[^/]+/[^/?]+")

# Sites with .html product pages
PRODUCT_URL_RE = re.compile(r"/[a-z0-9-]+-\d+\.html$")
```

Run `map_url` once, print the first 20 URLs, and adjust the regex to match that site's product URL pattern.

---

## Rate limits and credits

| Firecrawl plan | Pages/month | Concurrent requests |
|---|---|---|
| Free | 500 | 2 |
| Hobby ($16/mo) | 3,000 | 5 |
| Standard ($83/mo) | 100,000 | 10 |

Keep `WORKERS` at or below the concurrent request limit for your plan. The `map_with_retry` function handles 429 rate-limit errors automatically with exponential backoff.
