(function () {
	window._sb_sr_html = _srHtml;
	window._sb_sr_load = _loadSites;
	window._sb_sr_bind_events = _bindEvents;

	function _srHtml() {
		return `
<div class="sb-sr">
  <div class="sb-sr-left">
    <div class="sb-sr-left-header">
      <div class="sb-sr-title">Scrape Runner</div>
      <div class="sb-sr-subtitle">Select sites and pull the latest products.</div>
    </div>
    <div class="sb-sr-filter-wrap">
      <input class="sb-sr-filter" placeholder="Filter sites" type="text">
    </div>
    <div class="sb-sr-site-list"></div>
    <div class="sb-sr-footer">
      <span class="sb-sr-count">0 selected</span>
      <a class="sb-sr-select-all" href="#">Select all</a>
    </div>
    <div class="sb-sr-limit-wrap">
      <label class="sb-sr-limit-label">Products to fetch</label>
      <select class="sb-sr-limit-select">
        <option value="100">100</option>
        <option value="250">250</option>
        <option value="500" selected>500</option>
        <option value="1000">1,000</option>
        <option value="0">All</option>
      </select>
    </div>
    <button class="btn sb-sr-run-btn" disabled>
      ⏵ Run Scrape
    </button>
  </div>
  <div class="sb-sr-right">
    <div class="sb-sr-status-panel">
      <div class="sb-sr-idle">
        <div class="sb-sr-idle-icon">🔍</div>
        <div class="sb-sr-idle-text">Select sites on the left and hit Run Scrape.</div>
      </div>
    </div>
  </div>
</div>`;
	}

	function _loadSites(root) {
		frappe.call({
			method: "competitor_sites_connector.api.competitor_site.get_sites_with_stats",
			callback: (r) => {
				const list = root.querySelector(".sb-sr-site-list");
				const sites = (r.message || []).filter((s) => parseInt(s.is_active));
				list.innerHTML = sites.map(_siteItemHtml).join("");
				_bindSiteChecks(root);
			},
		});
	}

	function _siteItemHtml(s) {
		const cats = (s.categories || "")
			.split(",").map((c) => c.trim()).filter(Boolean).join(" · ");
		const lastScraped = s.last_scraped
			? `Last scraped ${frappe.datetime.prettyDate(s.last_scraped)}`
			: "Never scraped";
		return `
<label class="sb-sr-site-item" data-name="${_esc(s.name)}">
  <span class="sb-sr-checkbox"></span>
  <span class="sb-sr-site-info">
    <span class="sb-sr-site-name">${_esc(s.name)}</span>
    <span class="sb-sr-site-cats">${_esc(lastScraped)}${cats ? " · " + cats : ""}</span>
  </span>
  <input type="checkbox" class="sb-sr-check-input" style="display:none">
</label>`;
	}

	function _bindSiteChecks(root) {
		root.querySelectorAll(".sb-sr-site-item").forEach((item) => {
			item.addEventListener("click", (e) => {
				e.preventDefault();
				const inp = item.querySelector(".sb-sr-check-input");
				inp.checked = !inp.checked;
				item.classList.toggle("sb-sr-checked", inp.checked);
				_updateCount(root);
			});
		});
	}

	function _bindEvents(root) {
		root.querySelector(".sb-sr-filter").addEventListener("input", function () {
			const q = this.value.toLowerCase();
			root.querySelectorAll(".sb-sr-site-item").forEach((item) => {
				item.style.display = item.dataset.name.toLowerCase().includes(q) ? "" : "none";
			});
		});

		root.querySelector(".sb-sr-select-all").addEventListener("click", (e) => {
			e.preventDefault();
			const all = root.querySelectorAll(".sb-sr-site-item:not([style*='none'])");
			const anyUnchecked = [...all].some((i) => !i.querySelector(".sb-sr-check-input").checked);
			all.forEach((item) => {
				item.querySelector(".sb-sr-check-input").checked = anyUnchecked;
				item.classList.toggle("sb-sr-checked", anyUnchecked);
			});
			_updateCount(root);
		});

		root.querySelector(".sb-sr-run-btn").addEventListener("click", () => _runScrape(root));
	}

	function _updateCount(root) {
		const n = root.querySelectorAll(".sb-sr-check-input:checked").length;
		root.querySelector(".sb-sr-count").textContent = n === 1 ? "1 selected" : `${n} selected`;
		root.querySelector(".sb-sr-run-btn").disabled = n === 0;
	}

	// ── Progress panel ────────────────────────────────────────────────────────

	function _showProgress(root, sites) {
		const panel = root.querySelector(".sb-sr-status-panel");
		panel.innerHTML = `
<div class="sb-sr-progress-header">Scraping ${sites.length} site${sites.length !== 1 ? "s" : ""}…</div>
<div class="sb-sr-site-cards">
  ${sites.map((s) => `
  <div class="sb-sr-site-card" data-site="${_esc(s)}">
    <div class="sb-sr-card-left">
      <div class="sb-sr-card-name">${_esc(s)}</div>
      <div class="sb-sr-card-sub">Starting…</div>
    </div>
    <div class="sb-sr-card-badge sb-sr-badge-pending">Pending</div>
  </div>`).join("")}
</div>`;
	}

	function _updateSiteCard(root, site, count, done, error) {
		const card = root.querySelector(`.sb-sr-site-card[data-site="${_esc(site)}"]`);
		if (!card) return;
		const sub = card.querySelector(".sb-sr-card-sub");
		const badge = card.querySelector(".sb-sr-card-badge");
		if (error) {
			sub.textContent = "Could not reach site";
			badge.textContent = "Issue";
			badge.className = "sb-sr-card-badge sb-sr-badge-error";
		} else if (done) {
			sub.textContent = `${count} product${count !== 1 ? "s" : ""} found`;
			badge.textContent = "Done";
			badge.className = "sb-sr-card-badge sb-sr-badge-done";
		} else {
			sub.textContent = `${count} products so far…`;
			badge.textContent = "Running";
			badge.className = "sb-sr-card-badge sb-sr-badge-running";
		}
	}

	function _showComplete(root, total, siteCount, hasErrors) {
		const panel = root.querySelector(".sb-sr-status-panel");
		const site_cards = panel.querySelector(".sb-sr-site-cards")?.outerHTML || "";
		panel.innerHTML = `
<div class="sb-sr-result">
  <div class="sb-sr-result-icon">${hasErrors ? "⚠️" : "✅"}</div>
  <div class="sb-sr-result-title">Scrape complete</div>
  <div class="sb-sr-result-summary">
    <strong>${total}</strong> new product${total !== 1 ? "s" : ""} found across <strong>${siteCount}</strong> site${siteCount !== 1 ? "s" : ""}
    ${hasErrors ? `<div class="sb-sr-result-warn">One or more sites had issues. Products from other sites were saved.</div>` : ""}
  </div>
  ${total > 0 ? `<button class="btn btn-primary sb-sr-review-btn">Review Products →</button>` : ""}
</div>
${site_cards}`;
		if (total > 0) {
			panel.querySelector(".sb-sr-review-btn").addEventListener("click", () => {
				frappe.set_route("review-queue");
			});
		}
	}

	// ── Run ───────────────────────────────────────────────────────────────────

	function _runScrape(root) {
		const selected = [...root.querySelectorAll(".sb-sr-check-input:checked")]
			.map((i) => i.closest(".sb-sr-site-item").dataset.name);
		const limit = parseInt(root.querySelector(".sb-sr-limit-select").value) || 0;

		const btn = root.querySelector(".sb-sr-run-btn");
		btn.disabled = true;
		btn.textContent = "Running…";

		_showProgress(root, selected);

		frappe.call({
			method: "competitor_sites_connector.api.scrape_runner.scrape_selected_sites",
			args: { sites: JSON.stringify(selected), product_limit: limit },
			callback: (r) => {
				const scrape_id = r.message && r.message.scrape_id;
				if (!scrape_id) {
					_showError(root, "Failed to start scrape. Please try again.");
					btn.disabled = false;
					btn.textContent = "⏵ Run Scrape";
					return;
				}
				_pollProgress(root, btn, scrape_id, selected, {}, Date.now());
			},
			error: () => {
				_showError(root, "Could not connect. Please try again.");
				btn.disabled = false;
				btn.textContent = "⏵ Run Scrape";
			},
		});
	}

	function _pollProgress(root, btn, scrape_id, sites, seen, startedAt) {
		const start = startedAt || Date.now();
		const elapsed = Date.now() - start;

		// Hard timeout — if backend hasn't confirmed done in 90s, stop polling
		if (elapsed > 90000) {
			const siteCards = root.querySelector(".sb-sr-site-cards");
			const total = Object.values(seen).reduce((s, n) => s + n, 0);
			_showComplete(root, total, sites.length, false);
			btn.disabled = false;
			btn.textContent = "⏵ Run Scrape";
			return;
		}

		frappe.call({
			method: "competitor_sites_connector.api.scrape_runner.get_scrape_progress",
			args: { scrape_id },
			callback: (r) => {
				const data = r.message || {};
				const siteRows = data.sites || [];
				const doneSites = data.done_sites || {};
				const errors = data.errors || [];

				// Build a count map from DB rows
				const countMap = {};
				siteRows.forEach((row) => {
					countMap[row.source_site] = row.count;
					seen[row.source_site] = row.count;
				});

				// Update cards for all sites we know are done
				sites.forEach((site) => {
					if (doneSites[site]) {
						const count = countMap[site] || 0;
						_updateSiteCard(root, site, count, true, !!doneSites[site].error);
					} else if (countMap[site]) {
						_updateSiteCard(root, site, countMap[site], false, false);
					}
				});

				const allDone = sites.every((s) => doneSites[s]);
				if (!allDone) {
					setTimeout(() => _pollProgress(root, btn, scrape_id, sites, seen, start), 3000);
				} else {
					const total = siteRows.reduce((s, r) => s + r.count, 0);
					const hasErrors = sites.some((s) => doneSites[s] && doneSites[s].error);
					_showComplete(root, total, sites.length, hasErrors);
					btn.disabled = false;
					btn.textContent = "⏵ Run Scrape";
				}
			},
			error: () => {
				setTimeout(() => _pollProgress(root, btn, scrape_id, sites, seen, start), 5000);
			},
		});
	}

	function _showError(root, msg) {
		const panel = root.querySelector(".sb-sr-status-panel");
		panel.innerHTML = `
<div class="sb-sr-idle">
  <div class="sb-sr-idle-icon">⚠️</div>
  <div class="sb-sr-idle-text">${_esc(msg)}</div>
</div>`;
	}

	function _esc(str) {
		return (str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
	}
})();
