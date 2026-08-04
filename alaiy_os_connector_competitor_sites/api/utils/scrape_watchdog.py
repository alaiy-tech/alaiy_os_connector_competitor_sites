"""Reaps Scrape Log rows that were never resolved because the worker running
them died (OOM kill, bench restart, SIGKILL) without a chance to update the
row itself. Without this, a stuck row sits in status="Running" forever and
the frontend polls it indefinitely.

Two ways this gets called (both harmless to call repeatedly):
  1. Opportunistically, at the top of scrape_all_sites() / get_scrape_progress()
     — rate-limited to once per 60s via a Redis NX key — so stuck rows resolve
     the moment a user is actually looking at the UI, with zero cron/scheduler
     dependency.
  2. Via the cron entry in hooks.py (scheduler_events), as a backup in case
     nobody opens the UI for a while.
"""

import frappe

_STALE_RUNNING_SECONDS = 120  # no heartbeat/update in 2 min while "Running" = presumed dead
_STALE_QUEUED_SECONDS = 30 * 60  # never even started after 30 min = presumed lost
_RATE_LIMIT_KEY = "scrape_watchdog:last_run"
_RATE_LIMIT_SECONDS = 60


def maybe_reap(force=False):
    """Rate-limited wrapper safe to call from any request path."""
    if not force:
        cache = frappe.cache()
        if cache.get_value(_RATE_LIMIT_KEY):
            return
        cache.set_value(_RATE_LIMIT_KEY, "1", expires_in_sec=_RATE_LIMIT_SECONDS)
    reap_stale_scrape_logs()


def reap_stale_scrape_logs():
    """Cron entry point (hooks.py scheduler_events) and the function `maybe_reap`
    delegates to. Safe to call as often as you like — every UPDATE is scoped
    to `WHERE status = 'Running'/'Queued'` so it can never downgrade a row a
    worker is actively finalizing in the same instant, and it never touches a
    row that already reached a terminal status."""
    _reap_running()
    _reap_queued()


def _reap_running():
    # Compare against frappe.utils.now_datetime(), NOT SQL's NOW(). Frappe
    # stores every Datetime field in the site's configured local timezone
    # (System Settings.time_zone) — on this server that's Asia/Kolkata
    # (UTC+5:30) — while MySQL's own NOW() returns the DB server's system
    # clock (UTC here). Comparing started_at/last_heartbeat against NOW()
    # produced a large NEGATIVE age every time, so this never matched and
    # no row was ever reaped. Confirmed live: a genuinely stuck 12+ minute
    # Deep run sat in "Running" through several 5-min cron ticks untouched
    # because of this exact mismatch.
    rows = frappe.db.sql(
        """
        SELECT name, products_saved, started_at, last_heartbeat
        FROM `tabScrape Log`
        WHERE status = 'Running'
          AND TIMESTAMPDIFF(SECOND, COALESCE(last_heartbeat, started_at, creation), %s) > %s
        """,
        (frappe.utils.now_datetime(), _STALE_RUNNING_SECONDS),
        as_dict=True,
    )
    for row in rows:
        saved = row.products_saved or 0
        if saved > 0:
            status = "Partial"
            summary = f"Run ended abnormally (worker stopped responding) — {saved} products were saved before the stop."
        else:
            status = "Failed"
            summary = "Run ended abnormally (worker stopped responding) — no products were saved."

        updated = frappe.db.sql(
            """
            UPDATE `tabScrape Log`
            SET status = %s, summary_line = %s,
                log = CONCAT(COALESCE(log, ''), '\n\n[watchdog] ', %s),
                completed_at = %s
            WHERE name = %s AND status = 'Running'
            """,
            (status, summary, summary, frappe.utils.now_datetime(), row.name),
        )
        if updated:
            frappe.logger().info(f"scrape_watchdog: reaped stale Running row {row.name} -> {status}")
    if rows:
        frappe.db.commit()


def _reap_queued():
    # Same fix as _reap_running: compare against Frappe's site-local clock,
    # not MySQL's NOW().
    rows = frappe.db.sql(
        """
        SELECT name FROM `tabScrape Log`
        WHERE status = 'Queued'
          AND TIMESTAMPDIFF(SECOND, creation, %s) > %s
        """,
        (frappe.utils.now_datetime(), _STALE_QUEUED_SECONDS),
        as_dict=True,
    )
    for row in rows:
        summary = "Job never started — the background worker may have been down or the queue was backed up."
        frappe.db.sql(
            """
            UPDATE `tabScrape Log`
            SET status = 'Failed', summary_line = %s,
                log = CONCAT(COALESCE(log, ''), '\n\n[watchdog] ', %s),
                completed_at = %s
            WHERE name = %s AND status = 'Queued'
            """,
            (summary, summary, frappe.utils.now_datetime(), row.name),
        )
        frappe.logger().info(f"scrape_watchdog: reaped stale Queued row {row.name} -> Failed")
    if rows:
        frappe.db.commit()
