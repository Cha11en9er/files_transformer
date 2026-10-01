from dataclasses import dataclass, field

from prepare_transform_code.numbers import is_package_text, is_piece_text, package_count, parse_number


@dataclass
class Line:
    source: str = ""
    description: str = ""
    model: str = ""
    vendor: str = ""
    hs: str = ""
    qty_text: str = ""
    pieces: float | None = None
    packages: float | None = None
    price: float | None = None
    amount: float | None = None
    gross: float | None = None
    net: float | None = None
    volume: float | None = None
    measure_group: dict | None = None
    freight: bool = False
    extra: dict = field(default_factory=dict)

    def anchors(self):
        codes = []
        vendor = _norm(self.vendor)
        if vendor and any(ch.isdigit() for ch in vendor) and vendor not in {"--", "na"}:
            codes.append(vendor)
        model = _norm(self.model)
        if model and model not in {"--", "na", "отсутствует"}:
            codes.append("model:" + model)
        desc = _norm(self.description)
        if desc:
            codes.append("desc:" + desc[:48])
        return codes


def _norm(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def fill_qty(line, text, header_is_package=False):
    if isinstance(text, str) and text.startswith("="):
        return
    text = str(text).replace("\n", " ").strip()
    if not text or text.startswith("="):
        return
    line.qty_text = (line.qty_text + " " + text).strip()
    packs = package_count(text)
    if packs is not None:
        line.packages = packs if line.packages is None else line.packages + packs
        return
    if is_piece_text(text):
        number = parse_number(text)
        if number is not None and line.pieces is None:
            line.pieces = number
        return
    number = parse_number(text)
    if number is None:
        return
    if header_is_package or is_package_text(text):
        line.packages = number
    elif line.pieces is None:
        line.pieces = number
