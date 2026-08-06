"""
One-time data-preserving fix for the "Stellar Brands Connector Settings" ->
"Competitor Sites Connector Settings" rename (client name never belongs in
code, doctype names, or field names).

Renaming the doctype JSON alone does NOT migrate an existing deployment's
data: Frappe just creates a brand-new empty "Competitor Sites Connector
Settings" Single on next migrate, leaving the old "Stellar Brands Connector
Settings" doctype (and whatever Firecrawl API key was already saved there)
orphaned in the DB. This patch carries that one saved value across before
dropping the old doctype.

Scoped ONLY to this one Settings singleton -- Scraped Product / Competitor
Site / Scrape Log doctype names were never touched by the rename, so every
previously-scraped record on every existing deployment is unaffected by
this patch or the rename itself.
"""

import frappe
from frappe.utils.password import get_decrypted_password, set_encrypted_password

_OLD_DOCTYPE = "Stellar Brands Connector Settings"
_OLD_FIELD = "sb_firecrawl_api_key"
_NEW_DOCTYPE = "Competitor Sites Connector Settings"
_NEW_FIELD = "cs_firecrawl_api_key"


def execute():
    if not frappe.db.exists("DocType", _OLD_DOCTYPE):
        return  # fresh install, or already migrated -- nothing to carry over

    old_key = None
    try:
        old_key = get_decrypted_password(_OLD_DOCTYPE, _OLD_DOCTYPE, _OLD_FIELD, raise_exception=False)
    except Exception:
        frappe.log_error(
            title="Competitor Sites settings rename: could not read old Firecrawl key",
            message=frappe.get_traceback(),
        )

    if old_key and frappe.db.exists("DocType", _NEW_DOCTYPE):
        set_encrypted_password(_NEW_DOCTYPE, _NEW_DOCTYPE, old_key, _NEW_FIELD)
        frappe.db.commit()

    frappe.delete_doc("DocType", _OLD_DOCTYPE, ignore_permissions=True, force=True, ignore_missing=True)
    frappe.db.commit()
