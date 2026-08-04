# Plan: "Deep" scrape method (Playwright) for `alaiy_os_connector_competitor_sites`

## Context

The app has two scrape methods today: **Shopify** (`products.json`, free, fast) and **Firecrawl** (paid LLM extraction API). Neither handles "any site, all pages" reliably:

- Firecrawl is credit-limited and its pagination is broken — `_page_url` replaces the *entire* query string with `?p=N`, but almost none of the 48 configured competitor sites use that convention (they use `?page=`, `?page_num=`, `?start=&sz=`, `?offset=`, path-based, etc). Production evidence: 33 Firecrawl runs found 3277 products across pagination attempts but only ever saved ~99 unique ones — every "next page" was re-fetching page 1.
- Shopify only works on Shopify stores.
- Neither has a generic DOM/JS-rendering fallback for the rest: Salesforce Commerce Cloud (Penningtons), Next.js/Nuxt SPAs (ASOS, Anthropologie), Magento, BigCommerce, and bespoke themes.

The user wants a third method — **"Deep"** — that can scrape *any* e-commerce listing page: discover pagination, handle JS rendering, and save results reliably, on a **2-vCPU / 3.7GB RAM / no-swap EC2 box** that already runs Playwright successfully for a sibling app (`alaiy_os_stellarbrands`'s FN portal automation).

Production forensics turned up load-bearing bugs that Deep would inherit if left alone:
1. `frappe.log_error(message, title)` — args swapped vs Frappe's `log_error(title, message)` signature. Caused a real crash: `(1406, "Data too long for column 'method'")`, which masked the underlying error.
2. `_save_products` has no per-row rollback. One oversized URL raises inside the loop; MariaDB aborts the transaction; every subsequent insert in that batch silently fails too. Confirmed in production: multiple "Failed to save product" errors for Anthropologie, and it's why the good products on that run never landed.
3. Dedup checks a *cleaned* URL (`_clean_url`, query-stripped + truncated) but `_save_products` inserts the *raw* URL — the two don't agree, so dedup is unreliable.
4. `_page_url`'s `?p=N` replace-the-whole-query bug (above).
5. `Scraped Product.source_product_url` is varchar(140) **UNIQUE**, `product_image_url` is varchar(140) — real CDN/query-string URLs exceed this, causing silent data loss (confirmed for Anthropologie's Contentful images).
6. 3 `Scrape Log` rows have been stuck in `status="Running"` since 2026-07-28 — there's no watchdog and no `scheduler_events` configured at all.
7. Anthropologie's `site_url` is a bare homepage; the extractor returned nav links (`/new-clothes`, `/bottoms`) as "products" — no row validation exists anywhere.

Per your decisions: fix the foundation first (bugs 1–4, schema for #5, watchdog for #6, validation for #7), then build Deep as an opt-in method, then fold it into `Auto` once proven on the 3-site test.

**Outcome:** `scrape_all_sites` / `get_scrape_progress` request/response shapes are **unchanged**. A site can be set to `scrape_method = "Deep"` and it will find and save products from virtually any listing page, survive the server's memory ceiling, never get stuck, and leave a debuggable trail when something goes wrong.

---

## Phase 0 — Fix the foundation (independently valuable, ships first)

All in `alaiy_os_connector_competitor_sites/api/utils/scrape_utils.py` and `shopify_scraper.py`.

1. **`_log_error(title, message)` wrapper** — always call with keyword args, clamp `title` to 100 chars, wrap in its own try/except so logging itself can never crash a run. Replace all 5 existing swapped call sites.
2. **Per-row savepoints in `_save_products`** — `frappe.db.savepoint("prod")` before each insert; `frappe.db.rollback(save_point="prod")` + counter increment on failure; single `frappe.db.commit()` per batch. A `frappe.DuplicateEntryError` counts as `already_in_db`, not a failure.
3. **One canonical URL function**, used by *both* dedup and save so they can never disagree again:
   ```python
   def canonical_url(url: str) -> str:
       # lowercase scheme+host, strip www./default port/fragment,
       # drop tracking params (utm_*, gclid, fbclid, msclkid, ref, ...),
       # keep semantic params (variant, sku, pid, id, color, size, ...),
       # sort remaining params, strip trailing slash / index.html
   def url_hash(url: str) -> str:
       return hashlib.sha256(canonical_url(url).encode()).hexdigest()  # 64 chars, fixed length
   ```
4. **Fix `_page_url`** → replace with `merge_query(url, **params)` that preserves existing query params and only overrides the given key. This alone repairs Firecrawl's pagination too (bonus, zero extra cost).
5. **Chunk `_already_in_db`** — batch the `IN (...)` lookup at 500 keys per query (current unbounded query will hit `max_allowed_packet` at Deep's volumes).

## Phase 1 — Schema changes

`Scraped Product` doctype (`.../doctype/scraped_product/scraped_product.json`):

| field | change |
|---|---|
| `url_hash` | **new**, Data(64), `unique: 1` — the only dedup key going forward |
| `source_product_url` | widen to **Small Text**, drop `unique: 1` (kept for display/search, no longer the dedup key) |
| `product_image_url` | widen to **Small Text** (fixes the Contentful/CDN overflow) |
| `price_amount` | new, Float — parsed numeric price |
| `price_currency` | new, Data(8) |

`Competitor Site` doctype:
- `scrape_method` Select gains `Deep` → options become `Auto\nShopify\nFirecrawl\nDeep`
- `listing_urls` — new, Small Text, newline-separated (fixes the bare-homepage case; falls back to `site_url` if blank)
- `deep_state` — new, Long Text, hidden — JSON resume cursor + discovered pagination scheme
- `filter_jewelry` — new, Check, default 1 (per-site override for the adaptive filter)
- `require_price` — new, Check, default 1

`Scrape Log` doctype:
- `status` Select extended: `Queued\nRunning\nDone\nPartial\nFailed` (add `Partial` — a budget-exhausted or memory-limited stop with real products saved is success, not failure; **do not** add `Blocked`, keep that as `Failed` + a log message, to avoid a 3rd new status the frontend must learn)
- `last_heartbeat` — new, Datetime — liveness signal, updated at least every 15s during a run
- `summary_line` — new, Data — one-line, human-actionable cause+remedy, rendered by the UI

**Migration** (`patches/v1_0/add_deep_scrape_fields.py`, registered in `patches.txt` under `[post_model_sync]`):
1. Doctype sync adds the new columns.
2. Backfill `url_hash = url_hash(source_product_url)` for all 794 existing rows, batched 500 at a time.
3. On backfill collision (two existing truncated URLs hash differently but happen to collide — unlikely but check): keep the oldest by `creation`, set `review_status="Skipped"` + a note on the rest rather than deleting.
4. Drop the old unique index on `source_product_url`, add unique index on `url_hash`.

## Phase 2 — Watchdog (fixes the 3 stuck rows, prevents new ones)

New file `api/utils/scrape_watchdog.py`:
```python
def reap_stale_scrape_logs():
    # status="Running" AND (last_heartbeat or started_at) older than 120s -> stale
    #   products_saved > 0 -> Partial, else -> Failed
    #   summary_line: "Run ended abnormally — no heartbeat for Nm. X products saved."
    # status="Queued" AND age > 30min with no corresponding rq job -> Failed
    # UPDATE ... WHERE status='Running' (conditional, can't race a worker that's finalizing)
```

Two triggers (redundant on purpose, since `scheduler_events` is fully commented out today and may not be reliably running):
1. **Opportunistic**: call `reap_stale_scrape_logs()` at the top of `scrape_all_sites()` and `get_scrape_progress()`, rate-limited via a Redis `nx` key (once per 60s) — fixes stale rows the moment the user opens the UI, no infra dependency.
2. **Cron backup**: uncomment and add to `hooks.py`:
   ```python
   scheduler_events = {"cron": {"*/5 * * * *": ["alaiy_os_connector_competitor_sites.tasks.reap_stale_scrape_logs"]}}
   ```
   New thin `tasks.py` that imports and calls it.

This closes the 3 existing zombie rows on first run and makes a stuck job structurally impossible going forward.

## Phase 3 — Product-row validation (shared, used by Deep and retrofit-able to others)

New `api/utils/validate.py`:

```python
def validate_row(row: dict, listing_url: str, filter_jewelry: bool) -> tuple[bool, str]:
    """Returns (accepted, rejection_reason). Rejection reasons are tallied and
    sampled in the log, never silently dropped."""
```

Rejection rules (in order — the Anthropologie fix):
1. URL missing, or not same registrable domain as the listing → reject `off_site`
2. Canonical URL equals the listing URL or the site root → reject `is_listing_or_home`
3. Path matches a nav/category blocklist (`/shop/`, `/collections?/`, `/category/`, `/sale/`, `/new-arrivals/`, `/account/`, `/cart/`, `/blog/`, `/help/`, ...) **and** doesn't also contain a product marker (`/products/`, `/p/`, `/dp/`, `/item/`, or a slug with a ≥4-digit id) → reject `nav_url_shape`
4. **Substance test**: at least 2 of {price parses > 0, image URL present, name is 3–200 chars and not a nav word} must hold → reject `low_substance` (this is the actual Anthropologie catch — those nav rows had zero of the three)
5. Adaptive jewelry filter (only if `filter_jewelry`): apply `_is_jewelry`-style keyword match (extended with `huggie, solitaire, signet, cuff, tennis, stud, charm bar, choker, bangle`); **but** first measure the match rate across the run — if <30% of otherwise-valid rows match, keep everything and log `"jewelry filter matched only 12% — likely missing vocabulary, kept all rows"` instead of silently discarding the majority.

Run-level sanity gate in the caller: if rejected/candidates > 40% on a page or across the run, still save what passed but write an explicit `summary_line` naming the likely cause (e.g. "site_url looks like a homepage, not a listing").

## Phase 4 — The Deep scraper itself

New package `api/utils/deep/`:

```
deep/__init__.py       exports scrape_deep()
deep/budget.py         Budget (time), ResourceGuard (MemAvailable check)
deep/browser.py        launch flags, chromium_headless_shell binary pin, Redis+flock slot, teardown
deep/discovery.py      XHR/fetch interception during first page load -> ApiCandidate
deep/paginate.py       merge_query(), pagination-mode detection + verification, load-more, scroll
deep/extract.py        API JSON path / JSON-LD / __NEXT_DATA__ / DOM card extraction
deep/runner.py         scrape_deep() — orchestrates the tier cascade, calls validate.py, saves incrementally
deep/cli.py            bench execute entrypoint for local dev (test scenario 1)
```

### Tier cascade (each tier only runs if the previous found nothing usable)

0. **HTTP probes, no browser**: try `products.json` (Shopify pattern, reused from `shopify_scraper`), sitemap harvest as a fallback URL pool. If ≥20 products found, done — never launch a browser.
1. **API discovery**: open one page in Playwright, intercept XHR/fetch responses during load, find the JSON endpoint serving product arrays. Paginate it via **in-page `fetch()`** (not a separate `requests` session — this preserves the exact cookie/TLS/header fingerprint the site already trusts, avoiding the 403-after-3-calls failure mode a bare `requests.Session` hits on Akamai/PerimeterX-protected sites like ASOS/Anthropologie).
2. **Embedded JSON**: parse `page.content()` for JSON-LD `ItemList`, `__NEXT_DATA__`, `__NUXT__` — paginate by URL with plain `requests` (no browser needed per page once the shape is known).
3. **DOM extraction**: generic card-detection + URL-param pagination / "Load More" click loop / infinite scroll with harvest-and-prune.
4. **Firecrawl** (existing, reused) as the last resort if Deep's own tiers all come up empty.

### Pagination detection (fixes bug #4 generically, not just for Firecrawl)

Priority order: `<link rel="next">` → learn from the page's own pagination widget (diff two visible page links, find the numeric query key or path segment that changes) → reuse whatever key is already in the configured URL → scan the discovered API's request shape for common page/offset/cursor key names → last-resort guess list.

**Every hypothesis is verified before trusting it**: fetch page 1 and page 2, compare canonical-key sets. If overlap >90% (or page 2 == page 1), reject and try the next candidate; log the rejection with its overlap ratio. This is the direct fix for the `?p=N` bug class — a wrong guess gets caught before it wastes the whole run.

Termination conditions (any one stops the loop): 2 consecutive pages with 0 new canonical keys, page-cap hit, declared total reached, 3 consecutive fetch errors, time budget exhausted, memory guard tripped.

### Memory safety (2GB / no-swap)

- **Binary**: `chromium_headless_shell-1228` (already on disk) — lower RSS than full Chromium, no UI/GPU/compositor layers.
- **Launch flags**: `--no-sandbox --disable-setuid-sandbox` (proven on this box by `fn_portal.py`), `--disable-dev-shm-usage`, `--disable-gpu`, `--renderer-process-limit=1`, `--js-flags=--max-old-space-size=192`, plus the standard background-feature-disable set. **Not** `--single-process` (crashy, deadlock-prone with `page.evaluate`).
- **Resource blocking**: abort `image`/`media`/`font`/`stylesheet` requests and a third-party tracker host blocklist. Image *URLs* still come from DOM attributes (`data-src`, `srcset`), so this loses no data — confirmed this is how the sibling app's driver was NOT doing it, but Deep should.
- **One browser at a time, host-wide.** Enforced three ways: (a) Deep jobs are enqueued to the `long` queue, which only one of the two supervisor workers serves, so RQ itself limits Deep to 1 concurrent; (b) a Redis semaphore key (`pw:slot`, TTL 180s, refreshed every 15s) as a belt-and-suspenders for any out-of-band invocation; (c) an unconditional pre-flight check of `/proc/meminfo` `MemAvailable` — below 700MB, don't launch at all; below 350MB mid-run, finish the current page, commit, stop as `Partial`.
- **`oom_score_adj`**: write `800` to `/proc/<chromium_pid>/oom_score_adj` right after launch, so if the kernel OOM-kills something under pressure, it takes the browser (recoverable — caught as a normal exception, run ends `Partial`) rather than MariaDB or the Frappe worker.
- **Context recycling**: close and reopen the browser context every 20 page loads to reclaim detached-document memory; full browser restart if RSS exceeds ~700MB.

### Chromium launch failure (new — not previously specced)

The plan assumed launch succeeds because the sibling app (`fn_portal.py`) proves it can on this exact box. That's true today, but Deep must not silently assume it stays true — a bench update, an OS package change, or a disk-space issue could break the launch path independently of anything Deep does. This needs its own detection and its own message, distinct from every other failure mode (bot-block, timeout, OOM) because the fix is operational, not a scraping problem.

**Preflight check, before any tier that needs a browser (i.e. before tier 1):**
1. Assert `chromium_headless_shell-1228` exists on disk at the path Playwright resolves (`playwright.chromium.executable_path` when `channel="chromium-headless-shell"`, or the equivalent lookup for the installed browser). Missing binary → fail immediately with a distinct reason, no launch attempt.
2. Attempt `sync_playwright().start()` + `chromium.launch(...)` wrapped in its own try/except, **separate from** the per-page navigation try/except. Anything raised here (missing shared libraries, `--no-sandbox` rejected by a hardened kernel, permission denied on the profile dir, port bind failure for `--remote-debugging-port`) is caught as its own class: `BrowserLaunchError`.
3. On `BrowserLaunchError`: do **not** retry in a loop (a broken launch path fails identically every time and retrying just burns the time budget). Fall straight to tier 0's results if any were already found; otherwise stop the run.
4. Terminal state: `Failed` (not `Partial` — zero products can be attributed to this browser, whatever tier-0 found is reported honestly, but the run did not get a fair chance at tiers 1–3).
5. `summary_line`: **`"Chromium failed to start — <short exception summary>. This is a server issue, not a site issue. Check `playwright install` / disk space / permissions."`** — deliberately worded to point at the environment, not the target site, so it's never confused with a per-site block (which would wrongly suggest "try Firecrawl for this one site" when the real fix is server-side and affects every Deep run).
6. Log section addition: a `BROWSER` line in the transcript before `TIER 1`, e.g. `BROWSER  chromium_headless_shell-1228 binary found · launch FAILED: <exception>` so this is visible in the same place as every other diagnostic, not buried in a stack trace.
7. Because this failure mode is environmental rather than per-site, if it happens on the very first Deep job of a batch, the same job runner should also emit a **process-level warning** (not just this job's log) — e.g. `frappe.logger().error(...)` once — since the *next* two queued Deep jobs (test scenario 3's 3-site run) will hit the identical failure and burn their whole time budget doing so unless something short-circuits them. Concretely: cache a `deep_scrape:browser_broken` Redis flag (TTL 5 min) on first `BrowserLaunchError`; subsequent Deep jobs check it before attempting their own launch and fail fast with the same `summary_line`, rather than each independently rediscovering the same broken environment.

### Time budget vs the 600s job timeout

- Deep jobs move to `queue="long", timeout=2100` (35 min hard RQ backstop). Shopify/Firecrawl stay on `default`/600 — unchanged.
- **Soft budget 1500s** (25 min), checked at every page boundary, with a 90s reserve for the final flush/commit — so a budget-exhausted run always ends cleanly, never mid-write.
- **Incremental saves**: commit every ~50 validated rows (products + counters + resume cursor + heartbeat) in one transaction. This is the single most important reliability property here — a SIGKILLed worker or a tripped memory guard loses at most the current batch, never the whole run, and the user watches `products_saved` climb in the UI in near-real-time with zero frontend changes.
- **Resume cursor**: `Competitor Site.deep_state` stores `{listing_url, page, pagination_scheme, page1_fingerprint, complete}`. Next run validates the fingerprint (hash of first N product URLs) before trusting the cursor — if the listing re-sorted, discard and restart from page 1 (dedup still skips known URLs, so this is cheap, just not free).
- Catch `rq.timeouts.JobTimeoutException` explicitly, before the bare `except Exception` — flush whatever's buffered, write `Done`/`Partial` with a resume hint. This converts what would otherwise be bug-#6-style stuck rows into honest partial results.

### Insertion point in `_bg_scrape_site`

```python
elif scrape_method == "Deep":
    from .deep import scrape_deep
    products, urls_found, already_in_db = scrape_deep(
        site_url=site_url, site_name=site_name, scrape_id=scrape_id,
        log_name=log_name, listing_urls=site.listing_urls, limit=site.deep_max_products,
    )
    method_used = "Deep"
```
Same 3-tuple contract as Firecrawl's branch — `_save_products(products, ...)` call below it is untouched. (Deep saves incrementally inside `scrape_deep` via its own calls to the shared, now-savepoint-safe `_save_products`; the tuple it returns at the end just carries the final counts for the log, so the existing line still works without modification.)

**Per your decision**: this `elif` is added but `Auto`'s `else` branch is **not** touched in this phase — Deep is opt-in only until proven on the 3-site test. A follow-up (out of scope for this plan) inserts Deep between Shopify and Firecrawl in Auto.

## Phase 5 — Bot-block handling

Classify every navigation response before extraction: `403/401` → Forbidden, `429`/`Retry-After` → rate-limited, Cloudflare interstitial markers (`cf-chl`, "Just a moment...") → Challenge, CAPTCHA iframe/sitekey present → Captcha, redirect to homepage/root → geo or bot redirect.

Escalation, capped:
- Rate-limited → honor `Retry-After` or backoff (20s/60s/180s), then stop as `Partial` with cursor preserved (not a failure — a scheduling problem).
- Forbidden/redirect-home → one retry with a fresh browser context + realistic UA/locale/viewport, matching the actual bundled Chromium version (mismatched `Sec-CH-UA` vs UA is itself a bot signal — so these are **not** hand-set, just left to Chromium).
- Cloudflare challenge → wait up to 15s for passive auto-clear, then stop — **no bypass attempts, no CAPTCHA solving, ever.**
- CAPTCHA → immediate stop, `summary_line` says so plainly, `Competitor Site.needs_manual_review` style flag (or just a clear log message, per what's cheapest to build) so it's visible without digging through logs.
- Per-domain politeness: min 2.5s + jitter between requests to the same host (Redis-tracked so it holds across separate jobs), honoring `Crawl-delay` from robots.txt if larger.
- `robots.txt`: respected by default (`Disallow` skipped, `Crawl-delay` honored), with an explicit per-site override — recorded audibly in the log when used, never silent.

## Observability — `Scrape Log.log` structure

Fixed sections so the user learns where to look, capped at ~60KB:
```
SUMMARY   — one line: what happened, what to do next
COUNTERS  — candidates_found = rejected + dup_in_run + already_in_db + new
            new = saved + save_failed   (both identities checked; mismatch is
            logged loudly as a scraper bug, never silently swallowed)
TIMELINE  — per-page: url, status, timing, candidates, saved (elided in the
            middle if long, SUMMARY/COUNTERS never get cut)
REJECTED SAMPLES — up to 5 rejected rows with the failing reason (the exact
                   thing that would have caught Anthropologie's nav links
                   in one glance)
ERRORS    — deduped by signature, max 10
```

---

## Files touched

**New:**
- `api/utils/deep/` (7 files, listed above)
- `api/utils/scrape_watchdog.py`, `tasks.py`
- `api/utils/validate.py`
- `patches/v1_0/add_deep_scrape_fields.py`

**Modified:**
- `api/utils/scrape_utils.py` — `_log_error` wrapper, savepoints in `_save_products`, `canonical_url`/`url_hash`, `merge_query` replacing `_page_url`, chunked `_already_in_db`, new `elif scrape_method == "Deep"` branch in `_bg_scrape_site`
- `api/scrape_runner.py` — `_enqueue_site_scrape` routes Deep to `queue="long", timeout=2100`; opportunistic watchdog call at top of `scrape_all_sites`/`get_scrape_progress`
- `hooks.py` — uncomment `scheduler_events`, add the 5-min cron watchdog entry
- `alaiy_os_connector_competitor_sites/doctype/scraped_product/scraped_product.json` — new/widened fields
- `alaiy_os_connector_competitor_sites/doctype/competitor_site/competitor_site.json` — new fields, `Deep` method option
- `alaiy_os_connector_competitor_sites/doctype/scrape_log/scrape_log.json` — `Partial` status, heartbeat, summary_line
- `public/js/scrape_runner.js` — raise the client-side poll ceiling (currently caps at `sites*600_000`ms, too short for a 1500s Deep budget); render `Partial` status

**Untouched (per your decision):** the `else` (Auto) branch in `_bg_scrape_site` — Deep stays opt-in this phase.

---

## Verification

1. **Local, single site, full catalog** (test scenario 1): `bench execute alaiy_os_connector_competitor_sites.api.utils.deep.cli.cli_scrape --kwargs "{'site':'<name>','limit':0,'budget':0}'"` against one real competitor URL. Confirm: all products found and saved, COUNTERS reconcile, log is readable end to end.
2. **Kill-mid-run test**: `kill -9` the bench worker partway through the local run; confirm the `Scrape Log` shows `Partial` (via the watchdog, within 2 min) with products already saved intact, and that re-running resumes from the stored cursor rather than restarting.
3. **Deployed on the server** (test scenario 2): set one Competitor Site to `scrape_method="Deep"`, run via the app UI, watch `/proc/meminfo` `MemAvailable` and the browser's RSS throughout — confirm it never drops below the 350MB stop threshold, and that the sibling app's Playwright automation is unaffected if run concurrently.
4. **3 sites × 100 products via the app UI** (test scenario 3): set `Competitor Site.deep_max_products=100` on three sites, trigger `scrape_all_sites` with all three selected, confirm all three `Scrape Log`s reach a terminal state (`Done` or `Partial`), the second and third show a "waiting for browser" queued state while the first runs (since Deep serializes on the `long` queue), and each stops cleanly at 100 products.
5. **Bug-fix regression**: confirm the 3 existing stuck `Running` rows from 2026-07-28 flip to `Partial`/`Failed` the first time `scrape_all_sites` or `get_scrape_progress` is called after Phase 2 ships.
6. **Chromium launch failure**: simulate a broken launch locally (e.g. temporarily point the executable path at a nonexistent file, or revoke exec permission on the binary) and confirm: the job fails fast (no retry loop burning the time budget), `summary_line` names it as a server issue and not a site issue, the `BROWSER` log line appears, and — with 2+ Deep jobs queued — the second job short-circuits via the `deep_scrape:browser_broken` flag instead of independently re-discovering the same failure.
