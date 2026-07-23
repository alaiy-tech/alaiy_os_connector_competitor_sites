"""
Run with: bench execute your_app.api.utils.test_scrape.run --kwargs "{'site_url': 'https://...'}"
Or paste into `bench console` and call run(site_url="...") directly.
"""

from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import (
    _get_firecrawl,
    _scrape_listing_page,
    MAX_LISTING_PAGES,
)


def run(site_url, max_pages=MAX_LISTING_PAGES):
    print(f"\n=== START scrape: {site_url} ===")
    fc = _get_firecrawl()

    current_url = site_url
    page_num = 1
    total = 0

    while current_url and page_num <= max_pages:
        print(f"\n--- Page {page_num}: {current_url}")
        products, next_url = _scrape_listing_page(fc, current_url)
        print(f"    products found: {len(products)}")
        if products:
            print(f"    sample: {products[0]}")
        total += len(products)

        if not next_url:
            print("    no next page link found — stopping")
            break

        current_url = next_url
        page_num += 1

    print(f"\n=== DONE. Total products across {page_num} page(s): {total} ===\n")


if __name__ == "__main__":
    run("https://www.lulus.com/categories/99_100/jewelry.html")