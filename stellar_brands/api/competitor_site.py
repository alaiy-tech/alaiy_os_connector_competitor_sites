import frappe


@frappe.whitelist()
def list_sites():
    sites = frappe.get_all(
        "Competitor Site",
        fields=["site_name", "site_url"],
        order_by="site_name asc",
    )
    return sites


@frappe.whitelist()
def add_site(site_name, site_url):
    doc = frappe.get_doc({
        "doctype": "Competitor Site",
        "site_name": site_name,
        "site_url": site_url,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"site_name": doc.site_name, "site_url": doc.site_url}


@frappe.whitelist()
def update_site(site_name, site_url):
    doc = frappe.get_doc("Competitor Site", site_name)
    doc.site_url = site_url
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"site_name": doc.site_name, "site_url": doc.site_url}


@frappe.whitelist()
def delete_site(site_name):
    frappe.delete_doc("Competitor Site", site_name, ignore_permissions=True)
    frappe.db.commit()
    return {"deleted": site_name}
