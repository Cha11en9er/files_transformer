const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;");
}

function showError(message) {
  const el = $("#user-error");
  if (!message) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.textContent = message;
  el.classList.remove("hidden");
}

async function readError(res) {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
  } catch (err) { /* keep */ }
  return "Не получилось";
}

async function loadUsers() {
  const res = await fetch("/api/v1/auth/users", { credentials: "same-origin" });
  const box = $("#user-list");
  if (!res.ok) {
    box.innerHTML = `<p class="history-empty">${esc(await readError(res))}</p>`;
    return;
  }
  const data = await res.json();
  box.innerHTML = (data.items || [])
    .map((user) => {
      const muted = user.blocked ? " is-muted" : "";
      const role = user.role === "admin" ? "администратор" : "рабочий";
      return `
        <article class="history-card${muted}" data-user-id="${esc(user.id)}">
          <div class="history-card-top">
            <div>
              <strong>${esc(user.login)}</strong>
              <p class="history-meta">${role}${user.blocked ? " · заблокирован" : ""}</p>
            </div>
            <div class="history-card-actions">
              <button type="button" class="btn tiny" data-act="role">${user.role === "admin" ? "Сделать рабочим" : "Сделать админом"}</button>
              <button type="button" class="btn tiny" data-act="block">${user.blocked ? "Разблокировать" : "Заблокировать"}</button>
              <button type="button" class="btn tiny" data-act="password">Пароль</button>
            </div>
          </div>
        </article>`;
    })
    .join("");
}

$("#user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  const form = event.currentTarget;
  const res = await fetch("/api/v1/auth/users", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      login: form.login.value.trim(),
      password: form.password.value,
      role: form.role.value,
    }),
  });
  if (!res.ok) {
    showError(await readError(res));
    return;
  }
  form.reset();
  await loadUsers();
});

$("#user-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-act]");
  const card = event.target.closest("[data-user-id]");
  if (!button || !card) return;
  const id = card.dataset.userId;
  showError("");
  const act = button.dataset.act;
  let res;
  if (act === "password") {
    const password = window.prompt("Новый пароль (от 5 символов)");
    if (!password) return;
    res = await fetch(`/api/v1/auth/users/${encodeURIComponent(id)}/password`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
  } else if (act === "block") {
    const blocked = !button.textContent.includes("Разблок");
    res = await fetch(`/api/v1/auth/users/${encodeURIComponent(id)}/block`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blocked }),
    });
  } else {
    const role = button.textContent.includes("админ") ? "admin" : "worker";
    res = await fetch(`/api/v1/auth/users/${encodeURIComponent(id)}/role`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    });
  }
  if (!res.ok) {
    showError(await readError(res));
    return;
  }
  await loadUsers();
});

loadUsers();
