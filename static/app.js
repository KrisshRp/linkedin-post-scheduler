// Dashboard: plain fetch + DOM. All user-supplied text goes through textContent (no XSS).
const $ = (id) => document.getElementById(id);
const openLogs = new Set();
const files = { images: [], doc: null }; // uploaded attachments: {id, name, alt, url}
let tab = "p-img";
let timer = null;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401) { showLogin(); throw new Error("login"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed (" + r.status + ")");
  return data;
}

const fmtTime = (iso) => new Date(iso).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function el(tag, props = {}, ...kids) {
  const e = Object.assign(document.createElement(tag), props);
  kids.forEach((k) => e.append(k));
  return e;
}

/* ---------- theme ---------- */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  $("themeBtn").textContent = "Theme: " + (t === "dim" ? "Dim" : "Light");
  try { localStorage.setItem("theme", t); } catch (e) { /* ignore */ }
}
$("themeBtn").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dim" ? "light" : "dim"));

/* ---------- boot ---------- */
async function init() {
  applyTheme(document.documentElement.dataset.theme === "dim" ? "dim" : "light");
  const h = await fetch("/healthz").then((r) => r.json());
  $("banner").hidden = h.publish_mode !== "dry_run";
  $("devLogin").hidden = !h.dev_login;
  $("tz").textContent = Intl.DateTimeFormat().resolvedOptions().timeZone;
  try { await showApp(); } catch (_) { /* showLogin already ran */ }
}

function showLogin() { $("login").hidden = false; $("app").hidden = true; clearInterval(timer); }

async function showApp() {
  const me = await api("/api/me");
  $("login").hidden = true; $("app").hidden = false;
  $("name").textContent = me.name || "LinkedIn user";
  $("token").textContent = me.token_days_left != null ? "Connected · token valid " + Math.floor(me.token_days_left) + " days" : "Connected";
  const av = $("avatar");
  av.replaceChildren();
  if (me.picture) av.append(el("img", { src: me.picture, alt: "" }));
  else av.textContent = (me.name || "?").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  setDefaultWhen();
  await refreshAll();
  clearInterval(timer);
  timer = setInterval(() => refreshAll().catch(() => {}), 5000);
}

function setDefaultWhen() {
  const d = new Date(Date.now() + 2 * 60000);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  if (!$("when").value) $("when").value = d.toISOString().slice(0, 16);
}

const refreshAll = () => Promise.all([refreshStats(), refreshPosts()]);

/* ---------- stats ---------- */
async function refreshStats() {
  const s = await api("/api/stats");
  $("sScheduled").textContent = s.counts.scheduled + s.counts.processing;
  $("sPublished").textContent = s.counts.published;
  $("sFailed").textContent = s.counts.failed;
  $("sRate").textContent = s.success_rate == null ? "-" : s.success_rate + "%";
  const max = Math.max(1, ...s.daily.map((d) => d.published + d.failed));
  const chart = $("chart");
  chart.replaceChildren();
  s.daily.forEach((d) => {
    const col = el("div", { className: "col", title: d.date + ": " + d.published + " published, " + d.failed + " failed" });
    if (d.failed) col.append(el("div", { className: "f", style: "height:" + (d.failed / max) * 100 + "%" }));
    if (d.published) col.append(el("div", { className: "p", style: "height:" + (d.published / max) * 100 + "%" }));
    if (!d.published && !d.failed) col.append(el("div", { className: "z" }));
    chart.append(col);
  });
  const short = (iso) => new Date(iso + "T00:00:00").toLocaleDateString([], { day: "numeric", month: "short" });
  $("axisFrom").textContent = short(s.daily[0].date);
  $("axisTo").textContent = short(s.daily[s.daily.length - 1].date);
  const rows = [
    ["First-try success", s.first_try_rate == null ? "-" : s.first_try_rate + "%"],
    ["Avg. attempts per post", s.avg_attempts == null ? "-" : s.avg_attempts],
    ["Avg. publish delay", s.avg_publish_delay_seconds == null ? "-" : s.avg_publish_delay_seconds + " s"],
    ["Top failure", s.failure_reasons.length ? s.failure_reasons[0].reason.replace(/^LinkedIn \d+: /, "").slice(0, 34) + " (" + s.failure_reasons[0].n + ")" : "None"],
  ];
  const ul = $("reliab");
  ul.replaceChildren();
  rows.forEach(([k, v]) => ul.append(el("li", {}, k, el("span", { textContent: String(v) }))));
}

