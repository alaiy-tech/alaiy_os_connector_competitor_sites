import os
import requests
import frappe


@frappe.whitelist()
def start_scrape_run(sites):
    if isinstance(sites, str):
        sites = frappe.parse_json(sites)

    if not sites:
        frappe.throw("No sites selected")

    site_details = []
    for site_name in sites:
        site = frappe.get_doc("Scraping Source", site_name)
        site_details.append({"site_name": site.name, "site_url": site.site_url})

    doc = frappe.get_doc({
        "doctype": "Scrape Run",
        "started_at": frappe.utils.now(),
        "status": "Running",
        "triggered_by": frappe.session.user,
    })
    doc.insert()
    frappe.db.commit()

    callback_url = (
        f"{frappe.utils.get_url()}/api/method/"
        "stellar_brands.api.scrape_runner.receive_scrape_results"
    )
    scraper_url = os.environ.get("SCRAPER_URL", "http://localhost:8001")

    try:
        requests.post(
            f"{scraper_url}/scrape",
            json={"scrape_run_id": doc.name, "sites": site_details, "callback_url": callback_url},
            timeout=10,
        )
    except Exception:
        doc.status = "Failed"
        doc.save()
        frappe.db.commit()
        frappe.throw("Could not connect to scraper service")

    return {"scrape_run_id": doc.name, "sites": site_details}


@frappe.whitelist(allow_guest=True)
def receive_scrape_results(scrape_run_id, products):
    if isinstance(products, str):
        products = frappe.parse_json(products)

    products_added = 0
    for p in products:
        if frappe.db.exists("Product", {"product_source_url": p.get("product_source_url")}):
            continue

        frappe.get_doc({
            "doctype": "Product",
            "scrape_id": scrape_run_id,
            "source_site": p.get("source_site"),
            "product_name": p.get("product_name"),
            "product_image_url": p.get("product_image_url"),
            "product_source_url": p.get("product_source_url"),
            "sku": p.get("sku"),
            "categories": p.get("category"),
            "saved_at": frappe.utils.now(),
            "review_status": "Pending",
        }).insert(ignore_permissions=True)
        products_added += 1

    run = frappe.get_doc("Scrape Run", scrape_run_id)
    run.status = "Complete"
    run.completed_at = frappe.utils.now()
    run.products_added = (run.products_added or 0) + products_added
    run.save(ignore_permissions=True)
    frappe.db.commit()

    return {"products_added": products_added}
