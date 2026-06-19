import frappe


@frappe.whitelist()
def get_scrape_runs():
    return frappe.get_all(
        "Scrape Run",
        fields=["name", "started_at", "completed_at", "status", "products_added", "triggered_by"],
        order_by="started_at desc",
    )
