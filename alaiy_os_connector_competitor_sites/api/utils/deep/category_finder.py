"""Given a site's homepage and a desired category name (e.g. "Jewelry",
"Necklaces"), find the real category/listing page URL on THAT site --
generically, with no hardcoded per-site category-to-URL mapping.

This is the actual requirement behind Competitor Site's `categories` field:
an operator names a category once ("Jewelry"), and this finds wherever that
category actually lives on each specific site, rather than requiring the
operator to hand-discover and paste the exact listing URL for every site.

Two independent strategies, tried in order (nav-link matching is higher
confidence since it reflects the site's own information architecture; URL
templates are a last-resort guess):
  1. Scan every link on the homepage for one whose visible text or URL
     path matches the category name.
  2. Guess common category-URL path conventions directly.

Neither is trusted blind -- every candidate is verified by actually
checking it yields real product-shaped content (via the caller's verify_fn,
which should run a real extraction attempt) before being returned. A wrong
guess costs one extra page load, never a silently wrong result.
"""

import re
from urllib.parse import urljoin, urlparse

from alaiy_os_connector_competitor_sites.api.utils.deep import api_signatures

_NAV_LINK_JS = r"""
() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
  href: a.href,
  text: (a.innerText || a.getAttribute('aria-label') || '').trim(),
}))
"""


def _slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def _singular_plural_variants(name):
    n = (name or "").strip().lower()
    if not n:
        return set()
    variants = {n}
    if n.endswith("s"):
        variants.add(n[:-1])
    else:
        variants.add(n + "s")
    return variants


def find_category_nav_links(page, site_url, category_name):
    """Loads the homepage and scores every link on it against category_name
    (exact text match > substring match > slug-in-path match > singular/
    plural loose match). Returns candidate URLs, highest-confidence first,
    deduplicated. [] if the page can't be loaded or nothing scores at all."""
    norm_target = (category_name or "").strip().lower()
    if not norm_target:
        return []
    slug_target = _slugify(category_name)
    variants = _singular_plural_variants(category_name)

    try:
        page.goto(site_url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
    except Exception:
        return []

    try:
        links = page.evaluate(_NAV_LINK_JS) or []
    except Exception:
        links = []

    scored = []
    for link in links:
        href = (link or {}).get("href", "")
        text = (link or {}).get("text", "")
        if not href:
            continue
        text_norm = text.strip().lower()
        parsed = urlparse(href)
        # Category identifiers aren't always a readable slug in the path --
        # many platforms use an opaque numeric/query-param id instead
        # (Salesforce Commerce Cloud's ?cgid=, Magento's ?cat=, etc), which
        # urlparse().path alone would never see. Match against the whole
        # URL (path + query), not just the path, so a category_name value
        # that IS already an id/query-string fragment (an operator can pass
        # "cgid=25521" or "25521" directly, not only a human name) still
        # matches correctly.
        path = parsed.path.lower()
        full = href.lower()

        score = 0
        if text_norm == norm_target:
            score = 4
        elif norm_target in text_norm:
            score = 3
        elif slug_target and slug_target in path:
            score = 3
        elif norm_target and norm_target in full:
            score = 3
        elif text_norm in variants:
            score = 2
        elif any(v in path for v in variants if v):
            score = 1
        elif any(v and v in full for v in variants):
            score = 1

        if score:
            scored.append((score, href))

    scored.sort(key=lambda x: -x[0])
    seen = set()
    ordered = []
    for _score, href in scored:
        if href not in seen:
            seen.add(href)
            ordered.append(href)
    return ordered


def guess_category_url_templates(site_url, category_name):
    """Fallback candidates from common category-URL conventions -- tried
    only after (and in addition to) nav-link matching, since a template
    guess has no site-specific evidence behind it at all."""
    slug = _slugify(category_name)
    if not slug:
        return []
    return [urljoin(site_url, tmpl.format(slug=slug)) for tmpl in api_signatures.CATEGORY_PATH_TEMPLATES]


def find_category_listing_url(page, site_url, category_name, verify_fn):
    """Returns the first candidate URL verify_fn(url) confirms is a real
    listing page, or None if nothing checked out. verify_fn should attempt
    a real (cheap) extraction against the URL and return True only if it
    found genuine product-shaped rows -- never trust a URL match alone,
    a nav link's text can coincidentally match without it being a real
    category listing (a blog post titled "Our Jewelry Guide", for example)."""
    candidates = find_category_nav_links(page, site_url, category_name)
    candidates += guess_category_url_templates(site_url, category_name)

    checked = set()
    for url in candidates:
        if url in checked:
            continue
        checked.add(url)
        if verify_fn(url):
            return url
    return None
