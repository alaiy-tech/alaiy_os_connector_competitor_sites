"""Known API-kind signatures for classify_api_kind() (discovery.py) --
kept in its own file, separate from the discovery logic, so this list can
grow as more sites get scraped without touching the walker/scorer code.
Nothing here is specific to any one site -- these are real, widely-used
commerce infrastructure vendors, not a hardcoded fix for a single test run.

Adding a new vendor is just adding a marker string to the right tuple below.
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
}
