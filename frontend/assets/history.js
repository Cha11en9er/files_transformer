const PROFILE_RU = {
  "18233": "Три Excel: Инвойс + Пакинг + Спецификация",
  BEIJING: "Одна книга: Invoice + Packing list + Specification + Описание",
};

const state = {
  allItems: [],
  totalBytes: 0,
};

const $ = (sel) => document.querySelector(sel);

function formatBytes(n) {
  const value = Number(n) || 0;
  if (value < 1024) return `${value} Б`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} КБ`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} МБ`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} ГБ`;
}

function showToast(message, { kind = "ok", ms = 3200 } = {}) {
  const el = $("#toast");
  if (!el) return;
  el.hidden = false;
  el.className = `toast ${kind}`;
  el.textContent = message;
  window.clearTimeout(showToast._t);
  showToast._t = window.setTimeout(() => {
    el.hidden = true;
  }, ms);
}

function humanizeClientError(raw) {
  const text = String(raw || "").trim();
  if (!text) return "Неизвестная ошибка";
  try {
    const parsed = JSON.parse(text);
    if (parsed?.detail) return typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
  } catch {
    /* keep */
  }
  return text.slice(0, 400);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(humanizeClientError(text || res.statusText));
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

function searchHaystack(item) {
  const title = String(item.title || "").trim();
  const invoice = String(item.invoice_no || "").trim();
  return `${title} ${invoice}`.toLowerCase();
}

function displayName(item) {
  const title = String(item.title || "").trim();
  const invoice = String(item.invoice_no || "").trim();
  if (title) return title;
  if (invoice) return invoice;
  return "поставка";
}

function filteredItems() {
  const q = String($("#history-search")?.value || "")
    .trim()
    .toLowerCase();
  const from = String($("#history-date-from")?.value || "").trim();
  const to = String($("#history-date-to")?.value || "").trim();
  return (state.allItems || []).filter((item) => {
    if (q) {
      const hay = searchHaystack(item);
      if (!hay.includes(q)) return false;
    }
    const day = String(item.updated_date_moscow || item.created_date_moscow || "");
    if (from && (!day || day < from)) return false;
    if (to && (!day || day > to)) return false;
    return true;
  });
}

function renderHistoryList() {
  const box = $("#history-list");
  const summary = $("#history-summary");
  if (!box) return;
  const items = filteredItems();
  const total = state.allItems.length;
  if (summary) {
    if (!total) {
      summary.textContent = "Пока пусто. После обработки поставка появится здесь сама.";
    } else {
      summary.textContent =
        `Показано ${items.length} из ${total}, на диске ${formatBytes(state.totalBytes)}. Дата и время — по Москве.`;
    }
  }
  if (!items.length) {
    box.innerHTML = `<p class="history-empty">${total ? "Ничего не найдено по фильтру." : "Записей пока нет."}</p>`;
    return;
  }
  box.innerHTML = items
    .map((item) => {
      const profile = PROFILE_RU[item.profile_type] || item.profile_type || "";
      const title = String(displayName(item)).replace(/"/g, "&quot;");
      const invoiceHint = item.invoice_no
        ? ` · инвойс ${String(item.invoice_no).replace(/</g, "&lt;")}`
        : "";
      const muted = item.owner_unknown ? " is-muted" : "";
      const author = item.owner_unknown
        ? " · автор неизвестен"
        : item.owner_login
          ? ` · ${String(item.owner_login).replace(/</g, "&lt;")}`
          : "";
      return `
        <article class="history-card${muted}" data-history-id="${item.id}">
          <div class="history-card-top">
            <div>
              <input class="history-title-input" data-history-rename value="${title}" aria-label="Название поставки" />
              <p class="history-meta">
                ${item.updated_at_moscow || item.created_at_moscow || ""}
                · ${profile}${invoiceHint}${author}
                · исходники ${item.source_count || 0} (${formatBytes(item.source_bytes)})
                · архивы ${item.export_count || 0} (${formatBytes(item.export_bytes)})
              </p>
            </div>
            <div class="history-card-actions">
              <a class="btn tiny" data-history-open href="/?open_history=${encodeURIComponent(item.id)}">Открыть</a>
              <button type="button" class="btn tiny" data-history-expand>Файлы</button>
              <button type="button" class="btn tiny danger" data-history-delete>Удалить</button>
            </div>
          </div>
          <div class="history-files hidden" data-history-files></div>
        </article>`;
    })
    .join("");
}

async function loadHistoryList() {
  const data = await api("/api/v1/history/");
  state.allItems = data.items || [];
  state.totalBytes = data.total_bytes || 0;
  renderHistoryList();
  return data;
}

async function expandHistoryCard(card) {
  const id = card?.dataset?.historyId;
  const filesBox = card?.querySelector("[data-history-files]");
  if (!id || !filesBox) return;
  filesBox.classList.remove("hidden");
  filesBox.innerHTML = `<p class="hint">Загрузка…</p>`;
  const detail = await api(`/api/v1/history/${encodeURIComponent(id)}`);
  const sources = detail.sources || [];
  const exports = detail.exports || [];
  const renderRows = (rows, kind) => {
    if (!rows.length) return `<p class="history-empty">Пусто</p>`;
    return rows
      .map(
        (f) => `
      <div class="history-file-row">
        <span class="history-file-name">${f.name} · ${formatBytes(f.bytes)}${f.updated_at_moscow ? ` · ${f.updated_at_moscow}` : ""}</span>
        <span class="history-file-actions">
          <a class="btn tiny" href="/api/v1/history/${encodeURIComponent(id)}/files/${encodeURIComponent(kind)}/${encodeURIComponent(f.name)}">Скачать</a>
          <button type="button" class="btn tiny danger" data-history-file-del data-kind="${kind}" data-name="${String(f.name).replace(/"/g, "&quot;")}">Удалить</button>
        </span>
      </div>`
      )
      .join("");
  };
  filesBox.innerHTML = `
    <div>
      <h3>Исходники</h3>
      ${renderRows(sources, "sources")}
    </div>
    <div>
      <h3>Архивы Excel</h3>
      ${renderRows(exports, "exports")}
    </div>`;
}

$("#btn-history-refresh")?.addEventListener("click", async () => {
  try {
    await loadHistoryList();
    showToast("История обновлена", { kind: "ok" });
  } catch (err) {
    showToast(`История: ${humanizeClientError(err.message)}`, { kind: "error", ms: 5000 });
  }
});

["history-search", "history-date-from", "history-date-to"].forEach((id) => {
  $(`#${id}`)?.addEventListener("input", () => renderHistoryList());
  $(`#${id}`)?.addEventListener("change", () => renderHistoryList());
});

