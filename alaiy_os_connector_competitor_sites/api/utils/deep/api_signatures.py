"""Known signatures used across the Deep scraper's discovery/extraction
tiers -- API-kind markers, tracker hosts, SSR framework globals, and the
fuzzy product-field key vocabulary. Kept in its own file, separate from
the walking/scoring/extraction logic in discovery.py and extract.py, so
this data can grow as more sites get scraped without touching that code.
Nothing here is specific to any one site -- these are real, widely-used
commerce infrastructure vendors and framework conventions, not a
hardcoded fix for a single test run.

Adding a new vendor/framework is just adding a marker string to the right
tuple below.
"""

# Hosted search-as-a-service platforms commonly powering an e-commerce
# category/search page's own product API, instead of a bespoke REST/GraphQL
# endpoint. Detected by host/URL substring.
SEARCH_INDEX_HOST_MARKERS = (
    "algolia.net", "algolianet.com",       # Algolia
    "typesense.net",                        # Typesense
    ".search.windows.net",                  # Azure AI Search
    "cloud.es.io", "elastic-cloud.com",      # Elastic Cloud
    "searchspring.net", "searchspring.io",   # Searchspring
    "constructor.io",                        # Constructor.io
    "klevu.com",                             # Klevu
    "bloomreach.com", "bloomreach.io",       # Bloomreach Discovery
    "coveo.com",                             # Coveo
    "swiftype.com",                          # Swiftype (Elastic App Search)
    "lucidworks.io", "lucidworks.com",       # Lucidworks Fusion
    "yext.com",                              # Yext Answers/Search
)

# Server-side-rendering-framework internal action/data-fetch calls that flow
# through the browser's own fetch() but are NOT a product data API -- must
# be named explicitly so they're never mistaken for a real data source, even
# though they'd otherwise satisfy the "XHR/fetch returning JSON-ish content"
# filter. Detected via request header name or content-type substring.
FRAMEWORK_ACTION_HEADER_MARKERS = (
    "next-action",       # Next.js App Router Server Actions
    "x-remix-request",   # Remix framework data requests
    "x-astro-action",    # Astro Actions
)
FRAMEWORK_ACTION_CONTENT_TYPE_MARKERS = (
    "text/x-component",  # Next.js React Server Component streaming payload
)

# GraphQL detection -- by URL shape or by the request body carrying a
# query/mutation field (the request shape, not the response shape).
GRAPHQL_URL_MARKERS = ("graphql",)
GRAPHQL_BODY_MARKERS = ('"query"', "mutation ", "query ")

# Analytics/martech/ad-tech hosts -- never real product data even when a
# response coincidentally touches a couple of product-field-hint categories
# (confirmed live: Adobe Experience Platform's demdex.net "interact" beacon
# scored 2 on a real test site's traffic and got picked over -- instead of --
# the real catalog API). Used two ways: browser.py aborts requests to these
# hosts outright (saves bandwidth/memory, never loses image URLs since those
# come from DOM attributes, not fetched bytes), and discovery.py refuses to
# even score a response from one of these, regardless of what it contains.
ANALYTICS_TRACKER_HOST_MARKERS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "facebook.net", "facebook.com/tr", "hotjar.com", "segment.com",
    "segment.io", "optimizely.com", "criteo.com", "criteo.net", "taboola.com",
    "newrelic.com", "datadoghq.com", "cdn.cookielaw.org", "klaviyo.com",
    "bat.bing.com", "snap.com", "pinterest.com", "yotpo.com",
    "bazaarvoice.com", "mparticle.com",
    "demdex.net", "adobedc.net", "omtrdc.net", "adobedtm.com",
    "everesttech.net", "rlcdn.com", "adnxs.com", "rubiconproject.com",
    "pubmatic.com", "quantserve.com", "scorecardresearch.com",
    "fullstory.com", "amplitude.com", "mixpanel.com", "dynatrace.com",
    "onetrust.com", "clarity.ms", "tealiumiq.com",
)

