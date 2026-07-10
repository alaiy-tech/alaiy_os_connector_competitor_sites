import json
import os
import re

import frappe

from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import _run_scrape_strategy


def test_scrape_url(site_url, scrape_method="Auto", product_limit=500, output_dir=None):
    """Run the scrape strategy for a single URL and write raw results to a JSON file.

    Does not touch the Scraped Product doctype or the database in any way -
    for manually spot-checking a site's scrape output before deciding whether
    to add it as a Competitor Site.

    Usage:
        bench --site stellar.brands execute alaiy_os_connector_competitor_sites.api.utils.test_scrape.test_scrape_url \\
            --kwargs '{"site_url": "https://example.com/collections/jewelry"}'
    """
    products, method_used = _run_scrape_strategy(site_url, scrape_method, product_limit)

    output_dir = output_dir or os.path.join(frappe.utils.get_bench_path(), "scrape_test_output")
    os.makedirs(output_dir, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]+", "-", site_url.lower()).strip("-")[:60]
    timestamp = frappe.utils.now_datetime().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, f"{slug}_{timestamp}.json")

    with open(output_path, "w") as f:
        json.dump({
            "site_url": site_url,
            "scrape_method_requested": scrape_method,
            "method_used": method_used,
            "count": len(products),
            "products": products,
        }, f, indent=2, default=str)

    print(f"Scraped {len(products)} products from {site_url} via {method_used} -> {output_path}")
    return output_path
