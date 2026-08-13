"""Classifies a fetched page as OK or one of several bot-block/rate-limit
states, so the runner can tell "genuinely no products here" apart from
"the site is actively pushing back" — and react appropriately to each
without ever attempting to defeat a CAPTCHA or a hard block.

Confirmed necessary by direct evidence from this exact build: repeated test
runs against the same Fortunate One (Shopify) URL alternated between
finding 109 real products and finding 0, and the 0-result pages turned out
to be Shopify's own `local_rate_limited` response body — not a rendering
race at all. Silently treating that as "empty page" would have both hidden
the real cause and (if it happened in production) let a rate-limited page
poison the run's counts.
"""

import re

RATE_LIMITED = "rate_limited"
CHALLENGE = "challenge"
CAPTCHA = "captcha"
GEOBLOCK = "geoblock"
OK = "ok"

_RATE_LIMIT_MARKERS = (
    "local_rate_limited",
    "rate limit exceeded",
    "too many requests",
)
_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-chl",
    "checking your browser",
    "attention required",
    "cf-mitigated",
)
_CAPTCHA_MARKERS = (
    "captcha",
    "are you human",
    "hcaptcha",
    "recaptcha",
    "verify you are human",
)
_GEOBLOCK_MARKERS = (
    "not available in your country",
    "not available in your region",
    "shipping to your location",
)


def classify(html):
    """html: the raw page content. Returns one of the module-level
    constants. Checked in escalation order — a rate-limit body is short and
    won't also match challenge markers, but check narrowest-signal first
    regardless."""
    if not html:
        return OK  # empty page is handled by the caller's own "0 candidates" path
    text = html.lower()

    if any(m in text for m in _RATE_LIMIT_MARKERS):
        return RATE_LIMITED
    if any(m in text for m in _CAPTCHA_MARKERS):
        return CAPTCHA
    if any(m in text for m in _CHALLENGE_MARKERS):
        return CHALLENGE
    if any(m in text for m in _GEOBLOCK_MARKERS):
        return GEOBLOCK
    return OK


_RETRY_AFTER_RE = re.compile(r"retry.after[\"'\s:]*(\d+)", re.IGNORECASE)


def backoff_seconds(html, attempt):
    """Best-effort Retry-After style hint from the page body, else a fixed
    escalating backoff (20s, 60s, 180s — capped)."""
    if html:
        m = _RETRY_AFTER_RE.search(html)
        if m:
            try:
                return min(int(m.group(1)), 180)
            except ValueError:
                pass
    return min(20 * (2 ** attempt), 180)
