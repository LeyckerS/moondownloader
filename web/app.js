"use strict";
/* ════════════════════════════════════════════════════════════════════════
   MoonDownloader V2 — GUI logic

   Transports (same promise shape, picked at boot):
     · http       -> POST /api/<name> on the loopback server (start.bat)
     · pywebview  -> window.pywebview.api, when hosted from file://
     · mock       -> synthetic engine, for opening index.html on its own

   Pull model: the page asks for snapshot(cursor) ~12x per second instead of
   Python pushing at it. One call per frame, no cross-thread JS eval, and a late
   snapshot is a dropped frame instead of a stall.

   Wording lives here, not in the engine: the engine ships numbers and a stage
   name, the page turns them into a sentence in the chosen language.
   ════════════════════════════════════════════════════════════════════════ */

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const POLL_MS = 80;
const SPARK_N = 90;
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)");
/* Reference rate for the ambience glow only -- not a cap, not a target, and it
   never touches a number on screen. Picked so a normal fast run sits near the
   top of the curve without pinning it. */
const HEAT_REF_MBS = 60;

/* ── i18n ─────────────────────────────────────────────────────────────── */
const I18N = {
  en: {
    link: "Links", links_ph: "paste the links, one per line\nhttps://datanodes.to/...\nhttps://fuckingfast.co/...",
    load_txt: "Load .txt", clear: "Clear", others: "others",
    destination: "Destination", pick_folder: "Choose folder", pick_chrome: "Choose chrome.exe",
    mode_download: "Download", mode_links: "Links only",
    common: "Common", common_sub: "· both methods",
    extractors: "Extractors", dl_streams: "DL streams", retries: "Retries",
    rec16: "rec. 16", rec8: "rec. 8", rec8p: "rec. 8",
    note_streams: "fewer streams = more bandwidth per file; the pipe is still the ceiling",
    dn_note: "Real Chrome + Turnstile: it opens pages, not windows",
    pages: "Pages", captcha: "Captcha", captcha_hint: "manual wait",
    autodetect: "autodetect", api_key: "API key", premium: "premium",
    dn_api_note: "premium API key = direct JSON, no browser, no captcha",
    ff_ok: "curl_cffi active · hx-redirect, ~0.25s per link",
    ff_missing: "curl_cffi MISSING · pip install curl_cffi",
    ff_note: "nothing to tune: it opens no Chrome and has no captcha",
    start: "Start", stop: "Stop", stopping: "Closing…",
    speed: "Speed", completed: "Completed", downloaded: "Downloaded",
    pipeline: "Pipeline", extraction: "Extraction", download_lane: "Download",
    files_tab: "Transfers", log_tab: "Log", wipe: "Clear",
    empty_title: "nothing queued", empty_sub: "paste the links, pick a folder, hit Start",
    IDLE: "IDLE", RUNNING: "RUNNING", STOPPING: "STOPPING", DONE: "DONE",
    st_queue: "queued", st_extract: "extracting", st_download: "downloading",
    st_ok: "saved", st_fail: "failed", st_kill: "restarting",
    chip_auto: "auto", chip_chrome: "chrome", chip_api: "api key",
    no_proxy: "no proxy", empty_proxies: "0 valid proxies found",
    filter_ph: "filter", f_all: "All", f_active: "Active", f_ok: "Saved", f_fail: "Failed",
    filter_none: "nothing matches", filter_none_sub: "clear the filter to see the rest",
    drop_title: "drop links or a .txt", drop_sub: "they are appended to the list",
    help_title: "Shortcuts",
    k_start: "Start, or stop a running batch",
    k_open: "Load a .txt of links",
    k_filter: "Filter the transfer list",
    k_help: "This panel",
    k_esc: "Close, or clear the filter",
    copy_link: "Copy the source link", copied: "link copied", copy_failed: "could not copy",
    sum_title: "Batch finished", sum_saved: "Saved", sum_failed: "Failed",
    sum_bytes: "Downloaded", sum_elapsed: "Elapsed",
    sum_copy_failed: "Copy failed links",
    copied_n: (n) => `${n} link${n === 1 ? "" : "s"} copied`,
    links_n: (n) => `${n} link${n === 1 ? "" : "s"}`,
    remaining: (n) => `${n} file${n === 1 ? "" : "s"} remaining`,
    counts: (ok, ko, kill) => `${ok} ok · ${ko} failed · ${kill} kill`,
    proxy_n: (n) => `proxy ${n}`,
    tmp_n: (n) => `${n} .tmp to resume`,
    loaded_n: (n) => `${n} links loaded`,
    ph_idle: "idle",
    ph_extract: (ed, et, dd, dt, act) => `Extracting [${ed}/${et}] + downloading [${dd}/${dt} done · ${act} active]`,
    ph_download: (dd, dt, act) => `Downloading [${dd}/${dt} done · ${act} active]`,
    ph_done: "finished",
    toast_no_links: "no links pasted",
    toast_no_folder: "pick a destination folder",
  },
  it: {
    link: "Link", links_ph: "incolla i link, uno per riga\nhttps://datanodes.to/...\nhttps://fuckingfast.co/...",
    load_txt: "Carica .txt", clear: "Svuota", others: "altri",
    destination: "Destinazione", pick_folder: "Scegli cartella", pick_chrome: "Scegli chrome.exe",
    mode_download: "Download", mode_links: "Solo link",
    common: "Comuni", common_sub: "· entrambi i metodi",
    extractors: "Extractors", dl_streams: "DL streams", retries: "Retries",
    rec16: "cons. 16", rec8: "cons. 8", rec8p: "cons. 8",
    note_streams: "pochi stream = piu banda per file; la pipe resta il tetto",
    dn_note: "Chrome reale + Turnstile: apre pagine, non finestre",
    pages: "Pages", captcha: "Captcha", captcha_hint: "attesa manuale",
    autodetect: "autodetect", api_key: "API key", premium: "premium",
    dn_api_note: "API key premium = JSON diretto, zero browser, zero captcha",
    ff_ok: "curl_cffi attivo · hx-redirect, ~0.25s per link",
    ff_missing: "curl_cffi MANCANTE · pip install curl_cffi",
    ff_note: "niente da regolare: non apre Chrome, non ha captcha",
    start: "Avvia", stop: "Stop", stopping: "Chiusura…",
    speed: "Velocita", completed: "Completati", downloaded: "Scaricato",
    pipeline: "Pipeline", extraction: "Estrazione", download_lane: "Download",
    files_tab: "Trasferimenti", log_tab: "Log", wipe: "Pulisci",
    empty_title: "nessun file in coda", empty_sub: "incolla i link, scegli la cartella, premi Avvia",
    IDLE: "FERMO", RUNNING: "IN CORSO", STOPPING: "CHIUSURA", DONE: "FATTO",
    st_queue: "in coda", st_extract: "estrazione", st_download: "download",
    st_ok: "salvato", st_fail: "errore", st_kill: "riavvio",
    chip_auto: "auto", chip_chrome: "chrome", chip_api: "api key",
    no_proxy: "no proxy", empty_proxies: "0 proxy validi trovati",
    filter_ph: "filtra", f_all: "Tutti", f_active: "Attivi", f_ok: "Salvati", f_fail: "Errori",
    filter_none: "nessuna corrispondenza", filter_none_sub: "azzera il filtro per rivedere il resto",
    drop_title: "trascina link o un .txt", drop_sub: "vengono aggiunti in fondo alla lista",
    help_title: "Scorciatoie",
    k_start: "Avvia, o ferma un batch in corso",
    k_open: "Carica un .txt di link",
    k_filter: "Filtra la lista dei trasferimenti",
    k_help: "Questo pannello",
    k_esc: "Chiudi, o azzera il filtro",
    copy_link: "Copia il link sorgente", copied: "link copiato", copy_failed: "copia non riuscita",
    sum_title: "Batch completato", sum_saved: "Salvati", sum_failed: "Errori",
    sum_bytes: "Scaricati", sum_elapsed: "Durata",
    sum_copy_failed: "Copia i link falliti",
    copied_n: (n) => `${n} link copiati`,
    links_n: (n) => `${n} link`,
    remaining: (n) => `${n} file rimanenti`,
    counts: (ok, ko, kill) => `${ok} ok · ${ko} ko · ${kill} kill`,
    proxy_n: (n) => `proxy ${n}`,
    tmp_n: (n) => `${n} .tmp da riprendere`,
    loaded_n: (n) => `${n} link caricati`,
    ph_idle: "in attesa",
    ph_extract: (ed, et, dd, dt, act) => `Estrazione [${ed}/${et}] + download [${dd}/${dt} fatti · ${act} attivi]`,
    ph_download: (dd, dt, act) => `Download [${dd}/${dt} fatti · ${act} attivi]`,
    ph_done: "finito",
    toast_no_links: "nessun link incollato",
    toast_no_folder: "scegli la cartella di destinazione",
  },
};

