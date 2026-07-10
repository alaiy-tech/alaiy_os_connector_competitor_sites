import frappe


def sync_connector_registry():
    _fix_settings_as_single()

    if not frappe.db.exists("DocType", "OS Connector Registry"):
        return

    # Remove old registry row from before the rename
    if frappe.db.exists("OS Connector Registry", "stellar_brands"):
        frappe.delete_doc("OS Connector Registry", "stellar_brands", ignore_permissions=True)
        frappe.db.commit()

    from alaiy_os_connector_competitor_sites.connector_meta import connector_meta

    connector_id = connector_meta["connector_id"]

    if frappe.db.exists("OS Connector Registry", connector_id):
        doc = frappe.get_doc("OS Connector Registry", connector_id)
    else:
        doc = frappe.new_doc("OS Connector Registry")

    RUNTIME_FIELDS = {"connection_status", "last_tested_at"}

    if doc.is_new():
        for key, val in connector_meta.items():
            doc.set(key, val)
        doc.insert(ignore_permissions=True)
    else:
        for key, val in connector_meta.items():
            if key not in RUNTIME_FIELDS:
                doc.set(key, val)
        doc.save(ignore_permissions=True)

    frappe.db.commit()
    _update_alaiy_os_sidebar()


def _fix_settings_as_single():
    frappe.db.sql(
        "UPDATE `tabDocType` SET issingle=1 WHERE name='Stellar Brands Connector Settings' AND issingle=0"
    )
    frappe.db.commit()



_SIDEBAR_NAME = "OS"
_SECTION_LABEL = "Competitor Sites"
_SIDEBAR_ITEMS = [
    {"type": "Section Break", "label": _SECTION_LABEL,   "icon": "globe",           "child": 0, "indent": 1},
    {"type": "Link", "link_type": "Page", "link_to": "website-manager", "label": "Website Manager", "child": 1, "icon": "monitor",        "indent": 1},
    {"type": "Link", "link_type": "Page", "link_to": "scrape-runner",   "label": "Scrape Runner",   "child": 1, "icon": "play",            "indent": 1},
    {"type": "Link", "link_type": "Page", "link_to": "review-queue",    "label": "Review Queue",    "child": 1, "icon": "clipboard-check", "indent": 1},
    {"type": "Link", "link_type": "Page", "link_to": "pricing",         "label": "Pricing",         "child": 1, "icon": "tag",             "indent": 1},
    {"type": "Section Break", "label": "Logs",           "icon": "activity",        "child": 0, "indent": 1},
    {"type": "Link", "link_type": "Report", "link_to": "Competitor Product Logs", "label": "Scrape Logs", "child": 1, "icon": "activity", "indent": 1},
]
_INJECTED_LABELS = {_SECTION_LABEL, "Website Manager", "Scrape Runner", "Review Queue", "Pricing", "Logs", "Scrape Logs"}


def after_install():
    sync_connector_registry()


def after_migrate():
    sync_connector_registry()


def _update_alaiy_os_sidebar():
    try:
        from alaiy_os.setup.install import create_or_update_workspace_sidebar
        create_or_update_workspace_sidebar()
        frappe.db.commit()
        _inject_sidebar()
    except Exception:
        frappe.log_error(
            title="Stellar Brands connector: sidebar update failed",
            message=frappe.get_traceback(),
        )


def _inject_sidebar():
    try:
        if not frappe.db.exists("Workspace Sidebar", _SIDEBAR_NAME):
            return
        doc = frappe.get_doc("Workspace Sidebar", _SIDEBAR_NAME)
        doc.items = [r for r in doc.items if r.label not in _INJECTED_LABELS]
        for item in _SIDEBAR_ITEMS:
            doc.append("items", item)
        doc.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title="Stellar Brands: sidebar injection failed",
            message=frappe.get_traceback(),
        )
