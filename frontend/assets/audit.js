const ACTION_RU = {
  login: "вошёл",
  logout: "вышел",
  shipment_processed: "обработал поставку",
  history_saved: "сохранил версию",
  export_zip: "скачал архив",
  file_deleted: "удалил файл",
  shipment_deleted: "удалил поставку",
  user_created: "создал учётку",
  password_set: "задал пароль",
  user_blocked: "заблокировал",
  user_unblocked: "разблокировал",
  role_set: "сменил роль",
};

const $ = (sel) => document.querySelector(sel);

function dayStamp(date) {
  const z = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${z(date.getMonth() + 1)}-${z(date.getDate())}`;
}

function applyPreset() {
  const preset = Number($("#audit-preset").value || 0);
  const from = $("#audit-from");
  const to = $("#audit-to");
  if (!preset) return;
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - (preset - 1));
  from.value = dayStamp(start);
  to.value = dayStamp(end);
}

function esc(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;");
}

async function loadAudit(event) {
  if (event) event.preventDefault();
  applyPreset();
  const params = new URLSearchParams();
  const login = $("#audit-login").value.trim();
  const q = $("#audit-q").value.trim();
  const from = $("#audit-from").value;
  const to = $("#audit-to").value;
  if (login) params.set("login", login);
  if (q) params.set("q", q);
  if (from) params.set("date_from", from);
  if (to) params.set("date_to", to);
  const res = await fetch("/api/v1/auth/audit?" + params.toString(), { credentials: "same-origin" });
  const box = $("#audit-list");
  if (!res.ok) {
    box.innerHTML = `<p class="history-empty">Не удалось открыть журнал.</p>`;
    return;
  }
  const data = await res.json();
  const items = data.items || [];
  if (!items.length) {
    box.innerHTML = `<p class="history-empty">Записей нет.</p>`;
    return;
  }
  box.innerHTML = items
    .map((item) => {
      const muted = item.login_blocked ? " is-muted" : "";
      const head = item.events && item.events[0] ? ACTION_RU[item.events[0].action] || item.events[0].action : "";
      const title = item.title || item.invoice_no || (item.shipment_id ? "поставка" : head);
      const link = item.shipment_id
        ? `<a class="btn tiny" href="/?open_history=${encodeURIComponent(item.shipment_id)}">Открыть</a>`
        : "";
      const details = (item.events || [])
        .map((ev) => {
          const label = ACTION_RU[ev.action] || ev.action;
          const extra = ev.detail ? ` · ${esc(ev.detail)}` : "";
          return `<li>${esc(ev.at)} · ${esc(ev.login)} · ${esc(label)}${extra}</li>`;
        })
        .join("");
      return `
        <article class="history-card${muted}">
          <div class="history-card-top">
            <div>
              <strong>${esc(item.login || "неизвестно")}</strong>
              <p class="history-meta">${esc(item.latest_at)} · ${esc(title)}${item.invoice_no ? ` · инвойс ${esc(item.invoice_no)}` : ""}</p>
            </div>
            <div class="history-card-actions">
              <button type="button" class="btn tiny" data-audit-toggle>${item.count || 1}</button>
              ${link}
            </div>
          </div>
          <ol class="audit-details hidden">${details}</ol>
        </article>`;
    })
    .join("");
}

$("#audit-filters").addEventListener("submit", loadAudit);
$("#audit-preset").addEventListener("change", () => {
  if (!$("#audit-preset").value) return;
  loadAudit();
});
$("#audit-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-audit-toggle]");
  if (!button) return;
  const list = button.closest(".history-card")?.querySelector(".audit-details");
  if (list) list.classList.toggle("hidden");
});
applyPreset();
loadAudit();
