from pathlib import Path

path = Path("prepare_transform_code/shipment.py")
text = path.read_text(encoding="utf-8")
old = (
    'text = re.sub(r"(?i)^(?:address|адрес)\\s*:\\s*", "", " ".join(str(value or "").split()))\n'
    "    text = re.split(\n"
    '        r"(?i)\\b(?:address|адрес|bank|банк|inn|инн|kpp|кпп|contract|контракт|invoice|swift|account)\\b",\n'
)
new = (
    'text = re.sub(r"(?i)^(?:address|адрес|adress)\\s*:\\s*", "", " ".join(str(value or "").split()))\n'
    "    text = re.split(\n"
    '        r"(?i)\\b(?:address|адрес|adress|bank|банк|inn|инн|ogrn|огрн|kpp|кпп|contract|контракт|invoice|swift|account)\\b",\n'
)
if old not in text:
    raise SystemExit("pattern not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("ok")