let LANG = "en";
const T = (key, ...args) => {
  const table = I18N[LANG] || I18N.en;
  const val = table[key] !== undefined ? table[key] : I18N.en[key];
  return typeof val === "function" ? val(...args) : (val === undefined ? key : val);
};

function applyLang(lang) {
  LANG = I18N[lang] ? lang : "en";
  document.documentElement.lang = LANG;
  for (const el of $$("[data-i18n]")) el.textContent = T(el.dataset.i18n);
  for (const el of $$("[data-i18n-ph]")) el.placeholder = T(el.dataset.i18nPh);
  for (const el of $$("[data-i18n-title]")) el.title = T(el.dataset.i18nTitle);
  for (const btn of $$("#lang button")) btn.classList.toggle("on", btn.dataset.lang === LANG);
  // Anything already rendered from live data has to be relabelled by hand.
  setRunState(ui.runState);
  editor.counts();
  syncDnChip();
  if (ui.lastMetrics) renderMetrics(ui.lastMetrics, true);
  if (ui.lastFiles) {
    // Row labels are only written when the state CHANGES, so a language switch
    // has to forget the previous states or every row keeps its old wording.
    ui.prevState.clear();
    renderFiles(ui.lastFiles);
  }
  if (ui.lastProxies != null) setProxies();
  if (ui.lastTmp != null) setTmp(ui.lastTmp);
}

