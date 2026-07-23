"""
Run in bench console:
    from alaiy_os_connector_competitor_sites.api.utils.test_scrape import run
    run("https://www.boohoo.com/categories/womens-jewellery")
"""

from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import (
    _get_firecrawl,
    _scrape_listing_until_enough_new,
    _already_in_db,
    _clean_url,
)


def run(site_url, product_limit=50):
    print(f"\n=== START scrape: {site_url} (target {product_limit} new) ===")
    fc = _get_firecrawl()

    products = _scrape_listing_until_enough_new(fc, site_url, product_limit)

    urls = [p.get("product_source_url") for p in products if p.get("product_source_url")]
    clean_urls = [_clean_url(u) for u in urls]
    existing = _already_in_db(clean_urls)
    new_count = sum(1 for u in clean_urls if u not in existing)

    print(f"\n--- total products seen: {len(products)}")
    print(f"--- already in DB: {len(existing)}")
    print(f"--- new: {new_count}")
    if products:
        print(f"--- sample: {products[0]}")

    print(f"\n=== DONE ===\n")
    return products


if __name__ == "__main__":
    run("https://www.boohoo.com/categories/womens-jewellery")