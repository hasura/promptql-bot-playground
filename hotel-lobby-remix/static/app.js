/* Hotel Lobby Remix — one clip, two photos, one button. */
const $ = (s, r = document) => r.querySelector(s);
const state = { me: null, clip: null, face1: null, face2: null, jobs: [], busy: false, hf: "unknown" };
let PRICE = 0.681;

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let j = null; try { j = await r.json(); } catch (_) {}
  if (!r.ok) { const e = (j && j.error) || {}; throw Object.assign(new Error(e.message || r.statusText), { kind: e.kind, status: r.status }); }
  return j;
}
const fmtDur = s => `${Math.round(s)} s`;
const fmtUsd = d => `≈ $${(Math.ceil(Math.min(d, 30)) * PRICE).toFixed(2)}`;
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- boot ---------- */
async function boot() {
  try { state.me = await api("/api/me"); if (state.me.price_per_sec) PRICE = state.me.price_per_sec; } catch (_) { state.me = { anonymous: true }; }
  if (state.me.provider_label) $("#hf-provider-label").textContent = state.me.provider_label;
  renderWho();
  try { const s = await api("/samples/samples.json"); state.clip = s[0] || null; } catch (_) { state.clip = null; }
  renderClip();
  wireFaces();
  $("#consent").addEventListener("change", update);
  $("#generate").addEventListener("click", generate);
  $("#hf-recheck").addEventListener("click", e => { e.preventDefault(); checkHF(); });
  update();
  if (!state.me.anonymous) { checkHF(); await loadJobs(); setInterval(pollJobs, 15000); } else setPill("anon");
}

function renderWho() {
  const w = $("#who");
  w.hidden = !state.me.anonymous;
  w.innerHTML = state.me.anonymous ? `<span class="dot off"></span>Open this app from PromptQL to generate.` : "";
}

/* ---------- Higgsfield connection ---------- */
const PILLS = {
  checking: ["checking", "Checking connection…"],
  connected: ["on", "Higgsfield connected — you're set"],
  not_connected: ["off", "Higgsfield not connected yet — follow the steps"],
  consent: ["warn", "Connected, but this app isn't approved yet — reopen it and accept the prompt"],
  unknown: ["warn", "Couldn't confirm the connection — you can still try Generate"],
  misconfigured: ["warn", "This app's HF_PROVIDER doesn't exist in this project — the app owner needs to fix .env"],
  anon: ["off", "Open this app from PromptQL to connect"],
};
function setPill(k) {
  state.hf = k; const [cls, text] = PILLS[k] || PILLS.unknown;
  const p = $("#hf-pill"); p.className = "pill " + cls; $("#hf-pill-text").textContent = text;
  $("#setup").classList.toggle("done", k === "connected");
}
async function checkHF() {
  setPill("checking");
  try { const r = await api("/api/hf-status"); setPill(r.state); } catch (_) { setPill("unknown"); }
}

/* ---------- step 1 · the clip (fixed) ---------- */
function renderClip() {
  const g = $("#samples"); g.innerHTML = "";
  const s = state.clip; if (!s) { g.textContent = "Clip missing on the server."; return; }
  const b = document.createElement("div"); b.className = "tile selected clip";
  b.innerHTML = `<span class="rank">01</span>
    <img class="thumb" src="${esc(s.poster)}" alt="">
    <div class="meta"><div class="title">${esc(s.title)}</div><div class="tag">${fmtDur(s.duration)} · ${esc(s.tagline || "")}</div><div class="credit">${esc(s.credit || "")}</div></div>`;
  b.addEventListener("mouseenter", () => previewOn(b, s));
  b.addEventListener("mouseleave", () => previewOff(b, s));
  g.appendChild(b);
  $("#step-video").classList.add("done");
}
function previewOn(b, s) {
  if (b.querySelector("video")) return;
  const v = document.createElement("video"); v.className = "thumb"; v.src = s.file; v.muted = true; v.loop = true; v.playsInline = true; v.preload = "metadata";
  const img = b.querySelector("img.thumb"); img.replaceWith(v); v.play().catch(() => {});
}
function previewOff(b, s) {
  const v = b.querySelector("video.thumb"); if (!v) return;
  const img = document.createElement("img"); img.className = "thumb"; img.src = s.poster; v.replaceWith(img);
}

/* ---------- steps 2 & 3 · faces ---------- */
function wireFaces() {
  document.querySelectorAll(".drop").forEach(d => {
    const input = d.querySelector("input");
    input.addEventListener("change", () => uploadFace(input.files[0], d, input.dataset.slot));
    ["dragenter", "dragover"].forEach(ev => d.addEventListener(ev, e => { e.preventDefault(); d.classList.add("over"); }));
    ["dragleave", "drop"].forEach(ev => d.addEventListener(ev, e => { e.preventDefault(); d.classList.remove("over"); }));
    d.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) uploadFace(f, d, input.dataset.slot); });
  });
}
async function uploadFace(file, drop, slot) {
  if (!file) return;
  if (state.me.anonymous) return notice("Open this app from PromptQL to add faces.", true);
  const cta = drop.querySelector(".cta"); cta.textContent = "Uploading…";
  try {
    const r = await api(`/api/upload?kind=face&name=${encodeURIComponent(file.name)}`, { method: "PUT", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } });
    const img = drop.querySelector("img"); img.src = URL.createObjectURL(file); img.hidden = false;
    drop.classList.add("has"); cta.textContent = "Change photo";
    state[slot] = r.id; $(`#step-${slot}`).classList.add("done");
  } catch (e) { cta.textContent = "Add a photo"; notice(e.message, true); }
  update();
}

