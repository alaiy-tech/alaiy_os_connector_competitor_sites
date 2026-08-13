"""Backfill for the Deep-scraper schema change.

Runs after doctype sync has already added the new columns (url_hash on
Scraped Product; the new Select options / fields on Competitor Site and
Scrape Log don't need backfill, only Scraped Product does).

What this does:
1. For every existing Scraped Product, compute url_hash from its current
   source_product_url and write it.
2. If two existing rows collide on the same hash (their truncated-140-char
   URLs happened to be identical, or genuinely are the same product saved
   twice under the old buggy dedup), keep the oldest by creation and mark
   the rest review_status="Skipped" with a note, rather than deleting data.
3. Drop the old unique index on source_product_url (doctype sync removes
   the `unique` flag from the field, but Frappe can leave the underlying
   MariaDB index behind — this makes sure it's actually gone), so the new
   url_hash unique index is the only one enforcing dedup going forward.
"""

import frappe

from alaiy_os_connector_competitor_sites.api.utils.scrape_utils import url_hash


def execute():
    _backfill_url_hash()
    _drop_stale_unique_index()


def _backfill_url_hash():
    batch_size = 500
    seen_hashes = {}  # hash -> name of the row we're keeping

    rows = frappe.get_all(
        "Scraped Product",
        fields=["name", "source_product_url", "creation"],
        order_by="creation asc",
    )

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        for row in batch:
            h = url_hash(row.source_product_url)
            if not h:
                continue

            if h in seen_hashes:
                # Collision with an earlier (older) row — keep that one,
                # mark this one rather than deleting it outright.
                frappe.db.set_value(
                    "Scraped Product",
                    row.name,
                    {
                        "review_status": "Skipped",
                        "notes": f"Deduped during url_hash migration — collided with {seen_hashes[h]}",
                    },
                    update_modified=False,
                )
                continue

            seen_hashes[h] = row.name
            frappe.db.set_value("Scraped Product", row.name, "url_hash", h, update_modified=False)

        frappe.db.commit()

    frappe.logger().info(
        f"add_deep_scrape_fields: backfilled url_hash for {len(seen_hashes)} rows "
        f"({len(rows) - len(seen_hashes)} collisions marked Skipped) out of {len(rows)} total"
    )


def _drop_stale_unique_index():
    """Doctype sync (from the JSON change) handles adding/removing indexes for
    the fields it manages, but on some MariaDB/Frappe version combinations a
    leftover unique index on source_product_url can survive a field-level
    unique:0 change. Explicitly check and drop it so it can never collide
    with genuinely different products that share a >140-char-truncated
    prefix."""
    indexes = frappe.db.sql(
        "SHOW INDEX FROM `tabScraped Product` WHERE Key_name != 'PRIMARY'",
        as_dict=True,
    )
    stale = {
        idx["Key_name"]
        for idx in indexes
        if idx["Column_name"] == "source_product_url" and idx["Non_unique"] == 0
    }
    for key_name in stale:
        try:
            frappe.db.sql(f"ALTER TABLE `tabScraped Product` DROP INDEX `{key_name}`")
        except Exception as e:
            frappe.logger().warning(f"add_deep_scrape_fields: could not drop stale index {key_name}: {e}")
    if stale:
        frappe.db.commit()
