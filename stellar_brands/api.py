import frappe
from frappe.utils import cint


@frappe.whitelist()
def get_scraping_sources():
    return frappe.get_all(
        "Scraping Source",
        fields=[
            "name",
            "site_url",
            "categories",
            "is_active",
            "last_scraped_at",
            "last_scrape_status",
            "last_error_message",
            "products_scraped",
        ],
        order_by="creation asc",
    )


@frappe.whitelist()
def add_scraping_source(site_name, site_url):
    doc = frappe.get_doc({
        "doctype": "Scraping Source",
        "site_name": site_name,
        "site_url": site_url,
        "is_active": 1,
    })
    doc.insert()
    return doc.as_dict()


@frappe.whitelist()
def toggle_scraping_source(name, is_active):
    doc = frappe.get_doc("Scraping Source", name)
    doc.is_active = cint(is_active)
    doc.save()
    return {"name": doc.name, "is_active": doc.is_active}
