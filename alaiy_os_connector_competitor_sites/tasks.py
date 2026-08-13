"""Scheduled task entry points. See hooks.py scheduler_events."""

from alaiy_os_connector_competitor_sites.api.utils.scrape_watchdog import reap_stale_scrape_logs


def reap_stale_scrapes():
    reap_stale_scrape_logs()
