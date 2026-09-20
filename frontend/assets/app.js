const DOC_TYPE_RU = {
  INVOICE: "Инвойс",
  PACKING_LIST: "Упаковочный лист",
  SPECIFICATION: "Спецификация",
  CATALOG: "Справочник",
  PERMIT: "РД",
};

const STATUS_RU = {
  DRAFT: "Черновик",
  UPLOADING: "Загрузка",
  PARSING: "Обработка",
  RECONCILING: "Сверка",
  NEEDS_REVIEW: "Требуется проверка",
  READY_TO_EXPORT: "Готово к выгрузке",
  EXPORTING: "Выгрузка",
  EXPORTED: "Выгружено",
  FAILED: "Ошибка",
};

const state = { shipmentId: null, workspace: null, selectedItemId: null, pendingFiles: [], lastDupes: [], sessionLocked: false };
const extraFiles = new WeakSet();

const $ = (sel) => document.querySelector(sel);
const editField = (id) => document.getElementById(id);

const ACCEPT_RE = /\.(xlsx|xls|xlsm|pdf|png|jpe?g|tif{1,2}|bmp|webp)$/i;
const SKIP_PATH_PARTS = /(^|\/)(дс|ds|__macosx)(\/|$)/i;

function fileKey(file) {
  return file.webkitRelativePath || file.name;
}

function folderOf(file) {
  const rel = String(file.webkitRelativePath || "").replaceAll("\\", "/");
  const parts = rel.split("/").filter(Boolean);
  if (parts.length < 2) return "";
  return parts[0];
}

function fileLabel(file) {
  const rel = String(file.webkitRelativePath || file.name).replaceAll("\\", "/");
  const folder = folderOf(file);
  if (folder && rel.startsWith(`${folder}/`)) return rel.slice(folder.length + 1);
  return file.name;
}

