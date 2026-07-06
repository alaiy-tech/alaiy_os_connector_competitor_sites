import frappe
from firecrawl import V1FirecrawlApp as FirecrawlApp


@frappe.whitelist()
def test_connection():
    settings = frappe.get_single("Stellar Brands Connector Settings")
    api_key = settings.get_password("sb_firecrawl_api_key")

    if not api_key:
        return {"success": False, "message": "Firecrawl API key is not set"}

    try:
        fc = FirecrawlApp(api_key=api_key)
        # Scrape a minimal known URL to validate the key without side effects.
        # Using example.com — fast, always up, returns minimal content.
        result = fc.scrape_url("https://example.com", formats=["markdown"])
        if result:
            return {"success": True, "message": "Connected to Firecrawl successfully"}
        return {"success": False, "message": "Firecrawl returned an empty response"}
    except Exception as e:
        msg = str(e)
        if "401" in msg or "unauthorized" in msg.lower():
            return {"success": False, "message": "Invalid API key (401 Unauthorized)"}
        if "403" in msg or "forbidden" in msg.lower():
            return {"success": False, "message": "Access denied (403 Forbidden)"}
        return {"success": False, "message": f"Connection failed: {msg}"}
