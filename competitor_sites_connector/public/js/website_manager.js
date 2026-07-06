/**
 * Website Manager — shared logic used by the full Frappe Page (website-manager).
 *
 * Exposes window._sb_wm_html, window._sb_wm_load, window._sb_wm_show_add_form
 * so the page JS can call them directly.
 */
(function () {
	window._sb_wm_html = _html;
	window._sb_wm_load = _loadSites;
	window._sb_wm_show_add_form = _showAddForm;

	function _html() {
		return `
<div class="sb-wm">
  <div class="sb-wm-header">
    <div class="sb-wm-header-left">
      <div class="sb-wm-title">Website Manager</div>
      <div class="sb-wm-subtitle">Configure and monitor your jewelry scrape sources.</div>
    </div>
    <button class="btn sb-wm-add-btn"><span>+</span> Add Website</button>
  </div>
  <div class="sb-wm-table-wrap">
    <table class="sb-wm-table">
      <thead>
        <tr>
          <th class="sb-wm-col-check"><input type="checkbox" class="sb-wm-check-all"></th>
          <th class="sb-wm-col-website">Website</th>
          <th class="sb-wm-col-categories">Categories</th>
          <th class="sb-wm-col-status">Status</th>
          <th class="sb-wm-col-scraped">Last Scraped</th>
          <th class="sb-wm-col-products">Products</th>
          <th class="sb-wm-col-active">Active</th>
          <th class="sb-wm-col-del"></th>
        </tr>
      </thead>
      <tbody class="sb-wm-tbody">
        <tr><td colspan="8" class="sb-wm-loading">Loading…</td></tr>
      </tbody>
    </table>
  </div>
  <div class="sb-wm-add-form" style="display:none">
    <div class="sb-wm-add-form-title">Add Website</div>
    <div class="sb-wm-add-row">
      <input class="form-control sb-wm-input" data-field="site_name" placeholder="Site name (e.g. Lulus)">
      <input class="form-control sb-wm-input" data-field="site_url" placeholder="https://lulus.com">
      <input class="form-control sb-wm-input" data-field="categories" placeholder="Categories (comma-separated)">
      <button class="btn btn-primary sb-wm-save-btn">Add</button>
      <button class="btn btn-default sb-wm-cancel-btn">Cancel</button>
    </div>
  </div>
</div>`;
	}

	function _loadSites(root) {
		frappe.call({
			method: "competitor_sites_connector.api.competitor_site.get_sites_with_stats",
			callback: (r) => {
				const tbody = root.querySelector(".sb-wm-tbody");
				if (!tbody) return;
				const sites = r.message || [];
				if (!sites.length) {
					tbody.innerHTML = `<tr><td colspan="8" class="sb-wm-empty">No sites yet. Click "+ Add Website" to add one.</td></tr>`;
					return;
				}
				tbody.innerHTML = sites.map(_rowHtml).join("");
				_bindRowEvents(root);
			},
			error: (err) => {
				const tbody = root.querySelector(".sb-wm-tbody");
				if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="sb-wm-empty" style="color:red">Failed to load sites.</td></tr>`;
				console.error("[WebsiteManager]", err);
			},
		});
	}

	function _rowHtml(s) {
		const cats = (s.categories || "")
			.split(",").map((c) => c.trim()).filter(Boolean)
			.map((c) => `<span class="sb-wm-tag">${_esc(c)}</span>`).join("");
		const lastScraped = s.last_scraped
			? frappe.datetime.str_to_user(s.last_scraped)
			: '<span class="sb-wm-never">Never</span>';
		const active = parseInt(s.is_active) ? "checked" : "";
		return `
<tr data-site="${_esc(s.name)}">
  <td class="sb-wm-col-check"><input type="checkbox" class="sb-wm-row-check"></td>
  <td class="sb-wm-col-website">
    <div class="sb-wm-site-name">${_esc(s.name)}</div>
    <div class="sb-wm-site-url">${_esc(s.site_url)}</div>
  </td>
  <td class="sb-wm-col-categories">${cats || '<span class="sb-wm-none">—</span>'}</td>
  <td class="sb-wm-col-status"><span class="sb-wm-status-badge sb-wm-status-active">● Active</span></td>
  <td class="sb-wm-col-scraped">${lastScraped}</td>
  <td class="sb-wm-col-products">${s.product_count || 0}</td>
  <td class="sb-wm-col-active">
    <label class="sb-wm-toggle">
      <input type="checkbox" class="sb-wm-toggle-input" ${active}>
      <span class="sb-wm-toggle-slider"></span>
    </label>
  </td>
  <td class="sb-wm-col-del">
    <button class="sb-wm-del-btn" title="Delete">${frappe.utils.icon("delete", "sm")}</button>
  </td>
</tr>`;
	}

	function _bindRowEvents(root) {
		root.querySelectorAll(".sb-wm-toggle-input").forEach((toggle) => {
			toggle.addEventListener("change", function () {
				const siteName = this.closest("tr").dataset.site;
				frappe.call({
					method: "competitor_sites_connector.api.competitor_site.toggle_active",
					args: { site_name: siteName, is_active: this.checked ? 1 : 0 },
				});
			});
		});
		root.querySelectorAll(".sb-wm-del-btn").forEach((btn) => {
			btn.addEventListener("click", function () {
				const siteName = this.closest("tr").dataset.site;
				frappe.confirm(
					__("Delete {0}? This will not delete scraped products.", [siteName]),
					() => {
						frappe.call({
							method: "competitor_sites_connector.api.competitor_site.delete_site",
							args: { site_name: siteName },
							callback: () => _loadSites(root),
						});
					}
				);
			});
		});
		root.querySelector(".sb-wm-check-all").addEventListener("change", function () {
			root.querySelectorAll(".sb-wm-row-check").forEach((c) => { c.checked = this.checked; });
		});
	}

	function _showAddForm(root) {
		root.querySelector(".sb-wm-add-form").style.display = "";
		root.querySelector(".sb-wm-add-btn").style.display = "none";
		root.querySelector(".sb-wm-cancel-btn").onclick = () => {
			root.querySelector(".sb-wm-add-form").style.display = "none";
			root.querySelector(".sb-wm-add-btn").style.display = "";
		};
		root.querySelector(".sb-wm-save-btn").onclick = () => {
			const get = (f) => root.querySelector(`[data-field="${f}"]`).value.trim();
			const site_name = get("site_name");
			const site_url = get("site_url");
			const categories = get("categories");
			if (!site_name || !site_url) { frappe.msgprint(__("Site name and URL are required.")); return; }
			frappe.call({
				method: "competitor_sites_connector.api.competitor_site.add_site",
				args: { site_name, site_url, categories },
				callback: () => {
					root.querySelectorAll(".sb-wm-input").forEach((i) => (i.value = ""));
					root.querySelector(".sb-wm-add-form").style.display = "none";
					root.querySelector(".sb-wm-add-btn").style.display = "";
					_loadSites(root);
				},
			});
		};
	}

	function _esc(str) {
		return (str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
	}
})();
