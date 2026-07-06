import frappe


def execute(filters=None):
    columns = [
        {"label": "Scrape ID", "fieldname": "scrape_id", "fieldtype": "Data", "width": 320},
        {"label": "Source Site", "fieldname": "source_site", "fieldtype": "Link", "options": "Competitor Site", "width": 160},
        {"label": "Products Scraped", "fieldname": "product_count", "fieldtype": "Int", "width": 140},
        {"label": "Scraped At", "fieldname": "scraped_at", "fieldtype": "Datetime", "width": 180},
    ]

    data = frappe.db.sql("""
        SELECT
            scrape_id,
            source_site,
            COUNT(*) AS product_count,
            MIN(scraped_at) AS scraped_at
        FROM `tabScraped Product`
        GROUP BY scrape_id, source_site
        ORDER BY scraped_at DESC
    """, as_dict=True)

    return columns, data
