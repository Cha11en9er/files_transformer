"""Operator-facing Russian text for parser warnings and library exceptions."""

from __future__ import annotations

WARNING_RU: dict[str, str] = {
    "pdf_has_text_but_no_article_table": (
        "В PDF есть текст, но таблица без рамок не собралась автоматически. "
        "Модель проверит скан."
    ),
    "pdf_has_no_text_layer": "В PDF нет текстового слоя, нужен OCR или скан для модели.",
    "pdf_not_searchable_ocr_disabled": "PDF без текста, OCR для этого файла выключен.",
    "ocr_text_extracted_tables_not_detected": (
        "OCR прочитал текст, но строки товара не собрались. Модель проверит скан."
    ),
    "image_ocr_no_structured_table": "На изображении нет явной таблицы, модель проверит скан.",
    "image_ocr_disabled": "OCR для изображения выключен.",
    "doc_type_defaulted_invoice": "Тип документа не определился, временно считаем инвойсом.",
    "doc_type_unclassified_needs_operator": "Тип документа не определился, проверьте вручную.",
    "pdfplumber_missing_used_pypdf": "pdfplumber недоступен, текст взят запасным способом.",
    "pdf_reader_unavailable": "Чтение PDF недоступно на этой машине.",
}

_SNIPPETS: tuple[tuple[str, str], ...] = (
    (
        "excel file format cannot be determined",
        "Не удалось прочитать Excel: формат файла не распознан. "
        "PDF и остальные файлы всё равно обработаем, модель сверит скан.",
    ),
    (
        "no engine",
        "Не удалось подобрать способ чтения Excel. Файл пропустим, остальное обработаем.",
    ),
    (
        "file is not a zip file",
        "Файл .xlsx повреждён или это не книга Excel.",
    ),
    (
        "badzipfile",
        "Файл .xlsx повреждён или это не книга Excel.",
    ),
    (
        "xlrd",
        "Старый .xls не прочитался. Если это исходник, сохраните как .xlsx.",
    ),
    (
        "openpyxl",
        "Не удалось открыть книгу Excel.",
    ),
    (
        "unsupported format, or corrupt",
        "Файл Excel повреждён или формат не поддерживается.",
    ),
    (
        "10061",
        "Модель недоступна: OpenCode не отвечает на 127.0.0.1:4096.",
    ),
    (
        "отверг запрос на подключение",
        "Модель недоступна: OpenCode не отвечает на 127.0.0.1:4096.",
    ),
    (
        "connection refused",
        "Модель недоступна: OpenCode не отвечает на 127.0.0.1:4096.",
    ),
    (
        "econnrefused",
        "Модель недоступна: OpenCode не отвечает на 127.0.0.1:4096.",
    ),
    (
        "failed to establish a new connection",
        "Нет связи с OpenCode на 127.0.0.1:4096.",
    ),
    (
        "eaddrinuse",
        "Порт 4096 уже занят. Скорее всего OpenCode уже запущен. Не поднимай второй процесс, проверь systemctl status opencode.",
    ),
    (
        "address already in use",
        "Порт 4096 уже занят. OpenCode, скорее всего, уже работает в фоне.",
    ),
    (
        "serveerror",
        "OpenCode не смог занять порт 4096. Обычно порт уже занят работающим процессом. Проверь systemctl status opencode.",
    ),
    (
        "timed out",
        "Модель не ответила вовремя. Повтори обработку.",
    ),
    (
        "creditserror",
        "На OpenCode Zen нет оплаты или закончился баланс. Порт сервера тут ни при чём.",
    ),
    (
        "insufficient balance",
        "На OpenCode Zen закончился баланс. Пополни счёт в кабинете Zen.",
    ),
    (
        "no payment method",
        "OpenCode Zen просит карту или оплату. Баланса или привязанной карты нет.",
    ),
    (
        "free usage exceeded",
        "Бесплатный лимит модели кончился. Нужна оплата Zen или другая модель.",
    ),
    (
        "401",
        "OpenCode отклонил пароль. Пароль в backend/.env должен совпадать с OPENCODE_SERVER_PASSWORD сервиса.",
    ),
    (
        "unauthorized",
        "OpenCode отклонил пароль. Пароль в backend/.env должен совпадать с паролем сервиса.",
    ),
    (
        "password is not set",
        "У OpenCode не задан пароль. Пропиши OPENCODE_SERVER_PASSWORD в backend/.env и перезапусти сервис opencode.",
    ),
    (
        "not json serializable",
        "В Excel дата в ячейке. Обработку продолжим, дату запишем текстом.",
    ),
    (
        "403",
        "OpenCode отклонил доступ. Проверь логин и пароль сервера.",
    ),
)


def _strip_code_prefix(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    for prefix in (
        "parse_failed:",
        "pdf_read_failed:",
        "ocr_failed:",
        "pypdf_failed:",
        "unsupported_extension:",
    ):
        if raw.lower().startswith(prefix):
            return prefix[:-1], raw[len(prefix) :].strip()
    return "", raw


def humanize_message(text: str | None) -> str:
    """Turn a parser warning or exception into a short Russian sentence."""
    if not text:
        return "Неизвестная ошибка обработки."
    code, rest = _strip_code_prefix(text)
    key = (code or rest).lower()
    if rest in WARNING_RU:
        return WARNING_RU[rest]
    if text in WARNING_RU:
        return WARNING_RU[text]
    blob = f"{code} {rest}".lower()
    for snippet, russian in _SNIPPETS:
        if snippet in blob:
            return russian
    if code == "unsupported_extension":
        return f"Формат файла «{rest or 'неизвестный'}» не поддерживается."
    if code == "ocr_failed":
        return "OCR не смог прочитать файл. Модель всё равно увидит скан."
    if code == "pdf_read_failed":
        return "PDF не удалось разобрать обычным парсером. Модель увидит страницы как изображения."
    if code == "parse_failed":
        if rest:
            lowered = rest.lower()
            for snippet, russian in _SNIPPETS:
                if snippet in lowered:
                    return russian
            return "Файл не удалось прочитать автоматически. Остальные документы обработаем."
        return "Файл не удалось прочитать автоматически."
    if rest in WARNING_RU:
        return WARNING_RU[rest]
    if rest and rest.replace("_", "").isalnum() and "_" in rest:
        return "Парсер не собрал таблицу из этого файла. Модель проверит скан."
    return rest or "Ошибка обработки."


def humanize_exception(exc: BaseException) -> str:
    return humanize_message(str(exc))
