frappe.pages["pricing"].on_page_load = function (wrapper) {
    frappe.ui.make_app_page({ parent: wrapper, title: "Pricing", single_column: true });
    const body = $(wrapper).find(".layout-main-section");
    body.empty();
    window.pricing_init(body[0]);
};

frappe.pages["pricing"].on_page_show = function (wrapper) {
    const body = $(wrapper).find(".layout-main-section");
    if (!body.find(".pr-wrap").length) {
        body.empty();
        window.pricing_init(body[0]);
    }
};
