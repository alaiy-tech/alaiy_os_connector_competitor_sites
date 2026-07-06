import frappe


@frappe.whitelist()
def get_review_queue(site=None, category=None, status="Pending", search=None, page=1, page_size=24):
    """Return paginated products for the review queue with summary counts."""
    filters = {}
    if site:
        filters["source_site"] = site
    if status:
        filters["review_status"] = status

    conditions = ["1=1"]
    values = {}

    if site:
        conditions.append("source_site = %(site)s")
        values["site"] = site
    if status:
        conditions.append("review_status = %(status)s")
        values["status"] = status
    if category:
        conditions.append("categories LIKE %(category)s")
        values["category"] = f"%{category}%"
    if search:
        conditions.append("(product_name LIKE %(search)s OR sku LIKE %(search)s)")
        values["search"] = f"%{search}%"

    where = " AND ".join(conditions)
    offset = (int(page) - 1) * int(page_size)

    products = frappe.db.sql(f"""
        SELECT name, product_name, product_image_url, source_site,
               source_price, categories, review_status, source_product_url,
               sku, notes
        FROM `tabScraped Product`
        WHERE {where}
        ORDER BY scraped_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """, {**values, "limit": int(page_size), "offset": offset}, as_dict=True)

    counts = frappe.db.sql("""
        SELECT review_status, COUNT(*) AS total
        FROM `tabScraped Product`
        GROUP BY review_status
    """, as_dict=True)

    summary = {"Pending": 0, "Kept": 0, "Skipped": 0}
    for row in counts:
        if row["review_status"] in summary:
            summary[row["review_status"]] = row["total"]

    return {"products": products, "summary": summary}


@frappe.whitelist()
def set_review_status(name, status):
    """Set review_status on a single Scraped Product. Reusable by any connector."""
    if status not in ("Pending", "Kept", "Skipped"):
        frappe.throw("Invalid status. Must be Pending, Kept, or Skipped.")
    frappe.db.set_value("Scraped Product", name, "review_status", status)
    frappe.db.commit()
    return {"success": True, "name": name, "status": status}


@frappe.whitelist()
def save_notes(name, notes):
    """Save buyer/team notes on a Scraped Product."""
    frappe.db.set_value("Scraped Product", name, "notes", notes)
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def finalize_selected(collection_name, names=None):
    """
    Create ERPNext Items from all Kept Scraped Products, group them into a Collection.

    - Brands are auto-created if they don't exist.
    - source_product_url is stored in customer_code.
    - names: JSON list of specific scraped product names; empty/None = all Kept.
    """
    import json as _json
    from frappe.utils import today

    if isinstance(names, str):
        names = _json.loads(names)

    if not names:
        names = frappe.get_all("Scraped Product", filters={"review_status": "Kept"}, pluck="name")

    if not names:
        return {"collection": None, "count": 0}

    collection = frappe.get_doc({
        "doctype": "Collection",
        "collection_name": collection_name,
        "finalized_on": today(),
        "items": [],
    })

    created = []
    for name in names:
        sp = frappe.get_doc("Scraped Product", name)
        if sp.review_status != "Kept":
            continue

        item_code = (sp.sku or "").strip() or _generate_item_code(sp.product_name)

        if not frappe.db.exists("Item", item_code):
            _ensure_brand(sp.source_site)
            uom = _ensure_uom("Nos")
            item = frappe.get_doc({
                "doctype": "Item",
                "item_code": item_code,
                "item_name": sp.product_name or item_code,
                "item_group": _ensure_item_group("Competitor Products"),
                "stock_uom": uom,
                "is_stock_item": 0,
                "image": sp.product_image_url or "",
                "description": sp.description or "",
                "brand": sp.source_site or "",
                "customer_code": sp.source_product_url or "",
            })
            item.insert(ignore_permissions=True)

        price = _parse_price(sp.source_price)
        if price:
            _upsert_item_price(item_code, price)

        collection.append("items", {
            "item": item_code,
            "item_name": sp.product_name or item_code,
            "source_site": sp.source_site or "",
            "product_url": sp.source_product_url or "",
        })
        created.append(name)

    collection.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"collection": collection_name, "count": len(created)}


def _ensure_uom(uom_name):
    if not frappe.db.exists("UOM", uom_name):
        frappe.get_doc({"doctype": "UOM", "uom_name": uom_name}).insert(ignore_permissions=True)
    return uom_name


def _ensure_item_group(group_name):
    if frappe.db.exists("Item Group", group_name):
        return group_name
    # ensure root exists first
    if not frappe.db.exists("Item Group", "All Item Groups"):
        frappe.get_doc({
            "doctype": "Item Group",
            "item_group_name": "All Item Groups",
            "is_group": 1,
            "parent_item_group": "",
        }).insert(ignore_permissions=True)
    frappe.get_doc({
        "doctype": "Item Group",
        "item_group_name": group_name,
        "is_group": 0,
        "parent_item_group": "All Item Groups",
    }).insert(ignore_permissions=True)
    return group_name


def _ensure_brand(brand_name):
    if brand_name and not frappe.db.exists("Brand", brand_name):
        frappe.get_doc({"doctype": "Brand", "brand": brand_name}).insert(ignore_permissions=True)


def _generate_item_code(product_name):
    import re, uuid
    base = re.sub(r"[^a-zA-Z0-9\s]", "", product_name or "").strip()[:30]
    suffix = uuid.uuid4().hex[:6].upper()
    return f"{base}-{suffix}" if base else f"ITEM-{suffix}"


def _parse_price(price_str):
    import re
    if not price_str:
        return 0.0
    match = re.search(r"[\d,]+\.?\d*", str(price_str).replace(",", ""))
    return float(match.group().replace(",", "")) if match else 0.0


def _ensure_price_list(price_list_name):
    if not frappe.db.exists("Price List", price_list_name):
        frappe.get_doc({
            "doctype": "Price List",
            "price_list_name": price_list_name,
            "selling": 1,
            "currency": "USD",
            "enabled": 1,
        }).insert(ignore_permissions=True)
    return price_list_name


def _upsert_item_price(item_code, price):
    price_list = _ensure_price_list("Competitor USD")
    existing = frappe.db.get_value(
        "Item Price",
        {"item_code": item_code, "price_list": price_list},
        "name",
    )
    if existing:
        frappe.db.set_value("Item Price", existing, "price_list_rate", price)
    else:
        frappe.get_doc({
            "doctype": "Item Price",
            "item_code": item_code,
            "price_list": price_list,
            "selling": 1,
            "currency": "USD",
            "price_list_rate": price,
        }).insert(ignore_permissions=True)