/* ── formatting ───────────────────────────────────────────────────────── */
const fmtSpeed = (mbs) => (mbs >= 1 ? [mbs.toFixed(1), "MB/s"] : [(mbs * 1024).toFixed(0), "KB/s"]);
const fmtEta = (s) => {
  s = Math.max(0, Math.round(s));
  if (!s) return "—";
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  if (s >= 60) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${s}s`;
};
const fmtClock = (s) => `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
const hostOf = (url) => {
  const m = /^[a-z]+:\/\/([^/?#]+)/i.exec(url.trim());
  return m ? m[1].toLowerCase() : "";
};
const classify = (url) => {
  const h = hostOf(url);
  if (h.includes("datanodes.to")) return "dn";
  if (h.includes("fuckingfast.co")) return "ff";
  return "ot";
};
const escapeHtml = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

/* Middle ellipsis: these filenames differ only in the last few characters, so
   clipping the tail would make every row look identical. */
function elide(name, head = 34, tail = 22) {
  if (name.length <= head + tail + 1) return name;
  return name.slice(0, head) + "…" + name.slice(-tail);
}

/* Animated counter. The number is the thing the operator stares at; snapping it
   between two values hides how fast it is actually moving. */
function roll(el, to, fmt) {
  const from = Number(el.dataset.v || 0);
  if (!isFinite(to)) return;
  if (Math.abs(to - from) < 1e-4) { el.textContent = fmt(to); return; }
  cancelAnimationFrame(el._raf);
  const t0 = performance.now();
  const dur = 340;
  const tick = (now) => {
    const k = Math.min(1, (now - t0) / dur);
    const eased = 1 - Math.pow(1 - k, 3);
    el.textContent = fmt(from + (to - from) * eased);
    if (k < 1) el._raf = requestAnimationFrame(tick);
    else { el.dataset.v = String(to); el.textContent = fmt(to); }
  };
  el.dataset.v = String(to);
  el._raf = requestAnimationFrame(tick);
}

/* ── transports ───────────────────────────────────────────────────────── */
class HttpApi {
  constructor() { this.token = new URLSearchParams(location.search).get("t") || ""; }

  async _call(name, args = []) {
    try {
      const res = await fetch(`api/${name}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Moon-Token": this.token },
        body: JSON.stringify({ args }),
        cache: "no-store",
      });
      if (!res.ok) return { error: `HTTP ${res.status} on ${name}` };
      return await res.json();
    } catch (err) {
      return { error: `${name}: ${err}` };
    }
  }

  hello() { return this._call("hello"); }
  snapshot(cursor) { return this._call("snapshot", [cursor]); }
  start(cfg) { return this._call("start", [cfg]); }
  stop() { return this._call("stop"); }
  clear_files() { return this._call("clear_files"); }
  load_txt() { return this._call("load_txt"); }
  browse_folder() { return this._call("browse_folder"); }
  browse_chrome() { return this._call("browse_chrome"); }
  settings_save(cfg) { return this._call("settings_save", [cfg]); }
  settings_load() { return this._call("settings_load"); }
}

const bridge = {
  api: null,
  demo: false,

  async connect() {
    if (location.protocol === "http:" || location.protocol === "https:") {
      this.api = new HttpApi();
    } else if (window.pywebview && window.pywebview.api) {
      this.api = window.pywebview.api;
    } else {
      await new Promise((resolve) => {
        window.addEventListener("pywebviewready", () => resolve(true), { once: true });
        setTimeout(() => resolve(!!(window.pywebview && window.pywebview.api)), 700);
      });
      this.api = (window.pywebview && window.pywebview.api)
        ? window.pywebview.api : new MockApi();
    }
    this.demo = this.api instanceof MockApi;
    if (this.demo) $("#demoChip").hidden = false;
    return this.api;
  },
};

/* Refuse to pretend on a renderer that cannot draw this GUI. pywebview on
   Windows can silently fall back to MSHTML (IE11), where grid, clamp(),
   color-mix() and backdrop-filter do not exist and the page unrolls into a
   single unstyled column. A clear banner beats a broken app. */
function engineTooOld() {
  if (!window.CSS || !CSS.supports) return true;
  return !CSS.supports("display", "grid")
      || !CSS.supports("color", "color-mix(in srgb, red, blue)");
}

function showEngineWarning() {
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;inset:0;z-index:99;background:#05070c;color:#dfe8f6;"
    + "font:14px/1.7 Consolas,monospace;padding:34px;overflow:auto";
  box.innerHTML = "<b style='font-size:19px'>Rendering engine too old</b>"
    + "<p>This window is running on MSHTML (Internet Explorer), not Edge:"
    + " the GUI cannot be drawn.</p>"
    + "<p>Close it and launch <b>start.bat</b>, which opens the GUI in Edge/Chrome"
    + " with <code>--app</code> and does not depend on pywebview.</p>";
  document.body.appendChild(box);
}

/* ── UI state ─────────────────────────────────────────────────────────── */
const ui = {
  cursor: 0,
  rows: new Map(),
  prevState: new Map(),
  spark: [],
  runState: "idle",
  filter: "",
  fstate: "all",
  settingsTimer: null,
  lastMetrics: null,
  lastFiles: null,
  lastProxies: null,
  lastTmp: null,
};

/* ── link editor: highlighted overlay ─────────────────────────────────── */
const editor = {
  ta: null, paint: null, timer: null,

  init() {
    this.ta = $("#links");
    this.paint = $("#editorPaint");
    this.ta.addEventListener("input", () => this.changed());
    this.ta.addEventListener("scroll", () => this.sync());
    this.changed();
  },

  changed() {
    this.counts();
    clearTimeout(this.timer);
    // Repainting on every keystroke over a 400-line list is wasted work: the
    // counters update instantly, the colours land a beat later.
    this.timer = setTimeout(() => this.repaint(), 180);
    scheduleSettingsSave();
  },

  lines() { return this.ta.value.split("\n"); },
  links() { return this.lines().map((l) => l.trim()).filter(Boolean); },

  counts() {
    let dn = 0, ff = 0, ot = 0;
    for (const url of this.links()) {
      const k = classify(url);
      if (k === "dn") dn++; else if (k === "ff") ff++; else ot++;
    }
    const total = dn + ff + ot;
    $("#linkCount").textContent = T("links_n", total);
    $("#cntDn").textContent = dn;
    $("#cntFf").textContent = ff;
    $("#cntOther").textContent = ot;
    const pct = (n) => (total ? (n / total) * 100 : 0);
    $("#mixDn").style.width = pct(dn) + "%";
    $("#mixFf").style.width = pct(ff) + "%";
    $("#mixOther").style.width = pct(ot) + "%";
    for (const [sel, n] of [[".lg.dn", dn], [".lg.ff", ff], [".lg.ot", ot]]) {
      $(sel).classList.toggle("off", !n);
    }
  },

  repaint() {
    const lines = this.lines();
    if (lines.length > 1500) { this.paint.textContent = this.ta.value; return; }
    this.paint.innerHTML = lines
      .map((l) => (l.trim() ? `<span class="${classify(l)}">${escapeHtml(l)}</span>` : "&nbsp;"))
      .join("\n");
    this.sync();
  },

  sync() {
    this.paint.scrollTop = this.ta.scrollTop;
    this.paint.scrollLeft = this.ta.scrollLeft;
  },

  set(text) { this.ta.value = text; this.changed(); this.repaint(); },
};

/* ── sliders ──────────────────────────────────────────────────────────── */
function initSlider(id, outId, suffix = "") {
  const input = $("#" + id);
  const out = $("#" + outId);
  const label = input.closest(".slider");
  const rec = input.dataset.rec ? Number(input.dataset.rec) : null;

  const paint = () => {
    const min = Number(input.min), max = Number(input.max);
    const pct = ((Number(input.value) - min) / (max - min)) * 100;
    label.style.setProperty("--pct", pct.toFixed(2) + "%");
    out.textContent = input.value + suffix;
    if (rec !== null) {
      label.classList.add("has-rec");
      label.style.setProperty("--rec", (((rec - min) / (max - min)) * 100).toFixed(2) + "%");
    }
  };
  input.addEventListener("input", () => { paint(); scheduleSettingsSave(); });
  paint();
  return input;
}

/* ── run state ────────────────────────────────────────────────────────── */
function setRunState(state) {
  // applyLang() calls this with the state it already has, to relabel; comparing
  // first is what keeps that from re-firing the end-of-run panel.
  const prev = ui.runState;
  ui.runState = state;
  const pillKey = { idle: "IDLE", running: "RUNNING", stopping: "STOPPING", done: "DONE" }[state] || "IDLE";
  const btnKey = { idle: "start", running: "stop", stopping: "stopping", done: "start" }[state] || "start";
  $("#statePill").dataset.state = state;
  $("#stateLabel").textContent = T(pillKey);
  const b = $("#btnStart");
  b.dataset.state = state;
  $("#startLabel").textContent = T(btnKey);
  if (state === "done" && prev !== "done") showSummary();
}

function phaseText(m) {
  const parts = [];
  if (m.stage === "idle") parts.push(T("ph_idle"));
  else if (m.stage === "extracting") parts.push(T("ph_extract", m.extract_done, m.extract_total, m.dl_done, m.dl_total, m.active));
  else if (m.stage === "downloading") parts.push(T("ph_download", m.dl_done, m.dl_total, m.active));
  else parts.push(T("ph_done"));
  const gb = (m.bytes_total || 0) / 1e9;
  if (gb >= 0.01) parts.push(`${gb.toFixed(2)} GB`);
  if (m.elapsed_s > 0 && m.stage !== "idle") parts.push(fmtClock(m.elapsed_s));
  return parts.join("  ·  ");
}

/* ── metrics ──────────────────────────────────────────────────────────── */
function renderMetrics(m, relabelOnly = false) {
  ui.lastMetrics = m;
  const speed = m.speed_mbs || 0;
  const [, su] = fmtSpeed(speed);
  const vSpeed = $("#vSpeed");
  $("#uSpeed").textContent = su;
  roll(vSpeed, speed >= 1 ? speed : speed * 1024, (v) => (speed >= 1 ? v.toFixed(1) : v.toFixed(0)));
  $("#spark").closest(".card").classList.toggle("hot", speed > 0.05);

  roll($("#vDone"), m.dl_done || 0, (v) => String(Math.round(v)));
  $("#uDone").textContent = "/" + (m.dl_total || 0);
  setBar("#barDone", m.dl_done, m.dl_total, m.stage === "downloading" || m.stage === "extracting");
  // Scoped to its own readout, not to the card: speed and completed now share
  // one card, and two toggles of "hot" on the same element would fight.
  $("#vDone").closest(".hstat").classList.toggle("hot", (m.dl_done || 0) > 0);

  const gb = (m.bytes_total || 0) / 2 ** 30;
  roll($("#vBytes"), gb, (v) => (v >= 0.01 ? v.toFixed(2) : "0"));
  $("#subCounts").textContent = T("counts", m.ok || 0, m.fail || 0, m.kills || 0);

  $("#vEta").textContent = fmtEta(m.eta_s || 0);
  $("#subRemaining").textContent = T("remaining", Math.max(0, (m.dl_total || 0) - (m.dl_done || 0)));

  $("#phase").textContent = phaseText(m);
  $("#cntExtract").textContent = m.extract_total ? `${m.extract_done} / ${m.extract_total}` : "— / —";
  $("#cntDownload").textContent = m.dl_total ? `${m.dl_done} / ${m.dl_total}` : "— / —";
  setBar("#barExtract", m.extract_done, m.extract_total, m.stage === "extracting");
  setBar("#barDownload", m.dl_done, m.dl_total, m.stage === "downloading");
  setBar("#globalBar", m.dl_done, m.dl_total, false);

  if (!relabelOnly) { pushSpark(speed); setHeat(speed); }
}

/* Ambience intensity. Scaled against the peak of the run it would saturate and
   sit there, because a steady transfer is at its own peak almost all the time
   -- the glow never moved. An absolute reference with a square-root curve keeps
   the low end visible while leaving headroom, so the room brightens as the run
   ramps and dims when it stalls. Display only: the speed the engine reports is
   passed through untouched, this only decides how bright the room is. */
function setHeat(speed) {
  const heat = ui.runState === "running"
    ? Math.min(1, Math.sqrt(Math.max(0, speed) / HEAT_REF_MBS)) * 0.9
    : 0;
  document.documentElement.style.setProperty("--heat", heat.toFixed(3));
}

function setBar(sel, done, total, live = false) {
  const el = $(sel);
  const pct = total ? Math.min(100, (done / total) * 100) : 0;
  el.style.width = pct + "%";
  el.classList.toggle("on", pct > 0.4);
  el.classList.toggle("live", live && pct > 0.4 && pct < 99.9);
}

function pushSpark(v) {
  ui.spark.push(v);
  if (ui.spark.length > SPARK_N) ui.spark.shift();
  const pts = ui.spark;
  // An idle window still polls, so the buffer fills with zeroes and a rule keyed
  // only on sample count would draw itself across the whole card at the
  // baseline -- which is what it did, and it read as a stray hairline. The mean
  // is worth drawing when there is signal to average, not when there is a
  // window open.
  const live = pts.some((v) => v > 0.01);
  $("#sparkMean").style.visibility = live ? "visible" : "hidden";
  if (pts.length < 4 || !live) return;
  // 22% headroom: a flat series scaled to its own max fills the whole box and
  // reads as a solid block instead of a line.
  const peak = Math.max(...pts, 0.1) * 1.22;
  const step = 100 / (SPARK_N - 1);
  const off = SPARK_N - pts.length;
  const coords = pts.map((val, i) => [(off + i) * step, 29 - (val / peak) * 26]);
  const line = coords.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  $("#sparkLine").setAttribute("points", line);
  $("#sparkArea").setAttribute("points", `${line} 100,30 ${coords[0][0].toFixed(2)},30`);

  // Only across the samples that exist. Drawn from 0 it reads as a series that
  // is there before the run has produced anything.
  const mean = pts.reduce((a, b) => a + b, 0) / pts.length;
  const meanY = (29 - (mean / peak) * 26).toFixed(2);
  const meanLine = $("#sparkMean");
  meanLine.setAttribute("x1", coords[0][0].toFixed(2));
  meanLine.setAttribute("y1", meanY);
  meanLine.setAttribute("y2", meanY);

  // The head is an HTML dot over the plot, so it is placed in percentages of
  // the box rather than in viewBox units.
  const [hx, hy] = coords[coords.length - 1];
  const head = $("#sparkHead");
  head.style.setProperty("--hx", `${hx.toFixed(2)}%`);
  head.style.setProperty("--hy", `${((hy / 30) * 100).toFixed(2)}%`);
}

/* ── transfers ────────────────────────────────────────────────────────── */
/* Sorting rank: whatever is moving sits at the top. On a 124-file batch the
   finished tail would otherwise bury the four transfers actually in flight. */
const RANK = { download: 0, extract: 1, kill: 2, queue: 3, ok: 8, fail: 9 };
const STATE_KEY = {
  queue: "st_queue", extract: "st_extract", download: "st_download",
  ok: "st_ok", fail: "st_fail", kill: "st_kill",
};

function renderFiles(files) {
  ui.lastFiles = files;
  const seen = new Set();
  const list = $("#files");
  const total = files.length;

  files.forEach((f, i) => {
    seen.add(f.key);
    let row = ui.rows.get(f.key);
    if (!row) {
      row = $("#rowTpl").content.firstElementChild.cloneNode(true);
      row._name = row.querySelector(".fname");
      row._state = row.querySelector(".fstate em");
      row._pct = row.querySelector(".fstate u");
      row._speed = row.querySelector(".fspeed");
      row._ring = row.querySelector(".ring-fg");
      row._foot = row.querySelector(".foot > i");
      // Which host a file came from was nowhere on this list, and it is the
      // first thing you want when one provider starts failing and the other
      // does not. Read off the key, which IS the source URL, so no engine
      // field and no count is involved.
      const host = row.querySelector(".fhost");
      host.textContent = { dn: "datanodes", ff: "fuckingfast", ot: "other" }[classify(f.key)];
      host.dataset.host = classify(f.key);
      // ui.rows is keyed by the URL and a row is never reassigned to another
      // one, so closing over the key here stays correct for the row's life.
      row.querySelector(".frow-copy").addEventListener("click", (ev) => {
        ev.stopPropagation();
        copyText(f.key, T("copied"), ev.currentTarget);
      });
      ui.rows.set(f.key, row);
      list.appendChild(row);
    }

    const rank = RANK[f.state] !== undefined ? RANK[f.state] : 5;
    // Active: oldest first. Finished: newest first, so the last completion is
    // the one you see at the top of the tail.
    row.style.order = String(rank * 10000 + (rank >= 8 ? total - i : i));

    const was = ui.prevState.get(f.key);
    if (was !== f.state) {
      ui.prevState.set(f.key, f.state);
      row.dataset.state = f.state;
      row._state.textContent = T(STATE_KEY[f.state] || "st_queue");
      if (f.state === "ok" && was && was !== "ok") {
        row.classList.add("just-done");
        setTimeout(() => row.classList.remove("just-done"), 1000);
      }
    }

    const shown = elide(f.name);
    if (row._name.textContent !== shown) {
      row._name.textContent = shown;
      row._name.title = f.name;
    }

    const pct = f.pct == null ? null : Math.max(0, Math.min(1, f.pct));
    row._pct.textContent = pct != null && f.state === "download" ? `${(pct * 100).toFixed(0)}%` : "";
    if (f.state === "download" || f.state === "kill") {
      // Inline style, NOT setAttribute: a CSS declaration always beats an SVG
      // presentation attribute, so the stylesheet's `0 100` would win and every
      // ring would render empty.
      row._ring.style.strokeDasharray = `${((pct || 0) * 100).toFixed(1)} 100`;
      row._foot.style.width = ((pct || 0) * 100).toFixed(1) + "%";
    } else {
      row._ring.style.strokeDasharray = "";
    }

    const speed = f.mbs > 0.01 ? fmtSpeed(f.mbs).join(" ") : "";
    if (row._speed.textContent !== speed) row._speed.textContent = speed;
  });

  for (const [key, row] of ui.rows) {
    if (!seen.has(key)) { row.remove(); ui.rows.delete(key); ui.prevState.delete(key); }
  }

  // The badge counts what is IN FLIGHT, not how many rows are held. Showing the
  // row cap ("40") next to 124 completed files was simply a lie. It also counts
  // the whole batch, never the filtered subset: the filter is a way of looking
  // at the list, not a change to what is running.
  const active = files.filter((f) => f.state === "download" || f.state === "extract"
                                  || f.state === "kill" || f.state === "queue").length;
  $("#fileBadge").textContent = active || "";

  const shown = applyFilter(files);
  const filtering = ui.fstate !== "all" || ui.filter.trim() !== "";
  syncEmpty(filtering && files.length > 0 && shown === 0);
  $("#empty").hidden = shown > 0 || !$("#log").hidden;
}

/* ── transfer filter ──────────────────────────────────────────────────── */
const FSTATE_MATCH = {
  all: () => true,
  active: (s) => s === "download" || s === "extract" || s === "kill" || s === "queue",
  ok: (s) => s === "ok",
  fail: (s) => s === "fail",
};

/* Presentation only. These are the rows the engine sent, hidden or shown -- no
   count the engine owns is recomputed here, and nothing is dropped from the
   batch by narrowing the view. */
function applyFilter(files) {
  const q = ui.filter.trim().toLowerCase();
  const match = FSTATE_MATCH[ui.fstate] || FSTATE_MATCH.all;
  let shown = 0;
  for (const f of files) {
    const row = ui.rows.get(f.key);
    if (!row) continue;
    const visible = match(f.state) && (!q || f.name.toLowerCase().includes(q));
    row.hidden = !visible;
    if (visible) shown++;
  }
  return shown;
}

/* The empty panel says two different things. Rewriting data-i18n rather than
   the text alone is what lets a later language switch pick the right one. */
function syncEmpty(filtered) {
  const title = $("#empty p");
  const sub = $("#empty small");
  title.dataset.i18n = filtered ? "filter_none" : "empty_title";
  sub.dataset.i18n = filtered ? "filter_none_sub" : "empty_sub";
  title.textContent = T(title.dataset.i18n);
  sub.textContent = T(sub.dataset.i18n);
}

function setFilter(value) {
  ui.filter = value;
  $("#filter").value = value;
  if (ui.lastFiles) renderFiles(ui.lastFiles);
}

function appendLog(lines) {
  if (!lines || !lines.length) return;
  const log = $("#log");
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  const frag = document.createDocumentFragment();
  for (const [msg, tag] of lines) {
    const span = document.createElement("span");
    span.className = tag || "";
    span.textContent = msg + "\n";
    frag.appendChild(span);
  }
  log.appendChild(frag);
  while (log.childElementCount > 2000) log.removeChild(log.firstChild);
  if (atBottom) log.scrollTop = log.scrollHeight;
}

/* ── tabs ─────────────────────────────────────────────────────────────── */
function initTabs() {
  const tabs = $("#tabs");
  const line = $("#tabLine");
  const move = (btn) => {
    line.style.width = btn.offsetWidth + "px";
    line.style.transform = `translateX(${btn.offsetLeft}px)`;
  };
  tabs.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-tab]");
    if (!btn) return;
    const swap = () => {
      for (const b of tabs.querySelectorAll("button")) b.classList.toggle("on", b === btn);
      const isLog = btn.dataset.tab === "log";
      $("#log").hidden = !isLog;
      $("#files").hidden = isLog;
      $("#empty").hidden = isLog || ui.rows.size > 0;
    };
    // startViewTransition snapshots the document, so the swap has to happen
    // inside the callback. The underline is left outside it: it is a transform
    // that slides on its own, and captured mid-slide it would jump instead.
    if (typeof document.startViewTransition === "function" && !REDUCED.matches) {
      document.startViewTransition(swap);
    } else {
      swap();
    }
    move(btn);
  });
  requestAnimationFrame(() => move(tabs.querySelector("button.on")));
  window.addEventListener("resize", () => move(tabs.querySelector("button.on")));
}

/* ── chips ────────────────────────────────────────────────────────────── */
function setProxies(info) {
  // If called empty (language swap etc.), use last info
  if (!info) info = ui.lastProxyInfo;
  if (!info) return; // if we still have no info (eg. first millisecond of startup) stop here.
  ui.lastProxyInfo = info;

  const chip = $("#proxyChip");

  if (info.status === "none_configured") {
      chip.textContent = T("no_proxy");
      chip.className = "chip";
  } else if (info.status === "empty_file") {
      chip.textContent = T("empty_proxies");
      chip.className = "chip warn";
  } else if (info.status === "loaded") {
      chip.textContent = T("proxy_n", info.count);
      chip.className = "chip mint";
  }
}

function setTmp(n) {
  ui.lastTmp = n;
  const chip = $("#tmpChip");
  chip.hidden = !n;
  if (n) chip.textContent = T("tmp_n", n);
}

function syncDnChip() {
  const chip = $("#dnChip");
  if ($("#apiKey").value.trim()) { chip.textContent = T("chip_api"); chip.className = "chip mint"; }
  else if ($("#chromePath").value.trim()) { chip.textContent = T("chip_chrome"); chip.className = "chip teal"; }
  else { chip.textContent = T("chip_auto"); chip.className = "chip"; }
}

/* ── settings ─────────────────────────────────────────────────────────── */
function readConfig() {
  return {
    links: editor.links(),
    out_folder: $("#outFolder").value.trim(),
    mode: $("#modeSeg").querySelector("button.on").dataset.mode,
    workers: Number($("#workers").value),
    dl_streams: Number($("#streams").value),
    retries: Number($("#retries").value),
    dn_pages: Number($("#pages").value),
    dn_captcha: Number($("#captcha").value),
    dn_chrome: $("#chromePath").value.trim(),
    dn_apikey: $("#apiKey").value,
  };
}

function applyConfig(cfg) {
  if (!cfg) return;
  if (cfg.lang) applyLang(cfg.lang);
  if (cfg.links_text != null) editor.set(cfg.links_text);
  if (cfg.out_folder) $("#outFolder").value = cfg.out_folder;
  if (cfg.mode) setMode(cfg.mode);
  const set = (id, val) => {
    if (val == null) return;
    const el = $("#" + id);
    el.value = val;
    el.dispatchEvent(new Event("input"));
  };
  set("workers", cfg.workers);
  set("streams", cfg.dl_streams);
  set("retries", cfg.retries);
  set("pages", cfg.dn_pages);
  set("captcha", cfg.dn_captcha);
  if (cfg.dn_chrome != null) $("#chromePath").value = cfg.dn_chrome;
  if (cfg.dn_apikey != null) $("#apiKey").value = cfg.dn_apikey;
  syncDnChip();
}

function scheduleSettingsSave() {
  clearTimeout(ui.settingsTimer);
  ui.settingsTimer = setTimeout(async () => {
    const cfg = readConfig();
    delete cfg.links;
    cfg.links_text = editor.ta.value;
    cfg.lang = LANG;
    try { await bridge.api.settings_save(cfg); } catch (_e) { /* preview mode */ }
  }, 700);
}

function setMode(mode) {
  for (const b of $("#modeSeg").querySelectorAll("button")) b.classList.toggle("on", b.dataset.mode === mode);
}

/* ── toasts ───────────────────────────────────────────────────────────── */
function toast(msg, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 260); }, 4000);
}

/* ── cursor spotlight ─────────────────────────────────────────────────── */
function initSpotlight() {
  // One custom-property write per pointermove, no layout, no JS painting.
  document.addEventListener("pointermove", (ev) => {
    const card = ev.target.closest(".card");
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${ev.clientX - r.left}px`);
    card.style.setProperty("--my", `${ev.clientY - r.top}px`);
  }, { passive: true });
}

/* ── cold open ────────────────────────────────────────────────────────── */
/* Held just under the CSS timeline so the overlay leaves while the fill bar is
   still closing, instead of sitting there finished. */
const BOOT_MS = 1520;

function initBoot() {
  const boot = $("#boot");
  if (!boot) return;
  // Deliberately not gated on prefers-reduced-motion. Windows reports "reduce"
  // whenever its own animation setting is off, which is a display preference,
  // not a request to remove the product's launch sequence -- and skipping it
  // there is exactly why this looked like it never ran. Any key or click still
  // ends it instantly.
  const skip = () => endBoot(boot, false);
  // Any input ends it immediately. Nobody should sit through this twice.
  document.addEventListener("pointerdown", skip, { once: true });
  document.addEventListener("keydown", skip, { once: true });

  whenPainted(() => {
    if (boot.dataset.done) return;
    boot.classList.add("play");
    document.documentElement.classList.add("booted");
    setTimeout(skip, BOOT_MS);
  });
}

/* Two things have to be true before the sequence may start: the document is
   visible, and the compositor has produced a frame. moon_bridge launches the
   browser and hands it the URL, so the window can be mapped well after the
   document finished loading -- and a cold open that played into a window that
   was not on screen yet is the same as no cold open, which is how this first
   got reported. */
function whenPainted(run) {
  const go = () => requestAnimationFrame(() => requestAnimationFrame(run));
  if (document.visibilityState === "visible") { go(); return; }
  document.addEventListener("visibilitychange", function once() {
    if (document.visibilityState !== "visible") return;
    document.removeEventListener("visibilitychange", once);
    go();
  });
}

function endBoot(boot, instant) {
  if (boot.dataset.done) return;
  boot.dataset.done = "1";
  // Release the cascade and collapse its offset, so the cards are not still
  // waiting on an overlay that is already leaving.
  document.documentElement.classList.add("booted");
  document.documentElement.style.setProperty("--boot-delay", "0ms");
  if (instant) { boot.remove(); return; }
  boot.classList.add("out");
  setTimeout(() => boot.remove(), 440);
}

/* ── clipboard ────────────────────────────────────────────────────────── */
/* The GUI is served from 127.0.0.1, so the async clipboard API is available and
   is the path this normally takes. It still answers NotAllowedError when the
   write permission is refused -- group policy, or simply a page opened straight
   off disk to look at it -- so there is a fallback before giving up. */
async function copyText(text, okMsg, btn) {
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch (_e) {
    ok = execCopy(text);
  }
  if (!ok) { toast(T("copy_failed"), true); return; }
  toast(okMsg);
  if (btn) {
    btn.classList.add("done");
    setTimeout(() => btn.classList.remove("done"), 1200);
  }
}

/* Deprecated, and the only thing that works when the permission is denied. The
   textarea has to be in the document and selectable, so it is placed off-screen
   rather than hidden -- display:none cannot hold a selection. */
function execCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (_e) {
    ok = false;   // execCommand throws on engines that removed it entirely.
  }
  ta.remove();
  return ok;
}

/* ── run summary ──────────────────────────────────────────────────────── */
const failedLinks = () => (ui.lastFiles || []).filter((f) => f.state === "fail").map((f) => f.key);

/* Every figure here is read straight off the last snapshot. Nothing is counted
   a second time: whatever the engine reported is what this panel repeats. */
function showSummary() {
  const m = ui.lastMetrics || {};
  $("#sumOk").textContent = m.ok || 0;
  $("#sumFail").textContent = m.fail || 0;
  // Same conversion and the same label as the Downloaded card, deliberately:
  // one expression to find if the unit ever needs correcting.
  const gb = (m.bytes_total || 0) / 2 ** 30;
  $("#sumBytes").textContent = gb >= 0.01 ? `${gb.toFixed(2)} GB` : "0";
  $("#sumElapsed").textContent = m.elapsed_s > 0 ? fmtClock(m.elapsed_s) : "—";
  $("#btnCopyFailed").hidden = failedLinks().length === 0;
  $("#summary").hidden = false;
}

/* ── drag & drop ──────────────────────────────────────────────────────── */
/* The whole window is the target, not just the textarea: on a long batch the
   editor is usually scrolled somewhere else entirely. */
function initDropzone() {
  const zone = $("#dropzone");
  // dragenter and dragleave fire once per element the pointer crosses, so a
  // plain boolean makes the overlay flicker as it moves over the cards.
  let depth = 0;

  const carries = (dt) => dt && (dt.types.includes("Files") || dt.types.includes("text/plain"));

  document.addEventListener("dragenter", (ev) => {
    if (!carries(ev.dataTransfer)) return;
    ev.preventDefault();
    depth++;
    zone.hidden = false;
  });
  document.addEventListener("dragover", (ev) => { if (carries(ev.dataTransfer)) ev.preventDefault(); });
  document.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) zone.hidden = true;
  });
  document.addEventListener("drop", async (ev) => {
    if (!carries(ev.dataTransfer)) return;
    ev.preventDefault();
    depth = 0;
    zone.hidden = true;
    const before = editor.links().length;
    const text = await droppedText(ev.dataTransfer);
    if (!text.trim()) return;
    editor.set(mergeLinks(editor.ta.value, text));
    toast(T("loaded_n", Math.max(0, editor.links().length - before)));
    scheduleSettingsSave();
  });
}

