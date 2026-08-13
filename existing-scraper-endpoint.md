# Existing Scraper Endpoint — Research Notes

Source app: `alaiy_os_connector_competitor_sites` (on the server at `~/alaiy_os_bench/apps/`)

---

## The Endpoint

`api/scrape_runner.py` — two whitelisted methods:

### `scrape_all_sites(sites=None)`

**Input:**
- `sites` — optional. Can be:
  - omitted / `null` → scrapes every active `Competitor Site` in the DB
  - a single site name string → `"White Fox Boutique"`
  - a list of site names → `["White Fox Boutique", "Lulus"]`
  - a JSON-encoded string of either of the above

**Output:**
```json
{
  "message": "Scrape enqueued for 3 site(s)",
  "scrape_id": "uuid-string",
  "log_names": {
    "White Fox Boutique": "Scrape Log name",
    "Lulus": "Scrape Log name"
  }
}
```

Returns immediately — the actual scraping runs in the background worker queue. `log_names` is what you pass to the polling endpoint.

---

### `get_scrape_progress(log_names)`

**Input:**
- `log_names` — dict of `{ site_name: log_doc_name }` (what `scrape_all_sites` returned)

**Output:**
```json
{
  "White Fox Boutique": {
    "status": "Running | Done | Failed | Queued",
    "products_saved": 42,
    "urls_found": 150,
    "already_in_db": 108,
    "log": "...",
    "method_used": "Shopify | Firecrawl",
    "log_name": "...",
    "elapsed_seconds": 34
  }
}
```

---

## How the Scraping Actually Works

The background worker (`api/utils/scrape_utils._bg_scrape_site`) runs per site and picks a method:

### Method 1 — Shopify (`api/utils/shopify_scraper.py`)
- Hits `{base_url}/products.json?limit=250` directly (no browser, no API key needed)
- Paginates via `Link: <next>` response headers
- Filters to jewelry-only products by matching keywords against `product_type`, `tags`, and `title`
- Falls back to `/collections/{handle}/products.json` if a collection URL is given
- Returns normalised product dicts + skipped count (already in DB)

### Method 2 — Firecrawl (`api/utils/scrape_utils._scrape_firecrawl`)
- Calls `https://api.firecrawl.dev/v2/scrape` — paid API
- Passes a JSON schema to Firecrawl instructing it to extract every product on the page
- Paginates by incrementing `?p=N` up to `MAX_PAGES = 30`
- Stops when a page returns no new products
- Has retry logic for rate limits and timeouts; raises `FirecrawlCreditsError` on 402

### Auto mode (default)
Tries Shopify first. If it finds products, stops there (doesn't burn Firecrawl credits). Falls back to Firecrawl only if Shopify returns nothing.

---

## Doctypes

### `Competitor Site`
| Field | Type | Notes |
|---|---|---|
| `site_name` | Data | Name (used as doctype name) |
| `site_url` | Small Text | The listing/collection URL to scrape |
| `categories` | Data | e.g. "Necklaces, Earrings" |
| `is_active` | Check | Whether to include in "scrape all" runs |
| `scrape_method` | Select | `Auto / Shopify / Firecrawl` |

### `Scraped Product`
| Field | Type | Notes |
|---|---|---|
| `id` | Data | UUID, used as name |
| `scrape_id` | Data | Batch UUID from the run |
| `product_name` | Data | |
| `product_image_url` | Data | |
| `source_product_url` | Data | Unique — dedup key |
| `source_site` | Link → Competitor Site | |
| `source_site_url` | Data | |
| `sku` | Data | |
| `categories` | Data | |
| `source_price` | Data | String e.g. "29.99" |
| `description` | Small Text | |
| `scraped_at` | Datetime | |
| `review_status` | Select | For Jackie's review workflow |
| `notes` | Small Text | |

### `Scrape Log`
| Field | Type | Notes |
|---|---|---|
| `site_name` | Data | |
| `site_url` | Data | |
| `scrape_id` | Data | Batch UUID |
| `status` | Select | `Queued / Running / Done / Failed` |
| `method_used` | Data | `Shopify / Firecrawl` |
| `urls_found` | Int | Total products found on site |
| `already_in_db` | Int | How many were skipped (dedup) |
| `products_saved` | Int | Net new products inserted |
| `started_at` | Datetime | |
| `completed_at` | Datetime | |
| `log` | Long Text | Error messages / status notes |

---

## Normalised Product Schema (what gets saved)

```python
{
    "product_name":       str,
    "product_image_url":  str,   # first image only
    "product_source_url": str,   # dedup key, truncated to 140 chars
    "price":              str,   # e.g. "29.99"
    "sku":                str,
    "description":        str,
    "category":           str,
}
```

---

## API Key

Firecrawl API key is read from (in order):
1. `site_config.json` → `firecrawl_api_key`
2. `Stellar Brands Connector Settings` doctype → `sb_firecrawl_api_key` (password field)

---

## Key Details to Know

- **Dedup** is by `source_product_url` — exact URL match, truncated to 140 chars
- **Queue** is `default` (not `long`), timeout 600s per site
- **Shopify filter** — only saves products where `product_type`, `tags`, or `title` contain a jewelry keyword. Non-jewelry items on a Shopify store are skipped entirely.
- **Firecrawl wait** — sends `{"type": "wait", "milliseconds": 3500}` action before extraction to let JS grids hydrate
- **Pagination** — Shopify uses `Link` header; Firecrawl uses `?p=N` query param
- The `scrape_method` field on `Competitor Site` controls which method is forced. `Auto` is the default and tries Shopify first.