# First-party analytics/CDP proxy convention -- many sites route a
# third-party analytics vendor (Segment, RudderStack, etc) through their
# OWN subdomain instead of the vendor's domain directly, which the host-
# substring list above can never enumerate (it's a different hostname on
# every site). Confirmed live: analytics.mejuri.com's settings endpoint
# scored 2 on product-field hints and got picked as a Tier 1 candidate --
# caught safely by pagination verification that run, but not something to
# rely on catching every time. Matched by hostname PREFIX (the subdomain
# label itself), not substring-anywhere, to avoid false-positiving on a
# real product subdomain that happens to contain one of these words deeper
# in its name.
ANALYTICS_SUBDOMAIN_PREFIXES = (
    "analytics.", "tracking.", "telemetry.", "metrics.", "pixel.", "beacon.",
)

# Fuzzy key-name vocabulary for mapping an arbitrary product-API dict to our
# canonical row shape (discovery.py's map_generic_row) -- deliberately broad
# across the field names real commerce APIs actually use (Shopify Storefront
# GraphQL, Salesforce Commerce Cloud, Algolia-indexed product records,
# bespoke REST catalogs, etc), not just what one site happened to use.
PRODUCT_KEY_HINTS = {
    "name": (
        "name", "title", "productname", "displayname", "producttitle",
        "label", "headline",
    ),
    "url": (
        "url", "link", "producturl", "canonicalurl", "slug", "handle",
        "permalink", "path", "href",
    ),
    "image": (
        "image", "imageurl", "thumbnail", "img", "media", "photo",
        "picture", "swatch",
    ),
    "price": (
        "price", "currentprice", "saleprice", "listprice", "amount",
        "unitprice", "finalprice", "displayprice", "cost",
    ),
    "sku": (
        "sku", "id", "productid", "styleid", "itemid", "variantid",
        "gid", "articlenumber", "mpn", "upc", "gtin",
    ),
    "description": (
        "description", "shortdescription", "longdescription", "summary",
        "bodyhtml", "body_html", "details",
    ),
    "category": (
        "category", "categoryname", "producttype", "categorypath",
        "breadcrumb", "taxon",
    ),
}

# Known window globals SSR frameworks/libraries use to embed their initial
# payload (extract.py's extract_embedded_json). Every name here is a
# standard hydration-state convention used across every site built on
# that framework, not something any one site invented. Deliberately broad:
# cheap to check (one property read each), and missing a real one just
# means falling through to the next tier.
EMBEDDED_JSON_GLOBALS = (
    "__NEXT_DATA__",              # Next.js
    "__NUXT__",                    # Nuxt 2
    "__NUXT_DATA__",                # Nuxt 3
    "__INITIAL_STATE__",            # common bespoke SSR convention
    "__INITIAL_STATE",              # same, no trailing underscore variant
    "INITIAL_STATE",                # same, no underscores at all
    "__PRELOADED_STATE__",          # common Redux SSR convention
    "__REDUX_STATE__",              # Redux SSR (alternate naming)
    "__REDUX_DATA__",               # Redux SSR (alternate naming)
    "__APOLLO_STATE__",             # Apollo Client cache dehydration
    "__APOLLO_CLIENT__",            # Apollo Client cache, alternate naming
    "__RELAY_PAYLOADS__",           # Relay (Facebook GraphQL client)
    "__RELAY_STORE__",              # Relay, alternate naming
    "__TRANSFER_STATE__",           # Angular Universal
    "__remixContext",               # Remix / Shopify Hydrogen v2
    "__PAGE_DATA__",                 # common bespoke SSR convention
    "__SERVER_DATA__",               # common bespoke SSR convention
    "__STATE__",                     # common bespoke SSR convention
    "__DATA__",                      # common bespoke SSR convention
    "__APP_STATE__",                 # common bespoke SSR convention
    "__INITIAL_DATA__",              # common bespoke SSR convention
    "__PRELOADED_DATA__",            # common bespoke SSR convention
    "__SSR_DATA__",                  # common bespoke SSR convention
    "__vite_ssr_import_meta__",      # rare, but seen on some Vite-SSR sites
)