/* getData() has to be read before the first await -- the dataTransfer is
   emptied once the drop event finishes dispatching. */
async function droppedText(dt) {
  const files = Array.from(dt.files || []).filter((f) => /\.txt$/i.test(f.name));
  if (!files.length) return dt.getData("text/plain") || "";
  const chunks = await Promise.all(files.map((f) => f.text()));
  return chunks.join("\n");
}

/* Appends rather than replaces. Load .txt is the control that replaces, and a
   drop that wiped a list someone had just pasted would not be undoable. */
function mergeLinks(current, added) {
  const base = current.replace(/\s+$/, "");
  return base ? `${base}\n${added.trim()}` : added.trim();
}

/* ── keyboard ─────────────────────────────────────────────────────────── */
const isTyping = (el) => !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");

function toggleHelp(show) {
  $("#help").hidden = !show;
}

function initKeys() {
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      if (!$("#help").hidden) toggleHelp(false);
      else if (!$("#summary").hidden) $("#summary").hidden = true;
      else if (document.activeElement === $("#filter")) { setFilter(""); $("#filter").blur(); }
      return;
    }
    if (ev.ctrlKey && ev.key === "Enter") { ev.preventDefault(); $("#btnStart").click(); return; }
    if (ev.ctrlKey && (ev.key === "o" || ev.key === "O")) { ev.preventDefault(); $("#btnLoadTxt").click(); return; }

    // Bare keys stay out of the way of every field on the page, or "/" could
    // never be typed into a link and "?" never into a folder name.
    if (isTyping(document.activeElement) || ev.ctrlKey || ev.altKey || ev.metaKey) return;
    if (ev.key === "/") { ev.preventDefault(); $("#filter").focus(); $("#filter").select(); }
    else if (ev.key === "?") { ev.preventDefault(); toggleHelp($("#help").hidden); }
  });
}

