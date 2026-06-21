import frappe
from frappe.utils import getdate, today


@frappe.whitelist()
def get_products(review_status=None, source_site=None, category=None):
    filters = {}
    if review_status:
        filters["review_status"] = review_status
    if source_site:
        filters["source_site"] = source_site
    if category:
        filters["categories"] = category

    return frappe.get_all(
        "Product",
        filters=filters,
        fields=[
            "name",
            "product_name",
            "source_site",
            "product_image_url",
            "product_source_url",
            "sku",
            "categories",
            "scraped_price",
            "review_status",
            "saved_at",
        ],
        order_by="saved_at desc",
    )


@frappe.whitelist()
def get_product(name):
    doc = frappe.get_doc("Product", name)
    return doc.as_dict()


@frappe.whitelist()
def get_review_stats():
    return {
        "pending": frappe.db.count("Product", filters={"review_status": "Pending"}),
        "kept": frappe.db.count("Product", filters={"review_status": "Kept"}),
        "skipped": frappe.db.count("Product", filters={"review_status": "Skipped"}),
    }


@frappe.whitelist()
def update_review_status(name, status, notes=None):
    doc = frappe.get_doc("Product", name)
    doc.review_status = status
    if notes:
        doc.notes = notes
    doc.save()
    return {"name": doc.name, "review_status": doc.review_status}


@frappe.whitelist()
def finalize_products(names, collection_name=None):
    if isinstance(names, str):
        names = frappe.parse_json(names)

    if not collection_name:
        date = getdate(today())
        collection_name = f"Collection {date.strftime('%d/%m/%Y')}"

    for name in names:
        doc = frappe.get_doc("Product", name)
        doc.review_status = "Kept"
        doc.collection_name = collection_name
        doc.save()

    return {"finalized": len(names), "collection_name": collection_name}


@frappe.whitelist()
def bulk_update_review_status(names, status):
    if isinstance(names, str):
        names = frappe.parse_json(names)

    for name in names:
        doc = frappe.get_doc("Product", name)
        doc.review_status = status
        doc.save()

    return {"updated": len(names), "status": status}
