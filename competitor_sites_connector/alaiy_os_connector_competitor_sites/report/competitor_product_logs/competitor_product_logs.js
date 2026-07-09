frappe.query_reports["Competitor Product Logs"] = {
	onload(report) {
		report.page.main.on("click", ".dt-row", function () {
			const scrape_id = $(this).find('[data-col-index="1"]').text().trim();
			if (!scrape_id) return;
			frappe.route_options = { scrape_id: scrape_id };
			frappe.set_route("List", "Scraped Product");
		});
	},
};
