(function () {
	window._sb_sr_html = _srHtml;
	window._sb_sr_load = _loadSites;
	window._sb_sr_bind_events = _bindEvents;
	window._sb_sr_restore_state = _restoreState;

	const STORAGE_KEY = "sb_scrape_active_v2";
	const POLL_MS = 3000;

	// ── State (localStorage) ─────────────────────────────────────────────────

	function _saveState(log_names, startedAt) {
		// log_names: {site_name: log_doc_name}
		localStorage.setItem(STORAGE_KEY, JSON.stringify({ log_names, startedAt }));
	}

	function _clearState() {
		localStorage.removeItem(STORAGE_KEY);
		// also wipe the old v1 key so it doesn't interfere
		localStorage.removeItem("sb_scrape_active");
	}

	function _restoreState(root) {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return false;
		let state;
		try { state = JSON.parse(raw); } catch (e) { _clearState(); return false; }
		const { log_names, startedAt } = state;
		if (!log_names || !Object.keys(log_names).length) { _clearState(); return false; }

		const sites = Object.keys(log_names);
		_showProgress(root, sites);
		const btn = root.querySelector(".sb-sr-run-btn");
		btn.disabled = true;
		btn.textContent = "Running…";

		_pollProgress(root, btn, log_names, startedAt || Date.now());
		return true;
	}

	// ── HTML ──────────────────────────────────────────────────────────────────

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
        <option value="10">10</option>
        <option value="25">25</option>
        <option value="100" selected>100</option>
        <option value="250">250</option>
        <option value="500">500</option>
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

	// ── Site list ─────────────────────────────────────────────────────────────

	function _loadSites(root) {
		frappe.call({
			method: "alaiy_os_connector_competitor_sites.api.competitor_site.get_sites_with_stats",
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

	// ── Progress cards ────────────────────────────────────────────────────────

	function _showProgress(root, sites) {
		const panel = root.querySelector(".sb-sr-status-panel");
		panel.innerHTML = `
<div class="sb-sr-progress-header">Scraping ${sites.length} site${sites.length !== 1 ? "s" : ""}…</div>
<div class="sb-sr-site-cards">
  ${sites.map((s) => `
  <div class="sb-sr-site-card" data-site="${_esc(s)}">
    <div class="sb-sr-card-left">
      <div class="sb-sr-card-name">${_esc(s)}</div>
      <div class="sb-sr-card-sub">Queued…</div>
    </div>
    <div class="sb-sr-card-badge sb-sr-badge-pending">Queued</div>
  </div>`).join("")}
</div>`;
	}

	function _updateSiteCard(root, site, info) {
		const card = root.querySelector(`.sb-sr-site-card[data-site="${_esc(site)}"]`);
		if (!card) return;
		const sub = card.querySelector(".sb-sr-card-sub");
		const badge = card.querySelector(".sb-sr-card-badge");
		const { status, products_saved = 0, already_in_db = 0, urls_found = 0, log } = info;

		if (status === "Failed") {
			// Show the friendly error message directly on the card
			const errorText = log || "Unknown error";
			const isCredits = errorText.toLowerCase().includes("credits");
			sub.innerHTML = `<span class="sb-sr-card-error">${_esc(errorText)}</span>`;
			badge.textContent = isCredits ? "No Credits" : "Failed";
			badge.className = "sb-sr-card-badge sb-sr-badge-error";

		} else if (status === "Done") {
			if (already_in_db > 0 && products_saved === 0) {
				sub.textContent = `${already_in_db} already in DB — all up to date`;
			} else if (already_in_db > 0) {
				sub.textContent = `${products_saved} new · ${already_in_db} already saved`;
			} else {
				sub.textContent = `${products_saved} new product${products_saved !== 1 ? "s" : ""} saved`;
			}
			badge.textContent = "Done";
			badge.className = "sb-sr-card-badge sb-sr-badge-done";

		} else if (status === "Running") {
			if (!card._startedAt) card._startedAt = Date.now();
			const elapsed = Math.round((Date.now() - card._startedAt) / 1000);
			const timeStr = elapsed > 60 ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : `${elapsed}s`;
			sub.textContent = urls_found
				? `${urls_found} URLs found, ${products_saved} saved… (${timeStr})`
				: `Mapping site… (${timeStr})`;
			badge.textContent = "Running";
			badge.className = "sb-sr-card-badge sb-sr-badge-running";

		} else {
			// Queued
			sub.textContent = "Waiting in queue…";
			badge.textContent = "Queued";
			badge.className = "sb-sr-card-badge sb-sr-badge-pending";
		}
	}

	function _showComplete(root, log_names, results) {
		_clearState();
		const panel = root.querySelector(".sb-sr-status-panel");
		const site_cards = panel.querySelector(".sb-sr-site-cards")?.outerHTML || "";
		const sites = Object.keys(log_names);
		const total = sites.reduce((sum, s) => sum + (results[s]?.products_saved || 0), 0);
		const hasErrors = sites.some((s) => results[s]?.status === "Failed");

		panel.innerHTML = `
<div class="sb-sr-result">
  <div class="sb-sr-result-icon">${hasErrors ? "⚠️" : "✅"}</div>
  <div class="sb-sr-result-title">Scrape complete</div>
  <div class="sb-sr-result-summary">
    <strong>${total}</strong> new product${total !== 1 ? "s" : ""} found across <strong>${sites.length}</strong> site${sites.length !== 1 ? "s" : ""}
    ${hasErrors ? `<div class="sb-sr-result-warn">One or more sites had issues — see the cards below for details.</div>` : ""}
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
			method: "alaiy_os_connector_competitor_sites.api.scrape_runner.scrape_selected_sites",
			args: { sites: JSON.stringify(selected), product_limit: limit },
			callback: (r) => {
				const msg = r.message || {};
				const log_names = msg.log_names;
				if (!log_names || !Object.keys(log_names).length) {
					_showError(root, "Failed to start scrape. Please try again.");
					btn.disabled = false;
					btn.textContent = "⏵ Run Scrape";
					return;
				}
				const startedAt = Date.now();
				_saveState(log_names, startedAt);
				_pollProgress(root, btn, log_names, startedAt);
			},
			error: () => {
				_showError(root, "Could not connect. Please try again.");
				btn.disabled = false;
				btn.textContent = "⏵ Run Scrape";
			},
		});
	}

	function _pollProgress(root, btn, log_names, startedAt) {
		const myToken = (root._pollToken = (root._pollToken || 0) + 1);
		const alive = () => root._pollToken === myToken;

		const sites = Object.keys(log_names);
		const elapsed = Date.now() - (startedAt || Date.now());

		// Hard timeout: 10 min per site, minimum 10 min
		const timeoutMs = Math.max(600_000, sites.length * 600_000);
		if (elapsed > timeoutMs) {
			_clearState();
			const panel = root.querySelector(".sb-sr-status-panel");
			const site_cards = panel.querySelector(".sb-sr-site-cards")?.outerHTML || "";
			panel.innerHTML = `
<div class="sb-sr-result">
  <div class="sb-sr-result-icon">⏳</div>
  <div class="sb-sr-result-title">Still running in the background</div>
  <div class="sb-sr-result-summary">The scrape is taking a long time. Products are being saved as they arrive — check the Review Queue in a few minutes, or open Scrape Logs to see each site's status.</div>
  <button class="btn btn-primary sb-sr-review-btn">Check Review Queue →</button>
</div>
${site_cards}`;
			panel.querySelector(".sb-sr-review-btn").addEventListener("click", () => frappe.set_route("review-queue"));
			btn.disabled = false;
			btn.textContent = "⏵ Run Scrape";
			return;
		}

		frappe.call({
			method: "alaiy_os_connector_competitor_sites.api.scrape_runner.get_scrape_progress",
			args: { log_names: JSON.stringify(log_names) },
			callback: (r) => {
				if (!alive()) return;
				const results = r.message || {};

				sites.forEach((site) => {
					if (results[site]) _updateSiteCard(root, site, results[site]);
				});

				const allDone = sites.every((s) => {
					const st = results[s]?.status;
					return st === "Done" || st === "Failed";
				});

				if (allDone) {
					_showComplete(root, log_names, results);
					btn.disabled = false;
					btn.textContent = "⏵ Run Scrape";
				} else {
					setTimeout(() => _pollProgress(root, btn, log_names, startedAt), POLL_MS);
				}
			},
			error: () => {
				if (alive()) setTimeout(() => _pollProgress(root, btn, log_names, startedAt), 5000);
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
