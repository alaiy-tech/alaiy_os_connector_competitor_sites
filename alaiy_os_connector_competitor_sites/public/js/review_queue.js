/**
 * Review Queue — product card grid with Skip / Keep actions.
 * Entry point: window.rq_init(containerEl)
 *
 * Interactions:
 *   - Click card        → open detail modal
 *   - Hover Skip/Keep   → quick-action buttons on card
 *   - Finalize button   → finalizes ALL Kept products
 */
(function () {

	let _state = {
		site: "", category: "", status: "Pending",
		search: "", page: 1, pageSize: 24, loading: false,
	};

	// ── Entry point ───────────────────────────────────────────────────────────
	window.rq_init = function (container) {
		// reset state on each init
		_state.site = ""; _state.category = ""; _state.status = "Pending";
		_state.search = ""; _state.page = 1; _state.loading = false;

		container.innerHTML = _html();
		_populateSiteFilter(container);
		_bindFilters(container);
		_loadProducts(container);
	};

	// ── Skeleton ──────────────────────────────────────────────────────────────
	function _html() {
		return `
<div class="rq-wrap">
  <div class="rq-toolbar">
    <div class="rq-filters">
      <div class="rq-search-wrap">
        <input class="rq-search" placeholder="Search products..." type="text">
      </div>
      <select class="rq-filter rq-filter-site"><option value="">All Sites</option></select>
      <select class="rq-filter rq-filter-category"><option value="">All Categories</option></select>
      <select class="rq-filter rq-filter-status">
        <option value="Pending">Pending</option>
        <option value="Kept">Kept</option>
        <option value="Skipped">Skipped</option>
        <option value="">All</option>
      </select>
    </div>
    <button class="btn rq-finalize-btn" disabled>
      ✦ Finalize <span class="rq-finalize-count">0</span> Kept
    </button>
  </div>
  <div class="rq-stats">
    <span class="rq-stat-kept">0 kept</span>
    <span class="rq-stat-sep">·</span>
    <span class="rq-stat-skipped">0 skipped</span>
    <span class="rq-stat-sep">·</span>
    <span class="rq-stat-remaining">0 remaining</span>
  </div>
  <div class="rq-grid"></div>
  <div class="rq-pagination"></div>
</div>`;
	}

	// ── Site filter ───────────────────────────────────────────────────────────
	function _populateSiteFilter(container) {
		frappe.call({
			method: "alaiy_os_connector_competitor_sites.api.competitor_site.get_sites_with_stats",
			callback: (r) => {
				const sel = container.querySelector(".rq-filter-site");
				(r.message || []).forEach((s) => {
					const opt = document.createElement("option");
					opt.value = s.name; opt.textContent = s.name;
					sel.appendChild(opt);
				});
			},
		});
	}

	// ── Filters ───────────────────────────────────────────────────────────────
	function _bindFilters(container) {
		let t;
		container.querySelector(".rq-search").addEventListener("input", function () {
			clearTimeout(t);
			t = setTimeout(() => { _state.search = this.value; _state.page = 1; _loadProducts(container); }, 300);
		});
		container.querySelector(".rq-filter-site").addEventListener("change", function () {
			_state.site = this.value; _state.page = 1; _loadProducts(container);
		});
		container.querySelector(".rq-filter-category").addEventListener("change", function () {
			_state.category = this.value; _state.page = 1; _loadProducts(container);
		});
		container.querySelector(".rq-filter-status").addEventListener("change", function () {
			_state.status = this.value; _state.page = 1; _loadProducts(container);
		});
		container.querySelector(".rq-finalize-btn").addEventListener("click", () =>
			_finalize(container)
		);
	}

	// ── Load ──────────────────────────────────────────────────────────────────
	function _loadProducts(container) {
		if (_state.loading) return;
		_state.loading = true;
		container.querySelector(".rq-grid").innerHTML = `<div class="rq-loading">Loading…</div>`;

		frappe.call({
			method: "alaiy_os_connector_competitor_sites.api.review_queue.get_review_queue",
			args: {
				site: _state.site, category: _state.category,
				status: _state.status, search: _state.search,
				page: _state.page, page_size: _state.pageSize,
			},
			callback: (r) => {
				_state.loading = false;
				const { products, summary } = r.message || {};
				_renderStats(container, summary);
				_renderGrid(container, products || []);
				_renderPagination(container, summary);
				_updateFinalizeBtn(container, summary);
			},
			error: () => {
				_state.loading = false;
				container.querySelector(".rq-grid").innerHTML = `<div class="rq-empty">Failed to load.</div>`;
			},
		});
	}

	// ── Stats ─────────────────────────────────────────────────────────────────
	function _renderStats(container, summary) {
		const s = summary || {};
		container.querySelector(".rq-stat-kept").textContent = `${s.Kept || 0} kept`;
		container.querySelector(".rq-stat-skipped").textContent = `${s.Skipped || 0} skipped`;
		container.querySelector(".rq-stat-remaining").textContent = `${s.Pending || 0} remaining`;
	}

	function _updateFinalizeBtn(container, summary) {
		const kept = (summary || {}).Kept || 0;
		const btn = container.querySelector(".rq-finalize-btn");
		btn.disabled = kept === 0;
		container.querySelector(".rq-finalize-count").textContent = kept;
	}

	// ── Grid ──────────────────────────────────────────────────────────────────
	function _renderGrid(container, products) {
		const grid = container.querySelector(".rq-grid");
		if (!products.length) {
			grid.innerHTML = `<div class="rq-empty">No products found.</div>`;
			return;
		}
		grid.innerHTML = products.map(_cardHtml).join("");
		_bindCards(container, products);
	}

	function _cardHtml(p) {
		const img = p.product_image_url
			? `<img class="rq-card-img" src="${p.product_image_url}" alt="${_esc(p.product_name)}">`
			: `<div class="rq-card-img rq-card-img-placeholder"></div>`;
		const cls = p.review_status === "Kept" ? "rq-card-kept"
			: p.review_status === "Skipped" ? "rq-card-skipped" : "";
		return `
<div class="rq-card ${cls}" data-name="${_esc(p.name)}">
  <div class="rq-card-img-wrap">
    ${img}
    <div class="rq-card-actions">
      <button class="rq-card-btn rq-card-btn-skip" title="Skip">✕ Skip</button>
      <button class="rq-card-btn rq-card-btn-keep" title="Keep">✓ Keep</button>
    </div>
  </div>
  <div class="rq-card-body">
    <div class="rq-card-name">${_esc(p.product_name || "—")}</div>
    <div class="rq-card-meta">
      <span class="rq-card-site">${_esc(p.source_site || "")}</span>
      <span class="rq-card-price">${p.source_price ? _esc(String(p.source_price)) : ""}</span>
    </div>
  </div>
</div>`;
	}

	// ── Card bindings ─────────────────────────────────────────────────────────
	function _bindCards(container, products) {
		const map = {};
		products.forEach((p) => { map[p.name] = p; });

		container.querySelectorAll(".rq-card").forEach((card) => {
			card.querySelector(".rq-card-btn-keep").addEventListener("click", (e) => {
				e.stopPropagation();
				_setStatus(container, card.dataset.name, "Kept");
			});
			card.querySelector(".rq-card-btn-skip").addEventListener("click", (e) => {
				e.stopPropagation();
				_setStatus(container, card.dataset.name, "Skipped");
			});
			card.addEventListener("click", () => {
				const p = map[card.dataset.name];
				if (p) _openModal(container, p);
			});
		});
	}

	// ── Set status ────────────────────────────────────────────────────────────
	function _setStatus(container, name, status) {
		frappe.call({
			method: "alaiy_os_connector_competitor_sites.api.review_queue.set_review_status",
			args: { name, status },
			callback: (r) => {
				const card = container.querySelector(`.rq-card[data-name="${name}"]`);
				if (card) {
					card.classList.remove("rq-card-kept", "rq-card-skipped");
					if (status === "Kept") card.classList.add("rq-card-kept");
					if (status === "Skipped") card.classList.add("rq-card-skipped");
					if (_state.status && _state.status !== status) card.remove();
				}
				_refreshStats(container);
			},
		});
	}

	// ── Detail modal ──────────────────────────────────────────────────────────
	function _openModal(container, p) {
		document.querySelector(".rq-modal-overlay")?.remove();

		const overlay = document.createElement("div");
		overlay.className = "rq-modal-overlay";
		overlay.innerHTML = `
<div class="rq-modal">
  <div class="rq-modal-header">
    <h2 class="rq-modal-title">${_esc(p.product_name || "—")}</h2>
    <button class="rq-modal-close">✕</button>
  </div>
  <div class="rq-modal-body">
    <div class="rq-modal-img-wrap">
      ${p.product_image_url
			? `<img class="rq-modal-img" src="${p.product_image_url}" alt="${_esc(p.product_name)}">`
			: `<div class="rq-modal-img-placeholder"></div>`}
    </div>
    <div class="rq-modal-detail">
      <div class="rq-modal-fields">
        <div class="rq-modal-field">
          <div class="rq-modal-field-label">SKU</div>
          <div class="rq-modal-field-value">${_esc(p.sku || "—")}</div>
        </div>
        <div class="rq-modal-field">
          <div class="rq-modal-field-label">Source</div>
          <div class="rq-modal-field-value">${_esc(p.source_site || "—")}</div>
        </div>
        <div class="rq-modal-field">
          <div class="rq-modal-field-label">Category</div>
          <div class="rq-modal-field-value">${_esc(p.categories || "—")}</div>
        </div>
        <div class="rq-modal-field">
          <div class="rq-modal-field-label">Price</div>
          <div class="rq-modal-field-value rq-modal-price">${p.source_price ? "$" + _esc(p.source_price) : "—"}</div>
        </div>
      </div>
      ${p.source_product_url
			? `<a class="rq-modal-source-link" href="${_esc(p.source_product_url)}" target="_blank">Open on ${_esc(p.source_site || "Source")} ↗</a>`
			: ""}
      <div class="rq-modal-notes-label">Notes</div>
      <textarea class="rq-modal-notes" placeholder="Add a note for the buyer or team...">${_esc(p.notes || "")}</textarea>
      <div class="rq-modal-footer">
        <button class="rq-modal-btn-skip">✕ Skip</button>
        <button class="rq-modal-btn-keep">✓ Keep</button>
      </div>
    </div>
  </div>
</div>`;

		document.body.appendChild(overlay);

		overlay.querySelector(".rq-modal-close").addEventListener("click", () => overlay.remove());
		overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });

		let notesTimer;
		overlay.querySelector(".rq-modal-notes").addEventListener("input", function () {
			clearTimeout(notesTimer);
			const val = this.value;
			notesTimer = setTimeout(() => {
				frappe.call({ method: "alaiy_os_connector_competitor_sites.api.review_queue.save_notes", args: { name: p.name, notes: val } });
			}, 800);
		});

		overlay.querySelector(".rq-modal-btn-skip").addEventListener("click", () => {
			_setStatus(container, p.name, "Skipped");
			overlay.remove();
		});
		overlay.querySelector(".rq-modal-btn-keep").addEventListener("click", () => {
			_setStatus(container, p.name, "Kept");
			overlay.remove();
		});
	}

	// ── Finalize all kept ─────────────────────────────────────────────────────
	function _finalize(container) {
		const today = frappe.datetime.get_today();
		const d = new frappe.ui.Dialog({
			title: __("Create Collection"),
			fields: [
				{
					fieldname: "collection_name",
					fieldtype: "Data",
					label: __("Collection Name"),
					reqd: 1,
					default: __("Drop — {0}", [frappe.datetime.str_to_user(today)]),
				},
			],
			primary_action_label: __("Finalize & Create"),
			primary_action(values) {
				d.hide();
				frappe.call({
					method: "alaiy_os_connector_competitor_sites.api.review_queue.finalize_selected",
					args: { collection_name: values.collection_name, names: JSON.stringify([]) },
					callback: (r) => {
						const { collection, count } = r.message || {};
						frappe.msgprint(__(
							"{0} product(s) pushed to ERPNext and grouped into collection <b>{1}</b>.",
							[count, collection]
						));
						_loadProducts(container);
					},
				});
			},
		});
		d.show();
	}

	// ── Stats refresh ─────────────────────────────────────────────────────────
	function _refreshStats(container) {
		frappe.call({
			method: "alaiy_os_connector_competitor_sites.api.review_queue.get_review_queue",
			args: { page_size: 1, page: 1 },
			callback: (r) => {
				const summary = (r.message || {}).summary;
				_renderStats(container, summary);
				_updateFinalizeBtn(container, summary);
			},
		});
	}

	// ── Pagination ────────────────────────────────────────────────────────────
	function _renderPagination(container, summary) {
		const statusKey = _state.status || "Pending";
		const total = (summary || {})[statusKey] || 0;
		const pages = Math.ceil(total / _state.pageSize);
		const pg = container.querySelector(".rq-pagination");
		if (pages <= 1) { pg.innerHTML = ""; return; }
		pg.innerHTML = `
<button class="rq-page-btn" ${_state.page <= 1 ? "disabled" : ""} data-dir="-1">← Prev</button>
<span class="rq-page-info">Page ${_state.page} of ${pages}</span>
<button class="rq-page-btn" ${_state.page >= pages ? "disabled" : ""} data-dir="1">Next →</button>`;
		pg.querySelectorAll(".rq-page-btn").forEach((btn) => {
			btn.addEventListener("click", () => {
				_state.page += parseInt(btn.dataset.dir);
				_loadProducts(container);
			});
		});
	}

	function _esc(str) {
		return (str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
	}

})();