function hexDigest(buffer) {
  return [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function fnv1a(bytes) {
  let hash = 2166136261;
  for (let i = 0; i < bytes.length; i += 1) {
    hash ^= bytes[i];
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

async function fileFingerprint(file) {
  const headLen = Math.min(file.size, 256 * 1024);
  const chunks = [await file.slice(0, headLen).arrayBuffer()];
  if (file.size > 256 * 1024) {
    chunks.push(await file.slice(Math.max(0, file.size - 65536)).arrayBuffer());
  }
  const total = chunks.reduce((sum, part) => sum + part.byteLength, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  chunks.forEach((part) => {
    merged.set(new Uint8Array(part), offset);
    offset += part.byteLength;
  });
  if (globalThis.crypto?.subtle) {
    const digest = await crypto.subtle.digest("SHA-256", merged);
    return `${file.size}:${hexDigest(digest)}`;
  }
  return `${file.size}:${fnv1a(merged)}`;
}

function isSkippedRelPath(rel) {
  const normalized = String(rel || "").replaceAll("\\", "/").replace(/^\//, "");
  if (SKIP_PATH_PARTS.test(normalized)) return true;
  return normalized.split("/").some((part) => /^(дс|ds|__macosx)$/i.test(part));
}

function isUsefulUpload(file) {
  if (!file || !file.size) return false;
  const rel = String(file.webkitRelativePath || file.name).replaceAll("\\", "/");
  if (isSkippedRelPath(rel)) return false;
  const base = rel.split("/").pop() || file.name;
  if (/^(thumbs\.db|\.ds_store)$/i.test(base)) return false;
  return ACCEPT_RE.test(base);
}

let syncingInput = false;

function syncFileInput() {
  const input = $("#files");
  const list = $("#file-list");
  try {
    const dt = new DataTransfer();
    state.pendingFiles.forEach((file) => {
      if (extraFiles.has(file)) return;
      try {
        dt.items.add(file);
      } catch {
        /* directory File objects cannot always be copied into another input */
      }
    });
    syncingInput = true;
    input.files = dt.files;
  } catch {
    /* keep pendingFiles even if the native input cannot be synced */
  } finally {
    syncingInput = false;
  }
  list.innerHTML = "";
  const grouped = new Map();
  state.pendingFiles.forEach((file) => {
    const folder = folderOf(file);
    if (!grouped.has(folder)) grouped.set(folder, []);
    grouped.get(folder).push(file);
  });
  [...grouped.keys()]
    .sort((left, right) => String(left).localeCompare(String(right), "ru"))
    .forEach((folder) => {
      if (folder) {
        const title = document.createElement("li");
        title.className = "file-folder";
        title.textContent = `Папка «${folder}»`;
        list.appendChild(title);
      }
      grouped.get(folder).forEach((file, index) => {
        const li = document.createElement("li");
        li.className = extraFiles.has(file) ? "file-chip extra" : "file-chip";
        const name = document.createElement("span");
        name.className = "file-chip-name";
        name.textContent = fileLabel(file);
        name.title = fileLabel(file);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "file-chip-remove";
        remove.dataset.key = fileKey(file);
        remove.setAttribute("aria-label", `Удалить ${fileLabel(file)}`);
        remove.textContent = "×";
        remove.disabled = state.sessionLocked;
        if (extraFiles.has(file)) {
          const tag = document.createElement("span");
          tag.className = "file-chip-tag";
          tag.textContent = "справочник";
          li.append(name, tag, remove);
        } else {
          li.append(name, remove);
        }
        list.appendChild(li);
      });
    });
  (state.lastDupes || []).forEach((msg) => {
    const li = document.createElement("li");
    li.className = "file-dup";
    li.textContent = msg;
    list.appendChild(li);
  });
  const submitBtn = $("#btn-process") || $("#create-form button[type='submit']");
  if (submitBtn && !state.sessionLocked && !processing) submitBtn.textContent = "Обработать";
  const hint = $("#kit-check");
  if (!hint) return;
  if (state.sessionLocked) {
    hint.textContent = "Файлы этой обработки зафиксированы. Новый комплект - обновите страницу.";
    hint.className = "status";
    return;
  }
  if (!state.pendingFiles.length) {
    hint.textContent = "";
    hint.className = "status";
    return;
  }
  hint.textContent = `Выбрано ${fileCountLabel(state.pendingFiles.length)}. Можно добавить ещё или убрать файл, затем нажать Обработать.`;
  hint.className = "status kit-ok";
}

function humanizeClientError(text) {
  const blob = String(text || "");
  const low = blob.toLowerCase();
  if (
    low.includes("10061") ||
    low.includes("connection refused") ||
    low.includes("отверг запрос на подключение") ||
    low.includes("econnrefused") ||
    low.includes("failed to establish a new connection")
  ) {
    return "Модель недоступна: OpenCode не отвечает на 127.0.0.1:4096.";
  }
  if (low.includes("creditserror") || low.includes("insufficient balance") || low.includes("no payment method")) {
    return "На OpenCode Zen нет оплаты или закончился баланс.";
  }
  if (low.includes("free usage exceeded")) {
    return "Бесплатный лимит модели кончился.";
  }
  if (low.includes("serveerror") || low.includes("eaddrinuse") || low.includes("address already in use")) {
    return "Порт 4096 уже занят. OpenCode, скорее всего, уже работает в фоне.";
  }
  if (low.includes("unauthorized") || low.includes("password is not set")) {
    return "OpenCode отклонил пароль. Пароль в backend/.env должен совпадать с паролем сервиса.";
  }
  return blob;
}

function fileCountLabel(n) {
  const n10 = n % 10;
  const n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return `${n} файл`;
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return `${n} файла`;
  return `${n} файлов`;
}

const GENERIC_TITLES = new Set(["", "export", "комплект документов", "shipment"]);

function isGenericTitle(value) {
  return GENERIC_TITLES.has((value || "").trim().toLowerCase());
}

function shipmentTitleFromHeader(header) {
  return String((header || {}).invoice_no || "").trim();
}

function currentTitleInput() {
  return document.querySelector('#create-form [name="title"]');
}

function applyShipmentTitle(value, { force = false } = {}) {
  const next = (value || "").trim();
  if (!next) return;
  if (state.workspace) state.workspace.title = next;
  const formTitle = currentTitleInput();
  const wsTitle = $("#ws-title");
  if (formTitle && (force || document.activeElement !== formTitle)) formTitle.value = next;
  if (wsTitle && (force || document.activeElement !== wsTitle)) wsTitle.value = next;
}

function hideOldWorkspace() {
  state.shipmentId = null;
  state.workspace = null;
  state.selectedItemId = null;
  $("#workspace")?.classList.add("hidden");
  const tbody = $("#items-table tbody");
  if (tbody) tbody.innerHTML = "";
}

const fingerprints = new WeakMap();

async function fpOf(file) {
  if (fingerprints.has(file)) return fingerprints.get(file);
  const value = await fileFingerprint(file);
  fingerprints.set(file, value);
  return value;
}

async function addPendingFiles(fileList, { extra = false } = {}) {
  if (state.sessionLocked || processing) return;
  const raw = [...(fileList || [])];
  const incoming = raw.filter(isUsefulUpload);
  const hint = $("#kit-check");
  try {
    if (!incoming.length) {
      if (raw.length && hint) {
        hint.textContent = "В выборе нет Excel, PDF или картинок. Нужны исходники, не папка ДС.";
        hint.className = "status";
      }
      return;
    }
    const known = new Map();
    for (const file of state.pendingFiles) {
      known.set(await fpOf(file), file);
    }
    const dupes = [];
    for (const file of incoming) {
      const fp = await fpOf(file);
      const exist = known.get(fp);
      if (exist) {
        dupes.push(`Пропущен дубликат «${fileLabel(file)}» (тот же файл, что «${fileLabel(exist)}»)`);
        continue;
      }
      known.set(fp, file);
      if (extra) extraFiles.add(file);
    }
    state.lastDupes = dupes;
    state.pendingFiles = [...known.values()];
    hideOldWorkspace();
    syncFileInput();
  } catch (err) {
    if (hint) {
      hint.textContent = `Не удалось взять файлы: ${err && err.message ? err.message : err}`;
      hint.className = "status";
    }
  }
}

function readAllEntries(reader) {
  return new Promise((resolve, reject) => {
    const all = [];
    const tick = () => {
      reader.readEntries((batch) => {
        if (!batch.length) {
          resolve(all);
          return;
        }
        all.push(...batch);
        tick();
      }, reject);
    };
    tick();
  });
}

function entryToFile(entry) {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function walkEntry(entry, out) {
  if (!entry) return;
  const rel = String(entry.fullPath || entry.name).replace(/^\//, "").replaceAll("\\", "/");
  if (isSkippedRelPath(rel)) return;
  if (entry.isFile) {
    const file = await entryToFile(entry);
    try {
      Object.defineProperty(file, "webkitRelativePath", { value: rel });
    } catch {
      /* ignore */
    }
    out.push(file);
    return;
  }
  if (entry.isDirectory) {
    const children = await readAllEntries(entry.createReader());
    for (const child of children) {
      await walkEntry(child, out);
    }
  }
}

async function filesFromDrop(dataTransfer) {
  const items = [...(dataTransfer.items || [])];
  const fromEntries = [];
  let hadDirectory = false;
  for (const item of items) {
    const entry = item.webkitGetAsEntry?.();
    if (!entry) continue;
    if (entry.isDirectory) hadDirectory = true;
    await walkEntry(entry, fromEntries);
  }
  if (hadDirectory || fromEntries.length) return fromEntries;
  return [...(dataTransfer.files || [])];
}

let processing = false;

function processButton() {
  return $("#btn-process") || $("#create-form button[type='submit']");
}

function setSessionLocked(locked, { busy = false, buttonLabel = null } = {}) {
  state.sessionLocked = Boolean(locked);
  processing = Boolean(busy);
  const freeze = state.sessionLocked || processing;
  $("#upload-panel")?.classList.toggle("session-locked", freeze);
  ["#files", "#folder", "#extra-files"].forEach((sel) => {
    const el = $(sel);
    if (el) el.disabled = freeze;
  });
  const title = currentTitleInput();
  const profile = document.querySelector('#create-form [name="profile_type"]');
  if (title) title.disabled = Boolean(busy);
  if (profile) profile.disabled = freeze;
  const btn = processButton();
  if (btn) {
    btn.disabled = freeze;
    if (buttonLabel) btn.textContent = buttonLabel;
    else if (!freeze) btn.textContent = "Обработать";
  }
  syncFileInput();
}

function removePendingFile(key) {
  if (state.sessionLocked || processing) return;
  state.pendingFiles = state.pendingFiles.filter((file) => fileKey(file) !== key);
  syncFileInput();
}

$("#file-list")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".file-chip-remove");
  if (!btn) return;
  e.preventDefault();
  removePendingFile(btn.dataset.key);
});

$("#files").addEventListener("change", (e) => {
  if (syncingInput) return;
  addPendingFiles(e.target.files);
});

$("#folder")?.addEventListener("change", (e) => {
  addPendingFiles(e.target.files);
  e.target.value = "";
});

$("#extra-files")?.addEventListener("change", (e) => {
  addPendingFiles(e.target.files, { extra: true });
  e.target.value = "";
});

const extraZone = $("#dropzone-extra");
["dragenter", "dragover"].forEach((evt) => {
  extraZone?.addEventListener(evt, (e) => {
    e.preventDefault();
    e.stopPropagation();
    extraZone.classList.add("dragover");
  });
});
["dragleave"].forEach((evt) => {
  extraZone?.addEventListener(evt, (e) => {
    e.preventDefault();
    e.stopPropagation();
    extraZone.classList.remove("dragover");
  });
});
extraZone?.addEventListener("drop", async (e) => {
  e.preventDefault();
  e.stopPropagation();
  extraZone.classList.remove("dragover");
  if (state.sessionLocked || processing) return;
  const files = await filesFromDrop(e.dataTransfer);
  addPendingFiles(files, { extra: true });
});

const dropzoneWrap = $("#dropzone-wrap");
const dropzone = $("#dropzone");
["dragenter", "dragover"].forEach((evt) => {
  dropzoneWrap?.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone?.classList.add("dragover");
    $("#dropzone-folder")?.classList.add("dragover");
  });
});
["dragleave", "drop"].forEach((evt) => {
  dropzoneWrap?.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone?.classList.remove("dragover");
    $("#dropzone-folder")?.classList.remove("dragover");
  });
});
dropzoneWrap?.addEventListener("drop", async (e) => {
  e.preventDefault();
  if (state.sessionLocked || processing) return;
  const files = await filesFromDrop(e.dataTransfer);
  addPendingFiles(files);
});

async function processShipment() {
  if (processing || state.sessionLocked) return;
  if (!state.pendingFiles.length) {
    $("#upload-status").textContent = "Выберите файлы или папки.";
    return;
  }
  setSessionLocked(true, { busy: true, buttonLabel: "Обработка…" });
  const form = $("#create-form");
  const fd = new FormData();
  fd.append("title", form.title.value.trim());
  fd.append("profile_type", form.profile_type.value);
  fd.append("stream", "1");
  state.pendingFiles.forEach((f) => {
    const name = fileKey(f);
    if (extraFiles.has(f)) fd.append("extra_files", f, name);
    else fd.append("files", f, name);
  });
  const progressWrap = $("#parse-progress");
  const fill = $("#parse-progress-fill");
  const label = $("#parse-progress-label");
  const log = $("#parse-file-log");
  progressWrap.classList.remove("hidden");
  fill.style.width = "0%";
  log.innerHTML = "";
  $("#upload-status").textContent = `Обработка ${fileCountLabel(state.pendingFiles.length)}…`;
  try {
    const created = await parseUploadStream(fd, {
      onProgress(current, total, filename, extra) {
        const pct = total ? Math.round((current / total) * 100) : 0;
        fill.style.width = `${pct}%`;
        const message = extra?.message;
        const stage = extra?.stage;
        if (message) {
          label.textContent = extra?.stage === "model" ? "идет вердикт" : message;
        } else if (stage === "model") {
          label.textContent = "идет вердикт";
        } else if (stage === "reconcile") {
          label.textContent = "Сверка позиций";
        } else {
          label.textContent = filename
            ? `Файл ${current} из ${total}: ${filename}`
            : `Файл ${current} из ${total}`;
        }
        $("#upload-status").textContent = label.textContent;
        if (stage === "model") {
          const exists = [...log.querySelectorAll("li")].some((li) => li.dataset.verdict === "1");
          if (!exists) {
            const li = document.createElement("li");
            li.className = "model";
            li.dataset.verdict = "1";
            li.textContent = "идет вердикт";
            log.appendChild(li);
          }
        }
      },
      onFile(filename, status, message) {
        const li = document.createElement("li");
        li.className = status || "";
        if (status === "skipped") {
          li.textContent = `Пропущен: ${filename}${message ? ` (${message})` : ""}`;
        } else if (status === "review") {
          li.textContent = message ? `${filename}: ${message}` : `Нужна проверка: ${filename}`;
        } else {
          li.textContent = message || `файл ${filename} обработан кодом`;
        }
        log.appendChild(li);
      },
    });
    state.shipmentId = created.id;
    state.workspace = created;
    applyShipmentTitle(created.title, { force: true });
    refreshOpenCodeStatus();
    const skipped = created.skipped_count || 0;
    $("#upload-status").textContent =
      `Готово: ${created.item_count} позиций из ${created.files?.length || state.pendingFiles.length} файлов` +
      (skipped ? `, пропущено: ${skipped}` : "") +
      `, замечаний: ${created.warning_count}. Чтобы обработать другой комплект, обновите страницу.`;
    fill.style.width = "100%";
    const reviewOk = created.model_review?.status === "ok";
    label.textContent = reviewOk ? "идет вердикт" : "Сверка завершена";
    if (reviewOk) {
      (created.files || [])
        .filter((f) => f.parse_status === "ok" && f.filename && f.filename !== "сверка")
        .forEach((f) => {
          const n = String(f.parse_message || "").match(/нашлось\s+(\d+)/i)?.[1]
            || String(created.item_count || "");
          const li = document.createElement("li");
          li.className = "model";
          li.textContent = `файл ${f.filename} обработан моделью, нашлось ${n} позиций`;
          log.appendChild(li);
        });
    }
    setSessionLocked(true, { busy: false, buttonLabel: "Обработано" });
    renderWorkspace();
  } catch (err) {
    $("#upload-status").textContent = `Ошибка: ${humanizeClientError(err.message)}`;
    setSessionLocked(false, { busy: false, buttonLabel: "Обработать" });
  }
}

async function parseUploadStream(fd, hooks) {
  const res = await fetch("/api/v1/shipments/", { method: "POST", body: fd });
  const ct = res.headers.get("content-type") || "";
  if (!ct.includes("ndjson")) {
    if (!res.ok) {
      const text = await res.text();
      let detail = text || res.statusText;
      try {
        const parsed = JSON.parse(text);
        if (parsed?.detail) detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
      } catch {
        /* keep */
      }
      throw new Error(detail);
    }
    return res.json();
  }
  if (!res.body) throw new Error("Нет ответа сервера");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let donePayload = null;
  while (true) {
    const { done, value } = await reader.read();
    buf += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buf.split("\n");
    buf = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.event === "progress") {
        hooks.onProgress(event.current, event.total, event.filename, {
          stage: event.stage,
          message: event.message,
        });
      } else if (event.event === "file") {
        hooks.onFile(event.filename, event.status, event.message);
      } else if (event.event === "error") {
        throw new Error(event.detail || "Ошибка обработки");
      } else if (event.event === "done") {
        donePayload = event;
      }
    }
    if (done) break;
  }
  if (buf.trim()) {
    const event = JSON.parse(buf);
    if (event.event === "error") throw new Error(event.detail || "Ошибка обработки");
    if (event.event === "done") donePayload = event;
  }
  if (!res.ok && !donePayload) throw new Error(res.statusText);
  if (!donePayload) throw new Error("Не удалось получить результат обработки");
  const { event: _event, ...created } = donePayload;
  return created;
}