/* ---------- step 4 · generate ---------- */
function update() {
  const line = $("#summary-line"), btn = $("#generate");
  const ok = state.clip && state.face1 && state.face2;
  if (state.clip) {
    const secs = Math.min(state.clip.duration, 30);
    line.innerHTML = `<b>${esc(state.clip.title)}</b> <span class="mono">· ${fmtDur(secs)} · 720p · ${fmtUsd(secs)} · ~${Math.max(4, Math.round(secs * 0.65))} min</span>`;
  } else line.textContent = "Add two faces.";
  const missing = [!state.face1 && "face 1", !state.face2 && "face 2"].filter(Boolean);
  btn.title = missing.length ? `Still need ${missing.join(" and ")}` : "";
  btn.disabled = state.busy || !ok || !$("#consent").checked || state.me.anonymous;
}
function notice(msg, warn, detail) {
  const n = $("#notice"); n.hidden = !msg; n.className = "notice" + (warn ? " warn" : "");
  n.innerHTML = esc(msg) + (detail ? `<br><span class="mono">${esc(detail)}</span>` : "");
}
async function generate() {
  if (state.busy) return;
  state.busy = true; const btn = $("#generate"); btn.classList.add("busy"); btn.textContent = "Sending to Higgsfield…"; update(); notice("");
  try {
    const job = await api("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video: { sample: state.clip.id }, face1: state.face1, face2: state.face2, consent: true }) });
    state.jobs.unshift(job); renderJobs(); setPill("connected");
    notice(`Submitted — ${fmtUsd(job.duration)} on your Higgsfield account. It'll appear below when it's done; you can leave this page.`);
    $("#renders").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    if (e.kind === "not_connected") { setPill("not_connected"); notice("Higgsfield isn't connected to your account yet — follow the three steps at the top, then press Generate again.", true, e.message); $("#setup").scrollIntoView({ behavior: "smooth", block: "start" }); }
    else if (e.kind === "consent" || e.status === 403) { setPill("consent"); notice("This app isn't approved to use your Higgsfield connection yet. Reopen it, accept the permission prompt, then try again.", true, e.message); }
    else notice("Higgsfield didn't accept the render. Nothing was charged.", true, e.message);
  } finally { state.busy = false; btn.classList.remove("busy"); btn.textContent = "Generate"; update(); }
}

/* ---------- renders ---------- */
async function loadJobs() { try { state.jobs = await api("/api/jobs"); } catch (_) { state.jobs = []; } renderJobs(); }
async function pollJobs() {
  const live = state.jobs.filter(j => ["queued", "in_progress", "finalizing"].includes(j.state));
  for (const j of live) { try { const u = await api(`/api/jobs/${j.id}`); Object.assign(j, u); } catch (_) {} }
  if (live.length) renderJobs();
}
const STAGES = [["queued", "Submitted"], ["in_progress", "Rendering"], ["finalizing", "Finishing audio"], ["done", "Done"]];
function renderJobs() {
  const box = $("#jobs"), sec = $("#renders"); sec.hidden = !state.jobs.length; box.innerHTML = "";
  state.jobs.forEach(j => {
    const idx = STAGES.findIndex(s => s[0] === j.state);
    const el = document.createElement("div"); el.className = "job";
    const when = new Date(j.created_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    let stages = STAGES.map(([k, label], i) => `<span class="${j.state === "failed" ? "" : i < idx ? "on" : i === idx ? (k === "done" ? "on" : "live") : ""}">${label}</span>`).join("<i>→</i>");
    if (j.state === "failed") stages = `<span class="live" style="border-color:#9c3a1c;color:#9c3a1c">Failed</span>`;
    el.innerHTML = `<div><div class="label">${esc(j.video_label)}</div><div class="line">${when} · ${fmtDur(Math.min(j.duration, 30))} · 720p · ≈ $${Number(j.est_usd).toFixed(2)}</div><div class="stages">${stages}</div></div>
      <div>${j.output ? `<a class="dl" href="${j.output}" download="hotel-lobby-${j.id}.mp4">DOWNLOAD MP4 ↓</a>` : `<span class="line">${j.state === "failed" ? "" : "about " + Math.max(1, Math.round(Math.min(j.duration, 30) * 0.65)) + " min"}</span>`}</div>
      ${j.output ? `<video controls playsinline preload="metadata" src="${j.output}"></video>` : ""}
      ${j.state === "failed" && j.error ? `<div class="err">${esc(j.error)}</div>` : ""}`;
    box.appendChild(el);
  });
}

boot();