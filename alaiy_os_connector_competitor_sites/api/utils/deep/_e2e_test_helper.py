"""Throwaway helper for the 3-site end-to-end verification test — calls the
real whitelisted endpoints exactly as the frontend does, then polls until
terminal. Not part of the shipped feature; delete before merging if it's
still here."""

import json
import time

import frappe


def run(sites, poll_interval=5, max_wait=1800):
    from alaiy_os_connector_competitor_sites.api.scrape_runner import get_scrape_progress, scrape_all_sites

    result = scrape_all_sites(sites=json.dumps(sites))
    print("scrape_all_sites ->", json.dumps(result))
    log_names = result["log_names"]

    start = time.monotonic()
    while time.monotonic() - start < max_wait:
        frappe.db.commit()  # see other workers' commits in this same process
        progress = get_scrape_progress(log_names=json.dumps(log_names))
        print(f"--- t={int(time.monotonic() - start)}s ---")
        for site, info in progress.items():
            print(f"  {site}: status={info['status']} saved={info['products_saved']} "
                  f"already_in_db={info['already_in_db']} urls_found={info['urls_found']} "
                  f"method={info['method_used']} elapsed={info['elapsed_seconds']}s "
                  f"summary={info.get('summary_line','')}")
        all_done = all(info["status"] in ("Done", "Failed", "Partial") for info in progress.values())
        if all_done:
            print("ALL DONE")
            return progress
        time.sleep(poll_interval)

    print("TIMED OUT WAITING")
    return progress