$("#btn-history-filter-clear")?.addEventListener("click", () => {
  const search = $("#history-search");
  const from = $("#history-date-from");
  const to = $("#history-date-to");
  if (search) search.value = "";
  if (from) from.value = "";
  if (to) to.value = "";
  renderHistoryList();
});

$("#history-list")?.addEventListener("click", async (e) => {
  const card = e.target.closest(".history-card");
  if (!card) return;
  const id = card.dataset.historyId;
  try {
    if (e.target.closest("[data-history-expand]")) {
      await expandHistoryCard(card);
      return;
    }
    if (e.target.closest("[data-history-delete]")) {
      if (!window.confirm("Удалить всю поставку из истории вместе с файлами на сервере?")) return;
      await api(`/api/v1/history/${encodeURIComponent(id)}`, { method: "DELETE" });
      await loadHistoryList();
      showToast("Поставка удалена из истории", { kind: "ok" });
      return;
    }
    const delFile = e.target.closest("[data-history-file-del]");
    if (delFile) {
      if (!window.confirm("Удалить этот файл с сервера?")) return;
      await api(`/api/v1/history/${encodeURIComponent(id)}/files`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: delFile.dataset.kind, name: delFile.dataset.name }),
      });
      await expandHistoryCard(card);
      await loadHistoryList();
      showToast("Файл удалён", { kind: "ok" });
    }
  } catch (err) {
    showToast(`История: ${humanizeClientError(err.message)}`, { kind: "error", ms: 5200 });
  }
});

$("#history-list")?.addEventListener("change", async (e) => {
  const input = e.target.closest("[data-history-rename]");
  const card = e.target.closest(".history-card");
  if (!input || !card) return;
  const id = card.dataset.historyId;
  const title = (input.value || "").trim();
  if (!title) {
    showToast("Название не может быть пустым", { kind: "info" });
    return;
  }
  try {
    await api(`/api/v1/history/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    const hit = state.allItems.find((row) => row.id === id);
    if (hit) hit.title = title;
    showToast("Название обновлено", { kind: "ok" });
  } catch (err) {
    showToast(`Не удалось переименовать: ${humanizeClientError(err.message)}`, { kind: "error", ms: 5000 });
  }
});

loadHistoryList().catch((err) => {
  showToast(`История: ${humanizeClientError(err.message)}`, { kind: "error", ms: 5000 });
});
