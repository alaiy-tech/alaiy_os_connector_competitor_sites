# E-Commerce Product Scraper — Build Plan

**Goal:** A service that takes any e-commerce site URL and returns structured product data (name, price, image, description, link, source site), so products can be compared and sourced across competitors.

---

## 1. Architecture Overview

```
[Input: URL] → [Site Detector] → [Scraper Engine] → [Normalizer] → [Database] → [Filter/Query API]
```

- **Scraper Engine:** Node.js + Puppeteer (or Python + Playwright)
- **Storage:** PostgreSQL or MongoDB
- **Filter Layer:** Simple REST API or admin dashboard on top of the DB
- **Hosting:** Your existing small server to start; can scale to cloud VM or serverless functions later if needed

---

## 2. Phase 1 — Core Scraper (Week 1)

- [ ] Set up Node.js project with Puppeteer (or Playwright — better cross-browser support)
- [ ] Build a single endpoint: `POST /scrape` accepting `{ url }`
- [ ] Launch headless browser, load the page, wait for content to render
- [ ] Extract raw HTML/DOM and identify product-listing selectors manually for your first target site
- [ ] Parse out: product name, price, image URL, product page link, short description
- [ ] Return as JSON array

**Deliverable:** working scraper for one site, manually configured.

---

## 3. Phase 2 — Pagination & Full-Site Crawl (Week 1–2)

- [ ] Detect pagination pattern (numbered pages, "load more" button, or infinite scroll)
- [ ] Implement a loop that keeps scraping until no new products appear or a page limit is hit
- [ ] Add a product de-duplication check (by URL or SKU)
- [ ] Add configurable max-pages / max-products safety limit per run

**Deliverable:** scraper that pulls the full catalog (or a defined cap) from one site.

---

## 4. Phase 3 — Genericize Across Sites (Week 2–3)

- [ ] Build a lightweight "site config" system — a JSON/YAML file per site defining:
  - product container selector
  - name/price/image/link selectors
  - pagination type and selector
- [ ] Add a fallback generic extractor for unconfigured sites (heuristic-based: look for repeated card-like structures, price-like text patterns, etc.) — won't be perfect, but gives a baseline
- [ ] Add per-site rate limiting and delay config

**Deliverable:** ability to add a new competitor site by writing a config file instead of new code.

---

## 5. Phase 4 — Reliability & Anti-Blocking (Ongoing)

- [ ] Random delays between requests (2–5s, jittered)
- [ ] Rotate user-agent strings
- [ ] Add retry logic with backoff on failed requests
- [ ] Monitor for CAPTCHA/block pages and log them separately (don't try to bypass — treat as "manual review needed" flag)
- [ ] Respect `robots.txt` where feasible for long-term site relationships

---

## 6. Phase 5 — Storage & Normalization

- [ ] Define a unified product schema:
  ```json
  {
    "source_site": "string",
    "name": "string",
    "price": "number",
    "currency": "string",
    "image_url": "string",
    "product_url": "string",
    "description": "string",
    "scraped_at": "timestamp"
  }
  ```
- [ ] Store in PostgreSQL (better for structured filtering/joins) or MongoDB (better for schema flexibility across very different sites)
- [ ] Add a `category`/`tags` field if you want cross-site product matching later

---

## 7. Phase 6 — Filtering & Comparison Layer

- [ ] Build simple query endpoints: filter by price range, source site, keyword in name/description
- [ ] Optional: fuzzy-match similar products across sites (by name similarity or category) to build direct comparison views
- [ ] Optional: simple dashboard (even a spreadsheet export) to review sourcing options side by side

---

## 8. Suggested Timeline

| Week | Focus |
|------|-------|
| 1 | Core scraper + pagination for first site |
| 2 | Genericize config system, add 2nd/3rd site |
| 3 | Storage, normalization, filtering layer |
| 4+ | Anti-blocking hardening, add remaining sites |

---

## 9. Tech Stack Summary

- **Scraping:** Playwright (recommended over Puppeteer for better multi-engine support) or Puppeteer
- **Backend:** Node.js + Express (or Python + FastAPI if you prefer Python)
- **DB:** PostgreSQL (recommended for structured filtering)
- **Queue (optional, for scale):** BullMQ or a simple cron-based job runner if scraping many sites on a schedule
- **Hosting:** Your existing server to start

---

## Notes

- Start with **one site fully working end-to-end** before generalizing — don't build the config abstraction until you've hand-scraped at least 2 sites and see the actual variation.
- Public product listing pages generally carry lower legal risk than scraping behind logins or violating explicit ToS — worth a quick review of each target site's terms before scaling this up.
