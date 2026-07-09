/**
 * Competitor Products connector card renderer.
 * Only shows the Firecrawl Settings link — navigation to Website Manager
 * and Scrape Runner is via the sidebar.
 */
(function () {
	window.stellar_brands_connector_card = function (syncSection, connector) {
		const row = document.createElement("div");
		row.className = "sb-connector-action-row";
		const btn = document.createElement("button");
		btn.className = "btn btn-xs btn-default sb-connector-action-btn";
		btn.textContent = __("Firecrawl Settings");
		btn.addEventListener("click", () =>
			frappe.set_route("Form", connector.settings_doctype)
		);
		row.appendChild(btn);
		syncSection.appendChild(row);
	};
})();
