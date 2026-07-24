"""
Run in bench console:
    from alaiy_os_connector_competitor_sites.api.utils.test_scrape import run
    run("https://www.boohoo.com/categories/womens-jewellery")
"""

from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import _scrape_firecrawl


def run(site_url):
    print(f"\n=== START scrape: {site_url} ===")

    products, urls_found, already_in_db = _scrape_firecrawl(site_url)

    print(f"\n--- total products found: {urls_found}")
    print(f"--- already in DB: {already_in_db}")
    print(f"--- new: {len(products)}")
    if products:
        print(f"--- sample: {products[0]}")

    print(f"\n=== DONE ===\n")
    return products


if __name__ == "__main__":
    run("https://www.boohoo.com/categories/womens-jewellery")