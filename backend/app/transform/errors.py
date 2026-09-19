"""Friendly Russian errors for the operator.

Never surface a raw latin traceback. Every expected failure maps to a short,
actionable Russian sentence. Unknown failures fall back to a generic message but
still keep the original text for the log.
"""

from __future__ import annotations

import re


class FileReadError(Exception):
    """Raised when a file genuinely cannot be read. `.ru` is shown to the operator."""

    def __init__(self, ru_message: str, *, technical: str | None = None) -> None:
        super().__init__(technical or ru_message)
        self.ru = ru_message
        self.technical = technical or ru_message


# (regex over the lowered technical text) -> Russian message
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"password|encrypted|decrypt"),
     "Файл защищён паролем. Сними защиту в Excel и загрузи снова."),
    (re.compile(r"zip file|not a zip|badzipfile|central directory"),
     "Файл повреждён или это не настоящий Excel (возможно, переименованный .html или .csv). Открой его в Excel и пересохрани как .xlsx."),
    (re.compile(r"unsupported format|can't find workbook|不是|corrupt"),
     "Формат файла не распознан. Пересохрани документ в Excel как .xlsx и загрузи снова."),
    (re.compile(r"no such file|not found|cannot find the path"),
     "Файл не найден. Загрузи его ещё раз."),
    (re.compile(r"permission denied|being used by another process"),
     "Файл занят другой программой. Закрой его в Excel и загрузи снова."),
    (re.compile(r"empty|no valid sheets|no sheets|no data"),
     "В файле нет данных для обработки."),
    (re.compile(r"pdf.*(no text|not searchable|empty)|no text layer"),
     "В PDF нет текстового слоя (это скан). Нужен разбор сканов, либо приложи Excel."),
    (re.compile(r"memory|out of memory|cannot allocate"),
     "Не хватило памяти на разбор файла. Файл слишком большой или это тяжёлый скан."),
    (re.compile(r"timeout|timed out"),
     "Файл обрабатывался слишком долго и был остановлен. Попробуй уменьшить или разбить его."),
)

_FALLBACK = "Не удалось прочитать файл. Проверь, что он открывается в Excel, и загрузи снова."


def humanize(exc: BaseException | str) -> str:
    """Turn any exception / log string into a short Russian sentence."""
    if isinstance(exc, FileReadError):
        return exc.ru
    text = str(exc or "").strip()
    low = text.lower()
    for pattern, message in _PATTERNS:
        if pattern.search(low):
            return message
    return _FALLBACK


def humanize_for_file(filename: str, exc: BaseException | str) -> str:
    base = humanize(exc)
    name = (filename or "").strip()
    if name and name not in base:
        return f"«{name}»: {base}"
    return base
