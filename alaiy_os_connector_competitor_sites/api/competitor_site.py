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
    # Competitor Site grants create/write/delete to System Manager only, but
    # this inserts with ignore_permissions=True and so skips those rows
    # entirely. Ask the doctype rather than restating the rule here.
    frappe.has_permission("Competitor Site", "create", throw=True)
    doc = frappe.get_doc({
        "doctype": "Competitor Site",
        "site_name": site_name,
        "site_url": site_url,
        "categories": categories or "Jewellery, Accessories",
        "is_active": 1,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


@frappe.whitelist()
def toggle_active(site_name, is_active):
    # db.set_value bypasses DocType permissions, so without this any logged-in
    # user could enable or disable any competitor site.
    frappe.has_permission("Competitor Site", "write", throw=True)
    frappe.db.set_value("Competitor Site", site_name, "is_active", int(is_active))
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def delete_site(site_name):
    # ignore_permissions=True on a whitelisted endpoint means any logged-in user
    # could delete any competitor site. Gate on the doctype's own delete right.
    frappe.has_permission("Competitor Site", "delete", throw=True)
    frappe.delete_doc("Competitor Site", site_name, ignore_permissions=True)
    frappe.db.commit()
    return {"success": True}