$("#create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await processShipment();
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const CELL_LIMIT = 50;

function renderLongHtml(value) {
  const raw = value == null || value === "" ? "-" : String(value);
  if (raw === "-" || raw.length <= CELL_LIMIT) return escapeHtml(raw);
  return `<span class="cell-short">${escapeHtml(raw.slice(0, CELL_LIMIT))}…</span><span class="cell-full">${escapeHtml(raw)}</span><button type="button" class="cell-hide" hidden>свернуть</button>`;
}

function longCellClass(value, extra) {
  const raw = value == null || value === "" ? "" : String(value);
  const bits = [extra || ""];
  if (raw.length > CELL_LIMIT) bits.push("cell-long");
  return bits.join(" ").trim();
}

function longCell(value, extra) {
  const raw = value == null || value === "" ? "-" : String(value);
  const edit = extra === "article" ? "edit-article" : extra === "desc" ? "edit-desc-en" : "";
  const editAttr = edit ? ` data-edit="${edit}"` : "";
  return `<td class="${longCellClass(raw, extra)}" data-full="${escapeHtml(raw)}"${editAttr}>${renderLongHtml(raw)}</td>`;
}

function setModalScrollLock() {
  const open = [...document.querySelectorAll("dialog")].some((dialog) => dialog.open);
  document.documentElement.classList.toggle("modal-open", open);
}

function severityClass(errors) {
  // Full-row tint only for hard mismatches - yellow wash made the table look "all white"
  if (!errors?.length) return "";
  if (errors.some((e) => e.severity === "RED" && !e.resolved)) return "sev-RED";
  return "";
}

function formatNum(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return escapeHtml(value);
  const whole = Number.isInteger(n) || Math.abs(n - Math.round(n)) < 1e-9;
  return n.toLocaleString("ru-RU", {
    useGrouping: true,
    minimumFractionDigits: whole && digits === 0 ? 0 : whole ? 0 : Math.min(2, digits),
    maximumFractionDigits: digits,
  }).replace(/\s/g, "\u00a0");
}

function formatMoney(value) {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return escapeHtml(value);
  return n.toLocaleString("ru-RU", {
    useGrouping: true,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).replace(/\s/g, "\u00a0");
}

const SEVERITY_RU = {
  RED: "Расхождение",
  YELLOW: "Пропуск",
  ORANGE: "Распознавание",
  BLUE: "РД",
};

const FIELD_RU = {
  article: "артикул",
  rolls: "рулоны",
  meters: "метры",
  width: "ширина",
  area: "площадь",
  net_weight: "нетто",
  gross_weight: "брутто",
  price: "цена",
  amount: "сумма",
  qty: "кол-во",
  hs_code: "HS",
  tnved_code: "ТН ВЭД",
  description: "описание",
  design_family: "группа",
  ocr: "распознавание",
};

const ERROR_TYPE_RU = {
  SCAN_MISMATCH: "Скан",
  MODEL_CORRECTION: "Модель",
  MISSING_PAIR: "Нет пары",
  MISMATCH: "Расхождение",
  LOW_OCR_CONFIDENCE: "Распознавание",
  CATALOG_NOT_FOUND: "Справочник",
  PERMIT_MULTIPLE_CANDIDATES: "РД",
  catalog_not_found: "Справочник",
  description_missing: "Описание",
  mismatch: "Расхождение",
  weight_pl_vs_spec: "Вес",
};

const SCAN_VERDICT_RU = {
  ok: "совпало",
  question: "проверить",
  extra: "только на скане",
  missing: "нет на скане",
};

function renderFlags(errors) {
  const active = (errors || []).filter((e) => !e.resolved);
  if (!active.length) {
    return '<span class="flag-pill flag-pill-ok" title="Расхождений нет">✓ Без замечаний</span>';
  }
  return `<div class="flags-wrap">${active
    .map((e) => {
      const typeLabel = ERROR_TYPE_RU[e.error_type] || "";
      const label = typeLabel || SEVERITY_RU[e.severity] || e.severity;
      const field = FIELD_RU[e.field_name] || e.field_name || "";
      const msg = e.message || e.error_type || "";
      const title = escapeHtml([field, msg].filter(Boolean).join(" - "));
      const text = field ? `${label}: ${field}` : label;
      return `<span class="flag-pill flag-pill-${e.severity}" title="${title}">${escapeHtml(text)}</span>`;
    })
    .join("")}</div>`;
}

function fieldSeverity(errors, field) {
  const hit = (errors || []).find((e) => e.field_name === field && !e.resolved);
  return hit ? `sev-${hit.severity}` : "";
}

function itemQty(item) {
  const qty = item?.commercial_data?.qty;
  if (qty !== null && qty !== undefined && qty !== "") return qty;
  return item?.packing_data?.meters;
}

function sumField(items, getter) {
  let total = 0;
  let any = false;
  (items || []).forEach((item) => {
    const value = getter(item);
    const n = Number(value);
    if (value === null || value === undefined || value === "" || !Number.isFinite(n)) return;
    total += n;
    any = true;
  });
  return any ? total : null;
}

function fillExcelTotals(items) {
  const row = $("#items-totals-row");
  if (!row) return;
  const values = {
    rolls: sumField(items, (item) => item.packing_data?.rolls ?? item.packing_data?.boxes),
    meters: sumField(items, (item) => item.packing_data?.meters ?? item.commercial_data?.qty),
    area: sumField(items, (item) => item.packing_data?.area),
    net_weight: sumField(items, (item) => item.packing_data?.net_weight),
    gross_weight: sumField(items, (item) => item.packing_data?.gross_weight),
    amount: sumField(items, (item) => item.commercial_data?.amount),
  };
  row.querySelectorAll("[data-total]").forEach((cell) => {
    const key = cell.getAttribute("data-total");
    const value = values[key];
    cell.textContent = key === "amount" ? formatMoney(value) : formatNum(value, key === "rolls" ? 0 : 2);
  });
}

function recountWorkspaceWarnings() {
  const ws = state.workspace;
  if (!ws) return;
  const itemFlags = (ws.items || []).reduce(
    (n, item) => n + (item.validation_errors || []).filter((err) => !err.resolved).length,
    0
  );
  ws.warning_count = itemFlags + (ws.skipped_count || 0) + (ws.model_review?.totals_mismatch ? 1 : 0);
}

function contextCell(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return formatNum(value);
  return escapeHtml(value);
}

function recognizedRows(file) {
  return (file?.table || []).filter((row) => {
    if (!row) return false;
    if (row.article || row.description || row.hs_code || row.customs_code) return true;
    if (
      row.qty != null ||
      row.amount != null ||
      row.net_weight != null ||
      row.gross_weight != null ||
      row.price != null ||
      row.rolls != null ||
      row.meters != null
    ) {
      return true;
    }
    return Object.keys(row.raw || {}).length > 0;
  });
}

function renderRecognizedTable(rows) {
  const list = recognizedRows({ table: rows });
  if (!list.length) {
    return "<p class=\"hint\">В этом файле таблица товаров не собралась.</p>";
  }
  const body = list
    .slice(0, 250)
    .map((row, index) => {
      const code = row.customs_code || row.hs_code || "";
      const desc = row.description || "";
      return `<tr>
        <td class="row-no">${index + 1}</td>
        <td>${escapeHtml(row.article || "-")}</td>
        <td class="num">${contextCell(row.rolls ?? row.boxes)}</td>
        <td class="num">${contextCell(row.qty ?? row.meters)}</td>
        <td class="num">${contextCell(row.price)}</td>
        <td class="money">${contextCell(row.amount)}</td>
        <td class="num">${contextCell(row.net_weight)}</td>
        <td class="num">${contextCell(row.gross_weight)}</td>
        <td class="code">${escapeHtml(code || "-")}</td>
        ${longCell(desc)}
      </tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table class="context-mini">
    <thead><tr>
      <th class="row-no">№</th>
      <th>Артикул</th>
      <th class="num">Места</th>
      <th class="num">Кол-во</th>
      <th class="num">Цена</th>
      <th class="money">Сумма</th>
      <th class="num">Нетто</th>
      <th class="num">Брутто</th>
      <th>Код</th>
      <th>Описание</th>
    </tr></thead>
    <tbody>${body}</tbody>
  </table></div>`;
}

function renderTotalsStrip(review) {
  const excelTotals = review?.excel_totals || {};
  const scanTotals = review?.totals || {};
  const hasExcel = Boolean(review?.excel_attached || (review?.context?.excel || []).length);
  const parts = [
    ["qty", "Кол-во"],
    ["amount", "Сумма"],
    ["net_weight", "Нетто"],
    ["gross_weight", "Брутто"],
  ]
    .map(([key, label]) => {
      const tableVal = excelTotals[key] ?? (key === "qty" ? excelTotals.meters : null);
      const pdfVal = scanTotals[key] ?? (key === "qty" ? scanTotals.meters : null);
      if (tableVal == null && pdfVal == null) return "";
      const mismatch = tableVal != null && pdfVal != null && Math.abs(Number(tableVal) - Number(pdfVal)) > 0.5;
      const fmt = key === "amount" ? formatMoney : formatNum;
      const left = hasExcel ? `таблица ${fmt(tableVal)}` : `собрано ${fmt(tableVal)}`;
      return `<div class="scan-total${mismatch ? " mismatch" : ""}"><div class="k">${escapeHtml(label)}</div><div class="v">${left} · PDF ${fmt(pdfVal)}</div></div>`;
    })
    .filter(Boolean)
    .join("");
  return parts ? `<div class="scan-totals">${parts}</div>` : "";
}

function renderCompareTable(ws, review) {
  const hits = review?.items || [];
  if (!hits.length) return "";
  const hasExcel = Boolean(review?.excel_attached || (review?.context?.excel || []).length);
  const leftQty = hasExcel ? "Excel кол-во" : "В таблице";
  const rightQty = "В PDF";
  const leftSum = hasExcel ? "Excel сумма" : "Сумма";
  const byId = Object.fromEntries((ws.items || []).map((item) => [String(item.id), item]));
  const body = hits
    .map((hit, index) => {
      const item = hit.item_id ? byId[String(hit.item_id)] : null;
      const excelQty = item ? itemQty(item) : null;
      const scanQty = hit.qty ?? hit.meters;
      const excelAmount = item?.commercial_data?.amount ?? null;
      const canApply = Boolean(item) && hit.verdict === "question";
      const canAdd = !item && hit.verdict === "extra";
      let action = "";
      if (canApply) action = `<button type="button" class="btn" data-scan-action="apply" data-index="${index}">Подставить из PDF</button>`;
      else if (canAdd) action = `<button type="button" class="btn" data-scan-action="add" data-index="${index}">Добавить в таблицу</button>`;
      return `<tr>
        <td>${escapeHtml(hit.article || hit.matched_article || "-")}</td>
        <td class="num">${formatNum(excelQty)}</td>
        <td class="num">${formatNum(scanQty)}</td>
        <td class="money">${formatMoney(excelAmount)}</td>
        <td class="money">${formatMoney(hit.amount)}</td>
        <td class="verdict-${escapeHtml(hit.verdict || "question")}">${escapeHtml(SCAN_VERDICT_RU[hit.verdict] || hit.verdict || "")}${hit.notes ? ` · ${escapeHtml(hit.notes)}` : ""}</td>
        <td>${action}</td>
      </tr>`;
    })
    .join("");
  return `<h4>Сверка с таблицей поставки</h4>
    <div class="table-wrap"><table class="context-mini" id="scan-table">
      <thead><tr>
        <th>Артикул</th>
        <th class="num">${escapeHtml(leftQty)}</th>
        <th class="num">${escapeHtml(rightQty)}</th>
        <th class="money">${escapeHtml(leftSum)}</th>
        <th class="money">PDF сумма</th>
        <th>Вердикт</th>
        <th></th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;
}

function reviewSources(ws) {
  const ctx = ws?.model_review?.context || {};
  const pdfs = ctx.pdfs || [];
  const excel = ctx.excel || [];
  return { pdfs, excel, files: [...pdfs, ...excel] };
}

function renderReviewMeta(ws, review) {
  const reviewMeta = $("#model-review-meta");
  const launch = $("#review-launch");
  const { files } = reviewSources(ws);
  if (launch) launch.classList.toggle("hidden", !files.length);
  if (!reviewMeta) return;
  if (!review) {
    reviewMeta.textContent = "";
    return;
  }
  const bits = [];
  if (review.model_label || review.model) bits.push(review.model_label || review.model);
  if (review.image_count) bits.push(`${review.image_count} стр.`);
  if (review.review_cost_usd != null) bits.push(`это распознавание $${Number(review.review_cost_usd).toFixed(2)}`);
  if (review.usage_usd != null) bits.push(`расход $${Number(review.usage_usd).toFixed(2)}`);
  if (review.remaining_usd != null) {
    bits.push(`${review.remaining_is_key_limit ? "лимит ключа" : "остаток"} $${Number(review.remaining_usd).toFixed(2)}`);
  }
  if (review.status === "ok") bits.push("модель ответила");
  else if (review.status === "error") bits.push(humanizeClientError(review.error) || "модель не ответила");
  reviewMeta.textContent = bits.join(" · ");
}

function sourceKind(file) {
  const name = String(file?.filename || "").toLowerCase();
  if (file?.kind === "pdf" || name.endsWith(".pdf")) return "PDF";
  if (/\.(xlsx|xls|xlsm)$/.test(name) || file?.kind === "excel") return "Excel";
  return "Файл";
}

function findReviewFile(files, filename) {
  if (!filename) return files[0] || null;
  const wanted = String(filename).toLowerCase();
  return (
    files.find((file) => String(file.filename || "").toLowerCase() === wanted) ||
    files.find((file) => wanted.endsWith(String(file.filename || "").toLowerCase())) ||
    files.find((file) => String(file.filename || "").toLowerCase().endsWith(wanted)) ||
    null
  );
}

function renderFilePane(file) {
  const pages = (file.pages || []).length ? `стр. ${(file.pages || []).join(", ")}` : "";
  const sheets = (file.sheets || []).length ? `листы: ${(file.sheets || []).join(", ")}` : "";
  const n = recognizedRows(file).length;
  const kind = sourceKind(file);
  const preview =
    !n && file.text
      ? `<pre class="review-text">${escapeHtml(String(file.text).slice(0, 6000))}</pre>`
      : "";
  return `
    <div class="review-file-meta">
      <strong>${escapeHtml(kind)} ${escapeHtml(file.filename || "")}</strong>
      <span class="hint">${escapeHtml([pages || sheets, file.meaning, `${n} строк`].filter(Boolean).join(" · "))}</span>
    </div>
    ${renderRecognizedTable(file.table)}
    ${preview}
  `;
}

function fillReviewDialog(ws, filename) {
  const tabs = $("#review-dialog-tabs");
  const body = $("#review-dialog-body");
  const meta = $("#review-dialog-meta");
  if (!tabs || !body) return;
  const review = ws.model_review;
  const { files } = reviewSources(ws);
  const wanted = findReviewFile(files, filename);
  const title = $("#review-dialog-title");
  if (title) title.textContent = wanted ? `Распознавание ${sourceKind(wanted)}` : "Распознавание файла";
  tabs.innerHTML = files
    .map((file) => {
      const n = recognizedRows(file).length;
      const active = wanted && file.filename === wanted.filename ? " active" : "";
      return `<button type="button" class="review-tab${active}" data-review-file="${escapeHtml(file.filename || "")}">${escapeHtml(file.filename || "файл")} · ${n}</button>`;
    })
    .join("");
  if (meta) {
    meta.textContent = review?.meaning
      ? String(review.meaning).slice(0, 180)
      : wanted
        ? `${recognizedRows(wanted).length} строк`
        : "";
  }
  if (!wanted) {
    body.innerHTML = "<p class=\"hint\">Нет распознанных таблиц.</p>";
    return;
  }
  body.innerHTML = `
    ${renderFilePane(wanted)}
    ${renderTotalsStrip(review)}
    ${renderCompareTable(ws, review)}
  `;
}

function renderScanReview(ws, review) {
  renderReviewMeta(ws, review);
}

function openReviewPanel(filename) {
  const ws = state.workspace;
  if (!ws) return;
  const dialog = $("#review-dialog");
  if (!dialog) return;
  fillReviewDialog(ws, filename);
  if (typeof dialog.showModal === "function") dialog.showModal();
  setModalScrollLock();
}

function applyScanToItem(hit) {
  const ws = state.workspace;
  if (!ws) return;
  const item = (ws.items || []).find((row) => String(row.id) === String(hit.item_id));
  if (!item) return;
  item.commercial_data = { ...(item.commercial_data || {}) };
  item.packing_data = { ...(item.packing_data || {}) };
  if (hit.qty != null) item.commercial_data.qty = hit.qty;
  if (hit.meters != null) item.packing_data.meters = hit.meters;
  else if (hit.qty != null) item.packing_data.meters = hit.qty;
  if (hit.amount != null) item.commercial_data.amount = hit.amount;
  if (hit.price != null) item.commercial_data.price = hit.price;
  if (hit.rolls != null) item.packing_data.rolls = hit.rolls;
  if (hit.net_weight != null) item.packing_data.net_weight = hit.net_weight;
  if (hit.gross_weight != null) item.packing_data.gross_weight = hit.gross_weight;
  if (hit.area != null) item.packing_data.area = hit.area;
  item.validation_errors = (item.validation_errors || []).map((err) =>
    err.error_type === "SCAN_MISMATCH" ? { ...err, resolved: true } : err
  );
  hit.verdict = "ok";
  hit.notes = "подставлено из скана";
  recountWorkspaceWarnings();
  renderWorkspace();
}

function addItemFromScan(hit) {
  const ws = state.workspace;
  if (!ws) return;
  const id = crypto.randomUUID();
  ws.items.push({
    id,
    article: hit.article || null,
    model: hit.article || null,
    normalized_article: String(hit.article || "").replace(/\s+/g, "").toUpperCase(),
    commercial_data: { qty: hit.qty ?? null, price: hit.price ?? null, amount: hit.amount ?? null, unit: hit.unit ?? null },
    packing_data: {
      meters: hit.meters ?? hit.qty ?? null,
      rolls: hit.rolls ?? null,
      net_weight: hit.net_weight ?? null,
      gross_weight: hit.gross_weight ?? null,
      area: hit.area ?? null,
    },
    customs_data: {},
    source_traces: { scan: { article: hit.article } },
    validation_errors: [],
  });
  hit.item_id = id;
  hit.verdict = "ok";
  hit.notes = "добавлено из скана";
  recountWorkspaceWarnings();
  renderWorkspace();
}

function renderExportPreview(data) {
  const host = $("#export-preview");
  const meta = $("#export-preview-meta");
  if (!host) return;
  host.innerHTML = "";
  const files = data?.files || [];
  if (meta) {
    meta.textContent = files.length
      ? `В архив пойдёт ${files.length} файл(ов). В инвойсе, пакинге и спецификации одни и те же позиции.`
      : "Нет файлов для выгрузки.";
  }
  files.forEach((file) => {
    const card = document.createElement("article");
    card.className = "preview-file";
    const sheets = file.sheets || [];
    const tables = sheets
      .map((sheet) => {
        const headers = (sheet.headers || []).map((h) => `<th>${escapeHtml(h)}</th>`).join("");
        const rows = sheet.rows || [];
        const body = rows
          .slice(0, 80)
          .map((row) => {
            const cells = (row || [])
              .map((cell) => {
                const text = cell == null || cell === "" ? "-" : String(cell);
                return longCell(text);
              })
              .join("");
            return `<tr>${cells}</tr>`;
          })
          .join("");
        const more =
          rows.length > 80 ? `<p class="hint">Показаны первые 80 из ${rows.length} строк</p>` : "";
        return `<h4>${escapeHtml(sheet.title || "")} · ${rows.length} строк</h4>
          <div class="table-wrap"><table class="preview-table"><thead><tr>${headers}</tr></thead><tbody>${body}</tbody></table></div>${more}`;
      })
      .join("");
    card.innerHTML = `<h3>${escapeHtml(file.filename || "")}</h3>${tables}`;
    host.appendChild(card);
  });
}

async function refreshExportPreview() {
  const meta = $("#export-preview-meta");
  if (!state.workspace) return;
  try {
    const data = await api("/api/v1/shipments/export/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: state.workspace.title,
        profile_type: state.workspace.profile_type,
        header_fields: state.workspace.header_fields || {},
        items: state.workspace.items || [],
      }),
    });
    renderExportPreview(data);
  } catch (err) {
    if (meta) meta.textContent = `Предпросмотр недоступен: ${humanizeClientError(err.message)}`;
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const text = await res.text();
    let detail = text || res.statusText;
    try {
      const parsed = JSON.parse(text);
      if (parsed?.detail) {
        detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
      }
    } catch {
      /* keep raw text */
    }
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

async function downloadBlob(blob, filename) {
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

async function loadWorkspace(_id) {
  renderWorkspace();
}

function renderWorkspace() {
  const ws = state.workspace;
  if (!ws) return;
  $("#workspace").classList.remove("hidden");
  applyShipmentTitle(ws.title);
  $("#ws-meta").textContent =
    `${ws.profile_type} · ${STATUS_RU[ws.status] || ws.status} · ${(ws.items || []).length} позиций`;

  const badges = $("#files-badges");
  badges.innerHTML = "";
  (ws.files || []).forEach((f) => {
    const parseStatus = f.parse_status || "ok";
    const name = String(f.filename || "").toLowerCase();
    const isPdf = name.endsWith(".pdf");
    const isExcel = /\.(xlsx|xls|xlsm)$/.test(name);
    const clickable = parseStatus !== "skipped" && (isPdf || isExcel || parseStatus === "review");
    const span = document.createElement(clickable ? "button" : "span");
    span.type = clickable ? "button" : undefined;
    span.className = `badge ${f.doc_type ? "" : "unknown"} ${parseStatus === "ok" ? "" : parseStatus} ${clickable ? "clickable" : ""}`.trim();
    const typeRu = DOC_TYPE_RU[f.doc_type] || (isPdf ? "PDF" : isExcel ? "Excel" : "Не определен");
    const ocrHint =
      f.ocr_confidence != null ? ` · распознавание ${Math.round(Number(f.ocr_confidence) * 100)}%` : "";
    let statusHint = "";
    if (parseStatus === "skipped") statusHint = " · пропущен";
    else if (parseStatus === "review") statusHint = " · требуется проверка";
    else if (clickable) statusHint = " · таблица";
    span.title = f.parse_message || (clickable ? "Открыть, как распознался файл" : "");
    span.setAttribute("aria-controls", "review-dialog");
    span.textContent = `${f.filename} - ${typeRu}${ocrHint}${statusHint}`;
    if (clickable) {
      span.addEventListener("click", (event) => {
        event.preventDefault();
        openReviewPanel(f.filename);
      });
    }
    badges.appendChild(span);
  });

  renderScanReview(ws, ws.model_review);
  if ($("#export-preview-panel")) {
    refreshExportPreview();
  }

  const hf = ws.header_fields || {};
  const headerForm = $("#header-form");
  [
    "buyer",
    "buyer_address",
    "seller",
    "seller_address",
    "contract_no",
    "contract_date",
    "invoice_no",
    "invoice_date",
    "delivery_terms",
    "payment_terms",
    "manufacturer",
    "delivery_date",
    "warehouse_address",
  ].forEach((k) => {
    if (headerForm[k]) headerForm[k].value = hf[k] || "";
  });

  const tbody = $("#items-table tbody");
  tbody.innerHTML = "";
  (ws.items || []).forEach((item, idx) => {
    const tr = document.createElement("tr");
    const c = item.commercial_data || {};
    const p = item.packing_data || {};
    const u = item.customs_data || {};
    const errs = item.validation_errors || [];
    tr.className = `item-row ${severityClass(errs)}`.trim();
    tr.dataset.id = item.id;
    tr.title = "Нажмите ячейку или «Изменить», чтобы править позицию";
    const desc = u.description || u.description_ru || u.description_en || "-";
    tr.innerHTML = `
      <td class="row-no">${idx + 1}</td>
      ${longCell(item.article || "-", "article")}
      <td class="num ${fieldSeverity(errs, "rolls")}" data-edit="edit-rolls">${formatNum(p.rolls ?? p.boxes, 0)}</td>
      <td class="num ${fieldSeverity(errs, "meters")}" data-edit="edit-qty">${formatNum(p.meters ?? c.qty)}</td>
      <td class="num ${fieldSeverity(errs, "width")}" data-edit="edit-width">${formatNum(p.width, 3)}</td>
      <td class="num ${fieldSeverity(errs, "area")}" data-edit="edit-area">${formatNum(p.area, 3)}</td>
      <td class="num ${fieldSeverity(errs, "net_weight")}" data-edit="edit-net-weight">${formatNum(p.net_weight)}</td>
      <td class="num ${fieldSeverity(errs, "gross_weight")}" data-edit="edit-gross-weight">${formatNum(p.gross_weight)}</td>
      <td class="money ${fieldSeverity(errs, "price")}" data-edit="edit-price">${formatMoney(c.price)}</td>
      <td class="money ${fieldSeverity(errs, "amount")}" data-edit="edit-amount">${formatMoney(c.amount)}</td>
      <td class="code ${fieldSeverity(errs, "hs_code")}" data-edit="edit-hs">${escapeHtml(u.hs_code || "-")}</td>
      <td class="code ${fieldSeverity(errs, "tnved_code")}" data-edit="edit-tnved">${escapeHtml(u.tnved_code || "-")}</td>
      ${longCell(desc, "desc")}
      <td class="flags-cell">
        <button type="button" class="btn compact-btn item-edit-btn" data-edit-item="${escapeHtml(item.id)}">Изменить</button>
        ${renderFlags(errs)}
      </td>
    `;
    tbody.appendChild(tr);
  });
  fillExcelTotals(ws.items || []);
}

function toggleLongCell(td, open) {
  if (!td?.classList.contains("cell-long")) return;
  td.classList.toggle("is-open", open);
  const hide = td.querySelector(".cell-hide");
  if (hide) hide.hidden = !td.classList.contains("is-open");
}

$("#items-table")?.addEventListener("click", (e) => {
  if (e.target.closest(".cell-short, .cell-hide")) return;
  const row = e.target.closest("tbody tr.item-row");
  if (!row?.dataset.id) return;
  const focusId = e.target.closest("[data-edit]")?.dataset.edit || "";
  openEdit(row.dataset.id, focusId);
});

document.addEventListener("click", (e) => {
  const hide = e.target.closest(".cell-hide");
  if (hide) {
    e.preventDefault();
    e.stopPropagation();
    toggleLongCell(hide.closest("td.cell-long"), false);
    return;
  }
  const short = e.target.closest(".cell-short");
  if (!short) return;
  e.preventDefault();
  e.stopPropagation();
  toggleLongCell(short.closest("td.cell-long"), true);
});

$("#review-dialog")?.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-review-file]");
  if (tab) {
    e.preventDefault();
    fillReviewDialog(state.workspace, tab.dataset.reviewFile);
    return;
  }
  const btn = e.target.closest("[data-scan-action]");
  if (!btn) return;
  const index = Number(btn.dataset.index);
  const hit = state.workspace?.model_review?.items?.[index];
  if (!hit) return;
  if (btn.dataset.scanAction === "apply") applyScanToItem(hit);
  if (btn.dataset.scanAction === "add") addItemFromScan(hit);
  fillReviewDialog(state.workspace, $("#review-dialog-tabs .review-tab.active")?.dataset.reviewFile);
});

$("#btn-review")?.addEventListener("click", () => openReviewPanel());
$("#review-dialog-close")?.addEventListener("click", () => $("#review-dialog")?.close());
document.querySelectorAll("dialog").forEach((dialog) => {
  dialog.addEventListener("close", setModalScrollLock);
});
document.addEventListener(
  "wheel",
  (event) => {
    if (!document.documentElement.classList.contains("modal-open")) return;
    if (event.target.closest("dialog")) return;
    event.preventDefault();
  },
  { passive: false }
);

function editFlagHtml(errors) {
  const active = (errors || []).filter((e) => !e.resolved);
  if (!active.length) {
    return '<p class="hint">Замечаний по строке нет. Можно править значения вручную.</p>';
  }
  return active
    .map((e) => {
      const typeLabel = ERROR_TYPE_RU[e.error_type] || SEVERITY_RU[e.severity] || e.error_type || "";
      const field = FIELD_RU[e.field_name] || e.field_name || "";
      const details = e.details || {};
      const sources = Array.isArray(details.sources) ? details.sources.join("; ") : "";
      const compared = [
        details.was != null ? `было ${details.was}` : "",
        details.excel != null ? `Excel ${details.excel}` : "",
        details.scan != null ? `скан ${details.scan}` : "",
        details.catalog != null ? `справочник: ${details.catalog}` : "",
      ]
        .filter(Boolean)
        .join(", ");
      const reason = e.message || details.reason || "";
      return `<div class="edit-flag ${escapeHtml(e.severity || "")}">
        <strong>${escapeHtml(typeLabel)}${field ? ` · ${escapeHtml(field)}` : ""}</strong>
        <div>${escapeHtml(reason)}</div>
        ${compared ? `<div class="hint">С чем сравнить: ${escapeHtml(compared)}</div>` : ""}
        ${sources ? `<div class="hint">Откуда значение: ${escapeHtml(sources)}</div>` : ""}
      </div>`;
    })
    .join("");
}

function editLotsHtml(item) {
  const lots = item?.commercial_data?.lots || [];
  const lines = item?.packing_data?.lines || [];
  if (!lots.length && !lines.length) return "";
  const n = Math.max(lots.length, lines.length);
  const body = [];
  for (let i = 0; i < n; i += 1) {
    const lot = lots[i] || {};
    const line = lines[i] || {};
    body.push(`<tr>
      <td>${i + 1}</td>
      <td class="num">${formatNum(lot.qty ?? "")}</td>
      <td class="money">${formatMoney(lot.amount ?? "")}</td>
      <td class="num">${formatNum(line.net_weight ?? "")}</td>
      <td class="num">${formatNum(line.gross_weight ?? "")}</td>
      <td>${escapeHtml(line.measurement || lot.color || "")}</td>
    </tr>`);
  }
  return `<p class="hint">Строки-продолжения внутри этой позиции (не отдельные номера товара)</p>
    <table><thead><tr><th>№</th><th class="num">Кол-во</th><th>Сумма</th><th class="num">Нетто</th><th class="num">Брутто</th><th>Упаковка</th></tr></thead><tbody>${body.join("")}</tbody></table>`;
}

function openEdit(itemId, focusId) {
  if (!state.workspace?.items) {
    alert("Сначала загрузите и обработайте документы.");
    return;
  }
  const item = state.workspace.items.find((i) => String(i.id) === String(itemId));
  if (!item) {
    alert("Позиция не найдена. Загрузите документы повторно.");
    return;
  }
  state.selectedItemId = itemId;
  editField("edit-item-id").value = itemId;
  editField("edit-article").value = item.article || "";
  editField("edit-rolls").value = item.packing_data?.rolls ?? item.packing_data?.boxes ?? "";
  editField("edit-qty").value = item.packing_data?.meters ?? item.commercial_data?.qty ?? "";
  editField("edit-width").value = item.packing_data?.width ?? "";
  editField("edit-area").value = item.packing_data?.area ?? "";
  editField("edit-price").value = item.commercial_data?.price ?? "";
  editField("edit-amount").value = item.commercial_data?.amount ?? "";
  editField("edit-net-weight").value = item.packing_data?.net_weight ?? "";
  editField("edit-gross-weight").value = item.packing_data?.gross_weight ?? "";
  editField("edit-hs").value = item.customs_data?.hs_code ?? "";
  editField("edit-tnved").value = item.customs_data?.tnved_code ?? "";
  editField("edit-desc-en").value =
    item.customs_data?.description ?? item.customs_data?.description_en ?? "";
  editField("edit-desc-ru").value = item.customs_data?.description_ru ?? "";
  $("#edit-status").textContent = "";
  const flagsEl = $("#edit-flags");
  if (flagsEl) flagsEl.innerHTML = editFlagHtml(item.validation_errors);
  const srcEl = $("#edit-sources");
  if (srcEl) {
    const traces = item.source_traces || {};
    const sources = traces.sources || Object.keys(traces);
    srcEl.textContent = sources && sources.length
      ? `Откуда взяты данные: ${[].concat(sources).join("; ")}`
      : "";
  }
  const lotsEl = $("#edit-lots");
  if (lotsEl) lotsEl.innerHTML = editLotsHtml(item);
  $("#edit-dialog").showModal();
  setModalScrollLock();
  const focusEl = focusId ? editField(focusId) : editField("edit-article");
  if (focusEl && typeof focusEl.focus === "function") {
    requestAnimationFrame(() => {
      focusEl.focus();
      if (typeof focusEl.select === "function") focusEl.select();
    });
  }
}

async function saveEditedItem() {
  if (!state.workspace) {
    alert("Данные сеанса недоступны. Загрузите документы повторно.");
    return;
  }
  const itemId = editField("edit-item-id").value;
  if (!itemId) return;

  const saveBtn = $("#edit-save");
  const status = $("#edit-status");
  saveBtn.disabled = true;
  status.textContent = "Сохранение…";

  const payload = {
    article: editField("edit-article").value || null,
    commercial_data: {
      qty: numOrNull(editField("edit-qty").value),
      price: numOrNull(editField("edit-price").value),
      amount: numOrNull(editField("edit-amount").value),
    },
    packing_data: {
      rolls: numOrNull(editField("edit-rolls").value),
      boxes: numOrNull(editField("edit-rolls").value),
      meters: numOrNull(editField("edit-qty").value),
      width: numOrNull(editField("edit-width").value),
      area: numOrNull(editField("edit-area").value),
      net_weight: numOrNull(editField("edit-net-weight").value),
      gross_weight: numOrNull(editField("edit-gross-weight").value),
    },
    customs_data: {
      hs_code: editField("edit-hs").value || null,
      tnved_code: editField("edit-tnved").value || null,
      description: editField("edit-desc-en").value || null,
      description_en: editField("edit-desc-en").value || null,
      description_ru: editField("edit-desc-ru").value || null,
    },
  };

  try {
    const idx = state.workspace.items.findIndex((row) => String(row.id) === String(itemId));
    if (idx < 0) throw new Error("Позиция не найдена");
    const item = state.workspace.items[idx];
    state.workspace.items[idx] = {
      ...item,
      article: payload.article,
      commercial_data: { ...(item.commercial_data || {}), ...payload.commercial_data },
      packing_data: { ...(item.packing_data || {}), ...payload.packing_data },
      customs_data: { ...(item.customs_data || {}), ...payload.customs_data },
      validation_errors: (item.validation_errors || []).map((err) => ({ ...err, resolved: true })),
    };
    $("#edit-dialog").close();
    renderWorkspace();
    $("#upload-status").textContent = "Изменения сохранены в текущем сеансе. При обновлении страницы данные будут потеряны.";
  } catch (err) {
    status.textContent = `Ошибка: ${err.message}`;
    alert(`Не удалось сохранить позицию: ${err.message}`);
  } finally {
    saveBtn.disabled = false;
  }
}

$("#edit-form").addEventListener("submit", (e) => {
  e.preventDefault();
  saveEditedItem();
});

$("#edit-cancel").addEventListener("click", () => {
  $("#edit-dialog").close();
});

function numOrNull(v) {
  if (v === "" || v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

$("#header-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.workspace) return;
  const form = e.target;
  const payload = {
    buyer: form.buyer.value,
    buyer_address: form.buyer_address?.value || "",
    seller: form.seller.value,
    seller_address: form.seller_address?.value || "",
    contract_no: form.contract_no.value,
    contract_date: form.contract_date.value,
    invoice_no: form.invoice_no.value,
    invoice_date: form.invoice_date.value,
    delivery_terms: form.delivery_terms.value,
    payment_terms: form.payment_terms.value,
    manufacturer: form.manufacturer.value,
    delivery_date: form.delivery_date.value,
    warehouse_address: form.warehouse_address.value,
  };
  try {
    const prevInvoice = shipmentTitleFromHeader(state.workspace.header_fields);
    const nextInvoice = shipmentTitleFromHeader(payload);
    state.workspace.header_fields = { ...(state.workspace.header_fields || {}), ...payload };
    const titleNow = (state.workspace.title || "").trim();
    if (nextInvoice && (isGenericTitle(titleNow) || titleNow === prevInvoice)) {
      applyShipmentTitle(nextInvoice, { force: true });
    }
    renderWorkspace();
  } catch (err) {
    alert(`Не удалось сохранить шапку: ${err.message}`);
  }
});

$("#btn-export").addEventListener("click", async () => {
  const btn = $("#btn-export");
  const box = $("#export-result");
  if (!state.workspace) {
    box.textContent = "Сначала загрузите и обработайте документы.";
    return;
  }
  btn.disabled = true;
  const prevLabel = btn.textContent;
  btn.textContent = "Формирование…";
  box.textContent = "Формирование Excel…";
  try {
    const res = await fetch("/api/v1/shipments/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: state.workspace.title,
        profile_type: state.workspace.profile_type,
        header_fields: state.workspace.header_fields || {},
        items: state.workspace.items || [],
      }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText);
    }
    const blob = await res.blob();
    const zipName = `${state.workspace.title || "export"}_Excel.zip`;
    await downloadBlob(blob, zipName);
    box.textContent = `Файл сохранен: ${zipName}`;
  } catch (err) {
    box.textContent = `Ошибка экспорта: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = prevLabel;
  }
});

function onTitleEdited(event) {
  const value = (event.target.value || "").trim();
  if (state.workspace && value) state.workspace.title = value;
  const other = event.target === $("#ws-title") ? currentTitleInput() : $("#ws-title");
  if (other && document.activeElement !== other) other.value = event.target.value;
}

currentTitleInput()?.addEventListener("input", onTitleEdited);
$("#ws-title")?.addEventListener("input", onTitleEdited);

function paintOpenCodeStatus(data) {
  const el = $("#opencode-status");
  const label = $("#opencode-status-label");
  const money = $("#opencode-status-money");
  if (!el || !label) return;
  const status = data && data.status ? data.status : "down";
  el.className = `model-status ${status}`;
  const modelName = (data && (data.title || data.model_label)) || "Модель не отвечает";
  label.textContent = modelName;
  if (money) money.textContent = (data && data.money) || "";
  const detail = (data && data.detail) || "";
  el.title = detail ? `${modelName}. ${detail}` : "Нажми, чтобы обновить модель и баланс";
}

async function refreshOpenCodeStatus() {
  try {
    const res = await fetch("/api/health/opencode", { cache: "no-store" });
    const data = await res.json();
    if (!res.ok) {
      paintOpenCodeStatus({
        status: "down",
        title: res.status === 404 ? "Сайт ещё без проверки модели" : (data.title || "Модель не отвечает"),
        detail: humanizeClientError(data.detail || data.title || `HTTP ${res.status}`),
      });
      return;
    }
    paintOpenCodeStatus(data);
  } catch (err) {
    paintOpenCodeStatus({
      status: "down",
      title: "Модель не отвечает",
      detail: humanizeClientError(err && err.message),
    });
  }
}

async function pingOpenCode() {
  const label = $("#opencode-status-label");
  if (label) label.textContent = "Обновляю баланс…";
  await refreshOpenCodeStatus();
}

$("#opencode-status")?.addEventListener("click", pingOpenCode);
refreshOpenCodeStatus();
setInterval(refreshOpenCodeStatus, 15000);
