connector_meta = {
    "connector_id": "competitor_products",
    "connector_name": "Competitor Products",
    "connector_app": "competitor_sites",
    "connector_type": "other",
    "description": "Competitor product site scraper powered by Firecrawl",
    "icon": "search",
    "settings_doctype": "Competitor Sites Connector Settings",
    "test_method": "alaiy_os_connector_competitor_sites.api.test_connection.test_connection",
    "sync_categories_method": None,
    "sync_items_method": None,
    "is_enabled": 0,
    "connection_status": "untested",
    "health": [
        {
            # Scrapes run on demand, so there is no interval and a run is never overdue.
            "key": "scrape",
            "label": "Site scrape",
            "direction": "pull",
            "log_doctype": "Scrape Log",
            "fields": {
                "processed": "urls_found",
                "changed": "products_saved",
                "error": "summary_line",
            },
            # A partial run saved products before it stopped.
            "status_map": {"partial": "success"},
            "scheduled": False,
            "expects_rows": False,
        },
    ],
}