# Script tag MIME types treated specially -- JSON-LD gets its own dedicated
# extractor (extract.py's extract_json_ld) since it has a known Product/
# ItemList shape; extract_embedded_json's generic script-tag scan excludes
# this type so the two extractors never double-process the same tag.
JSON_LD_SCRIPT_TYPE = "application/ld+json"

# Access restriction and traffic verification page markers (blocking.py's classify()).
# Checked narrowest-signal first across multi-signal HTTP status, response headers, cookies, title, and body.
PROTECTION_VENDOR_SIGNATURES = {
    "AKAMAI": {
        "headers": ("server: akamaighost", "x-akamai-staging", "x-akamai-transformed"),
        "cookies": ("ak_bmsc", "bm_sv", "bm_sz", "_abck"),
        "markers": (
            "access is temporarily restricted",
            "unusual activity from your device",
            "access denied",
            "akamai-captcha",
            "reference #",
        ),
    },
    "CLOUDFLARE": {
        "headers": ("server: cloudflare", "cf-ray", "cf-mitigated"),
        "cookies": ("__cf_bm", "cf_clearance"),
        "markers": (
            "just a moment",
            "cf-chl",
            "checking your browser",
            "attention required",
            "cf-mitigated",
            "turnstile",
        ),
    },
    "DATADOME": {
        "headers": ("x-datadome", "x-datadome-response"),
        "cookies": ("datadome",),
        "markers": (
            "datadome",
            "dd-captcha",
            "geo.datadome.co",
        ),
    },
    "PERIMETERX": {
        "headers": ("x-px-",),
        "cookies": ("_px3", "_pxhd", "_pxvid"),
        "markers": (
            "px-captcha",
            "_pxAppId",
            "access to this page has been denied",
            "press & hold",
        ),
    },
    "KASADA": {
        "headers": ("x-ksd-",),
        "cookies": ("k_id", "k_key"),
        "markers": (
            "ips.js",
            "kasada",
        ),
    },
    "AWS_WAF": {
        "headers": ("x-amzn-waf-action",),
        "cookies": ("aws-waf-token",),
        "markers": (
            "403 forbidden",
            "awswaf",
        ),
    },
    "IMPERVA": {
        "headers": ("x-iinfo", "incap_ses"),
        "cookies": ("incap_ses", "visid_incap"),
        "markers": (
            "incapsula",
            "_incap_res",
            "request unsuccessful. incapsula incident ID",
        ),
    },
}

BOT_BLOCK_RATE_LIMIT_MARKERS = (
    "local_rate_limited",
    "rate limit exceeded",
    "too many requests",
    "429 too many requests",
)
BOT_BLOCK_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-chl",
    "checking your browser",
    "attention required",
    "cf-mitigated",
)
BOT_BLOCK_CAPTCHA_MARKERS = (
    "captcha",
    "are you human",
    "hcaptcha",
    "recaptcha",
    "verify you are human",
    "robot check",
)
BOT_BLOCK_GEOBLOCK_MARKERS = (
    "not available in your country",
    "not available in your region",
    "shipping to your location",
    "access from your country has been blocked",
)


# Pagination query-param keys to try, in order (paginate.py's
# detect_pagination). If the configured URL already carries one of these,
# it's tried first -- reusing what's already there rather than guessing.
# Every guess is verified (page1 vs page2 must actually differ) before
# being trusted, so a broad list costs a couple of extra HTTP round trips
# per wrong guess, never a wrong result -- worth erring toward more keys.
PAGINATION_CANDIDATE_KEYS = (
    "page", "p", "pg", "pn",
    "page_num", "pagenum", "page_no", "pageno", "pagenumber",
    "currentpage", "current_page",
    "start", "offset", "from", "skip", "index",
    "startindex", "start_index", "rownum",
)
# 0-indexed-by-convention keys -- these paginate from record 0, not page 1.
PAGINATION_ZERO_INDEXED_KEYS = ("start", "offset", "from", "skip", "index", "startindex", "start_index", "rownum")

