import frappe


@frappe.whitelist()
def get_sites_with_stats():
    sites = frappe.get_all(
        "Competitor Site",
        fields=["name", "site_url", "categories", "is_active"],
        order_by="creation asc",
    )
    for site in sites:
        row = frappe.db.sql(
            """
            SELECT MAX(scraped_at) AS last_scraped, COUNT(*) AS product_count
            FROM `tabScraped Product`
            WHERE source_site = %s
            """,
            site["name"],
            as_dict=True,
        )
        site["last_scraped"] = row[0]["last_scraped"] if row else None
        site["product_count"] = row[0]["product_count"] if row else 0
    return sites


@frappe.whitelist()
def add_site(site_name, site_url, categories=None):
    doc = frappe.get_doc({
        "doctype": "Competitor Site",
        "site_name": site_name,
        "site_url": site_url,
        "categories": categories or "",
        "is_active": 1,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


@frappe.whitelist()
def toggle_active(site_name, is_active):
    frappe.db.set_value("Competitor Site", site_name, "is_active", int(is_active))
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def delete_site(site_name):
    frappe.delete_doc("Competitor Site", site_name, ignore_permissions=True)
    frappe.db.commit()
    return {"success": True}
