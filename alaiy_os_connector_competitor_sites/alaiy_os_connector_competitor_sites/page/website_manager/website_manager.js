frappe.pages["website-manager"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: "Website Manager", single_column: true });
	const body = $(wrapper).find(".layout-main-section");
	body.addClass("sb-wm-page-body");
	body.html(window._sb_wm_html());
	const root = body.find(".sb-wm")[0];
	window._sb_wm_load(root);
	root.querySelector(".sb-wm-add-btn").addEventListener("click", () =>
		window._sb_wm_show_add_form(root)
	);
};
