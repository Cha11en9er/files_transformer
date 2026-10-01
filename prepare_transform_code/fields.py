"""Синонимы шапок. Новая поставка добавляет фразу сюда, не условие по имени файла."""

import re

# Более длинные фразы проверяются раньше коротких.
COLUMNS = (
    ("packages", ("q-ty packages", "q/ty cases", "qty cases", "qty of packages", "кол-во коробок", "кол-во мест", "quantity/package")),
    ("qty", ("q-ty items", "q/ty pairs", "quantity(mts)", "quantity (mts)", "qty(m)", "кол-во пар", "кол-во штук", "кол-во шт")),
    ("price", ("unit price", "price per", "price for pair", "цена за пару", "цена за ед", "цена за шт", "unitprice")),
    ("amount", ("total price", "total amount", "amount", "стоимость", "общая стоимость")),
    ("gross", ("gross weight", "total g. weight", "g.w", "gw", "брутто")),
    ("net", ("net weight", "total n. weight", "n.w", "nw", "нетто")),
    ("volume", ("measurement", "volume", "cbm", "м куб", "m3")),
    ("vendor", ("vendor code", "cat,#", "cat.#", "article", "артикул")),
    ("model", ("model", "модель")),
    ("hs", ("product code", "hs code", "код тн", "код товара", "тн вэд", "тнвэд")),
    ("description", ("description", "goods", "item/description", "наименование", "описание")),
    ("qty", ("quantity", "qty", "q-ty", "кол-во")),
    ("price", ("price", "цена")),
    ("packages", ("packages", "package", "roll", "carton")),
)

def column_of(header):
    text = " ".join(str(header or "").lower().replace("\n", " ").split())
    if not text:
        return None
    exact = {
        "net": "net",
        "n.w": "net",
        "nw": "net",
        "нетто": "net",
        "gross": "gross",
        "g.w": "gross",
        "gw": "gross",
        "брутто": "gross",
        "measure": "volume",
        "measurement": "volume",
    }
    if text in exact:
        return exact[text]
    if text.replace(" ", "") in {"measurement", "measure"} or "cbm" in text:
        return "volume"
    for name, phrases in COLUMNS:
        if any(phrase in text for phrase in phrases):
            return name
    return None


def is_stop_label(value):
    text = str(value or "").lower().replace("/", " ")
    if "say total" in text:
        return True
    token = re.split(r"[^a-zа-яё]+", text, maxsplit=1)[0]
    return token in {"total", "итого", "всего"}


def is_freight(value):
    text = str(value or "").lower()
    return "freight" in text or "перевоз" in text
