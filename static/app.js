/* ==========================================================================
   GPU Radar — SPA
   Filters live in the URL hash, so any view you like is a bookmark.
   ========================================================================== */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const CLASS_LABEL = { steal: "ACHADO", great: "ÓTIMO", good: "BOM", fair: "RAZOÁVEL", meh: "FRACO" };
const CLASS_COLOR = { steal: "#ff4d6d", great: "#fb923c", good: "#34d399", fair: "#60a5fa", meh: "#64748b" };
const FLAG_LABEL = {
  mining: "minerada", broken: "com defeito", new: "nova", used: "usada",
  warranty: "garantia", invoice: "nota fiscal", bundle: "com brinde", trade: "troca",
};

const brl = v => v == null ? "—" : "R$ " + Number(v).toLocaleString("pt-BR", { maximumFractionDigits: 0 });

/* SQLite stores tz-aware ISO strings ("…T19:32:20+00:00"). Only stamp a Z on
   the ones that carry no zone at all, or Date() gets an invalid string. */
const HAS_TZ = /(?:Z|[+-]\d{2}:?\d{2})$/;
const asDate = s => new Date(HAS_TZ.test(s) ? s : s + "Z");
const fmt = s => asDate(s).toLocaleString("pt-BR");

function ago(hours) {
  if (hours == null) return "";
  if (hours < 1)  return `${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 24) return `${Math.round(hours)} h`;
  const d = Math.round(hours / 24);
  return d < 30 ? `${d} d` : `${Math.round(d / 30)} mês`;
}

/* ------------------------------------------------------------------ state */

const DEFAULTS = {
  q: "", brand: "", city: "", sort: "score", classes: [],
  pmax: window.RADAR.budget, perfmin: 40,
  fresh: false, combos: false, suspect: false, gone: false,
};

const state = { ...DEFAULTS, ...readHash() };
let listings = [];
let stats = null;
let knownIds = new Set();
let firstLoad = true;

function readHash() {
  const out = {};
  const h = new URLSearchParams(location.hash.slice(1));
  for (const [k, v] of h) {
    if (k === "classes") out[k] = v ? v.split(",") : [];
    else if (k in DEFAULTS && typeof DEFAULTS[k] === "boolean") out[k] = v === "1";
    else if (k in DEFAULTS && typeof DEFAULTS[k] === "number") out[k] = Number(v);
    else if (k in DEFAULTS) out[k] = v;
  }
  return out;
}

function writeHash() {
  const p = new URLSearchParams();
  for (const [k, def] of Object.entries(DEFAULTS)) {
    const v = state[k];
    const same = Array.isArray(def) ? v.join() === def.join() : v === def;
    if (same) continue;
    p.set(k, Array.isArray(v) ? v.join(",") : (typeof v === "boolean" ? (v ? "1" : "0") : v));
  }
  const s = p.toString();
  history.replaceState(null, "", s ? "#" + s : location.pathname);
}

/* ------------------------------------------------------------------ fetch */

function query() {
  const p = new URLSearchParams({
    sort: state.sort, price_max: state.pmax, perf_min: state.perfmin, limit: 400,
  });
  if (state.q) p.set("q", state.q);
  if (state.brand) p.set("brand", state.brand);
  if (state.city) p.set("city", state.city);
  if (state.classes.length) p.set("class", state.classes.join(","));
  if (state.fresh) p.set("max_age_h", 24);
  if (state.combos) p.set("include_combos", 1);
  if (state.suspect) p.set("include_suspect", 1);
  if (state.gone) p.set("include_gone", 1);
  return p;
}

async function load({ announce = true } = {}) {
  const [l, s] = await Promise.all([
    fetch("/api/listings?" + query()).then(r => r.json()),
    fetch("/api/stats").then(r => r.json()),
  ]);
  const incoming = l.listings;

  if (announce && !firstLoad) {
    const fresh = incoming.filter(x => !knownIds.has(x.id) && x.deal_score >= window.RADAR.alertMin);
    fresh.slice(0, 4).forEach(x => toast(
      `${x.model_name} · ${brl(x.price)}`,
      `${CLASS_LABEL[x.deal_class]} — nota ${Math.round(x.deal_score)} · ${x.city || ""}`,
      x.deal_class === "steal", () => openDrawer(x.id)));
    if (fresh.length) notifyBrowser(fresh[0]);
  }
  knownIds = new Set(incoming.map(x => x.id));
  firstLoad = false;

  listings = incoming;
  stats = s;
  renderStats();
  renderHero();
  renderGrid();
  renderTables();
}

/* ------------------------------------------------------------------ render */

function renderStats() {
  $("#st-budget").textContent = stats.in_budget ?? 0;
  $("#st-steal").textContent  = (stats.by_class?.steal ?? 0) + (stats.by_class?.great ?? 0);
  $("#st-live").textContent   = stats.live ?? 0;

  const r = stats.last_run;
  if (r?.finished) {
    const h = (Date.now() - asDate(r.finished)) / 36e5;
    $("#st-run").textContent = ago(h);
    $("#st-run-pill").title =
      `${r.requests} requisições · ${r.http_errors} erros · ${r.matched} no catálogo · ${r.new} novos`;
  } else $("#st-run").textContent = "—";

  const n = stats.alerts ?? 0;
  const badge = $("#alert-count");
  badge.hidden = !n; badge.textContent = n > 99 ? "99+" : n;

  const sel = $("#city");
  if (sel.options.length <= 1 && stats.cities?.length) {
    stats.cities.forEach(c => sel.insertAdjacentHTML("beforeend",
      `<option value="${esc(c.city)}">${esc(c.city)} (${c.n})</option>`));
    sel.value = state.city;
  }

  const running = stats.scrape?.running;
  $("#btn-scrape").disabled = !!running;
  $("#scrape-label").textContent = running ? "Varrendo…" : "Varrer agora";
  $("#scrape-ico").classList.toggle("spin", !!running);
}

function ring(score, cls) {
  const R = 19, C = 2 * Math.PI * R, on = C * (score / 100);
  return `<div class="ring">
    <svg width="46" height="46" viewBox="0 0 46 46">
      <circle cx="23" cy="23" r="${R}" fill="rgba(7,7,12,.72)" stroke="rgba(255,255,255,.1)" stroke-width="3"/>
      <circle cx="23" cy="23" r="${R}" fill="none" stroke="${CLASS_COLOR[cls]}" stroke-width="3"
              stroke-linecap="round" stroke-dasharray="${on} ${C}"/>
    </svg>
    <span class="val" style="color:${CLASS_COLOR[cls]}">${Math.round(score)}</span>
  </div>`;
}

const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function tags(x) {
  const t = [];
  if (x.city === window.RADAR.homeCity) t.push(`<i class="tag local">${esc(x.city)}</i>`);
  else if (x.city) t.push(`<i class="tag">${esc(x.city)}</i>`);
  if (x.age_hours != null && x.age_hours <= 24) t.push('<i class="tag fresh">novo</i>');
  if (x.price_drops)  t.push(`<i class="tag drop">↓ ${x.price_drops}× baixou</i>`);
  if (x.suspect)      t.push('<i class="tag warn">suspeito</i>');
  if (x.kind === "combo") t.push('<i class="tag combo">PC completo</i>');
  if (x.gone)         t.push('<i class="tag">encerrado</i>');
  return `<div class="tags">${t.join("")}</div>`;
}

function card(x) {
  const off = Math.round((x.discount ?? 0) * 100);
  const img = x.image
    ? `<img src="${esc(x.image)}" alt="" loading="lazy" decoding="async"
            referrerpolicy="no-referrer"
            onerror="this.parentElement.innerHTML='<div class=noimg>sem foto</div>'">`
    : '<div class="noimg">sem foto</div>';
  const flags = (x.flags || []).filter(f => FLAG_LABEL[f])
    .map(f => `<i class="flag f-${f}">${FLAG_LABEL[f]}</i>`).join("");

  return `<article class="card k-${x.deal_class}" data-id="${x.id}" tabindex="0">
    <div class="thumb">
      ${img}${ring(x.deal_score, x.deal_class)}${tags(x)}
      <div class="thumb-foot">
        <span class="model-badge">
          <i class="brand-dot brand-${esc(x.brand)}"></i>${esc(x.model_name)}
          <b class="vram">${x.vram}GB</b>
        </span>
      </div>
    </div>
    <div class="body">
      <div class="price-row">
        <span class="price">${brl(x.price)}</span>
        ${off > 0 ? `<span class="off down">−${off}%</span>`
                  : `<span class="off up">${off === 0 ? "na média" : "+" + Math.abs(off) + "%"}</span>`}
      </div>
      <div class="ref">típico ${brl(x.reference_price)} · ${x.perf_per_1k?.toFixed(0)} pts/R$1k</div>
      <h3 class="title">${esc(x.title)}</h3>
      <div class="perfbar">
        <div class="track"><div class="fill" style="width:${Math.min(100, x.perf / 1.2)}%"></div></div>
        <small>tier ${Math.round(x.perf)}</small>
      </div>
      ${flags ? `<div class="flagline">${flags}</div>` : ""}
      <div class="foot">
        <span class="loc">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>
          </svg><span>${esc(x.location || x.city || "—")}</span>
        </span>
        <time>${ago(x.age_hours)}</time>
      </div>
    </div>
  </article>`;
}

function renderGrid() {
  const g = $("#grid");
  $("#count").textContent = `${listings.length} anúncio${listings.length === 1 ? "" : "s"}`;
  if (!listings.length) {
    g.innerHTML = `<div class="empty-state">
      <svg width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
      <h3>Nenhum anúncio com esses filtros</h3>
      <p>Solte o preço máximo, baixe a força mínima, ou rode uma varredura.</p></div>`;
    return;
  }
  g.innerHTML = listings.map(card).join("");
  $$(".card", g).forEach((el, i) => {
    el.style.animationDelay = Math.min(i, 8) * 18 + "ms";   // keep the stagger short
    el.addEventListener("click", () => openDrawer(el.dataset.id));
    el.addEventListener("keydown", e => { if (e.key === "Enter") openDrawer(el.dataset.id); });
  });
}

function renderHero() {
  const best = listings.find(x => x.in_budget && !x.suspect && x.kind === "gpu") || listings[0];
  const h = $("#hero");
  if (!best) {
    h.className = "hero empty";
    h.innerHTML = "Nada no orçamento ainda. Rode uma varredura ou solte os filtros.";
    return;
  }
  const off = Math.round((best.discount ?? 0) * 100);
  h.className = "hero";
  h.innerHTML = `
    <div class="hero-img">${best.image
      ? `<img src="${esc(best.image)}" alt="" referrerpolicy="no-referrer">`
      : '<div class="noimg" style="display:grid;place-items:center;height:100%;color:var(--ink-3)">sem foto</div>'}
    </div>
    <div class="hero-body">
      <div class="hero-kicker"><i class="dot"></i>Melhor achado agora · nota ${Math.round(best.deal_score)}/100</div>
      <h2>${esc(best.title)}</h2>
      <div class="hero-row">
        <span class="hero-price" style="color:${CLASS_COLOR[best.deal_class]}">${brl(best.price)}</span>
        <div class="hero-meta">
          <span>${off > 0 ? `<b style="color:var(--good)">−${off}%</b>` : "<b>na média</b>"} vs ${brl(best.reference_price)}</span>
          <span><b>${esc(best.model_name)}</b> · ${best.vram} GB · tier ${Math.round(best.perf)}</span>
          <span><b>${best.perf_per_1k?.toFixed(0)}</b> pts/R$1k</span>
          <span>${esc(best.location || "")} · <b>${ago(best.age_hours)}</b></span>
        </div>
      </div>
      <div class="row" style="margin-top:4px">
        <a class="btn primary" href="${esc(best.url)}" target="_blank" rel="noopener">Abrir na OLX ↗</a>
        <button class="btn" data-open="${best.id}">Ver detalhes</button>
      </div>
    </div>`;
  $("[data-open]", h)?.addEventListener("click", e => openDrawer(e.target.dataset.open));
}

function renderTables() {
  const mt = $("#models-tbl tbody");
  $("#models-n").textContent = `${stats.models.length} modelos vistos`;
  mt.innerHTML = stats.models.map(m => `<tr>
    <td style="color:var(--ink)"><i class="brand-dot brand-${esc(m.brand)}"
        style="display:inline-block;margin-right:7px"></i>${esc(m.model_name)}</td>
    <td>${esc(m.brand)}</td><td class="n">${m.vram} GB</td><td class="n">${Math.round(m.perf)}</td>
    <td class="n">${m.n}</td><td class="n">${brl(m.lo)}</td><td class="n">${brl(m.avg)}</td>
    <td class="n">${brl(m.hi)}</td><td class="n">${brl(m.ref)}</td></tr>`).join("")
    || `<tr><td colspan="9" style="color:var(--ink-3)">nada ainda</td></tr>`;
}

async function renderPanelAlerts() {
  const { alerts } = await fetch("/api/alerts").then(r => r.json());
  $("#alerts-tbl tbody").innerHTML = alerts.map(a => `<tr>
    <td>${fmt(a.ts)}</td>
    <td><a href="${esc(a.url)}" target="_blank" rel="noopener"
           style="color:var(--violet-2)">${esc((a.title || "").slice(0, 58))}</a></td>
    <td class="n">${brl(a.price)}</td><td class="n">${Math.round(a.deal_score)}</td>
    <td>${esc(a.reason)}</td><td>${(a.channels || []).join(", ") || "—"}</td></tr>`).join("")
    || `<tr><td colspan="6" style="color:var(--ink-3)">nenhum alerta enviado ainda</td></tr>`;
}

async function renderPanelRuns() {
  const { runs } = await fetch("/api/runs").then(r => r.json());
  $("#runs-tbl tbody").innerHTML = runs.map(r => `<tr>
    <td>${fmt(r.started)}</td>
    <td class="n">${r.requests}</td>
    <td class="n" style="${r.http_errors ? "color:var(--rose)" : ""}">${r.http_errors}</td>
    <td class="n">${r.cards}</td><td class="n">${r.matched}</td>
    <td class="n" style="${r.new ? "color:var(--good)" : ""}">${r.new}</td>
    <td class="n">${r.price_drops}</td><td class="n">${r.alerted}</td></tr>`).join("")
    || `<tr><td colspan="8" style="color:var(--ink-3)">nenhuma varredura registrada</td></tr>`;
}

/* ------------------------------------------------------------------ drawer */

function sparkline(history, w = 480, h = 74) {
  if (!history || history.length < 2) return "";
  const ps = history.map(p => p.price);
  const lo = Math.min(...ps), hi = Math.max(...ps), span = hi - lo || 1;
  const pts = history.map((p, i) => [
    12 + i * (w - 24) / (history.length - 1),
    h - 14 - ((p.price - lo) / span) * (h - 30),
  ]);
  const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = `${line} L${pts.at(-1)[0].toFixed(1)} ${h} L${pts[0][0].toFixed(1)} ${h} Z`;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
      <stop stop-color="#8b5cf6" stop-opacity=".35"/><stop offset="1" stop-color="#8b5cf6" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${area}" fill="url(#sg)"/>
    <path d="${line}" fill="none" stroke="#a78bfa" stroke-width="2" stroke-linejoin="round"/>
    ${pts.map(p => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.6" fill="#a78bfa"/>`).join("")}
    <text x="12" y="12" fill="#6e7288" font-size="10" font-family="monospace">${brl(hi)}</text>
    <text x="12" y="${h - 2}" fill="#6e7288" font-size="10" font-family="monospace">${brl(lo)}</text>
  </svg>`;
}

async function openDrawer(id) {
  const d = $("#drawer");
  d.innerHTML = `<div style="padding:60px;text-align:center;color:var(--ink-3)">carregando…</div>`;
  d.classList.add("open"); $("#scrim").classList.add("open");
  document.body.style.overflow = "hidden";

  const x = await fetch("/api/listing/" + id).then(r => r.json());
  if (x.error) { d.innerHTML = `<div style="padding:60px">não encontrado</div>`; return; }

  const off = Math.round((x.discount ?? 0) * 100);
  const ms = x.model_stats || {};
  const flags = (x.flags || []).filter(f => FLAG_LABEL[f])
    .map(f => `<i class="flag f-${f}">${FLAG_LABEL[f]}</i>`).join("");

  d.innerHTML = `
    <button class="close" title="Fechar (Esc)">✕</button>
    <div class="drawer-hero">${x.image
      ? `<img src="${esc(x.image)}" alt="" referrerpolicy="no-referrer">` : ""}</div>
    <div class="drawer-body">
      <div class="hero-kicker" style="color:${CLASS_COLOR[x.deal_class]}">
        ${CLASS_LABEL[x.deal_class]} · nota ${Math.round(x.deal_score)}/100
      </div>
      <h2>${esc(x.title)}</h2>
      ${flags ? `<div class="flagline">${flags}</div>` : ""}
      ${x.suspect ? `<p style="margin:0;padding:11px 14px;border-radius:10px;
        background:rgba(251,191,36,.1);border:1px solid rgba(251,191,36,.3);
        color:var(--amber);font-size:13px">⚠ Muito abaixo do mercado para esse modelo.
        Golpe é a explicação mais provável — nunca pague adiantado, veja funcionando.</p>` : ""}
      ${x.kind === "combo" ? `<p style="margin:0;padding:11px 14px;border-radius:10px;
        background:rgba(96,165,250,.1);border:1px solid rgba(96,165,250,.3);
        color:var(--fair);font-size:13px">🖥 O anúncio é de um PC completo, não da placa avulsa.</p>` : ""}

      <div class="kv">
        <div><dt>preço</dt><dd style="color:${CLASS_COLOR[x.deal_class]}">${brl(x.price)}</dd></div>
        <div><dt>típico</dt><dd>${brl(x.reference_price)}</dd></div>
        <div><dt>economia</dt><dd style="color:${off > 0 ? "var(--good)" : "var(--ink-2)"}">${off > 0 ? brl(x.saving) : "—"}</dd></div>
        <div><dt>desconto</dt><dd>${off > 0 ? "−" + off + "%" : off + "%"}</dd></div>
        <div><dt>modelo</dt><dd style="font-size:14px">${esc(x.model_name)}</dd></div>
        <div><dt>vram</dt><dd>${x.vram} GB</dd></div>
        <div><dt>tier</dt><dd>${Math.round(x.perf)}</dd></div>
        <div><dt>pts / R$1k</dt><dd>${x.perf_per_1k?.toFixed(0)}</dd></div>
        <div><dt>cidade</dt><dd style="font-size:14px">${esc(x.city || "—")}</dd></div>
        <div><dt>publicado</dt><dd style="font-size:14px">${esc(x.date_text || "—")}</dd></div>
        <div><dt>visto</dt><dd>${x.seen_count}×</dd></div>
        <div><dt>baixas</dt><dd>${x.price_drops}</dd></div>
      </div>

      <div class="row">
        <a class="btn primary" href="${esc(x.url)}" target="_blank" rel="noopener">Abrir na OLX ↗</a>
        <button class="btn" id="copy-url">Copiar link</button>
      </div>

      ${x.history?.length > 1 ? `<div>
        <p class="section-t">histórico de preço</p>${sparkline(x.history)}</div>` : ""}

      <div>
        <p class="section-t">referência — ${esc(x.model_name)}
          (${ms.n || 0} anúncios · ${x.ref_source || ""})</p>
        <div class="kv">
          <div><dt>mín</dt><dd>${brl(ms.lo)}</dd></div>
          <div><dt>médio</dt><dd>${brl(ms.avg ? Math.round(ms.avg) : null)}</dd></div>
          <div><dt>máx</dt><dd>${brl(ms.hi)}</dd></div>
        </div>
      </div>

      ${x.peers?.length ? `<div>
        <p class="section-t">outros ${esc(x.model_name)} na região</p>
        ${x.peers.map(p => `<a class="peer" href="${esc(p.url)}" target="_blank" rel="noopener">
          <span class="p">${brl(p.price)}</span>
          <span class="t">${esc(p.title)}</span>
          <span class="s">${esc(p.city || "")} · ${Math.round(p.deal_score)}</span></a>`).join("")}
      </div>` : ""}
    </div>`;

  $(".close", d).onclick = closeDrawer;
  $("#copy-url", d).onclick = e => {
    navigator.clipboard.writeText(x.url);
    e.target.textContent = "Copiado ✓";
  };
}

function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#scrim").classList.remove("open");
  document.body.style.overflow = "";
}

/* ------------------------------------------------------------------ toasts */

function toast(title, sub, hot = false, onClick) {
  const el = document.createElement("div");
  el.className = "toast" + (hot ? " hot" : "");
  el.innerHTML = `<i class="bar"></i><div><strong>${esc(title)}</strong><small>${esc(sub)}</small></div>`;
  if (onClick) el.onclick = () => { onClick(); dismiss(); };
  $("#toasts").appendChild(el);
  const dismiss = () => { el.classList.add("out"); setTimeout(() => el.remove(), 320); };
  setTimeout(dismiss, 9000);
  return el;
}

function notifyBrowser(x) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const n = new Notification(`${CLASS_LABEL[x.deal_class]}: ${x.model_name} — ${brl(x.price)}`, {
    body: `${x.title}\n${x.city || ""} · nota ${Math.round(x.deal_score)}/100`,
    icon: x.image || undefined, tag: "gpu-" + x.id,
  });
  n.onclick = () => { window.focus(); openDrawer(x.id); };
}

/* ------------------------------------------------------------------ wiring */

const debounce = (fn, ms = 260) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const refresh = () => { writeHash(); load({ announce: false }); };

function bind() {
  $("#q").value = state.q;
  $("#q").addEventListener("input", debounce(e => { state.q = e.target.value.trim(); refresh(); }));

  $$("#brand button").forEach(b => {
    b.setAttribute("aria-pressed", String(b.dataset.v === state.brand));
    b.onclick = () => {
      state.brand = b.dataset.v;
      $$("#brand button").forEach(o => o.setAttribute("aria-pressed", String(o === b)));
      refresh();
    };
  });

  $("#sort").value = state.sort;
  $("#sort").onchange = e => { state.sort = e.target.value; refresh(); };
  $("#city").onchange = e => { state.city = e.target.value; refresh(); };

  const pm = $("#pmax");
  pm.value = state.pmax;
  const showP = () => $("#pmax-out").textContent = brl(pm.value);
  showP();
  pm.oninput = () => { showP(); state.pmax = Number(pm.value); debouncedRefresh(); };

  const pf = $("#perfmin");
  pf.value = state.perfmin;
  const showF = () => $("#perfmin-out").textContent = "tier " + pf.value;
  showF();
  pf.oninput = () => { showF(); state.perfmin = Number(pf.value); debouncedRefresh(); };

  $$("#classes .chip").forEach(c => {
    c.setAttribute("aria-pressed", String(state.classes.includes(c.dataset.v)));
    c.onclick = () => {
      const v = c.dataset.v, i = state.classes.indexOf(v);
      i < 0 ? state.classes.push(v) : state.classes.splice(i, 1);
      c.setAttribute("aria-pressed", String(i < 0));
      refresh();
    };
  });

  [["#t-fresh", "fresh"], ["#t-combos", "combos"], ["#t-suspect", "suspect"], ["#t-gone", "gone"]]
    .forEach(([sel, key]) => {
      const el = $(sel);
      el.setAttribute("aria-pressed", String(state[key]));
      el.onclick = () => {
        state[key] = !state[key];
        el.setAttribute("aria-pressed", String(state[key]));
        refresh();
      };
    });

  $("#btn-scrape").onclick = async () => {
    const r = await fetch("/api/scrape", { method: "POST" }).then(r => r.json());
    if (!r.ok) return toast("Varredura já em andamento", "aguarde terminar");
    toast("Varredura iniciada", "leva ~3 min; a página se atualiza sozinha");
    renderStats();
    poll(4000);
  };

  $("#btn-test").onclick = async () => {
    const r = await fetch("/api/test-alert", { method: "POST" }).then(r => r.json());
    toast(r.channels?.length ? "Alerta de teste enviado" : "Nenhum canal respondeu",
      r.channels?.length ? "por: " + r.channels.join(", ") : "confira o config.json");
  };

  $("#btn-alerts").onclick = () => {
    const p = $("#panel-alerts");
    p.open = true; p.scrollIntoView({ block: "center" });
  };

  $("#panel-alerts").addEventListener("toggle", e => e.target.open && renderPanelAlerts());
  $("#panel-runs").addEventListener("toggle", e => e.target.open && renderPanelRuns());

  $("#topic").onclick = e => {
    navigator.clipboard.writeText(e.target.textContent.trim());
    toast("Tópico copiado", "inscreva-se no app ntfy");
  };

  $("#scrim").onclick = closeDrawer;

  document.addEventListener("keydown", e => {
    if (e.key === "Escape") return closeDrawer();
    if (e.target.matches("input, select, textarea")) return;
    if (e.key === "/") { e.preventDefault(); $("#q").focus(); }
    if (e.key === "r") $("#btn-scrape").click();
    if (e.key === "a") $("#btn-alerts").click();
  });
}

const debouncedRefresh = debounce(refresh, 420);

/* ------------------------------------------------------------------- boot */

let pollTimer = null;
function poll(ms = 60000) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    await load();
    // While a sweep runs we poll fast; once it lands, drop back to idle pace.
    if (ms < 30000 && !stats?.scrape?.running) poll(60000);
  }, ms);
}

(async function boot() {
  bind();

  // Paint from the server-inlined payload when the view is still the default
  // one; any hash filter means the inlined data does not match what was asked
  // for, so fall through to a fetch.
  const pristine = !location.hash.slice(1);
  const seed = window.RADAR.initial;
  if (pristine && seed?.listings) {
    listings = seed.listings;
    stats = seed.stats;
    knownIds = new Set(listings.map(x => x.id));
    firstLoad = false;
    renderStats(); renderHero(); renderGrid(); renderTables();
  } else {
    $("#grid").innerHTML = Array.from({ length: 8 },
      () => '<div class="sk"><div class="a"></div><div class="b"></div></div>').join("");
    await load({ announce: false });
  }
  poll();
  if ("Notification" in window && Notification.permission === "default") {
    document.addEventListener("click", () => Notification.requestPermission(), { once: true });
  }
})();
