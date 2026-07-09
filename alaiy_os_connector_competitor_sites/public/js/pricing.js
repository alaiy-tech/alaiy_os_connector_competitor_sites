(function () {
	window.pricing_init = function (container) {
		container.innerHTML = _html();
		_loadCollections(container);
	};

	function _html() {
		return `
<div class="pr-wrap">
  <div class="pr-sidebar">
    <div class="pr-sidebar-title">Collections</div>
    <div class="pr-collection-list"></div>
  </div>
  <div class="pr-main">
    <div class="pr-empty-state">Select a collection to view products</div>
  </div>
</div>`;
	}

	function _loadCollections(container) {
		frappe.call({
			method: "alaiy_os_connector_competitor_sites.api.pricing.get_collections",
			callback: (r) => {
				const list = container.querySelector(".pr-collection-list");
				const collections = r.message || [];
				if (!collections.length) {
					list.innerHTML = `<div class="pr-no-collections">No collections yet.</div>`;
					return;
				}
				list.innerHTML = collections.map(_collectionItemHtml).join("");
				list.querySelectorAll(".pr-collection-item").forEach((el) => {
					el.addEventListener("click", () => {
						list.querySelectorAll(".pr-collection-item").forEach((e) =>
							e.classList.remove("active"),
						);
						el.classList.add("active");
						_loadCollection(container, el.dataset.name, el.dataset.label);
					});
				});
				// auto-select first
				list.querySelector(".pr-collection-item")?.click();
			},
		});
	}

	function _collectionItemHtml(c) {
		const date = c.finalized_on ? frappe.datetime.str_to_user(c.finalized_on) : "—";
		return `
<div class="pr-collection-item" data-name="${_esc(c.name)}" data-label="${_esc(c.collection_name)}">
  <div class="pr-collection-name">${_esc(c.collection_name)}</div>
  <div class="pr-collection-meta">${date} · ${c.product_count || 0} products</div>
</div>`;
	}

	function _loadCollection(container, name, label) {
		const main = container.querySelector(".pr-main");
		main.innerHTML = `<div class="pr-loading">Loading…</div>`;

		frappe.call({
			method: "alaiy_os_connector_competitor_sites.api.pricing.get_collection_items",
			args: { collection_name: name },
			callback: (r) => {
				const items = r.message || [];
				main.innerHTML = _mainHtml(label, name, items.length);
				_renderTable(main, items);
				main.querySelector(".pr-btn-excel").addEventListener("click", () =>
					_downloadExcel(name, label),
				);
			},
		});
	}

	function _mainHtml(label, name, count) {
		return `
<div class="pr-main-header">
  <div>
    <div class="pr-main-title">${_esc(label)}</div>
    <div class="pr-main-count"><span class="pr-count-num"></span> products</div>
  </div>
  <div class="pr-main-actions">
    <button class="btn btn-primary pr-btn-excel">⬇ Generate Factory Excel</button>
  </div>
</div>
<div class="pr-table-wrap">
  <table class="pr-table">
    <thead>
      <tr>
        <th class="pr-th-img">Image</th>
        <th>Style Number</th>
        <th>Product Name</th>
        <th>Price (USD)</th>
        <th>Source</th>
      </tr>
    </thead>
    <tbody class="pr-tbody"></tbody>
  </table>
</div>`;
	}

	function _renderTable(main, items) {
		main.querySelector(".pr-count-num").textContent = items.length;
		const tbody = main.querySelector(".pr-tbody");
		if (!items.length) {
			tbody.innerHTML = `<tr><td colspan="5" class="pr-td-empty">No products in this collection.</td></tr>`;
			return;
		}
		tbody.innerHTML = items
			.map((item) => {
				const img = item.image
					? `<img class="pr-row-img" src="${_esc(item.image)}" loading="lazy">`
					: `<div class="pr-row-img-placeholder"></div>`;
				const price = item.price ? `$${parseFloat(item.price).toFixed(2)}` : "—";
				return `
<tr class="pr-row">
  <td class="pr-td-img">${img}</td>
  <td class="pr-td-code">${_esc(item.item || "—")}</td>
  <td class="pr-td-name">${_esc(item.item_name || "—")}</td>
  <td class="pr-td-price">${price}</td>
  <td class="pr-td-source">${_esc(item.source_site || "—")}</td>
</tr>`;
			})
			.join("");
	}

	function _downloadExcel(name, label) {
		const btn = document.querySelector(".pr-btn-excel");
		if (btn) {
			btn.disabled = true;
			btn.textContent = "Generating…";
		}

		frappe.call({
			method: "alaiy_os_connector_competitor_sites.api.pricing.generate_factory_excel",
			args: { collection_name: name },
			callback: (r) => {
				if (btn) {
					btn.disabled = false;
					btn.textContent = "⬇ Generate Factory Excel";
				}
				const { filename, data } = r.message || {};
				if (!data) {
					frappe.msgprint("Failed to generate Excel.");
					return;
				}
				const binary = atob(data);
				const bytes = new Uint8Array(binary.length);
				for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
				const blob = new Blob([bytes], {
					type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
				});
				const url = URL.createObjectURL(blob);
				const a = document.createElement("a");
				a.href = url;
				a.download = filename || "factory.xlsx";
				document.body.appendChild(a);
				a.click();
				document.body.removeChild(a);
				URL.revokeObjectURL(url);
			},
			error: () => {
				if (btn) {
					btn.disabled = false;
					btn.textContent = "⬇ Generate Factory Excel";
				}
				frappe.msgprint("Error generating Excel.");
			},
		});
	}

	function _esc(str) {
		return (str || "")
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;");
	}
})();
