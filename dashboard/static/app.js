/* Supply Chain Atlas — live API front-end.
   Bootstraps the graph substrate from /api/bootstrap once, then polls /api/live
   every 30 s. Vanilla JS, no dependencies; rendering follows DESIGN_PATTERNS.md
   (color = entity type or state; dashed edges = disclosed supply relationships;
   emphasized dashed edges = supply edge changed recently). */
(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const root = document.documentElement;
  const SVGNS = "http://www.w3.org/2000/svg";
  const el = (t, a) => { const e = document.createElementNS(SVGNS, t); for (const k in a) e.setAttribute(k, a[k]); return e; };
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
    ch => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[ch]));
  const cssv = n => getComputedStyle(root).getPropertyValue(n).trim();
  const fill = t => cssv(`--n-${t}-fill`), stroke = t => cssv(`--n-${t}-stroke`);
  const shorten = (s, n) => s && s.length > n ? s.slice(0, n - 1) + "…" : (s || "");

  const roleType = { supplier:"supply", customer:"company", owner:"owner" };
  const MODLABEL = { A_semiconductors:"Semiconductors", B_datacenter_power:"Datacenter / power",
    C_ev_battery:"EV / battery", D_defense_space:"Defense / space", E_bioprocessing:"Bioprocessing", owner:"Owners" };
  const typeName = { company:"company", owner:"owner", insider:"insider", subsidiary:"subsidiary", supply:"supplier" };
  const SEVCLS = { high:"s-danger", watch:"s-warning", info:"s-info" };
  const CHGKIND = {
    material_8k:        { label:"material 8-K",    cls:"b-company" },
    supply_edge_new:    { label:"new supply edge", cls:"b-supply" },
    supply_edge_changed:{ label:"supply changed",  cls:"b-supply" },
    stake_change:       { label:"stake change",    cls:"b-owner" },
    insider_cluster:    { label:"insider cluster", cls:"b-insider" },
  };
  const HORIZONS = ["3d", "1w", "1m", "3m"];
  const POLL_MS = 30000;

  /* ---- state ---- */
  let BOOT = null, LIVE = null;
  let tickerToCik = {}, groups = {};
  let mode = "universe", searchQuery = "", focusCik = null, selected = null;
  let view = "dashboard", failCount = 0, lastOkAt = null;
  const svg = byId("graph"), tip = byId("tip"), W = 760, H = 470;

  /* ---- theme ---- */
  const tBtn = byId("themeBtn"), tIcon = byId("themeIcon");
  const sysDark = () => matchMedia("(prefers-color-scheme: dark)").matches;
  let theme = (() => { try { return localStorage.getItem("sca-theme"); } catch (e) { return null; } })()
    || (sysDark() ? "dark" : "light");
  const applyTheme = t => { root.setAttribute("data-theme", t); tIcon.textContent = t === "dark" ? "☾" : "☀"; draw(); };
  applyTheme(theme);
  tBtn.addEventListener("click", () => {
    theme = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    try { localStorage.setItem("sca-theme", theme); } catch (e) {}
    applyTheme(theme);
  });

  /* ---- live status indicator ---- */
  const dot = byId("liveDot"), statusTxt = byId("statusTxt"), asOfEl = byId("asOf");
  function setStatus(state, txt, asOf) {
    dot.className = "dot" + (state ? " " + state : "");
    statusTxt.textContent = txt;
    asOfEl.textContent = asOf ? `as of ${asOf}` : "";
  }

  /* ---- change-flag helpers (bootstrap.changes) ---- */
  const changeFor = cik => BOOT && BOOT.changes ? BOOT.changes[cik] : null;
  const supplyChanged = cik => {
    const ch = changeFor(cik);
    return !!(ch && ch.types.some(t => t === "supply_edge_new" || t === "supply_edge_changed"));
  };
  const changeTip = ch => `Δ ${ch.types.map(t => (CHGKIND[t] || { label:t }).label).join(", ")} · latest ${ch.latest}`;

  /* ---- data loading ---- */
  async function loadBootstrap() {
    try {
      const r = await fetch("/api/bootstrap");
      if (!r.ok) throw new Error(`bootstrap HTTP ${r.status}`);
      BOOT = await r.json();
    } catch (e) {
      setStatus("stale", "couldn't load graph data · retrying");
      drawMessage("Couldn't reach the API. Retrying in 15s.");
      setTimeout(loadBootstrap, 15000);
      return;
    }
    // Non-traded filers (e.g. 13F owners like Vanguard / FMR) have no ticker;
    // derive a short display label so sorting/search/labels stay total.
    for (const cik in BOOT.companies) {
      const c = BOOT.companies[cik];
      if (!c.t) c.t = (c.n || cik).split(/\s+/)[0].toUpperCase().slice(0, 8);
    }
    tickerToCik = {};
    for (const cik in BOOT.companies) tickerToCik[BOOT.companies[cik].t] = cik;
    groups = {};
    for (const cik in BOOT.companies) { const m = BOOT.companies[cik].m; (groups[m] = groups[m] || []).push(cik); }
    for (const m in groups) groups[m].sort((a, b) => BOOT.companies[a].t.localeCompare(BOOT.companies[b].t));
    focusCik = tickerToCik["UCTT"] || tickerToCik["TSLA"] || Object.keys(BOOT.companies)[0];
    byId("nComp").textContent = Object.keys(BOOT.companies).length;
    setMode();
    if (view === "directory") renderDirectory();
  }

  async function fetchLive() {
    let ok = false;
    try {
      const r = await fetch("/api/live");
      if (!r.ok) throw new Error(`live HTTP ${r.status}`);
      LIVE = await r.json();
      ok = true;
    } catch (e) { /* handled below */ }
    if (ok) {
      failCount = 0; lastOkAt = LIVE.asOf;
      setStatus("live", "feed live", LIVE.asOf);
      renderLive();
    } else {
      failCount += 1;
      if (failCount === 1) setStatus("warn", "poll failed · retrying", lastOkAt);
      else setStatus("stale", `offline · ${failCount} polls failed`, lastOkAt);
    }
  }

  function renderLive() {
    renderPulse(); renderMarket(); renderAlerts(); renderStakes();
    renderFeed(); renderOutlook();
    if (view === "changes") renderChanges();
  }

  /* ---- pulse strip (all values from /api/live) ---- */
  function renderPulse() {
    const P = LIVE.pulse, cells = [];
    const cell = (k, v, extra) =>
      cells.push(`<div class="cell"><span class="eyebrow">${k}</span><span class="val">${v}</span>${extra || ""}</div>`);
    cell("Model", esc(P.model));
    cell("8-K classifier F1", P.f1 != null ? P.f1.toFixed(2) : "–");
    cell("ROC-AUC", P.auc != null ? P.auc.toFixed(2) : "–");
    const psiCls = P.psi == null ? "" : P.psi < 0.2 ? "healthy" : P.psi < 0.3 ? "warning" : "danger";
    cell("Drift (PSI)", (P.psi != null ? P.psi.toFixed(2) : "–")
      + (psiCls ? ` <span class="swatch ${psiCls}"></span>` : ""));
    const hr = HORIZONS.map(h => P.hitRates && P.hitRates[h] != null
      ? `<span><em>${h}</em>${P.hitRates[h].toFixed(2)}</span>` : "").join("");
    cells.push(`<div class="cell"><span class="eyebrow">Hit rate · horizon</span><span class="val hr">${hr || "–"}</span></div>`);
    cell("Filings ingested", (P.filings || 0).toLocaleString());
    cell("Change events", (P.events || 0).toLocaleString());
    cell("Alerts", (P.alerts || 0).toLocaleString());
    cell("Predictions", (P.predictions || 0).toLocaleString(),
      `<span class="sub">${(P.actuals || 0).toLocaleString()} with actuals</span>`);
    byId("pulse").innerHTML = cells.join("");
    byId("nEdges").textContent = (P.edges || 0).toLocaleString();
  }

  /* ---- live market context (trade windows) ---- */
  function renderMarket() {
    const m = byId("mkt"), w = LIVE.windows || [];
    if (!w.length) { m.hidden = true; m.innerHTML = ""; return; }
    m.hidden = false;
    m.innerHTML = `<span class="eyebrow">Live market · vwap</span>` + w.map(([sym, t, vwap]) =>
      `<span class="mk"><span class="sym">${esc(sym)}</span>` +
      `<span class="vw">${vwap != null ? vwap.toFixed(2) : "–"}</span>` +
      `<span class="at">${esc(t)}</span></span>`).join("");
  }

  /* ---- alerts rail (real alerts, severity + source per DESIGN_PATTERNS) ---- */
  function renderAlerts() {
    const all = LIVE.alerts || [], show = all.slice(0, 8);
    byId("alertMeta").textContent = all.length ? `${show.length} of ${all.length} recent` : "live";
    if (!all.length) {
      byId("alerts").innerHTML = `<div class="empty">No alerts yet — detectors run on the batch tick.</div>`;
      return;
    }
    byId("alerts").innerHTML = show.map(a => {
      const cls = SEVCLS[a.severity] || "s-info";
      return `<div class="alert ${cls}">`
        + `<div class="a-top"><span class="a-title" title="${esc(a.title)}">${esc(a.title)}</span>`
        + `<span class="a-src src-${esc(a.source)}">${esc(a.source)}</span>`
        + `<span class="a-tag">${esc(a.severity)}</span></div>`
        + `<div class="a-detail"><b>${esc(a.at)}</b> · ${esc(a.who)} · ${esc(a.rule.replace(/_/g, " "))}</div>`
        + `</div>`;
    }).join("");
  }

  function renderStakes() {
    byId("stakes").innerHTML = (LIVE.topStakes || []).map(([tk, pct, own]) =>
      `<tr data-tk="${esc(tk)}"><td class="tk">${esc(tk)}</td><td class="own">${esc(own)}</td>` +
      `<td class="pct">${pct.toFixed(1)}%</td></tr>`).join("");
    byId("stakes").querySelectorAll("tr").forEach(tr => tr.addEventListener("click", () => {
      const cik = tickerToCik[tr.dataset.tk]; if (cik) { setView("dashboard"); focusCompany(cik); }
    }));
  }

  /* ---- filing feed ---- */
  const codeCls = cd => cd.startsWith("4") || cd.startsWith("3") || cd.startsWith("5") ? "insider"
    : cd.startsWith("8") || cd.startsWith("10") ? "company" : "owner";
  function renderFeed() {
    const company = BOOT && mode === "company" && BOOT.cfilings[focusCik];
    byId("feedScope").innerHTML = company
      ? `${esc(BOOT.companies[focusCik].t)} · <a id="feedReset">← all</a>`
      : "universe · daily index";
    const rows = company
      ? BOOT.cfilings[focusCik].map(([d, cd]) => [d, BOOT.companies[focusCik].t, cd, ""])
      : (LIVE ? LIVE.feed : []);
    byId("feed").innerHTML = rows.map(([d, tk, cd, nm]) =>
      `<div class="feed-row"><span class="t">${esc(d)}</span><span class="tk">${esc(tk)}</span>`
      + `<span class="cd c-${codeCls(cd)}">${esc(cd)}</span><span class="tag">${esc((nm || "").trim())}</span></div>`).join("");
    const rst = byId("feedReset");
    if (rst) rst.addEventListener("click", e => { e.preventDefault(); mode = "universe"; selected = null; setMode(); });
  }

  /* ---- outlook chips (company view; per-horizon P(up) from live.outlook) ---- */
  function renderOutlook() {
    const row = byId("outlookRow");
    if (!BOOT || !LIVE || mode !== "company") { row.hidden = true; row.innerHTML = ""; return; }
    const tk = BOOT.companies[focusCik].t, o = (LIVE.outlook || {})[tk];
    if (!o) { row.hidden = true; row.innerHTML = ""; return; }
    row.hidden = false;
    row.innerHTML = `<span class="eyebrow">Model outlook · P(up)</span>`
      + HORIZONS.filter(h => o[h] != null).map(h => {
        const p = o[h], cls = p >= 0.55 ? "up" : p <= 0.45 ? "down" : "";
        return `<span class="chip ${cls}" title="P(price up) over ${h} after the latest event">${h}<b>${p.toFixed(2)}</b></span>`;
      }).join("")
      + `<span class="chips-note">reaction model estimate after the latest filing event — not investment advice</span>`;
  }

  /* ---- search-as-you-type ---- */
  const search = byId("search"), results = byId("results");
  let matches = [], active = -1;
  function closeResults() { results.hidden = true; results.innerHTML = ""; active = -1; }
  function nodeMatches(cik) { const c = BOOT.companies[cik];
    return c && (c.t.toLowerCase().includes(searchQuery) || c.n.toLowerCase().includes(searchQuery)); }
  function renderResults() {
    if (!matches.length) { closeResults(); return; }
    results.hidden = false;
    results.innerHTML = matches.map((cik, i) =>
      `<div class="res${i === active ? ' active' : ''}" data-cik="${cik}"><b>${esc(BOOT.companies[cik].t)}</b>${esc(BOOT.companies[cik].n)}</div>`).join("");
    results.querySelectorAll(".res").forEach(r =>
      r.addEventListener("mousedown", e => { e.preventDefault(); focusCompany(r.dataset.cik); }));
  }
  function doSearch() {
    if (!BOOT) return;
    searchQuery = search.value.trim().toLowerCase();
    matches = searchQuery ? Object.keys(BOOT.companies).filter(nodeMatches)
      .sort((a, b) => BOOT.companies[a].t.localeCompare(BOOT.companies[b].t)).slice(0, 8) : [];
    renderResults();
    if (mode === "universe") draw();   // live-dim non-matching nodes
  }
  search.addEventListener("input", () => { active = -1; doSearch(); });
  search.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, matches.length - 1); renderResults(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); renderResults(); }
    else if (e.key === "Enter") { e.preventDefault(); const cik = matches[active >= 0 ? active : 0]; if (cik) focusCompany(cik); }
    else if (e.key === "Escape") { search.value = ""; searchQuery = ""; closeResults(); if (mode === "universe") draw(); }
  });
  search.addEventListener("blur", () => setTimeout(closeResults, 150));

  /* ---- graph mode ---- */
  const btnU = byId("btnUniverse"), btnC = byId("btnCompany");
  btnU.addEventListener("click", () => { mode = "universe"; selected = null; setMode(); });
  btnC.addEventListener("click", () => { if (focusCik) focusCompany(focusCik); });
  function focusCompany(cik) {
    mode = "company"; focusCik = cik; searchQuery = "";
    search.value = BOOT.companies[cik].t; closeResults(); setMode();
  }
  function setMode() {
    btnU.setAttribute("aria-pressed", mode === "universe");
    btnC.setAttribute("aria-pressed", mode === "company");
    selected = mode === "company" ? focusCik : null;
    draw();
    renderFeed();
    renderOutlook();
  }

  /* ---- view switch: dashboard | directory | changes ---- */
  const viewDash = byId("viewDash"), viewDir = byId("viewDir"), viewChg = byId("viewChg");
  function setView(v) {
    view = v;
    viewDash.setAttribute("aria-pressed", v === "dashboard");
    viewDir.setAttribute("aria-pressed", v === "directory");
    viewChg.setAttribute("aria-pressed", v === "changes");
    byId("gridMain").hidden = v !== "dashboard";
    byId("directory").hidden = v !== "directory";
    byId("changes").hidden = v !== "changes";
    if (v === "directory") renderDirectory();
    if (v === "changes") renderChanges();
  }
  viewDash.addEventListener("click", () => setView("dashboard"));
  viewDir.addEventListener("click", () => setView("directory"));
  viewChg.addEventListener("click", () => setView("changes"));

  /* ---- directory ---- */
  function renderDirectory() {
    if (!BOOT) { byId("directory").innerHTML = `<div class="footnote">Loading the universe…</div>`; return; }
    const order = ["A_semiconductors", "B_datacenter_power", "C_ev_battery", "D_defense_space", "E_bioprocessing", "owner"];
    let html = "";
    for (const m of order) {
      if (!groups[m]) continue;
      html += `<div class="dir-group"><span class="eyebrow">${MODLABEL[m]} · ${groups[m].length}</span><div class="dir-cards">`;
      for (const cik of groups[m]) {
        const c = BOOT.companies[cik], s = BOOT.stats[cik] || {}, owner = (BOOT.stakes[cik] || [])[0];
        const ch = changeFor(cik);
        html += `<div class="dir-card" data-cik="${cik}" tabindex="0" role="button" aria-label="${esc(c.t)} — ${esc(c.n)}">`
          + `<div class="dc-top"><span class="tk">${esc(c.t)}</span>`
          + (ch ? `<span class="badge b-change" title="${esc(changeTip(ch))}">Δ ${ch.latest.slice(5)}</span>` : ``)
          + `<span class="badge b-${roleType[c.r]}">${esc(c.r)}</span></div>`
          + `<div class="name">${esc(c.n)}</div>`
          + `<div class="dc-stats">${s.fil || 0} filings · ${s.ow || 0} own · ${s.ins || 0} ins · ${s.sub || 0} sub</div>`
          + (owner ? `<div class="dc-owner">top owner: ${esc(shorten(owner[0], 20))} ${owner[1].toFixed(1)}%</div>` : ``)
          + `</div>`;
      }
      html += `</div></div>`;
    }
    const dir = byId("directory"); dir.innerHTML = html;
    dir.querySelectorAll(".dir-card").forEach(card => {
      const go = () => { setView("dashboard"); focusCompany(card.dataset.cik); };
      card.addEventListener("click", go);
      card.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });
  }

  /* ---- changes view ---- */
  const fmtPct = v => v == null ? "undisclosed %" : `${(+v).toFixed(1)}%`;
  const fmtMoney = v => {
    if (v == null) return "";
    const a = Math.abs(v);
    return a >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : a >= 1e6 ? `$${(v / 1e6).toFixed(1)}M`
      : a >= 1e3 ? `$${(v / 1e3).toFixed(0)}K` : `$${v.toFixed(0)}`;
  };
  function chgSummary(c) {
    const d = c.details || {};
    switch (c.type) {
      case "material_8k":
        return `Items <b>${esc((d.item_codes || []).join(", ") || "?")}</b>`
          + (d.clf_proba != null ? ` · P(material) <b>${(+d.clf_proba).toFixed(2)}</b>` : " · not yet scored");
      case "supply_edge_new":
        return `First disclosure of customer <b>${esc(d.customer_name || "?")}</b>`
          + (d.pct_of_revenue != null ? ` at <b>${fmtPct(d.pct_of_revenue)}</b> of revenue` : "");
      case "supply_edge_changed": {
        const delta = d.delta_pp;
        return `Concentration on <b>${esc(d.customer_name || "?")}</b> moved `
          + `<b>${fmtPct(d.prev_pct)} → ${fmtPct(d.pct_of_revenue)}</b>`
          + (delta != null ? ` (${delta > 0 ? "+" : ""}${(+delta).toFixed(1)}pp)` : "");
      }
      case "stake_change": {
        const move = d.prev_percent != null
          ? `<b>${fmtPct(d.prev_percent)} → ${fmtPct(d.class_percent)}</b>`
          : `<b>${fmtPct(d.class_percent)}</b>`;
        const crossed = (d.crossed_levels || []).length
          ? ` · crossed ${d.crossed_levels.map(l => `${(+l).toFixed(0)}%`).join(", ")}` : "";
        return `${esc(d.filer_name || "An owner")} now holds ${move}${crossed}`;
      }
      case "insider_cluster":
        return `<b>${d.n_insiders != null ? d.n_insiders : "?"}</b> insiders bought open-market`
          + (d.total_value != null ? ` · <b>${fmtMoney(d.total_value)}</b> total` : "")
          + (d.window_days ? ` within ${d.window_days}d` : "");
      default:
        return esc(JSON.stringify(d));
    }
  }
  function renderChanges() {
    const list = byId("chgList");
    if (!LIVE) { list.innerHTML = `<div class="empty">Waiting for the live feed…</div>`; return; }
    const rows = LIVE.changesFeed || [];
    byId("chgMeta").textContent = `detected from filings · latest ${rows.length}` +
      (BOOT ? ` · badge window ${BOOT.changeWindowDays}d` : "");
    if (!rows.length) { list.innerHTML = `<div class="empty">No change events detected yet.</div>`; return; }
    list.innerHTML = rows.map(c => {
      const kind = CHGKIND[c.type] || { label:c.type.replace(/_/g, " "), cls:"b-company" };
      const cik = tickerToCik[c.ticker];
      return `<button class="chg-row" data-cik="${cik || ""}" ${cik ? "" : 'title="Not in the curated universe"'}>`
        + `<span class="d">${esc(c.date)}</span>`
        + `<span><span class="badge ${kind.cls}">${esc(kind.label)}</span></span>`
        + `<span class="tk">${esc(c.ticker)}</span>`
        + `<span class="nm">${esc(c.name)}</span>`
        + `<span class="sum">${chgSummary(c)}</span>`
        + `</button>`;
    }).join("");
    list.querySelectorAll(".chg-row").forEach(row => row.addEventListener("click", () => {
      const cik = row.dataset.cik;
      if (cik && BOOT && BOOT.companies[cik]) { setView("dashboard"); focusCompany(cik); }
    }));
  }

  /* ---- graph models ---- */
  function model() {
    return mode === "universe" ? universeModel() : companyModel(focusCik);
  }

  function universeModel() {
    const centers = { A_semiconductors:[190,150], B_datacenter_power:[565,140],
      C_ev_battery:[640,315], D_defense_space:[420,405], E_bioprocessing:[150,340], owner:[390,250] };
    const nodes = [], pos = {};
    for (const m in centers) {
      const list = (groups[m] || []).slice();
      const [cx, cy] = centers[m], R = Math.min(78, 22 + list.length * 3.4);
      list.forEach((cik, i) => {
        const ang = (i / list.length) * Math.PI * 2 - Math.PI / 2;
        const x = cx + R * Math.cos(ang), y = cy + R * Math.sin(ang);
        pos[cik] = [x, y];
        const c = BOOT.companies[cik];
        nodes.push({ id:cik, type:roleType[c.r], label:c.t, x, y, r:9, company:true,
          detail:companyDetail(cik) });
      });
    }
    const edges = [];
    for (const [f, s] of BOOT.ownuni) if (pos[f] && pos[s]) edges.push({ a:f, b:s, type:"owner" });
    for (const [sup, cus] of BOOT.supply) if (cus && pos[sup] && pos[cus])
      edges.push({ a:sup, b:cus, type:"supply", emph:supplyChanged(sup) });
    return { nodes, edges };
  }

  function companyModel(cik) {
    const c = BOOT.companies[cik];
    const nodes = [{ id:cik, type:"company", label:c.t, x:W/2, y:H/2 - 6, r:32, company:true,
      sub:c.n.length > 18 ? c.n.slice(0, 17) + "…" : c.n, detail:companyDetail(cik) }];
    const edges = [];
    const place = (items, a0, a1, rad, mk) => {
      items.forEach((it, i) => {
        const a = items.length === 1 ? (a0 + a1) / 2 : a0 + (a1 - a0) * (i / (items.length - 1));
        const x = W/2 + rad * Math.cos(a), y = (H/2 - 6) + rad * 0.82 * Math.sin(a);
        mk(it, x, y, i);
      });
    };
    // owners (top) pink
    const owners = (BOOT.stakes[cik] || []).slice(0, 5);
    place(owners, -Math.PI*0.95, -Math.PI*0.05, 150, (o, x, y, i) => {
      nodes.push({ id:`own${i}`, type:"owner", label:shorten(o[0], 16), sub:o[1].toFixed(1) + "%", x, y, r:17,
        detail:{ name:o[0], type:"owner", rows:[["Stake in " + c.t, o[1].toFixed(1) + "%"], ["Filing", "Schedule 13D/G"]] } });
      edges.push({ a:cik, b:`own${i}`, type:"owner" });
    });
    // insiders (right) purple
    const ins = (BOOT.insiders[cik] || []).slice(0, 4);
    place(ins, -Math.PI*0.35, Math.PI*0.25, 168, (p, x, y, i) => {
      nodes.push({ id:`ins${i}`, type:"insider", label:shorten(p[0], 16), sub:shorten(p[1], 14), x, y, r:16,
        detail:{ name:p[0], type:"insider", rows:[["Role", p[1]], ["Filing", "Form 4"]] } });
      edges.push({ a:cik, b:`ins${i}`, type:"insider" });
    });
    // subsidiaries (bottom-right) green
    const sub = BOOT.subs[cik];
    if (sub) {
      const names = (sub.names || []).slice(0, 3);
      const items = names.concat(sub.n > names.length ? [`+${sub.n - names.length} more`] : []);
      place(items, Math.PI*0.15, Math.PI*0.6, 150, (nm, x, y, i) => {
        const more = nm.startsWith("+");
        nodes.push({ id:`sub${i}`, type:"subsidiary", label:shorten(nm, 16), x, y, r:more ? 16 : 14, expand:more,
          detail:{ name:more ? `${sub.n} subsidiaries` : nm, type:"subsidiary",
                   rows:[["Parent", c.t], ["Total", sub.n], ["Source", "Exhibit 21"]] } });
        edges.push({ a:cik, b:`sub${i}`, type:"subsidiary" });
      });
    }
    // supply partners (left) orange dashed — customers if we're a supplier, suppliers if a customer
    const asSupplier = BOOT.supply.filter(e => e[0] === cik);
    const asCustomer = BOOT.supply.filter(e => e[1] === cik);
    const partners = [];
    for (const [, cus, nm, p, tier] of asSupplier)
      partners.push({ dir:"→", label:cus && BOOT.companies[cus] ? BOOT.companies[cus].t : shorten(nm, 14),
        pct:p, tier, cik:cus, supplier:cik });
    for (const [sup, , , p, tier] of asCustomer)
      partners.push({ dir:"←", label:BOOT.companies[sup] ? BOOT.companies[sup].t : "?", pct:p, tier, cik:sup, supplier:sup });
    place(partners.slice(0, 6), Math.PI*0.72, Math.PI*1.28, 165, (pt, x, y, i) => {
      nodes.push({ id:`sup${i}`, type:"supply", label:pt.label, sub:pt.pct != null ? pt.pct.toFixed(0) + "%" : "", x, y, r:16,
        company:!!pt.cik, refocus:pt.cik,
        detail:{ name:pt.label, type:"supply",
                 rows:[[pt.dir === "→" ? "Customer of " + c.t : "Supplier to " + c.t,
                        pt.pct != null ? pt.pct.toFixed(1) + "% of rev" : "n/a"],
                       ["Tier", pt.tier], ["Source", "10-K (disclosed)"]] } });
      edges.push({ a:cik, b:`sup${i}`, type:"supply", emph:supplyChanged(pt.supplier) });
    });
    return { nodes, edges };
  }

  function companyDetail(cik) {
    const c = BOOT.companies[cik];
    const owners = BOOT.stakes[cik] || [];
    const rows = [["Ticker", c.t], ["Module", c.m], ["Graph role", c.r]];
    const ch = changeFor(cik);
    if (ch) rows.push([`Changed · last ${BOOT.changeWindowDays}d`,
      ch.types.map(t => (CHGKIND[t] || { label:t }).label).join(", ")]);
    if (owners.length) rows.push([">5% owners", owners.length]);
    if (BOOT.insiders[cik]) rows.push(["Insiders (Form 4)", BOOT.insiders[cik].length]);
    if (BOOT.subs[cik]) rows.push(["Subsidiaries", BOOT.subs[cik].n]);
    const sup = BOOT.supply.filter(e => e[0] === cik).length;
    const cus = BOOT.supply.filter(e => e[1] === cik && e[4] === "resolved").length;
    if (sup) rows.push(["Discloses customers", sup]);
    if (cus) rows.push(["Named as supplier by", cus]);
    return { name:c.n, type:"company", rows };
  }

  /* ---- render ---- */
  function clearSvg() { while (svg.firstChild) svg.removeChild(svg.firstChild); }
  function drawMessage(msg) {
    clearSvg();
    const t = el("text", { x:W/2, y:H/2, "text-anchor":"middle", class:"graph-msg" });
    t.textContent = msg; svg.appendChild(t);
  }

  function draw() {
    if (!BOOT) { drawMessage("Search for a company or person to explore its network."); return; }
    const { nodes, edges } = model();
    const byNode = Object.fromEntries(nodes.map(n => [n.id, n]));
    clearSvg();
    byId("graphTitle").textContent = mode === "universe"
      ? "Universe — company relationship network"
      : `${BOOT.companies[focusCik].n} — relationship network`;

    for (const e of edges) {
      const A = byNode[e.a], B = byNode[e.b]; if (!A || !B) continue;
      // supply edges: dashed (disclosed, lower confidence); emphasized weight when
      // the supplier has a recent supply_edge_new / supply_edge_changed event.
      const width = e.type === "supply" ? (e.emph ? 2.8 : 1.6) : 1.4;
      const opacity = e.type === "supply" && e.emph ? 0.85 : 0.5;
      const ln = el("line", { x1:A.x, y1:A.y, x2:B.x, y2:B.y, stroke:stroke(e.type),
        "stroke-width":width, "stroke-opacity":opacity, "stroke-linecap":"round" });
      if (e.type === "supply") ln.setAttribute("stroke-dasharray", "5 4");
      svg.appendChild(ln);
    }
    for (const n of nodes) {
      const g = el("g", { class:"node-hit", tabindex:"0", role:"button", "aria-label":`${n.label}, ${typeName[n.type]}` });
      if (mode === "universe" && searchQuery) g.setAttribute("opacity", nodeMatches(n.id) ? "1" : "0.16");
      const isSel = n.id === selected;
      const c = el("circle", { cx:n.x, cy:n.y, r:n.r, fill:fill(n.type), stroke:stroke(n.type), "stroke-width":isSel ? 4 : 2 });
      if (n.expand) c.setAttribute("stroke-dasharray", "4 3");
      if (isSel) g.appendChild(el("circle", { cx:n.x, cy:n.y, r:n.r + 5, fill:"none", stroke:stroke(n.type), "stroke-width":1, "stroke-opacity":0.4 }));
      g.appendChild(c);
      const big = n.r > 24;
      const lbl = el("text", { x:n.x, y:big ? n.y + n.r + 15 : n.y - n.r - 5, "text-anchor":"middle",
        class:"node-label", "font-size":mode === "universe" ? 9 : 11 });
      lbl.textContent = n.label; g.appendChild(lbl);
      if (n.sub) { const s = el("text", { x:n.x, y:n.y + 4, "text-anchor":"middle", class:"node-sub",
        "font-size":big ? 10 : 9, fill:stroke(n.type) }); s.textContent = n.sub; g.appendChild(s); }

      // delta badge: recent change event on this company (bootstrap.changes)
      const chCik = n.refocus || n.id;
      const ch = BOOT.companies[chCik] ? changeFor(chCik) : null;
      if (ch) {
        const br = n.r >= 24 ? 8 : 5.5, off = n.r * 0.78 + 1;
        const bx = n.x + off, by = n.y - off;
        g.appendChild(el("circle", { cx:bx, cy:by, r:br,
          fill:cssv("--info-bg"), stroke:cssv("--info-fg"), "stroke-width":1.2 }));
        const dt = el("text", { x:bx, y:by + (br >= 8 ? 3.2 : 2.4), "text-anchor":"middle",
          "font-size":br >= 8 ? 9 : 7, fill:cssv("--info-fg"), "font-family":cssv("--font-mono") });
        dt.textContent = "Δ"; g.appendChild(dt);
      }

      const show = ev => { const p = ev.touches ? ev.touches[0] : ev;
        tip.querySelector(".tt").textContent = n.detail.name;
        tip.querySelector(".tm").textContent = typeName[n.type];
        tip.querySelector(".tc").textContent = ch ? changeTip(ch) : "";
        tip.style.opacity = 1; tip.style.left = Math.min(p.clientX + 14, innerWidth - 270) + "px";
        tip.style.top = (p.clientY + 14) + "px"; };
      g.addEventListener("mouseenter", show); g.addEventListener("mousemove", show);
      g.addEventListener("mouseleave", () => tip.style.opacity = 0);
      const act = () => {
        const target = n.refocus || (n.company ? n.id : null);
        if (target && BOOT.companies[target]) focusCompany(target);
        else { selected = n.id; renderDetail(n.detail); draw(); }
      };
      g.addEventListener("click", act);
      g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); act(); } });
      svg.appendChild(g);
    }
    renderLegend();
    renderDetail(byNode[selected] ? byNode[selected].detail
      : (mode === "company" ? companyDetail(focusCik) : universeDetail()));
  }

  function universeDetail() {
    const nC = Object.keys(BOOT.companies).length;
    return { name:"Curated universe", type:"company", rows:[
      ["Companies", nC], ["Modules", 5], ["Supply edges", BOOT.supply.filter(e => e[1]).length],
      ["Inter-owner edges", BOOT.ownuni.length],
      ["Changed recently", Object.keys(BOOT.changes || {}).length],
      ["Tip", "click a node to focus"]] };
  }

  function renderLegend() {
    const items = mode === "universe"
      ? [["company","customer"],["supply","supplier"],["owner","owner / holder"]]
      : [["company","company"],["owner","owner"],["insider","insider"],["subsidiary","subsidiary"],["supply","supplier/customer"]];
    byId("legend").innerHTML = items.map(([t, l]) =>
      `<span class="li"><span class="lk" style="border-color:${stroke(t)};background:${fill(t)}"></span> ${l}</span>`).join("")
      + `<span class="edge-note"><i></i> structured</span>`
      + `<span class="edge-note"><i class="dash"></i> supply-chain · disclosed</span>`
      + `<span class="edge-note"><i class="emph"></i> supply edge changed</span>`
      + `<span class="delta-key"><span class="dk">Δ</span> changed · last ${BOOT ? BOOT.changeWindowDays : 45}d</span>`;
  }

  function renderDetail(d) {
    if (!d) return;
    byId("detailHead").innerHTML = `<h3>${esc(d.name)}</h3><span class="badge b-${d.type}">${d.type}</span>`;
    byId("detailKv").innerHTML = d.rows.map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>`).join("");
  }

  /* ---- boot ---- */
  drawMessage("Loading the graph…");
  loadBootstrap();
  fetchLive();
  setInterval(fetchLive, POLL_MS);
})();