# URL path segments that are almost always navigation/category/utility
# pages, not products (validate.py's validate_row), unless a product marker
# (below) also appears in the same path.
NAV_BLOCKLIST_WORDS = (
    "shop", "collections?", "category", "categories", "c", "browse",
    "women", "womens", "men", "mens", "kids", "gifts?", "bottoms", "tops",
    "dresses", "clothing", "accessories", "jewelry", "jewellery", "sale",
    "clearance", "new", "new-arrivals", "search", "blog", "about", "help",
    "faq", "account", "cart", "checkout", "login", "pages", "stores?",
    "lookbook", "size-guide", "wishlist", "contact",
)
# Any of these appearing in a path is a strong positive signal it IS a
# product page, even if a nav-blocklist word also appears earlier in it.
PRODUCT_MARKER_PATTERNS = (r"products?", r"prod", r"p", r"dp", r"item", r"pd")
# A trailing numeric id 4+ digits long is also a strong product-page signal,
# independent of any path-segment word (validate.py builds this separately
# since it's a different pattern shape, not a plain word alternation).
PRODUCT_MARKER_NUMERIC_ID_PATTERN = r"-\d{4,}(\.html?)?(/|$|\?)"

# Plain link-text words that are never a real product name (validate.py).
NAV_WORD_NAMES = (
    "new", "sale", "shop all", "view all", "see all", "back", "menu", "filter",
    "sort", "new arrivals", "clearance", "gifts", "sign in", "my account",
    "free shipping", "collection", "category", "lookbook", "guide",
)

# Base jewelry-category keywords (shopify_scraper.py's _is_jewelry) plus the
# extended vocabulary (validate.py's is_jewelry_extended) that under-collects
# on general fashion sites' jewelry categories if left out.
JEWELRY_KEYWORDS = (
    "jewelry", "jewellery", "ring", "rings", "necklace", "necklaces",
    "earring", "earrings", "bracelet", "bracelets", "bangle", "bangles",
    "anklet", "anklets", "pendant", "pendants", "brooch", "brooches",
    "chain", "chains", "charm", "charms", "cufflink", "cufflinks",
)
JEWELRY_KEYWORDS_EXTENDED = (
    "huggie", "huggies", "solitaire", "signet", "cuff", "tennis", "stud", "studs",
    "charm bar", "choker", "chokers", "bangle", "bangles",
)

# Category-listing URL path conventions across major e-commerce platforms,
# themes, and common non-English naming (category_finder.py's fallback,
# tried only after real nav-link matching, and every candidate is still
# verified against real content before use -- a broad guess list costs
# nothing but a few extra page loads on a wrong guess).
CATEGORY_PATH_TEMPLATES = (
    "/collections/{slug}",                  # Shopify
    "/collection/{slug}",                   # Shopify, singular variant
    "/category/{slug}",                     # generic / many bespoke themes
    "/categories/{slug}",                   # generic
    "/c/{slug}",                             # common short form
    "/shop/{slug}",                          # common short form
    "/shop/category/{slug}",                 # common compound form
    "/shop-by-category/{slug}",              # common compound form
    "/store/category/{slug}",                # confirmed live on a real Next.js storefront
    "/store/{slug}",                         # generic
    "/product-category/{slug}",              # WooCommerce
    "/catalog/category/view/s/{slug}",       # Magento (default URL key style)
    "/catalog/{slug}",                       # generic catalog-style
    "/catalogsearch/category/{slug}",        # Magento search-driven category
    "/s/{slug}",                             # BigCommerce-style short form
    "/browse/{slug}",                        # generic
    "/department/{slug}",                    # generic department-style nav
    "/list/{slug}",                          # generic
    "/search/{slug}",                        # generic
    "/p/{slug}",                             # some bespoke themes use this for category too
    "/boutique/{slug}",                      # French-language storefronts
    "/categorie/{slug}",                     # French spelling
    "/kategorie/{slug}",                     # German spelling
    "/categoria/{slug}",                     # Spanish/Italian spelling
    "/rayon/{slug}",                         # French department-store convention
    "/{slug}",                               # bare top-level path, last resort
    "?cgid={slug}",                          # Salesforce Commerce Cloud category id
    "?cid={slug}",                           # common category-id query param
    "?cat={slug}",                           # Magento-style category id
    "?category_id={slug}",                  # generic
    "?category={slug}",                     # generic
)
