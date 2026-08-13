"""Multi-signal response verification and traffic diagnostic system.

Analyzes HTTP status codes, response headers, cookies, page titles, and body content
to classify access restrictions, rate limits, and protection vendors.
Provides structured diagnostic reports and recovery hints without attempting
bypasses.
"""

import dataclasses
import re
from typing import Dict, List, Optional

from alaiy_os_connector_competitor_sites.api.utils.deep import api_signatures

# Core Status Categories
RATE_LIMITED = "rate_limited"
CHALLENGE = "challenge"
CAPTCHA = "captcha"
GEOBLOCK = "geoblock"
HARD_BLOCK = "hard_block"
AUTH_REQUIRED = "auth_required"
NETWORK_ERROR = "network_error"
OK = "ok"


@dataclasses.dataclass
class DiagnosticResult:
    status: str
    vendor: str = "NONE"
    confidence: float = 1.0
    status_code: int = 200
    reasons: List[str] = dataclasses.field(default_factory=list)
    recovery_action: str = "CONTINUE"
    evidence_snippet: Optional[str] = None

    def is_blocked(self) -> bool:
        return self.status != OK


def analyze_response(
    html: Optional[str] = None,
    status_code: int = 200,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    title: Optional[str] = None,
    url: Optional[str] = None,
) -> DiagnosticResult:
    """Performs multi-signal diagnostic analysis across HTTP status, headers,
    cookies, title, and body markers to classify response state."""
    headers_lower = {k.lower(): str(v).lower() for k, v in (headers or {}).items()}
    cookies_lower = {k.lower(): str(v).lower() for k, v in (cookies or {}).items()}
    text = (html or "").lower()
    title_text = (title or "").lower()

    reasons = []
    detected_vendor = "NONE"
    confidence = 0.0

    # 1. Vendor Header & Cookie Inspection
    for vendor, sigs in api_signatures.PROTECTION_VENDOR_SIGNATURES.items():
        # Header match
        for h_sig in sigs.get("headers", ()):
            for hk, hv in headers_lower.items():
                if h_sig in hk or h_sig in f"{hk}: {hv}":
                    detected_vendor = vendor
                    confidence = max(confidence, 0.85)
                    reasons.append(f"Header match for {vendor}: {hk}")
                    break

        # Cookie match
        for c_sig in sigs.get("cookies", ()):
            for ck in cookies_lower:
                if c_sig in ck:
                    detected_vendor = vendor
                    confidence = max(confidence, 0.90)
                    reasons.append(f"Cookie match for {vendor}: {ck}")
                    break

        # Content marker match
        for m_sig in sigs.get("markers", ()):
            if m_sig in text or m_sig in title_text:
                if detected_vendor == "NONE":
                    detected_vendor = vendor
                confidence = max(confidence, 0.95)
                reasons.append(f"Marker match for {vendor}: '{m_sig}'")
                break

    # 2. Status Code Rule Evaluation
    if status_code == 429:
        reasons.append("HTTP 429 Too Many Requests")
        return DiagnosticResult(
            status=RATE_LIMITED,
            vendor=detected_vendor if detected_vendor != "NONE" else "GENERIC",
            confidence=1.0,
            status_code=status_code,
            reasons=reasons,
            recovery_action="BACKOFF",
            evidence_snippet=text[:300] if text else None,
        )

    if status_code in (401, 407):
        reasons.append(f"HTTP {status_code} Authentication Required")
        return DiagnosticResult(
            status=AUTH_REQUIRED,
            vendor=detected_vendor,
            confidence=1.0,
            status_code=status_code,
            reasons=reasons,
            recovery_action="HALT",
            evidence_snippet=text[:300] if text else None,
        )

    if status_code in (403, 406):
        reasons.append(f"HTTP {status_code} Access Denied / IP Restriction")
        # Distinguish between active CAPTCHA/Challenge and Hard Block
        if any(m in text for m in api_signatures.BOT_BLOCK_CAPTCHA_MARKERS):
            return DiagnosticResult(
                status=CAPTCHA,
                vendor=detected_vendor if detected_vendor != "NONE" else "GENERIC",
                confidence=0.95,
                status_code=status_code,
                reasons=reasons,
                recovery_action="ROTATE_PROXY",
                evidence_snippet=text[:300] if text else None,
            )
        if any(m in text for m in api_signatures.BOT_BLOCK_CHALLENGE_MARKERS):
            return DiagnosticResult(
                status=CHALLENGE,
                vendor=detected_vendor if detected_vendor != "NONE" else "GENERIC",
                confidence=0.95,
                status_code=status_code,
                reasons=reasons,
                recovery_action="ROTATE_PROXY",
                evidence_snippet=text[:300] if text else None,
            )
        return DiagnosticResult(
            status=HARD_BLOCK,
            vendor=detected_vendor if detected_vendor != "NONE" else "GENERIC",
            confidence=0.90,
            status_code=status_code,
            reasons=reasons,
            recovery_action="ROTATE_PROXY",
            evidence_snippet=text[:300] if text else None,
        )

    # 3. Body & Marker Signal Evaluation
    if any(m in text for m in api_signatures.BOT_BLOCK_RATE_LIMIT_MARKERS):
        reasons.append("Body keyword match: Rate Limited")
        return DiagnosticResult(
            status=RATE_LIMITED,
            vendor=detected_vendor if detected_vendor != "NONE" else "GENERIC",
            confidence=0.90,
            status_code=status_code,
            reasons=reasons,
            recovery_action="BACKOFF",
            evidence_snippet=text[:300] if text else None,
        )

    if any(m in text for m in api_signatures.BOT_BLOCK_CAPTCHA_MARKERS):
        reasons.append("Body keyword match: CAPTCHA")
        return DiagnosticResult(
            status=CAPTCHA,
            vendor=detected_vendor if detected_vendor != "NONE" else "GENERIC",
            confidence=0.90,
            status_code=status_code,
            reasons=reasons,
            recovery_action="ROTATE_PROXY",
            evidence_snippet=text[:300] if text else None,
        )

    if any(m in text for m in api_signatures.BOT_BLOCK_CHALLENGE_MARKERS):
        reasons.append("Body keyword match: Challenge Interstitial")
        return DiagnosticResult(
            status=CHALLENGE,
            vendor=detected_vendor if detected_vendor != "NONE" else "GENERIC",
            confidence=0.90,
            status_code=status_code,
            reasons=reasons,
            recovery_action="ROTATE_PROXY",
            evidence_snippet=text[:300] if text else None,
        )

    if any(m in text for m in api_signatures.BOT_BLOCK_GEOBLOCK_MARKERS):
        reasons.append("Body keyword match: Geo Blocked")
        return DiagnosticResult(
            status=GEOBLOCK,
            vendor=detected_vendor if detected_vendor != "NONE" else "GENERIC",
            confidence=0.90,
            status_code=status_code,
            reasons=reasons,
            recovery_action="ROTATE_PROXY_REGION",
            evidence_snippet=text[:300] if text else None,
        )

    # Title check for access restrictions
    if any(term in title_text for term in ("access denied", "attention required!", "security check", "just a moment")):
        reasons.append(f"Page title restriction match: '{title}'")
        return DiagnosticResult(
            status=CHALLENGE if "just a moment" in title_text else HARD_BLOCK,
            vendor=detected_vendor if detected_vendor != "NONE" else "GENERIC",
            confidence=0.85,
            status_code=status_code,
            reasons=reasons,
            recovery_action="ROTATE_PROXY",
            evidence_snippet=title,
        )

    return DiagnosticResult(
        status=OK,
        vendor=detected_vendor,
        confidence=1.0,
        status_code=status_code,
        reasons=["Normal response"],
        recovery_action="CONTINUE",
    )


def classify(
    html: Optional[str] = None,
    status_code: int = 200,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    title: Optional[str] = None,
) -> str:
    """Backward-compatible classification method returning string status."""
    result = analyze_response(
        html=html, status_code=status_code, headers=headers, cookies=cookies, title=title
    )
    return result.status


_RETRY_AFTER_RE = re.compile(r"retry.after[\"'\s:]*(\d+)", re.IGNORECASE)


def backoff_seconds(html: Optional[str], attempt: int, headers: Optional[Dict[str, str]] = None) -> int:
    """Best-effort Retry-After hint extraction from response headers or HTML,
    falling back to escalating backoff (20s, 60s, 180s — capped)."""
    if headers:
        for k, v in headers.items():
            if k.lower() == "retry-after":
                try:
                    return min(int(v), 180)
                except ValueError:
                    pass

    if html:
        m = _RETRY_AFTER_RE.search(html)
        if m:
            try:
                return min(int(m.group(1)), 180)
            except ValueError:
                pass
    return min(20 * (2**attempt), 180)