/* ── main loop ────────────────────────────────────────────────────────── */
async function poll() {
  try {
    const snap = await bridge.api.snapshot(ui.cursor);
    if (snap) {
      if (snap.state && snap.state !== ui.runState) setRunState(snap.state);
      if (snap.metrics) renderMetrics(snap.metrics);
      if (snap.files) renderFiles(snap.files);
      if (snap.log && snap.log.length) { appendLog(snap.log); ui.cursor = snap.cursor; }
      if (
        snap.proxy_info &&
        (
          snap.proxy_info.status !== ui.lastProxyStatus ||
          snap.proxies !== ui.lastProxies ||
          LANG !== ui.lastLang
        )
      ) {
        ui.lastProxyStatus = snap.proxy_info.status;
        ui.lastProxies = snap.proxies;
        ui.lastLang = LANG;
        setProxies(snap.proxy_info);
      }
      if (snap.tmp != null && snap.tmp !== ui.lastTmp) setTmp(snap.tmp);
      if (snap.error) toast(snap.error, true);
    }
  } catch (_err) {
    /* A dropped snapshot is not worth a toast; the next tick recovers. */
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

/* ── wiring ───────────────────────────────────────────────────────────── */
async function boot() {
  // First, and outside the engine check: the overlay retires itself on its own
  // timer either way, so the too-old warning is never left behind it.
  initBoot();
  if (engineTooOld()) { showEngineWarning(); return; }

  editor.init();
  initSlider("workers", "outWorkers");
  initSlider("streams", "outStreams");
  initSlider("retries", "outRetries");
  initSlider("pages", "outPages");
  initSlider("captcha", "outCaptcha", "s");
  initTabs();
  initSpotlight();
  initDropzone();
  initKeys();
  applyLang("en");

  $("#filter").addEventListener("input", (ev) => setFilter(ev.target.value));

  $("#fchips").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-fstate]");
    if (!btn) return;
    ui.fstate = btn.dataset.fstate;
    for (const b of $$("#fchips button")) b.classList.toggle("on", b === btn);
    if (ui.lastFiles) renderFiles(ui.lastFiles);
  });

  $("#btnHelpClose").addEventListener("click", () => toggleHelp(false));
  // Clicking the backdrop, not the card, closes it.
  $("#help").addEventListener("click", (ev) => { if (ev.target === $("#help")) toggleHelp(false); });

  $("#btnSumClose").addEventListener("click", () => { $("#summary").hidden = true; });
  $("#summary").addEventListener("click", (ev) => { if (ev.target === $("#summary")) $("#summary").hidden = true; });
  $("#btnCopyFailed").addEventListener("click", (ev) => {
    const links = failedLinks();
    copyText(links.join("\n"), T("copied_n", links.length), ev.currentTarget);
  });

  $("#lang").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-lang]");
    if (!btn) return;
    applyLang(btn.dataset.lang);
    scheduleSettingsSave();
  });

  $("#modeSeg").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-mode]");
    if (btn) { setMode(btn.dataset.mode); scheduleSettingsSave(); }
  });
  for (const id of ["#chromePath", "#apiKey"]) {
    $(id).addEventListener("input", () => { syncDnChip(); scheduleSettingsSave(); });
  }
  $("#outFolder").addEventListener("input", scheduleSettingsSave);

  await bridge.connect();

  $("#btnStart").addEventListener("click", async () => {
    if (ui.runState === "running") { setRunState("stopping"); await bridge.api.stop(); return; }
    const cfg = readConfig();
    if (!cfg.links.length) { toast(T("toast_no_links"), true); return; }
    if (!cfg.out_folder) { toast(T("toast_no_folder"), true); return; }
    ui.spark = [];
    ui.cursor = 0;
    $("#log").textContent = "";
    setRunState("running");
    const res = await bridge.api.start(cfg);
    if (res && res.error) { toast(res.error, true); setRunState("idle"); }
  });

  $("#btnLoadTxt").addEventListener("click", async () => {
    const res = await bridge.api.load_txt();
    if (res && res.text != null) { editor.set(res.text); toast(T("loaded_n", res.count)); }
    else if (res && res.error) toast(res.error, true);
  });

  $("#btnClear").addEventListener("click", () => editor.set(""));

  $("#btnFolder").addEventListener("click", async () => {
    const res = await bridge.api.browse_folder();
    if (res && res.path) { $("#outFolder").value = res.path; scheduleSettingsSave(); }
  });

  $("#btnChrome").addEventListener("click", async () => {
    const res = await bridge.api.browse_chrome();
    if (res && res.path) { $("#chromePath").value = res.path; syncDnChip(); scheduleSettingsSave(); }
  });

  $("#btnWipe").addEventListener("click", async () => {
    if ($("#log").hidden) {
      await bridge.api.clear_files();
      for (const [, r] of ui.rows) r.remove();
      ui.rows.clear();
      ui.prevState.clear();
      renderFiles([]);
    } else {
      $("#log").textContent = "";
    }
  });

  try {
    const info = await bridge.api.hello();
    if (info) {
      $("#version").textContent = info.version || "v3.0";
      if (info.have_curl === false) {
        const line = $("#curlLine");
        line.dataset.i18n = "ff_missing";
        line.textContent = T("ff_missing");
        line.classList.add("bad");
      }
      applyConfig(info.settings);
    }
  } catch (_e) { /* preview mode */ }

  setRunState("idle");
  poll();
}

