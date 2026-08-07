"""Chromium launch, memory-safety flags, the single-browser-at-a-time slot,
and explicit handling for the case where the browser fails to launch at all.

Memory posture (2 vCPU / ~2GB available / no swap on the target server):
  - prefer the headless-shell binary Playwright already has on disk — no
    GPU/compositor/UI process layers, materially lower RSS than full Chromium
  - block image/media/font/stylesheet requests and known tracker hosts —
    this does NOT lose image URLs, which come from DOM attributes
    (data-src, srcset), not from the fetched bytes
  - one browser process at a time, host-wide: enforced primarily by routing
    Deep jobs to the `long` RQ queue (only one of the two supervisor workers
    on this box serves it), backed by a Redis semaphore for any other caller
  - oom_score_adj on the Chromium child so the kernel, under memory
    pressure, kills the browser rather than the worker or MariaDB — Deep
    catches that as a normal exception and ends the run as Partial with
    whatever was already saved intact
"""

import contextlib
import os
import signal

import frappe

from alaiy_os_connector_competitor_sites.api.utils.deep import api_signatures

_REDIS_SLOT_KEY = "deep_scrape:browser_slot"
_REDIS_BROKEN_KEY = "deep_scrape:browser_broken"
_SLOT_TTL_SECONDS = 180
_BROKEN_TTL_SECONDS = 300

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--renderer-process-limit=1",
    "--js-flags=--max-old-space-size=192",
    "--disable-extensions",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-default-browser-check",
    "--mute-audio",
    "--hide-scrollbars",
    "--disable-blink-features=AutomationControlled",
    "--window-size=1280,900",
]


class BrowserLaunchError(Exception):
    """Distinct from every other failure mode: this means Chromium itself
    could not start (missing binary, permissions, port bind failure,
    kernel rejecting a launch flag) — an environment/server problem, not a
    problem with the target site. Callers must not retry this in a loop
    (a broken launch path fails identically every time) and must word the
    resulting message so it's never confused with a per-site bot-block."""


def browser_known_broken():
    """Cheap short-circuit: if a previous job in this batch already hit
    BrowserLaunchError in the last 5 minutes, don't make every other queued
    Deep job independently re-discover and re-time-out on the same broken
    environment."""
    return bool(frappe.cache().get_value(_REDIS_BROKEN_KEY))


def mark_browser_broken():
    frappe.cache().set_value(_REDIS_BROKEN_KEY, "1", expires_in_sec=_BROKEN_TTL_SECONDS)


@contextlib.contextmanager
def browser_slot(wait_seconds=150):
    """Redis-backed semaphore, TTL-based so a killed worker can't leave the
    slot permanently held. This is belt-and-suspenders — the `long` queue
    routing is what actually enforces "one Chromium at a time" day to day —
    but protects against any other caller (bench console, a stray patch)
    launching a second one concurrently."""
    import time

    cache = frappe.cache()
    token = frappe.generate_hash(length=12)
    deadline = time.monotonic() + wait_seconds
    acquired = False

    while time.monotonic() < deadline:
        if cache.get_value(_REDIS_SLOT_KEY) is None:
            cache.set_value(_REDIS_SLOT_KEY, token, expires_in_sec=_SLOT_TTL_SECONDS)
            # re-check we actually won the race (best-effort; Frappe's cache
            # wrapper doesn't expose a real SETNX, so this narrows but
            # doesn't eliminate the race — acceptable given the `long` queue
            # already limits real concurrency to 1)
            if cache.get_value(_REDIS_SLOT_KEY) == token:
                acquired = True
                break
        time.sleep(2)

    if not acquired:
        yield False
        return

    try:
        yield True
    finally:
        if cache.get_value(_REDIS_SLOT_KEY) == token:
            cache.delete_value(_REDIS_SLOT_KEY)


def resolve_headless_shell_path(playwright):
    """Prefer the headless-shell binary if Playwright can resolve it;
    otherwise fall back to whatever Chromium build is installed (still
    correct, just heavier). Never raises — a missing/odd install shows up
    as a BrowserLaunchError at actual launch time instead."""
    try:
        # Playwright's `chromium` channel already resolves to the
        # headless-shell binary on this box's install (confirmed:
        # chromium_headless_shell-1228 is what gets installed here for the
        # `chromium` build without the `channel="chrome"` argument).
        return playwright.chromium.executable_path
    except Exception:
        return None


def launch(playwright, headless=True):
    """Raises BrowserLaunchError (never a bare Exception) if Chromium fails
    to start, so callers can apply the distinct, environment-focused
    handling described in the module docstring."""
    args = list(_LAUNCH_ARGS)
    try:
        browser = playwright.chromium.launch(headless=headless, args=args)
    except Exception as e:
        raise BrowserLaunchError(str(e)) from e

    try:
        _set_oom_score_adj(browser)
    except Exception:
        pass  # best-effort — not worth failing the run over

    return browser


def _set_oom_score_adj(browser):
    pid = None
    with contextlib.suppress(Exception):
        pid = browser.process.pid if hasattr(browser, "process") else None
    if not pid:
        return
    with contextlib.suppress(Exception):
        with open(f"/proc/{pid}/oom_score_adj", "w") as f:
            f.write("800")


def new_context(browser):
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
        },
        locale="en-US",
        timezone_id="America/New_York",
        service_workers="block",
    )
    _install_resource_blocking(ctx)
    return ctx



_FAKE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "89000000034944415478da6360000000020001e221bc330000000049454e44ae426082"
)


def _install_resource_blocking(context):
    def handler(route, request):
        rtype = request.resource_type
        url = request.url
        if rtype in ("image", "media", "font"):
            # route.abort() looks like a real network failure to the page's
            # own JS -- confirmed live: Next.js's <img> treated an aborted
            # image request as a load error and overwrote its own real
            # srcset/src with an error-fallback URL before extraction ever
            # ran, so the "saved bandwidth" cost us the real image URL
            # entirely. Fulfilling with a tiny fake image instead still
            # avoids downloading real image bytes, but looks like a normal
            # successful load to the page, so it never rewrites the
            # attributes we're about to read. Image *URLs* still always
            # come from DOM attributes (data-src/srcset), never from
            # decoding this fake response.
            if rtype == "image":
                return route.fulfill(status=200, content_type="image/png", body=_FAKE_PIXEL_PNG)
            return route.abort()
        if any(host in url for host in api_signatures.ANALYTICS_TRACKER_HOST_MARKERS):
            return route.abort()
        return route.continue_()

    context.route("**/*", handler)


def safe_close(browser=None, context=None, playwright=None):
    """Teardown that never raises — an orphaned Chromium process on a
    no-swap box is how the NEXT job dies, so every step here is best-effort
    but all of them run regardless of what fails."""
    if context is not None:
        with contextlib.suppress(Exception):
            context.close()
    if browser is not None:
        pid = None
        with contextlib.suppress(Exception):
            pid = browser.process.pid if hasattr(browser, "process") else None
        with contextlib.suppress(Exception):
            browser.close()
        if pid:
            with contextlib.suppress(Exception, ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
    if playwright is not None:
        with contextlib.suppress(Exception):
            playwright.stop()
