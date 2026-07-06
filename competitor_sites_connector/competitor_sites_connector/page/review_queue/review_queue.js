frappe.pages["review-queue"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: "Review Queue", single_column: true });
	const body = $(wrapper).find(".layout-main-section");
	body.empty();
	window.rq_init(body[0]);
};

frappe.pages["review-queue"].on_page_show = function (wrapper) {
	const body = $(wrapper).find(".layout-main-section");
	// re-init if the grid is empty (e.g. navigated back to this page)
	if (!body.find(".rq-wrap").length) {
		body.empty();
		window.rq_init(body[0]);
	}
};
