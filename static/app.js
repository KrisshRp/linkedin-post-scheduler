// Dashboard: plain fetch + DOM. All user text goes through textContent (no XSS).
const $ = (id) => document.getElementById(id);
const open = new Set(); // post ids whose event log is expanded
let timer = null;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401) { showLogin(); throw new Error("login"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

const local = (iso) => new Date(iso).toLocaleString();
function el(tag, props = {}, ...kids) {
  const e = Object.assign(document.createElement(tag), props);
  kids.forEach((k) => e.append(k));
  return e;
}

async function init() {
  const h = await fetch("/healthz").then((r) => r.json());
  if (h.publish_mode === "dry_run") {
    $("banner").hidden = false;
    $("banner").textContent = "DRY-RUN mode: no real posts are made to LinkedIn.";
  }
  $("devLogin").hidden = !h.dev_login;
  $("tz").textContent = Intl.DateTimeFormat().resolvedOptions().timeZone;
  try { await showApp(); } catch (_) { /* showLogin already ran */ }
}

function showLogin() { $("login").hidden = false; $("app").hidden = true; clearInterval(timer); }

async function showApp() {
  const me = await api("/api/me");
  $("login").hidden = true; $("app").hidden = false;
  $("name").textContent = me.name || "LinkedIn user";
  $("token").textContent = me.token_days_left != null ? `Token valid ${me.token_days_left} more days` : "";
  if (me.picture) { $("avatar").src = me.picture; $("avatar").hidden = false; }
  const d = new Date(Date.now() + 2 * 60000); // default: 2 minutes ahead
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  if (!$("when").value) $("when").value = d.toISOString().slice(0, 16);
  await refresh();
  clearInterval(timer);
  timer = setInterval(() => refresh().catch(() => {}), 5000);
}

function detailText(p) {
  if (p.status === "published") return p.linkedin_post_urn || "";
  if (p.status === "failed") return p.last_error || "";
  if (p.status === "scheduled" && p.attempts > 0) return `Retry ${p.attempts}/${p.max_attempts} at ${local(p.next_attempt_at)}. ${p.last_error || ""}`;
  if (p.status === "processing") return "Publishing...";
  return "";
}

async function refresh() {
  const posts = await api("/api/posts");
  $("empty").hidden = posts.length > 0;
  const body = $("rows");
  body.replaceChildren();
  for (const p of posts) {
    const actions = el("td");
    actions.append(el("button", { className: "btn small", textContent: open.has(p.id) ? "Hide log" : "Log", onclick: () => { open.has(p.id) ? open.delete(p.id) : open.add(p.id); refresh(); } }));
    if (p.status === "scheduled" && p.attempts === 0)
      actions.append(" ", el("button", { className: "btn small", textContent: "Cancel", onclick: () => act(`/api/posts/${p.id}`, "DELETE") }));
    if (p.status === "failed")
      actions.append(" ", el("button", { className: "btn small", textContent: "Retry", onclick: () => act(`/api/posts/${p.id}/retry`, "POST") }));
    const detail = el("td", { textContent: detailText(p) });
    if (p.status === "published" && p.linkedin_post_urn && !p.linkedin_post_urn.includes("dryrun")) {
      detail.replaceChildren(el("a", { href: `https://www.linkedin.com/feed/update/${encodeURIComponent(p.linkedin_post_urn)}`, target: "_blank", rel: "noopener", textContent: "View on LinkedIn" }));
    }
    body.append(el("tr", {},
      el("td", {}, el("span", { className: `badge ${p.status}`, textContent: p.status })),
      el("td", { className: "content", textContent: p.content }),
      el("td", { textContent: local(p.scheduled_at) }),
      el("td", { textContent: `${p.attempts}/${p.max_attempts}` }),
      detail, actions));
    if (open.has(p.id)) {
      const ev = await api(`/api/posts/${p.id}/events`);
      const box = el("div", { className: "events" });
      ev.forEach((e) => box.append(el("div", { className: e.level, textContent: `${local(e.at)}  ${e.event}  ${e.detail}` })));
      body.append(el("tr", {}, el("td", { colSpan: 6 }, box)));
    }
  }
}

async function act(path, method) {
  try { await api(path, { method }); await refresh(); } catch (e) { msg(e.message, true); }
}

function msg(text, err) { const m = $("formMsg"); m.textContent = text; m.className = "small " + (err ? "err" : "ok"); }

$("content").addEventListener("input", () => { $("count").textContent = `${$("content").value.length} / 3000`; });
$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const scheduled_at = new Date($("when").value).toISOString(); // local picker value -> UTC
    await api("/api/posts", { method: "POST", body: JSON.stringify({ content: $("content").value, scheduled_at }) });
    $("content").value = ""; $("count").textContent = "0 / 3000";
    msg("Scheduled.", false);
    await refresh();
  } catch (e) { msg(e.message, true); }
});
$("logout").addEventListener("click", async () => { await fetch("/auth/logout", { method: "POST" }); showLogin(); });

init();