/* ---------- posts table ---------- */
function chips(p) {
  const out = [];
  const imgs = p.media.filter((m) => m.kind === "image").length;
  if (imgs) out.push(imgs + (imgs === 1 ? " image" : " images"));
  if (p.media.some((m) => m.kind === "document")) out.push("PDF");
  if (p.kind === "poll") out.push("Poll · " + p.poll_duration + (p.poll_duration === 1 ? " day" : " days"));
  if (p.kind === "article") out.push("Link");
  if (p.visibility === "CONNECTIONS") out.push("Connections");
  return out;
}

async function refreshPosts() {
  const posts = await api("/api/posts");
  $("empty").hidden = posts.length > 0;
  const body = $("rows");
  const frag = document.createDocumentFragment();
  for (const p of posts) {
    const retrying = p.status === "scheduled" && p.attempts > 0;
    const label = retrying ? "Retrying" : p.status[0].toUpperCase() + p.status.slice(1);
    const stCell = el("td", {}, el("span", { className: "st " + (retrying ? "retrying" : p.status) }, el("i"), label));
    if (retrying) stCell.append(el("div", { className: "sub", textContent: "next try " + new Date(p.next_attempt_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }));
    const txt = el("td", { className: "txt" });
    txt.append(p.content.length > 160 ? p.content.slice(0, 160) + "…" : p.content);
    if (p.kind === "poll") txt.append(el("div", { className: "sub", textContent: "Poll: " + p.poll_question }));
    txt.append(el("br"));
    chips(p).forEach((c) => txt.append(el("span", { className: "chip", textContent: c })));
    if (p.status === "failed" && p.last_error) txt.append(el("div", { className: "err", textContent: p.last_error }));
    const acts = el("td");
    if (p.status === "published" && p.linkedin_post_urn && !p.linkedin_post_urn.includes("dryrun"))
      acts.append(el("a", { className: "btn sm", target: "_blank", rel: "noopener", textContent: "View on LinkedIn",
        href: "https://www.linkedin.com/feed/update/" + encodeURIComponent(p.linkedin_post_urn) }), " ");
    if (p.status === "scheduled" && p.attempts === 0) acts.append(el("button", { className: "btn sm", textContent: "Cancel", onclick: () => act("/api/posts/" + p.id, "DELETE") }), " ");
    if (p.status === "failed") acts.append(el("button", { className: "btn sm", textContent: "Retry", onclick: () => act("/api/posts/" + p.id + "/retry", "POST") }), " ");
    acts.append(el("button", { className: "btn sm", textContent: openLogs.has(p.id) ? "Hide log" : "Log", onclick: () => { openLogs.has(p.id) ? openLogs.delete(p.id) : openLogs.add(p.id); refreshPosts(); } }));
    frag.append(el("tr", {}, stCell, txt, el("td", { textContent: fmtTime(p.scheduled_at) }), el("td", { textContent: p.attempts + " / " + p.max_attempts }), acts));
    if (openLogs.has(p.id)) {
      const ev = await api("/api/posts/" + p.id + "/events");
      const box = el("div", { className: "log" });
      ev.forEach((e) => box.append(el("div", { className: e.level }, fmtTime(e.at) + "  ", el("b", { textContent: e.event }), "  " + e.detail)));
      frag.append(el("tr", {}, el("td", { colSpan: 5, style: "padding:0" }, box)));
    }
  }
  body.replaceChildren(frag);
}

async function act(path, method) {
  try { await api(path, { method }); await refreshAll(); } catch (e) { msg(e.message, true); }
}

/* ---------- composer ---------- */
function msg(text, err) { const m = $("formMsg"); m.textContent = text; m.className = err ? "err" : "ok"; }

$("content").addEventListener("input", () => { $("count").textContent = $("content").value.length + " / 3000"; });
$("tabs").addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if (!t) return;
  tab = t.dataset.p;
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === t));
  document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("on", p.id === tab));
});