/* ════════════════════════════════════════════════════════════════════════
   MockApi — synthetic engine for previewing the page without Python.
   Same shape as the real bridge, so the render path under test ships.
   ════════════════════════════════════════════════════════════════════════ */
class MockApi {
  constructor() {
    this.names = Array.from({ length: 30 }, (_, i) =>
      `sample-archive.part${String(i + 1).padStart(2, "0")}.rar`);
    this.total = 85;
    this.done = 2; this.ok = 2; this.fail = 1; this.kills = 1;
    this.extracted = 6;
    this.bytes = 2.6 * 2 ** 30;
    this.log = [
      ["▶  85 links  ·  16 extractors  ·  8 streams  ·  3 retries  ·  v3.0", "info"],
      ["   fuckingfast: direct HTTP   ·   datanodes: 8 pages, captcha 30s", "dim"],
      ["   chrome: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "dim"],
      ["   proxies: 12 loaded — rotating per download", "info"],
      ["  → sample-archive.part01.rar", "dim"],
      ["    ✓  Saved: sample-archive.part01.rar  (12.4 MB/s)", "ok"],
      ["    ⚡  Kill #1: sample-archive.part07.rar  (612MB) → re-extract", "kill"],
      ["    ✗  sample-archive.part06.rar: HTTP 403", "fail"],
    ];
    this.live = [
      { key: "https://datanodes.to/x1/part01.rar", i: 0, state: "ok", pct: 1, mbs: 12.4 },
      { key: "https://fuckingfast.co/x2/part02.rar", i: 1, state: "download", pct: 0.62, mbs: 11.1 },
      { key: "https://datanodes.to/x3/part03.rar", i: 2, state: "download", pct: 0.31, mbs: 8.9 },
      { key: "https://fuckingfast.co/x4/part04.rar", i: 3, state: "extract", pct: null, mbs: 0 },
      { key: "https://datanodes.to/x5/part05.rar", i: 4, state: "download", pct: 0.08, mbs: 4.2 },
      { key: "https://fuckingfast.co/x6/part06.rar", i: 5, state: "fail", pct: null, mbs: 0 },
      { key: "https://datanodes.to/x7/part07.rar", i: 6, state: "kill", pct: 0.44, mbs: 0.3 },
      { key: "https://fuckingfast.co/x8/part08.rar", i: 7, state: "queue", pct: null, mbs: 0 },
    ];
    this.seq = 8;
    this.t0 = Date.now();
    this.state = "running";
    setInterval(() => this.step(), 260);
  }

  step() {
    if (this.state !== "running") return;
    for (const f of this.live) {
      if (f.state !== "download") continue;
      f.pct = Math.min(1, f.pct + f.mbs / 900);
      this.bytes += f.mbs * 0.26 * 2 ** 20;
      if (f.pct >= 1) {
        f.state = "ok"; this.done++; this.ok++;
        this.log.push([`    ✓  Saved: ${this.names[f.i]}  (${f.mbs.toFixed(1)} MB/s)`, "ok"]);
      }
    }
    if (this.live.filter((f) => f.state === "download").length < 4 && this.extracted < this.total) {
      this.extracted++;
      this.seq++;
      const i = this.seq % this.names.length;
      this.live.push({ key: `https://${this.seq % 2 ? "datanodes.to" : "fuckingfast.co"}/x${this.seq}/part.rar`, i, state: "download", pct: 0.01, mbs: 3 + Math.random() * 11 });
      this.log.push([`  → ${this.names[i]}`, "dim"]);
    }
    while (this.live.length > 26) this.live.shift();
  }

  async hello() {
    return { version: "v3.0 · preview", have_curl: true, settings: {
      out_folder: "D:\\downloads", mode: "download", workers: 16, dl_streams: 8, retries: 3,
      dn_pages: 8, dn_captcha: 30,
      dn_chrome: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
      dn_apikey: "", links_text: Array.from({ length: 12 }, (_, n) =>
        `https://${n % 3 ? "datanodes.to" : "fuckingfast.co"}/${(n + 1).toString(36).padStart(6, "0")}`).join("\n"),
    } };
  }

  async snapshot(cursor) {
    const active = this.live.filter((f) => f.state === "download" || f.state === "extract").length;
    const speed = this.live.filter((f) => f.state === "download").reduce((a, f) => a + f.mbs, 0);
    return {
      state: this.state,
      metrics: {
        speed_mbs: speed, dl_done: this.done, dl_total: this.total, ok: this.ok,
        fail: this.fail, kills: this.kills, bytes_total: this.bytes,
        eta_s: speed > 0 ? ((this.total - this.done) * 1.4e9) / (speed * 2 ** 20) : 0,
        extract_done: this.extracted, extract_total: this.total, active,
        stage: this.extracted < this.total ? "extracting" : "downloading",
        elapsed_s: (Date.now() - this.t0) / 1000,
      },
      files: this.live.map((f) => ({ key: f.key, name: this.names[f.i], state: f.state, mbs: f.mbs, pct: f.pct })),
      log: this.log.slice(cursor),
      cursor: this.log.length,
      proxies: 12,
      tmp: 3,
    };
  }

  async start(cfg) { this.state = "running"; this.log.push([`▶  ${cfg.links.length} links  ·  ${cfg.workers} extractors`, "info"]); return {}; }
  async stop() { this.state = "idle"; return {}; }
  async clear_files() { this.live = this.live.filter((f) => f.state !== "ok" && f.state !== "fail"); return {}; }
  async load_txt() { return { error: "preview: no Python bridge" }; }
  async browse_folder() { return {}; }
  async browse_chrome() { return {}; }
  async settings_save() { return {}; }
  async settings_load() { return {}; }
}

window.addEventListener("DOMContentLoaded", boot);
