from pathlib import Path

head = Path("study_tools/_ship_head.py").read_text(encoding="utf-8")
cur = Path("prepare_transform_code/shipment.py").read_text(encoding="utf-8")

hs = head.index("def _address_blocks(text):")
he = head.index("def _to_company(text):")
head_block = head[hs:he]

needle = """        side = _party_side(line)
        if side and _ADDRESS_VALUE.match(line) is None:
            pending.append(side)
            index += 1
            continue
        match = _ADDRESS_VALUE.match(line)
        if match is None:
            index += 1
            continue
        value = _clean_address(match.group(1) or "")
        if not value and index + 1 < len(lines):
            nxt = lines[index + 1]
            if nxt and _party_side(nxt) is None and _ADDRESS_VALUE.match(nxt) is None:
                value = nxt
                index += 1
        if value and value not in fallback:
            fallback.append(value)
            if pending:
                owner = pending.pop(0)
                if owner in assigned and not assigned[owner]:
                    assigned[owner] = value
        index += 1"""

if needle not in head_block:
    raise SystemExit("needle missing")

replacement = """        dual = _dual_party_labels(line)
        if dual:
            pending.extend(dual)
            index += 1
            continue
        side = _party_side(line)
        if side and _ADDRESS_VALUE.match(line) is None and _BENEFICIARY_ADDRESS.match(line) is None:
            pending.append(side)
            index += 1
            continue
        dual_addr = _dual_addresses(line)
        if dual_addr and pending:
            for owner, value in zip(list(pending), dual_addr):
                if value and owner in assigned and not assigned[owner]:
                    assigned[owner] = value
                if value and value not in fallback:
                    fallback.append(value)
            pending = pending[len(dual_addr):]
            index += 1
            continue
        match = _ADDRESS_VALUE.match(line) or _BENEFICIARY_ADDRESS.match(line)
        if match is None:
            index += 1
            continue
        value = _clip_postal(_clean_address(match.group(1) or ""))
        if not value and index + 1 < len(lines):
            nxt = lines[index + 1]
            if nxt and _party_side(nxt) is None and _ADDRESS_VALUE.match(nxt) is None:
                value = _clip_postal(_clean_address(nxt))
                index += 1
        if value and value not in fallback:
            fallback.append(value)
            if pending:
                owner = pending.pop(0)
                if owner in assigned and not assigned[owner]:
                    assigned[owner] = value
            elif _BENEFICIARY_ADDRESS.match(line) and not assigned["seller"]:
                assigned["seller"] = value
        index += 1"""

head_block = head_block.replace(needle, replacement, 1)

# HEAD postal cleaner is also named _clean_address — rename it to _clip_postal
# so it does not override the transport _clean_address already in the current file.
head_block = head_block.replace("def _clean_address(value):", "def _clip_postal(value):", 1)
head_block = head_block.replace(
    'r"(?i)^(?:address|адрес)\\s*:\\s*"',
    'r"(?i)^(?:address|адрес|adress)\\s*:\\s*"',
    1,
)
head_block = head_block.replace(
    'r"(?i)\\b(?:address|адрес|bank|банк|inn|инн|kpp|кпп|contract|контракт|invoice|swift|account)\\b"',
    'r"(?i)\\b(?:address|адрес|adress|bank|банк|inn|инн|ogrn|огрн|kpp|кпп|contract|контракт|invoice|swift|account)\\b"',
    1,
)
# Calls inside HEAD block that still say _clean_address for postal — already using _clip_postal in replacement.
# Remaining _clean_address(match in HEAD longer?) — only in replacement we use both.

helpers = r'''
def _dual_party_labels(line):
    """THE BUYER: RECIPIENT: — две подписи, не одно имя."""
    text = str(line or "")
    if not re.search(r"(?i)\bbuyer\b", text):
        return []
    if not re.search(r"(?i)\b(?:recipient|consignee|получатель)\b", text):
        return []
    if re.search(r"\b(LLC|LTD|LIMITED|ООО|GMBH)\b", text, re.I) and not re.search(
        r"(?i)buyer\s*:?\s*recipient|покупатель\s*:?\s*получатель", text
    ):
        return []
    return ["buyer", "consignee"]


def _dual_addresses(line):
    """Address: … Address: … в одной строке двух колонок."""
    parts = re.split(r"(?i)(?:^|\s)(?:address|адрес|adress)\b(?:[^:]{0,40}):\s*", str(line or ""))
    values = [_clip_postal(part) for part in parts[1:] if _clip_postal(part)]
    return values if len(values) >= 2 else []


_BENEFICIARY_ADDRESS = re.compile(
    r"(?i)^(?:beneficiary['’`]?s?\s+address|адрес\s+бенефициара)\s*:?\s*(.*)$"
)

'''

head_block = head_block.replace("_ADDRESS_VALUE = re.compile(", helpers + "_ADDRESS_VALUE = re.compile(", 1)
head_block = head_block.replace(
    r'r"(?i)^(buyer|покупатель|importer|seller|продавец|exporter|recipient|consignee|получатель|грузополучатель)\b"',
    r'r"(?i)^(?:the\s+)?(buyer|покупатель|importer|seller|продавец|exporter|recipient|consignee|получатель|грузополучатель)\b"',
    1,
)

if "def _output_book_name(name):" in cur:
    start = min(cur.index("def _output_book_name(name):"), cur.index("def _address_blocks(text):"))
else:
    start = cur.index("def _address_blocks(text):")
end = cur.index("def _to_company(text):")

output_book = (
    "def _output_book_name(name):\n"
    '    """Книга «для ЭД» / «ТСД …» — выход профиля, не исходник поставщика."""\n'
    "    low = str(name or \"\").casefold()\n"
    '    if "для эд" in low or "для_эд" in low:\n'
    "        return True\n"
    '    if re.match(r"(?i)^тсд\\b", str(name or "").strip()):\n'
    "        return True\n"
    "    return False\n\n\n"
)

new_text = cur[:start] + output_book + head_block + cur[end:]
Path("prepare_transform_code/shipment.py").write_text(new_text, encoding="utf-8")
print(
    "ok",
    new_text.count("def _address_blocks"),
    new_text.count("def _clip_postal"),
    new_text.count("def _dual_party_labels"),
    "_party_sides" in new_text,
)
