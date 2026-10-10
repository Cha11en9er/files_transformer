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

const PROFILE_RU = {
  "18233": "Три Excel: Инвойс + Пакинг + Спецификация",
  BEIJING: "Одна книга: Invoice + Packing list + Specification + Описание",
};

const ROLE_FROM = {
  invoice: "инвойса",
  packing: "пакинга",
  specification: "спецификации",
};

function emptyColumnLayout() {
  return {
    invoice: { hidden: [], extra: [] },
    packing: { hidden: [], extra: [] },
    specification: { hidden: [], extra: [] },
  };
}

const state = {
  shipmentId: null,
  workspace: null,
  selectedItemId: null,
  pendingFiles: [],
  lastDupes: [],
  sessionLocked: false,
  role: "invoice",
  columnLayout: emptyColumnLayout(),
  roleColumns: null,
  roleColumnsKey: "",
  pendingDelete: null,
  pendingAdd: null,
};
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
  if (
    low.includes("failed to fetch") ||
    low.includes("networkerror") ||
    low === "network error" ||
    low.includes("network error") ||
    low.includes("load failed") ||
    low.includes("the network connection was lost") ||
    low.includes("ns_error_net")
  ) {
    return "Связь с сервером оборвалась во время обработки. Повтори загрузку — если код уже прочитал файлы, таблица должна прийти даже без ответа модели.";
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
  if (low.includes("не удалось получить результат")) {
    return "Сервер не вернул результат обработки. Повтори загрузку.";
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

function rowCountLabel(n) {
  const n10 = n % 10;
  const n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return `${n} строку таблицы`;
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return `${n} строки таблицы`;
  return `${n} строк таблицы`;
}

function showToast(message, { kind = "ok", ms = 3200 } = {}) {
  const text = String(message || "").trim();
  if (!text) return;
  const host = $("#toast-stack");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  const mark = kind === "error" ? "!" : kind === "info" ? "i" : "✓";
  el.innerHTML = `<span class="toast-mark" aria-hidden="true">${mark}</span><span class="toast-text"></span>`;
  el.querySelector(".toast-text").textContent = text;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  window.setTimeout(() => {
    el.classList.add("hide");
    el.classList.remove("show");
    window.setTimeout(() => el.remove(), 220);
  }, ms);
}

function stableJson(value) {
  return JSON.stringify(value ?? null);
}

function headerSnapshot(fields) {
  const src = fields || {};
  const keys = [
    "buyer",
    "buyer_address",
    "seller",
    "seller_address",
    "contract_no",
    "contract_date",
    "invoice_no",
    "invoice_date",
    "delivery_terms",
    "currency",
    "payment_terms",
    "manufacturer",
    "delivery_date",
    "warehouse_address",
  ];
  const out = {};
  keys.forEach((key) => {
    out[key] = String(src[key] ?? "").trim();
  });
  return out;
}

function itemEditSnapshot(item) {
  const c = item?.commercial_data || {};
  const p = item?.packing_data || {};
  const u = item?.customs_data || {};
  return {
    article: item?.article ?? null,
    qty: c.qty ?? null,
    price: c.price ?? null,
    amount: c.amount ?? null,
    rolls: p.rolls ?? p.boxes ?? null,
    meters: p.meters ?? null,
    width: p.width ?? null,
    area: p.area ?? null,
    net_weight: p.net_weight ?? null,
    gross_weight: p.gross_weight ?? null,
    hs_code: u.hs_code ?? null,
    tnved_code: u.tnved_code ?? null,
    description_en: u.description_en || u.description || null,
    description_ru: u.description_ru || null,
  };
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
  state.role = "invoice";
  state.columnLayout = emptyColumnLayout();
  state.roleColumns = null;
  state.roleColumnsKey = "";
  $("#workspace")?.classList.add("hidden");
  const tbody = $("#items-table tbody");
  if (tbody) tbody.innerHTML = "";
  const thead = $("#items-table thead");
  if (thead) thead.innerHTML = "";
  const tfoot = $("#items-table tfoot");
  if (tfoot) tfoot.innerHTML = "";
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
  const beforeCount = state.pendingFiles.length;
  try {
    if (!incoming.length) {
      if (raw.length && hint) {
        const skipped = raw
          .filter((file) => file && file.name && !isUsefulUpload(file))
          .map((file) => file.name);
        const odd = skipped.filter((name) => /\.(doc|docx|rtf|odt)$/i.test(name));
        if (odd.length) {
          hint.textContent =
            `Формат не поддерживается на этапе 1: ${odd.join(", ")}. Нужны Excel (.xlsx/.xls) или PDF.`;
        } else {
          hint.textContent = "В выборе нет Excel, PDF или картинок. Нужны исходники, не папка ДС.";
        }
        hint.className = "status";
      }
      return;
    }
    const rejected = raw.filter((file) => file && file.name && !isUsefulUpload(file) && !isSkippedRelPath(file.webkitRelativePath || file.name));
    const odd = rejected.filter((file) => /\.(doc|docx|rtf|odt)$/i.test(file.name));
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
    const added = Math.max(0, state.pendingFiles.length - beforeCount);
    if (added > 0) {
      showToast(`Вы загрузили ${fileCountLabel(added)}`, { kind: "ok" });
    }
    if (odd.length && hint) {
      hint.textContent =
        `Пропущен неподдерживаемый формат: ${odd.map((f) => f.name).join(", ")}. Этап 1: Excel и PDF.`;
      hint.className = "status";
    }
  } catch (err) {
    if (hint) {
      hint.textContent = `Не удалось взять файлы: ${err && err.message ? err.message : err}`;
      hint.className = "status";
    }
    showToast(`Не удалось взять файлы: ${humanizeClientError(err && err.message ? err.message : err)}`, {
      kind: "error",
      ms: 4500,
    });
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

const PIPELINE = [
  { id: "receive", title: "Приём файлов" },
  { id: "reconcile", title: "Сверка позиций" },
  { id: "read", title: "Чтение файлов кодом" },
  { id: "photos", title: "Фото страниц" },
  { id: "verdict", title: "Обычная модель" },
  { id: "verdict2", title: "Улучшенная модель" },
  { id: "assemble", title: "Сборка таблицы" },
  { id: "done", title: "Распознавание выполнено" },
];

function formatStepSeconds(ms) {
  const sec = Math.max(0, Math.round(ms / 1000));
  if (sec < 60) return `${sec} с`;
  const min = Math.floor(sec / 60);
  const rest = sec % 60;
  return rest ? `${min} мин ${rest} с` : `${min} мин`;
}

const pipeline = {
  index: 0,
  timer: null,
  finished: false,
  failed: false,
  steps: [],
  reset() {
    this.index = 0;
    this.finished = false;
    this.failed = false;
    this.steps = PIPELINE.map((step) => ({
      ...step,
      state: "wait",
      started: 0,
      ended: 0,
      serverSeconds: null,
      detail: "",
    }));
    const error = $("#parse-error");
    error.textContent = "";
    error.classList.add("hidden");
    $("#parse-progress").classList.remove("hidden");
    $("#parse-progress-fill").style.width = "0%";
    this.reach("receive");
    this.timer = setInterval(() => this.paint(false), 1000);
  },
  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  },
  reach(id, extra) {
    const next = this.steps.findIndex((step) => step.id === id);
    const seconds = extra && Number.isFinite(extra.seconds) ? extra.seconds : null;
    const detail = extra?.detail || "";
    if (next < 0) return;
    if (next === this.index && this.steps[this.index].state === "run") {
      const current = this.steps[this.index];
      if (seconds != null) current.serverSeconds = seconds;
      if (detail) current.detail = detail;
      this.paint(false);
      return;
    }
    if (next < this.index) return;
    const now = Date.now();
    const current = this.steps[this.index];
    if (current && current.state === "run") {
      current.ended = now;
      current.state = "done";
    }
    for (let i = this.index + 1; i < next; i += 1) {
      const skipped = this.steps[i];
      if (skipped.state === "wait") skipped.state = "skip";
    }
    this.index = next;
    const step = this.steps[next];
    step.state = "run";
    step.started = step.started || now;
    if (seconds != null) step.serverSeconds = seconds;
    if (detail) step.detail = detail;
    this.paint(true);
  },
  complete() {
    const now = Date.now();
    const last = this.steps.length - 1;
    this.steps.forEach((step, i) => {
      if (step.state === "done" || step.state === "skip" || step.state === "fail") return;
      if (step.state === "wait" && i !== last) {
        step.state = "skip";
        return;
      }
      if (!step.started) step.started = now;
      step.ended = now;
      step.state = "done";
    });
    this.index = last;
    this.finished = true;
    this.stop();
    this.paint(true);
  },
  fail(message) {
    const step = this.steps[this.index];
    if (step && step.state === "run") {
      step.ended = Date.now();
      step.state = "fail";
    }
    this.failed = true;
    this.stop();
    const error = $("#parse-error");
    error.textContent = message;
    error.classList.remove("hidden");
    const rail = $("#step-rail");
    if (rail) rail.dataset.index = "";
    this.paint(false);
  },
  windowOf() {
    const last = this.steps.length - 1;
    if (this.finished || this.index >= last) return { start: last, length: 1, highlight: 0 };
    if (this.index === last - 1) return { start: this.index, length: 2, highlight: 0 };
    if (this.index <= 1) return { start: 0, length: 3, highlight: this.index };
    return { start: this.index - 1, length: 3, highlight: 1 };
  },
  timeLabel(step, idleText) {
    const note = step.detail ? ` · ${step.detail}` : "";
    if (step.state === "run" && step.started) {
      return `идёт ${formatStepSeconds(Date.now() - step.started)}${note}`;
    }
    if ((step.state === "done" || step.state === "fail") && (step.started || step.serverSeconds != null)) {
      const client = step.started ? (step.ended || Date.now()) - step.started : 0;
      const server = step.serverSeconds != null ? step.serverSeconds * 1000 : 0;
      return `${formatStepSeconds(Math.max(client, server))}${note}`;
    }
    if (step.state === "skip") return idleText === "" ? "не было" : "";
    return idleText || "";
  },
  paint(animate) {
    const rail = $("#step-rail");
    if (!rail) return;
    const view = this.windowOf();
    const same = !animate
      && rail.dataset.start === String(view.start)
      && rail.dataset.count === String(view.length)
      && rail.dataset.index === String(this.index);
    if (same) {
      this.refreshTimes();
      return;
    }
    const prev = rail.dataset.start;
    this.paintToken = (this.paintToken || 0) + 1;
    const token = this.paintToken;
    rail.dataset.start = String(view.start);
    rail.dataset.count = String(view.length);
    rail.dataset.index = String(this.index);
    const slice = this.steps.slice(view.start, view.start + view.length);
    const html = slice.map((step, slot) => {
      const tone = step.state === "fail"
        ? "is-fail"
        : slot === view.highlight
          ? "is-current"
          : this.steps.indexOf(step) < this.index
            ? "is-past"
            : "is-next";
      const time = this.timeLabel(step);
      return `<div class="step-chip ${tone}" data-step="${step.id}"><span class="step-name">${escapeHtml(step.title)}</span><span class="step-time">${
        time ? escapeHtml(time) : ""
      }</span></div>`;
    }).join("");
    const denom = Math.max(1, this.steps.length - 1);
    const pct = this.finished ? 100 : Math.round((this.index / denom) * 100);
    const slide = animate && prev !== undefined && prev !== "" && Number(view.start) > Number(prev);
    const apply = () => {
      rail.classList.remove("is-leaving", "is-forward");
      rail.innerHTML = html;
      if (slide) {
        void rail.offsetWidth;
        rail.classList.add("is-forward");
      }
      this.refreshTimes();
      $("#parse-progress-fill").style.width = `${pct}%`;
    };
    if (slide && rail.childElementCount) {
      rail.classList.add("is-leaving");
      window.setTimeout(() => {
        if (token !== this.paintToken) return;
        apply();
      }, 200);
      return;
    }
    apply();
  },
  refreshTimes() {
    this.steps.forEach((step) => {
      const chip = document.querySelector(`.step-chip[data-step="${step.id}"] .step-time`);
      if (chip) chip.textContent = this.timeLabel(step);
    });
    const pop = $("#step-popover");
    pop.innerHTML = this.steps.map((step) => {
      const time = this.timeLabel(step, "");
      return `<div class="step-row is-${step.state}"><span>${escapeHtml(step.title)}</span><span>${escapeHtml(time)}</span></div>`;
    }).join("");
  },
};

function progressSeconds(message) {
  const match = String(message || "").match(/\((\d+)\s*с\)/);
  return match ? Number(match[1]) : null;
}

function progressFrames(message) {
  const match = String(message || "").match(/(\d+)\s*кадр/);
  return match ? `${match[1]} кадров` : "";
}

function progressModel(message) {
  const text = String(message || "").toLowerCase();
  if (text.includes("улучшенная")) return "улучшенная модель";
  if (text.includes("обычная") || text.includes("вердикт модели")) return "обычная модель";
  return "";
}

function pipelineStep(filename, stage, message) {
  const text = `${filename || ""} ${message || ""}`.toLowerCase();
  if (stage === "model2" || text.includes("улучшенная модель") || text.includes("вторая модель")) return "verdict2";
  if (stage === "model" || text.includes("обычная модель") || text.includes("вердикт модели") || text.includes("вердикт")) return "verdict";
  if (text.includes("сборк")) return "assemble";
  if (text.includes("фото")) return "photos";
  if (text.includes("чтени")) return "read";
  if (text.includes("сверк")) return "reconcile";
  if (stage === "parse") return "receive";
  return "";
}

async function processShipment() {
  if (processing || state.sessionLocked) return;
  if (!state.pendingFiles.length) {
    $("#upload-status").textContent = "Выберите файлы или папки.";
    showToast("Выберите файлы или папки", { kind: "info" });
    return;
  }
  setSessionLocked(true, { busy: true, buttonLabel: "Обработка…" });
  showToast("Началась обработка", { kind: "info", ms: 2500 });
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
  $("#upload-status").textContent = "";
  pipeline.reset();
  try {
    const created = await parseUploadStream(fd, {
      onProgress(_current, _total, filename, extra) {
        const id = pipelineStep(filename, extra?.stage, extra?.message);
        if (id) {
          const model = progressModel(extra?.message);
          const step = pipeline.steps.find((item) => item.id === id);
          if (step && model) {
            step.title = id === "verdict2" ? "Улучшенная модель" : "Обычная модель";
          }
          pipeline.reach(id, {
            seconds: progressSeconds(extra?.message),
            detail: progressFrames(extra?.message) || (model && id === "verdict2" ? model : ""),
          });
        }
      },
      onFile() {
        pipeline.reach("assemble");
      },
    });
    pipeline.reach("assemble");
    const run = created.verdict_run || {};
    const firstStep = pipeline.steps.find((item) => item.id === "verdict");
    const secondStep = pipeline.steps.find((item) => item.id === "verdict2");
    const doneStep = pipeline.steps.find((item) => item.id === "done");
    if (firstStep && run.first) firstStep.title = "Обычная модель";
    if (secondStep && run.used_second && run.second) {
      secondStep.title = "Улучшенная модель";
      secondStep.detail = secondStep.detail || "после обычной";
    }
    if (doneStep) {
      if (run.used_second && run.second) {
        doneStep.title = "Готово · обычная + улучшенная";
        doneStep.detail = "сначала обычная, потом улучшенная";
      } else if (run.first) {
        doneStep.title = "Готово · обычная модель";
      }
    }
    await new Promise((resolve) => window.setTimeout(resolve, 700));
    pipeline.complete();
    state.shipmentId = created.id;
    state.workspace = created;
    applyShipmentTitle(created.title, { force: true });
    refreshOpenCodeStatus();
    const skipped = created.skipped_count || 0;
    $("#upload-status").textContent =
      `Готово: ${created.item_count} позиций из ${created.files?.length || state.pendingFiles.length} файлов` +
      (skipped ? `, пропущено: ${skipped}` : "") +
      `, замечаний: ${created.warning_count}. Чтобы обработать другой комплект, обновите страницу.`;
    setSessionLocked(true, { busy: false, buttonLabel: "Обработано" });
    renderWorkspace();
    showToast("Обработка прошла успешно", { kind: "ok", ms: 4000 });
  } catch (err) {
    pipeline.fail(`Ошибка: ${humanizeClientError(err.message)}`);
    setSessionLocked(false, { busy: false, buttonLabel: "Обработать" });
  }
}

async function parseUploadStream(fd, hooks) {
  let res;
  try {
    res = await fetch("/api/v1/shipments/", { method: "POST", body: fd });
  } catch (err) {
    throw new Error(humanizeClientError(err && err.message ? err.message : err));
  }
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
      throw new Error(humanizeClientError(detail));
    }
    return res.json();
  }
  if (!res.body) throw new Error("Нет ответа сервера");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let donePayload = null;
  try {
    while (true) {
      const { done, value } = await reader.read();
      buf += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        let event;
        try {
          event = JSON.parse(line);
        } catch {
          throw new Error("Ответ сервера повреждён. Повтори загрузку.");
        }
        if (event.event === "progress") {
          hooks.onProgress(event.current, event.total, event.filename, {
            stage: event.stage,
            message: event.message,
          });
        } else if (event.event === "file") {
          hooks.onFile(event.filename, event.status, event.message);
        } else if (event.event === "error") {
          throw new Error(humanizeClientError(event.detail || "Ошибка обработки"));
        } else if (event.event === "done") {
          donePayload = event;
        }
      }
      if (done) break;
    }
  } catch (err) {
    if (donePayload) {
      const { event: _partial, ...created } = donePayload;
      return created;
    }
    const msg = err && err.message ? err.message : String(err);
    throw new Error(humanizeClientError(msg));
  }
  if (buf.trim()) {
    try {
      const event = JSON.parse(buf);
      if (event.event === "error") throw new Error(humanizeClientError(event.detail || "Ошибка обработки"));
      if (event.event === "done") donePayload = event;
    } catch (err) {
      if (donePayload) {
        const { event: _event, ...created } = donePayload;
        return created;
      }
      if (err && err.message && !String(err.message).includes("JSON")) throw err;
      throw new Error("Ответ сервера обрезан. Повтори загрузку.");
    }
  }
  if (!res.ok && !donePayload) throw new Error(humanizeClientError(res.statusText || "Ошибка сервера"));
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

let lockedScrollY = 0;

function lockPageScroll() {
  const root = document.documentElement;
  const body = document.body;
  if (root.classList.contains("modal-open")) return;
  lockedScrollY = window.scrollY || window.pageYOffset || 0;
  body.style.position = "fixed";
  body.style.top = `-${lockedScrollY}px`;
  body.style.left = "0";
  body.style.right = "0";
  body.style.width = "100%";
  root.classList.add("modal-open");
}

function unlockPageScroll() {
  const root = document.documentElement;
  const body = document.body;
  if (!root.classList.contains("modal-open")) return;
  root.classList.remove("modal-open");
  body.style.position = "";
  body.style.top = "";
  body.style.left = "";
  body.style.right = "";
  body.style.width = "";
  window.scrollTo(0, lockedScrollY);
}

function setModalScrollLock() {
  const open = [...document.querySelectorAll("dialog")].some((dialog) => dialog.open);
  if (open) lockPageScroll();
  else unlockPageScroll();
}

function openDialog(dialog) {
  if (!dialog) return;
  // Lock before showModal so the browser does not jump the page to top.
  lockPageScroll();
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  setModalScrollLock();
}

function severityClass(errors) {
  // Full-row tint only for hard mismatches - yellow wash made the table look "all white"
  if (!errors?.length) return "";
  if (errors.some((e) => e.severity === "RED" && !e.resolved)) return "sev-RED";
  return "";
}

function formatNum(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "";
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
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  if (!Number.isFinite(n)) return escapeHtml(value);
  return n.toLocaleString("ru-RU", {
    useGrouping: true,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).replace(/\s/g, "\u00a0");
}

// Цена за единицу бывает 0,1030 или 0,0655: два знака её искажают. Знаки после двух показываются, пока они не нули.
function formatPrice(value) {
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  if (!Number.isFinite(n)) return escapeHtml(value);
  return n.toLocaleString("ru-RU", {
    useGrouping: true,
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
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

function articleLabel(item) {
  const raw = item?.article;
  const art = raw == null || raw === "" || raw === "-" ? "" : String(raw);
  const color = item?.commercial_data?.color;
  if (color == null || color === "") return art;
  const c = String(color).trim();
  if (!c) return art;
  if (!art) return c;
  if (String(art).toUpperCase().includes(c.toUpperCase())) return art;
  return `${art} ${c}`;
}

function itemQty(item) {
  const qty = item?.commercial_data?.qty;
  if (qty !== null && qty !== undefined && qty !== "") return qty;
  return item?.packing_data?.meters;
}

function sumField(items, getter, skip) {
  let total = 0;
  let any = false;
  (items || []).forEach((item) => {
    if (skip && skip(item)) return;
    const value = getter(item);
    const n = Number(value);
    if (value === null || value === undefined || value === "" || !Number.isFinite(n)) return;
    total += n;
    any = true;
  });
  return any ? total : null;
}

function isContinuedPackages(item) {
  return Boolean(item?.packing_data?.rolls_continued);
}

function isFabricItems(items) {
  const rows = (items || []).filter((item) => item?.article && item.article !== "-");
  if (!rows.length) return false;
  const hits = rows.filter((item) => {
    const packing = item.packing_data || {};
    return packing.width != null && packing.width !== "" || packing.meters != null && packing.meters !== "";
  });
  return hits.length >= Math.max(1, rows.length * 0.4);
}

function itemField(item, key) {
  const commercial = item?.commercial_data || {};
  const packing = item?.packing_data || {};
  const customs = item?.customs_data || {};
  if (key === "article") return articleLabel(item);
  if (key === "description") return customs.description || customs.description_ru || customs.description_en;
  if (key === "hs_code") return customs.hs_code;
  if (key === "hs_alt") return customs.tnved_code;
  if (key === "qty") return commercial.qty ?? packing.meters;
  if (key === "packages") return packing.rolls ?? packing.boxes;
  if (key === "unit") return commercial.unit;
  if (key === "price") return commercial.price;
  if (key === "amount") return commercial.amount;
  if (key === "color") return commercial.color;
  if (key === "size") return commercial.size;
  if (key === "brand") return customs.brand;
  if (key === "manufacturer") return customs.manufacturer;
  if (key === "country") return customs.country;
  if (key === "net_weight") return packing.net_weight;
  if (key === "gross_weight") return packing.gross_weight;
  if (key === "width") return packing.width;
  if (key === "area") return packing.area;
  if (key === "volume") return packing.volume;
  if (key === "gsm") return packing.gsm;
  if (key === "measurement") return packing.measurement;
  if (key === "package_type") return packing.package_type;
  if (key === "pcs_per_carton") return packing.pcs_per_carton;
  return null;
}

const EDIT_FOR = {
  article: "edit-article",
  qty: "edit-qty",
  packages: "edit-rolls",
  width: "edit-width",
  area: "edit-area",
  net_weight: "edit-net-weight",
  gross_weight: "edit-gross-weight",
  price: "edit-price",
  amount: "edit-amount",
  hs_code: "edit-hs",
  hs_alt: "edit-tnved",
  description: "edit-desc-en",
};

function fallbackRoleColumns(role) {
  const common = [
    { key: "article", title: "Art No. / Артикул", kind: "text" },
    { key: "description", title: "Description / Наименование", kind: "text" },
    { key: "qty", title: "Quantity / Количество", kind: "num" },
    { key: "packages", title: "Packages / Места", kind: "num" },
    { key: "amount", title: "Amount / Сумма", kind: "money" },
  ];
  if (role === "packing") {
    return [
      ...common,
      { key: "net_weight", title: "Net weight / Нетто", kind: "num" },
      { key: "gross_weight", title: "Gross weight / Брутто", kind: "num" },
    ];
  }
  if (role === "specification") {
    return [
      ...common,
      { key: "hs_code", title: "HS code / Код ТН ВЭД", kind: "code" },
      { key: "brand", title: "Brand / Торговая марка", kind: "text" },
      { key: "manufacturer", title: "Manufacturer / Производитель", kind: "text" },
      { key: "country", title: "Country / Страна", kind: "text" },
      { key: "color", title: "Color / Цвет", kind: "text" },
      { key: "size", title: "Size / Размер", kind: "text" },
    ];
  }
  return [
    ...common,
    { key: "hs_code", title: "H.S. Code / Код ТН ВЭД", kind: "code" },
    { key: "price", title: "Price / Цена", kind: "money" },
    { key: "color", title: "Color / Цвет", kind: "text" },
    { key: "brand", title: "Brand / Торговая марка", kind: "text" },
  ];
}

function visibleRoleColumns() {
  const role = state.role || "invoice";
  const base = (state.roleColumns && state.roleColumns[role]) || fallbackRoleColumns(role);
  const layout = state.columnLayout[role] || { hidden: [], extra: [] };
  const hidden = new Set(layout.hidden || []);
  const cols = base.filter((col) => !hidden.has(col.key));
  (layout.extra || []).forEach((extra) => {
    cols.push({
      key: `extra:${extra.id}`,
      title: extra.title,
      kind: "text",
      extra: true,
    });
  });
  return cols;
}

function extraItemValue(item, columnId) {
  return ((item.extra_columns || {})[state.role] || {})[columnId];
}

function formatRoleCell(item, col) {
  const key = col.key || "";
  const value = key.startsWith("extra:") ? extraItemValue(item, key.slice(6)) : itemField(item, key);
  if (col.kind === "money") return formatMoney(value);
  if (col.kind === "num") {
    if (key === "packages") return formatNum(value, 0);
    if (key === "width" || key === "area" || key === "volume") return formatNum(value, 3);
    return formatNum(value);
  }
  if (value == null || value === "" || value === "-") return "";
  return escapeHtml(value);
}

function cellClassFor(col, errs) {
  const bits = [];
  if (col.kind === "num") bits.push("num");
  if (col.kind === "money") bits.push("money");
  if (col.kind === "code") bits.push("code");
  if (col.key === "article") bits.push("article");
  if (col.key === "description") bits.push("desc");
  const field = col.key === "packages" ? "rolls" : col.key === "hs_alt" ? "tnved_code" : col.key;
  const sev = fieldSeverity(errs, field);
  if (sev) bits.push(sev);
  return bits.join(" ");
}

async function ensureRoleColumns() {
  const ws = state.workspace;
  if (!ws) return;
  const fabric = isFabricItems(ws.items || []);
  const key = `${ws.profile_type}:${fabric}`;
  if (state.roleColumnsKey === key && state.roleColumns) return;
  const data = await api(
    `/api/v1/shipments/columns?profile_type=${encodeURIComponent(ws.profile_type)}&fabric=${fabric ? "true" : "false"}`
  );
  state.roleColumns = data.roles;
  state.roleColumnsKey = key;
}

function fillExcelTotals(items, review) {
  const row = $("#items-totals-row");
  if (!row) return;
  const stated = review?.totals || {};
  const sumKeys = new Set(["packages", "qty", "area", "net_weight", "gross_weight", "amount", "volume"]);
  row.querySelectorAll("[data-total]").forEach((cell) => {
    const key = cell.getAttribute("data-total");
    if (!sumKeys.has(key)) {
      cell.textContent = key === "article" ? "Итого" : "";
      return;
    }
    // Итог — сумма клеток таблицы, не TOTAL из файла. Слитое число мест в сумму один раз.
    const value = sumField(
      items,
      (item) => itemField(item, key),
      key === "packages" ? isContinuedPackages : null
    );
    const fmt = key === "amount" ? formatMoney : (n) => formatNum(n, key === "packages" ? 0 : 2);
    cell.textContent = value == null ? "" : fmt(value);
    const fileKey = key === "packages" ? "rolls" : key === "qty" ? "meters" : key;
    const fileValue = stated[fileKey] ?? (key === "qty" ? stated.qty : null);
    const fileNum = Number(fileValue);
    const tableNum = value == null ? null : Number(value);
    const fileOk = fileValue != null && fileValue !== "" && Number.isFinite(fileNum);
    const tableOk = tableNum != null && Number.isFinite(tableNum);
    const differs =
      fileOk && ((tableOk && Math.abs(fileNum - tableNum) > 0.05) || !tableOk);
    cell.classList.toggle("sev-RED", differs);
    cell.title = differs
      ? `в файле ${fmt(fileNum)}, в таблице ${tableOk ? fmt(tableNum) : "нет"}`
      : fileOk && tableOk
        ? `сумма строк ${fmt(tableNum)}, в файле ${fmt(fileNum)}`
        : "";
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
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "number") return formatNum(value);
  const text = String(value).trim();
  if (!text || text === "-") return "";
  return escapeHtml(value);
}

function isIndexHeader(title) {
  return /^(№|no\.?|n[°º]|#|item|poz\.?)$/i.test(String(title || "").trim());
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

function renderRecognizedTable(rows, file) {
  const list = recognizedRows({ table: rows });
  if (!list.length) {
    return "<p class=\"hint\">В этом файле таблица товаров не собралась.</p>";
  }
  // Слева уже есть порядковый № — колонку №/No из документа не дублируем.
  const headers = sourceColumnHeaders(file || { table: rows }).filter((title) => !isIndexHeader(title));
  const plus = file
    ? (title) =>
        `<button type="button" class="col-action" data-col-add="${escapeHtml(title)}" aria-label="Добавить столбец">+</button>`
    : "";
  const body = list
    .slice(0, 250)
    .map((row, index) => {
      const cells = headers
        .map((title) => `<td>${contextCell(sourceColumnValue(row, title))}</td>`)
        .join("");
      return `<tr><td class="row-no">${index + 1}</td>${cells}</tr>`;
    })
    .join("");
  const head = headers
    .map(
      (title) =>
        `<th><span class="th-inner">${escapeHtml(title)}${plus ? plus(title) : ""}</span></th>`
    )
    .join("");
  return `<div class="table-wrap"><table class="context-mini">
    <thead><tr><th class="row-no">№</th>${head}</tr></thead>
    <tbody>${body}</tbody>
  </table></div>`;
}

function renderTotalsStrip(review) {
  const excelTotals = review?.excel_totals || {};
  const scanTotals = review?.totals || {};
  const hasExcel = Boolean(review?.excel_attached || (review?.context?.excel || []).length);
  const parts = [
    ["rolls", "Места"],
    ["qty", "Кол-во"],
    ["amount", "Сумма"],
    ["net_weight", "Нетто"],
    ["gross_weight", "Брутто"],
  ]
    .map(([key, label]) => {
      const tableVal = excelTotals[key] ?? (key === "qty" ? excelTotals.meters : null);
      const pdfVal = scanTotals[key] ?? (key === "qty" ? scanTotals.meters : null);
      if (tableVal == null && pdfVal == null) return "";
      const tableOk = tableVal != null && tableVal !== "" && Number.isFinite(Number(tableVal));
      const fileOk = pdfVal != null && pdfVal !== "" && Number.isFinite(Number(pdfVal));
      const mismatch =
        fileOk &&
        ((tableOk && Math.abs(Number(tableVal) - Number(pdfVal)) > (key === "rolls" ? 0.05 : 0.5)) ||
          !tableOk);
      const fmt = key === "amount" ? formatMoney : (n) => formatNum(n, key === "rolls" ? 0 : 2);
      const shown = (n) => (n == null || n === "" ? "нет" : fmt(n));
      const left = hasExcel ? `таблица ${shown(tableVal)}` : `собрано ${shown(tableVal)}`;
      return `<div class="scan-total${mismatch ? " mismatch" : ""}"><div class="k">${escapeHtml(label)}</div><div class="v">${left} · в файле ${shown(pdfVal)}</div></div>`;
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
  const raw = [...pdfs, ...excel];
  return { pdfs, excel, files: raw.filter((file) => recognizedRows(file).length > 0) };
}

function renderReviewMeta(ws, _review) {
  const reviewMeta = $("#model-review-meta");
  const launch = $("#review-launch");
  const hasTable = (ws.items || []).length > 0 || reviewSources(ws).files.length > 0;
  if (launch) launch.classList.toggle("hidden", !hasTable);
  if (!reviewMeta) return;
  const run = ws.verdict_run || {};
  if (run.used_second && run.second) {
    reviewMeta.textContent = "Сначала обычная модель, потом улучшенная.";
  } else if (run.first) {
    reviewMeta.textContent = "Вердикт обычной модели.";
  } else if (_review?.meaning) {
    reviewMeta.textContent = String(_review.meaning).slice(0, 180);
  } else {
    reviewMeta.textContent = "";
  }
}

function sourceKind(file) {
  const name = String(file?.filename || "").toLowerCase().split(" / ")[0];
  if (file?.kind === "pdf" || name.endsWith(".pdf")) return "PDF";
  if (file?.kind === "image" || /\.(jpe?g|png)$/.test(name)) return "Картинка";
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

function finalReviewRows(ws) {
  return (ws.items || []).map((item) => {
    const c = item.commercial_data || {};
    const p = item.packing_data || {};
    const u = item.customs_data || {};
    return {
      article: articleLabel(item),
      rolls: p.rolls ?? p.boxes,
      qty: c.qty,
      meters: p.meters,
      price: c.price,
      amount: c.amount,
      net_weight: p.net_weight,
      gross_weight: p.gross_weight,
      hs_code: u.hs_code,
      customs_code: u.tnved_code,
      description: u.description || u.description_ru || u.description_en,
    };
  });
}

function renderFilePane(file) {
  const pages = (file.pages || []).length ? `стр. ${(file.pages || []).join(", ")}` : "";
  const sheets = (file.sheets || []).length ? `листы: ${(file.sheets || []).join(", ")}` : "";
  const n = recognizedRows(file).length;
  const kind = sourceKind(file);
  const capped =
    file.total_rows && file.total_rows > (file.table || []).length
      ? `<p class="hint">Показаны первые ${(file.table || []).length} из ${file.total_rows} строк.</p>`
      : "";
  return `
    <div class="review-file-meta">
      <strong>${escapeHtml(kind)} ${escapeHtml(file.filename || "")}</strong>
      <span class="hint">${escapeHtml([pages || sheets, file.meaning, `${n} строк`].filter(Boolean).join(" · "))}</span>
    </div>
    ${renderRecognizedTable(file.table, file)}
    ${capped}
  `;
}

function fillReviewDialog(ws, filename) {
  const tabs = $("#review-dialog-tabs");
  const body = $("#review-dialog-body");
  const meta = $("#review-dialog-meta");
  if (!tabs || !body) return;
  const review = ws.model_review;
  const { files } = reviewSources(ws);
  const finalRows = finalReviewRows(ws);
  const wanted = filename && filename !== "__final__" ? findReviewFile(files, filename) : null;
  const title = $("#review-dialog-title");
  if (title) title.textContent = wanted ? `Таблица из ${sourceKind(wanted)}` : "Что распозналось";
  const finalActive = !wanted ? " active" : "";
  const fileTabs = files
    .map((file) => {
      const n = recognizedRows(file).length;
      const active = wanted && file.filename === wanted.filename ? " active" : "";
      return `<button type="button" class="review-tab${active}" data-review-file="${escapeHtml(file.filename || "")}">${escapeHtml(file.filename || "файл")} · ${n}</button>`;
    })
    .join("");
  tabs.innerHTML =
    `<button type="button" class="review-tab${finalActive}" data-review-file="__final__">Итог · ${finalRows.length}</button>` +
    fileTabs;
  if (meta) {
    meta.textContent = review?.meaning || (wanted ? `${recognizedRows(wanted).length} строк` : `${finalRows.length} строк`);
  }
  if (wanted) {
    body.innerHTML = renderFilePane(wanted);
    body.dataset.reviewFile = wanted.filename || "";
    return;
  }
  if (!finalRows.length) {
    body.innerHTML = "<p class=\"hint\">Таблицу собрать не удалось.</p>";
    return;
  }
  body.innerHTML = `
    <div class="review-file-meta">
      <strong>Итоговая таблица</strong>
      <span class="hint">${finalRows.length} строк после кода и модели</span>
    </div>
    ${renderRecognizedTable(finalRows)}
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
  openDialog(dialog);
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
      body: JSON.stringify(exportPayload()),
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

function renderHeaderChanges(ws, form) {
  const box = $("#header-changes");
  const badge = $("#header-summary-badge");
  const panel = $("#header-panel");
  if (!box || !form) return;
  form.querySelectorAll("label.header-changed, label.header-filled").forEach((label) => {
    label.classList.remove("header-changed", "header-filled");
    label.querySelectorAll(".header-was").forEach((node) => node.remove());
  });
  const changes = ws.header_changes || [];
  const notes = ws.header_notes || [];
  const rows = [];
  changes.forEach((change) => {
    const label = form[change.field]?.closest("label");
    const filled = change.kind === "filled";
    if (label) {
      label.classList.add(filled ? "header-filled" : "header-changed");
      const was = document.createElement("span");
      was.className = "header-was";
      was.textContent = filled
        ? "Код поля не нашёл, значение вписала модель по фото"
        : `Код прочитал: ${change.before}`;
      label.appendChild(was);
    }
    rows.push(
      filled
        ? `<li><strong>${escapeHtml(change.label)}</strong>: код не нашёл, модель вписала «${escapeHtml(change.after)}»</li>`
        : `<li><strong>${escapeHtml(change.label)}</strong>: код прочитал «${escapeHtml(change.before)}», модель по фото поставила «${escapeHtml(change.after)}»</li>`
    );
  });
  const noteRows = notes.map((text) => `<li class="header-note-row">${escapeHtml(text)}</li>`);
  const parts = [];
  if (rows.length) parts.push(`<p class="header-changes-title">Что изменила модель в шапке</p><ul>${rows.join("")}</ul>`);
  if (noteRows.length) parts.push(`<p class="header-changes-title">Замечания к разбору</p><ul>${noteRows.join("")}</ul>`);
  box.innerHTML = parts.join("");
  box.hidden = !parts.length;
  if (badge) {
    const bits = [];
    if (changes.length) bits.push(`изменено моделью: ${changes.length}`);
    if (notes.length) bits.push(`замечаний: ${notes.length}`);
    badge.textContent = bits.join(" · ");
    badge.hidden = !bits.length;
  }
  if (panel && parts.length && !ws._headerShown) {
    panel.open = true;
    ws._headerShown = true;
  }
}

function renderWorkspace() {
  const ws = state.workspace;
  if (!ws) return;
  $("#workspace").classList.remove("hidden");
  applyShipmentTitle(ws.title);
  $("#ws-meta").textContent =
    `${PROFILE_RU[ws.profile_type] || ws.profile_type} · ${STATUS_RU[ws.status] || ws.status} · ${(ws.items || []).length} позиций`;

  const badges = $("#files-badges");
  badges.innerHTML = "";
  // Нажимается каждый файл, у которого есть вкладка с тем, что в нём прочитано. Лист Excel называется «книга.xlsx / Лист1».
  const reviewNames = new Set(reviewSources(ws).files.map((entry) => entry.filename));
  (ws.files || []).forEach((f) => {
    const parseStatus = f.parse_status || "ok";
    const name = String(f.filename || "").toLowerCase().split(" / ")[0];
    const isPdf = name.endsWith(".pdf");
    const isExcel = /\.(xlsx|xls|xlsm)$/.test(name);
    const hasOwnTable = reviewNames.has(f.filename);
    const clickable = parseStatus !== "skipped";
    const span = document.createElement(clickable ? "button" : "span");
    span.type = clickable ? "button" : undefined;
    span.className = `badge ${f.doc_type ? "" : "unknown"} ${parseStatus === "ok" ? "" : parseStatus} ${clickable ? "clickable" : ""}`.trim();
    const typeRu = DOC_TYPE_RU[f.doc_type] || (isPdf ? "PDF" : isExcel ? "Excel" : "Не определен");
    const ocrHint =
      f.ocr_confidence != null ? ` · распознавание ${Math.round(Number(f.ocr_confidence) * 100)}%` : "";
    let statusHint = "";
    if (parseStatus === "skipped") statusHint = " · пропущен";
    else if (parseStatus === "review") statusHint = " · требуется проверка";
    else if (hasOwnTable) statusHint = " · таблица";
    span.title = f.parse_message || (clickable ? "Открыть итоговую таблицу" : "");
    span.setAttribute("aria-controls", "review-dialog");
    const named = String(f.filename || "");
    const showMessage = (named === "модель" || named === "сверка") && f.parse_message;
    if (showMessage) {
      span.textContent = `${named} - ${String(f.parse_message).replace(/\s+/g, " ").slice(0, 180)}`;
    } else {
      span.textContent = `${f.filename} - ${typeRu}${ocrHint}${statusHint}`;
    }
    if (clickable) {
      span.addEventListener("click", (event) => {
        event.preventDefault();
        openReviewPanel(hasOwnTable ? f.filename : "__final__");
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
    "currency",
    "payment_terms",
    "manufacturer",
    "delivery_date",
    "warehouse_address",
  ].forEach((k) => {
    if (headerForm[k]) headerForm[k].value = hf[k] || "";
  });
  renderHeaderChanges(ws, headerForm);

  syncRoleTabs();
  ensureRoleColumns()
    .catch(() => {
      state.roleColumns = state.roleColumns || {};
    })
    .finally(() => paintItemsTable());
}

function paintItemsTable() {
  const ws = state.workspace;
  if (!ws) return;
  const cols = visibleRoleColumns();
  const thead = $("#items-table thead");
  const tbody = $("#items-table tbody");
  const tfoot = $("#items-table tfoot");
  if (!thead || !tbody || !tfoot) return;
  const headCells = [
    `<th class="row-no">№</th>`,
    ...cols.map(
      (col) =>
        `<th class="${col.kind === "num" ? "num" : col.kind === "money" ? "money" : ""}" data-col-key="${escapeHtml(col.key)}">
          <span class="th-inner">${escapeHtml(col.title)}<button type="button" class="col-action" data-col-delete="${escapeHtml(col.key)}" aria-label="Удалить столбец">×</button></span>
        </th>`
    ),
    `<th>Отметки</th>`,
  ];
  thead.innerHTML = `<tr>${headCells.join("")}</tr>`;
  tbody.innerHTML = "";
  (ws.items || []).forEach((item, idx) => {
    const tr = document.createElement("tr");
    const errs = item.validation_errors || [];
    tr.className = `item-row ${severityClass(errs)}`.trim();
    tr.dataset.id = item.id;
    tr.title = "Нажмите ячейку или «Изменить», чтобы править позицию";
    const body = cols
      .map((col) => {
        if (col.key === "article" || col.key === "description") {
          const raw = itemField(item, col.key) || "";
          return longCell(raw, col.key === "article" ? "article" : "desc");
        }
        const edit = EDIT_FOR[col.key] ? ` data-edit="${EDIT_FOR[col.key]}"` : "";
        const klass = cellClassFor(col, errs);
        const text = col.kind === "money" && col.key === "price" ? formatPrice(itemField(item, col.key)) : formatRoleCell(item, col);
        return `<td class="${klass}"${edit}>${text}</td>`;
      })
      .join("");
    tr.innerHTML = `
      <td class="row-no">${idx + 1}</td>
      ${body}
      <td class="flags-cell">
        <button type="button" class="btn compact-btn item-edit-btn" data-edit-item="${escapeHtml(item.id)}">Изменить</button>
        ${renderFlags(errs)}
      </td>
    `;
    tbody.appendChild(tr);
  });
  const totalCells = [
    `<th></th>`,
    ...cols.map((col) => {
      const klass = col.kind === "num" ? "num" : col.kind === "money" ? "money" : "";
      return `<th class="${klass}" data-total="${escapeHtml(col.key)}">${col.key === "article" ? "Итого" : ""}</th>`;
    }),
    `<th></th>`,
  ];
  tfoot.innerHTML = `<tr id="items-totals-row">${totalCells.join("")}</tr>`;
  fillExcelTotals(ws.items || [], ws.model_review);
}

function toggleLongCell(td, open) {
  if (!td?.classList.contains("cell-long")) return;
  td.classList.toggle("is-open", open);
  const hide = td.querySelector(".cell-hide");
  if (hide) hide.hidden = !td.classList.contains("is-open");
}

$("#items-table")?.addEventListener("click", (e) => {
  const del = e.target.closest("[data-col-delete]");
  if (del) {
    e.preventDefault();
    e.stopPropagation();
    askDeleteColumn(del.dataset.colDelete);
    return;
  }
  if (e.target.closest(".cell-short, .cell-hide")) return;
  const row = e.target.closest("tbody tr.item-row");
  if (!row?.dataset.id) return;
  const focusId = e.target.closest("[data-edit]")?.dataset.edit || "";
  openEdit(row.dataset.id, focusId);
});

$("#role-tabs")?.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-role]");
  if (!tab) return;
  state.role = tab.dataset.role;
  syncRoleTabs();
  paintItemsTable();
});

function columnTitle(key) {
  const cols = visibleRoleColumns();
  const hit = cols.find((col) => col.key === key);
  if (hit) return hit.title;
  return key;
}

function askDeleteColumn(key) {
  const roleName = ROLE_FROM[state.role] || state.role;
  const title = columnTitle(key);
  state.pendingDelete = { key, role: state.role };
  const text = $("#column-delete-text");
  if (text) text.textContent = `Вы точно хотите удалить столбец ${title} из ${roleName}?`;
  openDialog($("#column-delete-dialog"));
}

function applyDeleteColumn() {
  const pending = state.pendingDelete;
  if (!pending) return;
  const layout = state.columnLayout[pending.role] || { hidden: [], extra: [] };
  if (String(pending.key).startsWith("extra:")) {
    const id = pending.key.slice(6);
    layout.extra = (layout.extra || []).filter((col) => col.id !== id);
    (state.workspace?.items || []).forEach((item) => {
      if (item.extra_columns && item.extra_columns[pending.role]) {
        delete item.extra_columns[pending.role][id];
      }
    });
  } else if (!layout.hidden.includes(pending.key)) {
    layout.hidden = [...(layout.hidden || []), pending.key];
  }
  state.columnLayout[pending.role] = layout;
  state.pendingDelete = null;
  paintItemsTable();
}

$("#column-delete-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  applyDeleteColumn();
  $("#column-delete-dialog")?.close();
});
$("#column-delete-cancel")?.addEventListener("click", () => $("#column-delete-dialog")?.close());
$("#column-add-roles")?.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-add-role]");
  if (!btn) return;
  previewAddColumn(btn.dataset.addRole);
});
$("#column-add-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  applyAddColumn();
  $("#column-add-dialog")?.close();
});
$("#column-add-cancel")?.addEventListener("click", () => $("#column-add-dialog")?.close());

function sourceColumnHeaders(file) {
  const fromFile = file?.headers || [];
  if (fromFile.length) return fromFile;
  const seen = [];
  const known = new Set();
  (file?.table || []).forEach((row) => {
    Object.keys(row?.raw || {}).forEach((title) => {
      if (known.has(title)) return;
      known.add(title);
      seen.push(title);
    });
  });
  if (seen.length) return seen;
  return ["article", "rolls", "qty", "price", "amount", "net_weight", "gross_weight", "hs_code", "description"];
}

function sourceColumnValue(row, header) {
  if (row?.raw && row.raw[header] != null && row.raw[header] !== "") return row.raw[header];
  if (row?.[header] != null && row[header] !== "") return row[header];
  const h = String(header || "").toLowerCase();
  const aliases = [
    [["description", "наименован"], ["description"]],
    [["hs", "code", "код", "тн"], ["hs_code", "customs_code"]],
    [["qty", "q-ty", "quantity", "кол"], ["qty"]],
    [["amount", "сумм"], ["amount"]],
    [["price", "цена"], ["price"]],
    [["netto", "net wt", "net_weight", "нетто"], ["net_weight"]],
    [["brutto", "gross", "брутто"], ["gross_weight"]],
    [["packag", "carton", "мест", "rolls"], ["rolls"]],
    [["country", "origin", "стран"], ["country"]],
    [["manufacturer", "изготов"], ["manufacturer"]],
    [["model", "design", "art", "артикул"], ["article", "model"]],
  ];
  for (const [needles, keys] of aliases) {
    if (!needles.some((n) => h.includes(n))) continue;
    for (const key of keys) {
      if (row?.[key] != null && row[key] !== "") return row[key];
    }
  }
  return null;
}

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
  const add = e.target.closest("[data-col-add]");
  if (add) {
    e.preventDefault();
    const filename = $("#review-dialog-body")?.dataset.reviewFile || "";
    const { files } = reviewSources(state.workspace);
    const file = findReviewFile(files, filename);
    openAddColumn(add.dataset.colAdd, file);
    return;
  }
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
  openDialog($("#edit-dialog"));
  const focusEl = focusId ? editField(focusId) : editField("edit-article");
  if (focusEl && typeof focusEl.focus === "function") {
    requestAnimationFrame(() => {
      focusEl.focus({ preventScroll: true });
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
    const nextItem = {
      ...item,
      article: payload.article,
      commercial_data: { ...(item.commercial_data || {}), ...payload.commercial_data },
      packing_data: { ...(item.packing_data || {}), ...payload.packing_data },
      customs_data: { ...(item.customs_data || {}), ...payload.customs_data },
      validation_errors: (item.validation_errors || []).map((err) => ({ ...err, resolved: true })),
    };
    const changed = stableJson(itemEditSnapshot(item)) !== stableJson(itemEditSnapshot(nextItem));
    if (!changed) {
      status.textContent = "";
      $("#edit-dialog").close();
      return;
    }
    state.workspace.items[idx] = nextItem;
    $("#edit-dialog").close();
    renderWorkspace();
    $("#upload-status").textContent = "Изменения сохранены в текущем сеансе. При обновлении страницы данные будут потеряны.";
    showToast(`Вы изменили ${rowCountLabel(1)}`, { kind: "ok" });
  } catch (err) {
    status.textContent = `Ошибка: ${err.message}`;
    alert(`Не удалось сохранить позицию: ${err.message}`);
    showToast(`Не удалось сохранить строку: ${err.message}`, { kind: "error", ms: 4500 });
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

function readHeaderForm() {
  const form = $("#header-form");
  if (!form) return {};
  return {
    buyer: form.buyer?.value || "",
    buyer_address: form.buyer_address?.value || "",
    seller: form.seller?.value || "",
    seller_address: form.seller_address?.value || "",
    contract_no: form.contract_no?.value || "",
    contract_date: form.contract_date?.value || "",
    invoice_no: form.invoice_no?.value || "",
    invoice_date: form.invoice_date?.value || "",
    delivery_terms: form.delivery_terms?.value || "",
    currency: form.currency?.value || "",
    payment_terms: form.payment_terms?.value || "",
    manufacturer: form.manufacturer?.value || "",
    delivery_date: form.delivery_date?.value || "",
    warehouse_address: form.warehouse_address?.value || "",
  };
}

function applyHeaderFields(payload, { syncItems = false } = {}) {
  if (!state.workspace) return;
  const prevInvoice = shipmentTitleFromHeader(state.workspace.header_fields);
  const nextInvoice = shipmentTitleFromHeader(payload);
  state.workspace.header_fields = { ...(state.workspace.header_fields || {}), ...payload };
  if (syncItems && payload.manufacturer) {
    const maker = String(payload.manufacturer || "").trim();
    if (maker) {
      state.workspace.items = (state.workspace.items || []).map((item) => ({
        ...item,
        customs_data: { ...(item.customs_data || {}), manufacturer: maker },
      }));
    }
  }
  const titleNow = (state.workspace.title || "").trim();
  if (nextInvoice && (isGenericTitle(titleNow) || titleNow === prevInvoice)) {
    applyShipmentTitle(nextInvoice, { force: true });
  }
}

function exportPayload() {
  // Always take the latest form values so unsaved реквизиты still go into Excel.
  applyHeaderFields(readHeaderForm(), { syncItems: true });
  return {
    title: state.workspace.title,
    profile_type: state.workspace.profile_type,
    header_fields: state.workspace.header_fields || {},
    items: state.workspace.items || [],
    column_layout: state.columnLayout,
  };
}

function syncRoleTabs() {
  document.querySelectorAll("#role-tabs .role-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.role === state.role);
  });
}

function syncAddRoleButtons() {
  const beijing = String(state.workspace?.profile_type || "").toUpperCase() === "BEIJING";
  const labels = beijing
    ? { invoice: "Лист Invoice", packing: "Лист Packing list", specification: "Лист Specification" }
    : { invoice: "Файл инвойс", packing: "Файл пакинг", specification: "Файл спецификация" };
  document.querySelectorAll("#column-add-roles [data-add-role]").forEach((btn) => {
    btn.textContent = labels[btn.dataset.addRole] || btn.textContent;
  });
}

async function openAddColumn(header, file) {
  if (!file || !state.workspace) return;
  state.pendingAdd = { header, file, preview: null, role: null };
  const title = $("#column-add-title");
  if (title) title.textContent = `Столбец «${header}» из ${file.filename || "файла"}`;
  const match = $("#column-add-match");
  if (match) match.textContent = "Выберите файл или лист, затем проверьте склейку.";
  const confirmBtn = $("#column-add-confirm");
  if (confirmBtn) confirmBtn.disabled = true;
  syncAddRoleButtons();
  document.querySelectorAll("#column-add-roles [data-add-role]").forEach((btn) => {
    btn.classList.remove("primary");
  });
  openDialog($("#column-add-dialog"));
}

async function previewAddColumn(role) {
  const pending = state.pendingAdd;
  if (!pending || !state.workspace) return;
  pending.role = role;
  document.querySelectorAll("#column-add-roles [data-add-role]").forEach((btn) => {
    btn.classList.toggle("primary", btn.dataset.addRole === role);
  });
  const match = $("#column-add-match");
  const confirmBtn = $("#column-add-confirm");
  if (match) match.textContent = "Сверяю строки источника с таблицей после склейки…";
  if (confirmBtn) confirmBtn.disabled = true;
  try {
    const preview = await api("/api/v1/shipments/columns/attach", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: state.workspace.items || [],
        source_rows: pending.file.table || [],
        column: pending.header,
      }),
    });
    pending.preview = preview;
    const keyHint = {
      "article+qty": "по артикулу и количеству",
      "article-sum": "по артикулу: количество источника равно сумме лотов",
      article: "по артикулу, значение копируется на все лоты этой позиции",
      "description+qty": "по названию и количеству",
      "description-sum": "по названию: количество источника равно сумме лотов",
      description: "по названию",
      order: "по порядку строк",
    };
    if (match) {
      let text =
        `В источнике ${preview.source_rows} строк, в таблице ${preview.item_rows}. ` +
        `Склейка ${keyHint[preview.key] || preview.key}: заполнено ${preview.matched} из ${preview.item_rows}.`;
      if (preview.unmatched_source) {
        text += ` Из источника не вошло ${preview.unmatched_source}.`;
      }
      match.textContent = text;
    }
    if (confirmBtn) confirmBtn.disabled = preview.matched < 1;
  } catch (err) {
    if (match) match.textContent = `Не удалось склеить столбец: ${humanizeClientError(err.message)}`;
  }
}

function applyAddColumn() {
  const pending = state.pendingAdd;
  if (!pending?.preview || !pending.role || !state.workspace) return;
  const layout = state.columnLayout[pending.role] || { hidden: [], extra: [] };
  const id = pending.preview.column_id;
  layout.extra = [...(layout.extra || []), { id, title: pending.header }];
  state.columnLayout[pending.role] = layout;
  const values = pending.preview.values || {};
  (state.workspace.items || []).forEach((item) => {
    const value = values[String(item.id)];
    if (value == null || value === "") return;
    item.extra_columns = item.extra_columns || {};
    item.extra_columns[pending.role] = { ...(item.extra_columns[pending.role] || {}), [id]: value };
  });
  state.role = pending.role;
  state.pendingAdd = null;
  syncRoleTabs();
  paintItemsTable();
  showToast("Столбец добавлен в конец таблицы", { kind: "ok" });
}

$("#header-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.workspace) return;
  try {
    const before = headerSnapshot(state.workspace.header_fields);
    const next = headerSnapshot(readHeaderForm());
    if (stableJson(before) === stableJson(next)) {
      return;
    }
    applyHeaderFields(readHeaderForm(), { syncItems: true });
    renderWorkspace();
    $("#upload-status").textContent =
      "Реквизиты сохранены в текущем сеансе. При обновлении страницы правки будут потеряны.";
    showToast("Реквизиты изменились", { kind: "ok" });
  } catch (err) {
    alert(`Не удалось сохранить шапку: ${err.message}`);
    showToast(`Не удалось сохранить реквизиты: ${err.message}`, { kind: "error", ms: 4500 });
  }
});

$("#btn-export").addEventListener("click", async () => {
  const btn = $("#btn-export");
  const box = $("#export-result");
  if (!state.workspace) {
    box.textContent = "Сначала загрузите и обработайте документы.";
    showToast("Сначала загрузите и обработайте документы", { kind: "info" });
    return;
  }
  btn.disabled = true;
  const prevLabel = btn.textContent;
  btn.textContent = "Формирование…";
  box.textContent = "Формирование Excel…";
  showToast("Формирование Excel", { kind: "info", ms: 2200 });
  try {
    const res = await fetch("/api/v1/shipments/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(exportPayload()),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText);
    }
    const blob = await res.blob();
    const zipName = `${state.workspace.title || "export"}_Excel.zip`;
    await downloadBlob(blob, zipName);
    box.textContent = `Файл сохранен: ${zipName}`;
    showToast("Excel сформирован", { kind: "ok" });
  } catch (err) {
    box.textContent = `Ошибка экспорта: ${err.message}`;
    showToast(`Ошибка экспорта: ${humanizeClientError(err.message)}`, { kind: "error", ms: 5200 });
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
  const labels = {
    ok: "Система активна",
    off: "Система выключена",
    auth: "Система недоступна",
    credits: "Система недоступна",
    error: "Система недоступна",
    down: "Система недоступна",
    unknown: "Система…",
  };
  label.textContent = labels[status] || "Система недоступна";
  if (money) {
    money.textContent = "";
    money.hidden = true;
  }
  el.title = "Проверить состояние сервиса";
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
  if (label) label.textContent = "Проверяю…";
  await refreshOpenCodeStatus();
}

$("#opencode-status")?.addEventListener("click", pingOpenCode);
refreshOpenCodeStatus();
setInterval(refreshOpenCodeStatus, 15000);
