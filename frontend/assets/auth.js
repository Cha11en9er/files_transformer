/* Session chip, login, admin pages. Loaded before page scripts. */
(function () {
  const orig = window.fetch.bind(window);
  let refreshing = null;

  async function refreshSession() {
    if (!refreshing) {
      refreshing = orig("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" }).finally(() => {
        refreshing = null;
      });
    }
    return refreshing;
  }

  window.fetch = async function (input, init) {
    const opts = init ? { ...init } : {};
    if (!opts.credentials) opts.credentials = "same-origin";
    let res = await orig(input, opts);
    const url = typeof input === "string" ? input : input && input.url ? input.url : "";
    if (res.status === 401 && url.indexOf("/api/v1/auth/") === -1) {
      const again = await refreshSession();
      if (again && again.ok) res = await orig(input, opts);
      else if (!location.pathname.startsWith("/login")) location.replace("/login");
    }
    return res;
  };

  async function paint() {
    if (location.pathname.startsWith("/login")) return;
    const slot = document.querySelector(".top-actions");
    if (!slot || document.getElementById("session-chip")) return;
    const res = await orig("/api/v1/auth/me", { credentials: "same-origin" });
    if (!res.ok) return;
    const body = await res.json();
    const user = body.user || {};
    const chip = document.createElement("span");
    chip.id = "session-chip";
    chip.className = "session-chip";
    chip.textContent = user.login || "";
    slot.appendChild(chip);
    if (user.role === "admin") {
      if (!document.getElementById("nav-users")) {
        const users = document.createElement("a");
        users.id = "nav-users";
        users.className = "btn";
        users.href = "/users";
        users.textContent = "Пользователи";
        slot.insertBefore(users, chip);
      }
      if (!document.getElementById("nav-audit")) {
        const audit = document.createElement("a");
        audit.id = "nav-audit";
        audit.className = "btn";
        audit.href = "/audit";
        audit.textContent = "История действий";
        slot.insertBefore(audit, chip);
      }
    }
    const out = document.createElement("button");
    out.type = "button";
    out.className = "btn";
    out.id = "btn-logout";
    out.textContent = "Выйти";
    out.addEventListener("click", async () => {
      await orig("/api/v1/auth/logout", { method: "POST", credentials: "same-origin" });
      location.replace("/login");
    });
    slot.appendChild(out);
    document.body.dataset.role = user.role || "worker";
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", paint);
  else paint();
})();
