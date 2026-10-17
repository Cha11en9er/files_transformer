# -*- coding: utf-8 -*-
import json
from pathlib import Path

from prepare_transform_code.shipment import analyze

folders = {
    "0810": Path("documents/9_pravka/0810_Светильники_8200/0810_Светильники_8200"),
    "mario": Path("documents/9_pravka/Mario/Mario"),
    "froca": Path("documents/9_pravka/Froca/Froca"),
    "marm": Path("documents/9_pravka/МАРМИТЫ/МАРМИТЫ"),
    "m82": Path(
        "documents/9_pravka/Пакинг M82 (светильники + цветы)/Пакинг M82 (светильники + цветы)"
    ),
    "logdv": Path("documents/9_pravka/Поставка LOGDV_7_пусто/Поставка LOGDV_7_пусто"),
    "p5": Path("documents/9_pravka/3 поставки/5"),
    "p6": Path("documents/9_pravka/3 поставки/6"),
    "p61": Path("documents/9_pravka/3 поставки/6_1"),
}

line_keys = (
    "vendor",
    "model",
    "name",
    "qty",
    "pieces",
    "packages",
    "net",
    "gross",
    "net_primary",
    "price",
    "amount",
    "hs",
    "finish",
)

out = []
for key, folder in folders.items():
    result = analyze(folder)
    out.append("==== " + key + " ====")
    header = result.get("header") or {}
    out.append("header: " + json.dumps(header, ensure_ascii=False)[:2500])
    for doc in result.get("documents") or []:
        lines = doc.get("lines") or []
        text = (doc.get("text") or "")[:400].replace("\n", " | ")
        out.append(
            f"DOC role={doc.get('role')} name={doc.get('name')} "
            f"nlines={len(lines)} currency={doc.get('currency')} "
            f"readable={doc.get('readable')} text0={text!r}"
        )
        for line in lines[:3]:
            sample = {
                item: line.get(item)
                for item in line_keys
                if line.get(item) not in (None, "")
            }
            out.append("  LINE " + json.dumps(sample, ensure_ascii=False))
    lots = result.get("lots") or []
    out.append(
        "lots={0} pieces={1} qty={2} packages={3} net_primary={4} vendor={5} model={6}".format(
            len(lots),
            sum(1 for lot in lots if lot.get("pieces") not in (None, "")),
            sum(1 for lot in lots if lot.get("qty") not in (None, "")),
            sum(1 for lot in lots if lot.get("packages") not in (None, "")),
            sum(1 for lot in lots if lot.get("net_primary") not in (None, "")),
            sum(1 for lot in lots if lot.get("vendor") not in (None, "")),
            sum(1 for lot in lots if lot.get("model") not in (None, "")),
        )
    )
    if lots:
        compact = {
            item: lots[0].get(item)
            for item in sorted(lots[0])
            if lots[0].get(item) not in (None, "", [], {})
        }
        out.append("lot0: " + json.dumps(compact, ensure_ascii=False)[:1800])
        if len(lots) > 1:
            compact_last = {
                item: lots[-1].get(item)
                for item in sorted(lots[-1])
                if lots[-1].get(item) not in (None, "", [], {})
            }
            out.append("lot_last: " + json.dumps(compact_last, ensure_ascii=False)[:1800])
    out.append("flags=" + json.dumps(result.get("flags"), ensure_ascii=False))
    out.append("")

Path("study_tools/out/audit_9_deep.txt").write_text("\n".join(out), encoding="utf-8")
print("wrote audit_9_deep.txt")