async function uploadFile(file) {
  const r = await fetch("/api/media?name=" + encodeURIComponent(file.name), { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(file.name + ": " + (data.detail || "upload failed"));
  return { id: data.id, name: data.name, alt: "", url: URL.createObjectURL(file) };
}

function renderFiles() {
  const il = $("imgList");
  il.replaceChildren();
  files.images.forEach((f, i) => {
    il.append(el("div", { className: "file" },
      el("img", { className: "th", src: f.url, alt: "" }),
      el("input", { placeholder: "Alt text for " + f.name + " (optional)", value: f.alt, maxLength: 400, oninput: (e) => { f.alt = e.target.value; } }),
      el("button", { className: "btn sm", type: "button", textContent: "Remove", onclick: () => removeFile("images", i) })));
  });
  const dl = $("docList");
  dl.replaceChildren();
  if (files.doc) dl.append(el("div", { className: "file" }, el("div", { className: "th", textContent: "PDF" }), el("div", { className: "nm", textContent: files.doc.name }),
    el("button", { className: "btn sm", type: "button", textContent: "Remove", onclick: () => removeFile("doc") })));
}

function removeFile(kind, i) {
  const f = kind === "doc" ? files.doc : files.images[i];
  fetch("/api/media/" + f.id, { method: "DELETE" }).catch(() => {});
  if (kind === "doc") files.doc = null; else files.images.splice(i, 1);
  renderFiles();
}

async function addFiles(list, kind) {
  try {
    for (const f of list) {
      if (kind === "images" && files.images.length >= 9) { msg("Maximum 9 images.", true); break; }
      const up = await uploadFile(f);
      if (kind === "doc") { if (files.doc) removeFile("doc"); files.doc = up; } else files.images.push(up);
    }
    renderFiles();
  } catch (e) { msg(e.message, true); }
}

function wireDrop(zoneId, inputId, kind) {
  const z = $(zoneId), inp = $(inputId);
  z.addEventListener("click", () => inp.click());
  inp.addEventListener("change", () => { addFiles([...inp.files], kind); inp.value = ""; });
  z.addEventListener("dragover", (e) => { e.preventDefault(); z.classList.add("over"); });
  z.addEventListener("dragleave", () => z.classList.remove("over"));
  z.addEventListener("drop", (e) => { e.preventDefault(); z.classList.remove("over"); addFiles([...e.dataTransfer.files], kind); });
}
wireDrop("dropImg", "fileImg", "images");
wireDrop("dropDoc", "fileDoc", "doc");

function buildPayload(postNow) {
  const content = $("content").value.trim();
  if (!content) throw new Error("Write something first.");
  const p = { content, visibility: $("visibility").value };
  if (postNow) p.post_now = true;
  else {
    if (!$("when").value) throw new Error("Pick a publish time.");
    p.scheduled_at = new Date($("when").value).toISOString(); // local picker value -> UTC
  }
  if (tab === "p-img" && files.images.length) p.media = files.images.map((f) => ({ id: f.id, alt: f.alt }));
  if (tab === "p-doc" && files.doc) p.media = [{ id: files.doc.id, alt: "" }];
  if (tab === "p-poll") {
    p.poll = { question: $("pollQ").value, options: [...document.querySelectorAll(".pollOpt")].map((i) => i.value), days: Number($("pollDays").value) };
  }
  if (tab === "p-link") p.link = { url: $("linkUrl").value, title: $("linkTitle").value, description: $("linkDesc").value };
  return p;
}

function resetForm() {
  $("content").value = ""; $("count").textContent = "0 / 3000";
  files.images = []; files.doc = null; renderFiles();
  ["pollQ", "linkUrl", "linkTitle", "linkDesc"].forEach((id) => { $(id).value = ""; });
  document.querySelectorAll(".pollOpt").forEach((i) => { i.value = ""; });
  $("when").value = ""; setDefaultWhen();
}

async function submit(postNow) {
  const buttons = [$("postNow"), $("schedule")];
  try {
    const payload = buildPayload(postNow);
    if (postNow && !confirm("Publish this to your LinkedIn profile right now?")) return;
    buttons.forEach((b) => { b.disabled = true; });
    await api("/api/posts", { method: "POST", body: JSON.stringify(payload) });
    resetForm();
    msg(postNow ? "Publishing now. Watch the status below." : "Scheduled.", false);
    await refreshAll();
    if (postNow) setTimeout(() => refreshAll().catch(() => {}), 2000);
  } catch (e) { msg(e.message, true); }
  finally { buttons.forEach((b) => { b.disabled = false; }); }
}
$("postNow").addEventListener("click", () => submit(true));
$("schedule").addEventListener("click", () => submit(false));
$("logout").addEventListener("click", async () => { await fetch("/auth/logout", { method: "POST" }); showLogin(); });

init();
