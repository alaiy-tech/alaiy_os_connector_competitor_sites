frappe.pages["scrape-runner"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: "Scrape Runner", single_column: true });
	const body = $(wrapper).find(".layout-main-section");
	body.addClass("sb-sr-page-body");
	body.html(window._sb_sr_html());
	const root = body.find(".sb-sr")[0];
	window._sb_sr_load(root);
	window._sb_sr_bind_events(root);
};
