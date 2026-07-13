frappe.pages["scrape-runner"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: "Scrape Runner", single_column: true });
	const body = $(wrapper).find(".layout-main-section");
	body.addClass("sb-sr-page-body");
	body.html(window._sb_sr_html());
	const root = body.find(".sb-sr")[0];
	window._sb_sr_load(root);
	window._sb_sr_bind_events(root);
	wrapper._sb_root = root;
};

frappe.pages["scrape-runner"].on_page_show = function (wrapper) {
	const root = wrapper._sb_root;
	if (!root) return;
	const saved = window._sb_sr_restore_state && window._sb_sr_restore_state(root);
};
