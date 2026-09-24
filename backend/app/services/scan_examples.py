"""Few-shot shapes of real invoices / packing lists for the scan model.

Not RAG over a vector store: a compact atlas of how goods lines look versus noise
(bank, address, stamps). Prepended to every OpenCode prompt.
"""

DOCUMENT_SHAPES = """
How source files look. EXAMPLES from other shipments, not the current job.
Use them only to recognise layout. Copy numbers only from this shipment's Excel, images and parser_json.

Languages mix freely (RU/EN/ZH/TR/IT and later others). Same field, different title:
  article = DESIGN / Art No. / Articul / Артикул / 货号 / Pattern / Ürün Kodu / Müşteri Kodu / ARTICLE
  qty = QUANTITY / PACKAGES? no: PACKAGES=rolls/cartons, QUANTITY=pcs or meters / HIDES / Miktar / 数量
  meters = METERS / TOTAL METERS / Net Metre / Q-ty meters / 米
  price = UNIT PRICE / Euro/m2 / Birim Fiyat / 单价
  amount = AMOUNT / Tutar / 金额
  description (customs) = Product name / Наименование товара  — NOT the article, NOT empty Annotation/Remarks
  article on Zhongfang spec = PRODUCT NAME (Melange 928), even though the words look like a name
Decimals: EU 1.740,82 = 1740.82; US/TR 3,85 or 3.85. Currency ISO on PRICE/AMOUNT may be USD/EUR/CNY/GBP/TRY/AED/SAR/KWD/IQD and others (pound, lira, dirham, dinar). If USD and TRY both print, the commercial amount is the foreign ISO (usually USD), not the local-tax column. Do not guess a currency when two ISO codes sit on the same price line with no winner.

A goods row is structural, not a named product. Keep it when the line has numbers
(qty and/or price/amount and/or net/gross and/or packages) PLUS any identity:
Art No., OR description, OR HS/TNVED, OR a running No.
The article cell may be blank, "-", "n/a", "б/н", "." or a repeated description.
That is still a goods line. Match INV+PL+SPEC by description (or HS+No) when SKU is missing.
Two own Quantity/Amount (or two own packing qty) with the same description are TWO lots.
Merged Art No. covering extra Quantity/Amount cells is ONE item with lots[].
Headers may be two stacked rows (WEIGHT over NETTO/BRUTTO) or split mid-word (PACKAG E).
PACKAGE / PACKAGES / CARTONS = places (rolls/boxes). QUANTITY = pcs/sets/meters. Never swap them.
parser_json.items may be EMPTY even when the table is visible. Then fill items[] from the
pages/workbook. Do not return items:[]. Overlap-with-draft applies only when the draft has rows.

Read EVERY sheet. Empty Foglio2/Foglio3, date-only Sheet2, and catalog card sheets named like 1601057 are not goods.
Merged cells: a family name in column A may cover several colour rows — keep colours separate if numbers differ.
Do not invent HS / TN VED. Catalog (справочник, сводная, Item/Артикул + ТНВЭД) fills codes only on exact article match.
If a code IS printed, normalize it: strip dots/spaces (54.07.73.00.90.11 → 540773009011), drop Excel .0, keep 6-13 digits. TNVED for the broker is at most 10 digits, no dots. HS with dots can sit in any cell (not only the HS column). A short PO like 2604 is not a code.

Junk rows can sit under the header, in the middle, or at the end: a lone PO number, a repeated 3-5 digit cell, a translation header (DESEN ADI / Sack nr), "page 2", "continued". Skip them. Do not remap columns from those rows. Stop only at TOTAL / a real new table header.

Two columns with the same title (DESING / DESIGN): the text one is article, the numeric one is color (997), not a second article. UNIT PICE is a price typo. METRS / AMOUNT (M) are meters, never amount money. PACKAGE is places, not qty.
On packing, Customer Name / Müşteri Kodu is the goods key; mill Design / Ürün Kodu is not. Area m2 = meters × width when m2 is not printed (width 1.38 M or 140 cm).
Sheet names QC / QCReport / certificate / menşe are not goods. A second sheet "Опис"/"описание" with qty=1 is a catalog identity overlay, not a second lot.

PDF invoices may be a letter or slash-line blob with no grid. Read the page text: "294,70 JACQUARD –LORENSA 6,60 1.945,02 USD" and "D15-5745 / DYER 290 / … 622,00 MT 3,85$ 2.394,70$". Design Name between slashes is the article. HS CODE under the line still counts. Two-row EN then TR headers merge into one title row.

--- Cross-supplier principles (apply to ANY new file, not only the kits below) ---
Letterhead: scan the rows ABOVE the goods table. Invoice No and Contract/Container are often label+adjacent cell.
Invoice/shipment DATE may be only inside a title phrase (any language): "shipment of …", "dated …", "dd.mm.yyyy",
"Jul.18,2026", "дата …" next to Invoice/Packing/Specification. That date is header.invoice_date, never invoice_no.
Do not use B/L Date, ETD, ETA, sailing, or delivery / сроки поставки / not later than as invoice_date.
A piece of the invoice number (EXD4-26-095) is not a date. Contract date is only the date on the Contract line, not the Specification dd date.
Buyer and Seller on one row: read down each column. "TO: Messrs" is not the buyer. Manufacturer is the Производитель label, even if it matches the seller.
Two DESIGN columns: article text plus colour code stay one item, and the exported article keeps both (MAXWELL 997). Do not merge those packing lines into one family row unless the packing list printed a single family total.
Weights: packing-list net/gross (family or article) are the commercial truth for the finished export.
Sender Specification roll rows, summed by article, are a cross-check (about ±0.2 kg usual). If they diverge more,
keep packing and flag. When packing is family-level and goods are per colour, share packing weight by each colour's
sender-spec weight share (fallback meters/area/qty). Do not invent a third total.
Descriptions: bilingual customs text usually lives in a separate catalog workbook/sheet, not on the invoice grid.
If Annotation/Remarks is empty and there is no Product name column, leave description null unless catalog matches.

--- 18233 Hangzhou Zhongfang fabric 626-1 / 626-2 (three Excel: invoice, packing, spec) ---
Letterhead Chinese 杭州中纺进出口有限公司 + English HANGZHOU ZHONGFANG. INV.NO. ZFRMB26148-626-1 vs 626-2 are DIFFERENT jobs.
Invoice header: NO. | DESIGN | H.S. CODE | ROLLS | WIDTH M | TOTAL M2 | METERS | UNIT MT/PIECE | UNIT PRICE(RMB) | AMOUNT(RMB)
Two row kinds on one invoice:
  1) numbered product: "2 | Noble 110 | 5407610000 | 15 | 1.42 | 868.33 | 611.5 | meters"  — often NO price on this row
  2) next unnumbered family: "SOFA FABRIC / Noble | 5407610000 | 27 | ... | 43.8 | 48990.3" — price/amount sit HERE; rolls 27 = 15+12 (Noble 110 + Noble 624)
Same for SOFA FABRIC / Melange under Melange 928; 626-2 uses ARTIFICIAL LEATHER / NAPPA under NAPPA 000, NAPPA 110, ...
Keep Noble 110 and Noble 624 as SEPARATE articles. Copy unit price from the family row onto each child. Do not replace children with the family.
Packing is family-level only: DESIGN "SOFA FABRIC / Noble", ROLLS 27, TOTAL METERS 1118.5, NET/GROSS.
Share that family net/gross across colour children by each child's sender-spec weight share (fallback: meters). Packing total stays authoritative; sender-spec sum is usually within ±0.2 kg.
626-2 packing may list ARTIFICIAL LEATHER / Magic twice (71 rolls and 38 rolls, same G/M 700) — two lots, keep both if amounts differ.
Spec is ROLL-level: ROLL No. | DATE | PRODUCT NAME | LOT | UNIT PRICE￥ | METERS | WIDTH | M2 | N.W | G.W. Product name = article (Melange 928). Sum rolls to invoice DESIGN.
Etalon spec sheet "описание": Art./Артикул = Noble 110, Product name / Наименование товара = bilingual customs text. Do not swap those two columns.

--- 18312 Hangzhou Element (same factory, different invoice grid) ---
Invoice: NO. | DESIGN | H.S. CODE | PACKAGES | QUANTITY | UNIT M/PC | UNIT PRICE(RMB) | AMOUNT(RMB)
PACKAGES = cartons/rolls. QUANTITY = 18000 meters/pcs. UNIT M/PC = M or PC. Do not put PRICE into qty (Профиль О-30: qty 18000, price 0.13, amount 2340, packages 3).
DESIGN may be two lines in one cell (newline): category then article. Treat as "Мебельный профиль / Профиль О-30 (круглый, 5015)".
Group row without NO. under the numbered line, same as 626.
Spec: PRODUCT NAME = article; QUANTITY per roll; DESCRIPTION column may be unit (M) not customs text. Sheet2 of спецификация.xls is only dates — ignore.
File "справочник сводная.xlsx" is a 2019 furniture catalog (INV/PL/Specification). Use Specification as catalog. Do NOT ingest INV/PL as this shipment's goods.

--- Beijing Goldluck (one workbook, several sheets) ---
File "Invoice n Packing list" / "поступление инвойс и пакинг.xlsx":
  Title row may carry the shipment date inside the heading (not the Invoice No.). Invoice No. / Contract / Container sit on the next letterhead rows as label + value.
  sheet "Invoice + Packing list": Art No. | Color | Quantity | Unit | Price | Amount | Measurement | Gross | Net | Cartons | Volume | pcs per carton — goods WITH prices
  sheet "Packing list": same articles, weights, cartons, Volume m3, pcs per carton — no price
Both sheets are the SAME shipment. Match Art No. Color Zinc is colour, not article.
Merged cells: Art No. covering two Quantity/Amount rows is ONE item with lots[] (continuation), not two numbered goods lines. Keep two items[] only when the Art No. is written again as its own cell. Do not add the two quantities into one commercial line without keeping lots[].
If Quantity/Price/Amount are merged down onto component SKUs of a set, that is ONE commercial line; sum component net/gross.
Articles may start with digits and still be articles.
Catalog "справочник сводная.xlsx" sheet Specification: Item/Артикул | Description/Наименование | ТНВЭД. Extra sheets 1601057, 1601030MB are mechanism cards, not invoice lines. Fill ТНВЭД + bilingual description only on exact article match. If there is no catalog, leave Description empty; do not invent text from an etalon.
Signed PDF confirms totals. Output profile BEIJING: four sheets Invoice / Packing list / Specification / описание.
Specification has No, Item, Description, cartons, qty, unit, net, gross, price, amount, customs code — no Color column. Invoice and packing DO have Color.
Put sequential No. on every goods row. Quantity is pieces/sets, not meters. Cartons ≠ Quantity.

--- Mora 18049 Italy (PDF invoice + .xls packing + xlsx spec) ---
Packing sheet Foglio1 (ignore empty Foglio2/3): PALLET | ARTICLE | COLOUR | HIDES | m2 | kg | Euro/m2
Key is ARTICLE + COLOUR (RIO NATURAL 1° ≠ RIO DESERT). HIDES = pieces. m2 is the commercial qty. Euro/m2 = price. kg = net.
Spec workbook: Sheet1 = specification grid; sheet "описание" = Product name / Наименование + ТНВЭД. Italian + RU + EN headers.

--- Yestar 18252 Zhejiang ---
PDF invoice(+packing). Excel packing sheet DPL: Pattern | Total Roll | Total Meter | CBM | N.W.KGS | G.W.KGS | Vendor PO
Pattern = article (Zoom 695). Many rows: article totals and/or roll lots. Chinese letterhead 浙江亦星.
Spec + sheet "описание": ART/Артикул vs Product name / Наименование товара as on Zhongfang etalon.

--- Turkish 18259 Tosun / Weavers / Ipekis / Escada / KDF ---
Invoice Tosun (ORIGINAL INVOICE, glued columns, no grid):
  ZIMMY  1740.82 MT  5.78 USD  10061.93 USD
Printed TOTAL: 1781.25 MT, 10311.38 USD, 35 ROLLS. Ignore bank/IBAN, TRY if USD present, certification notes.
Invoice Weavers: blob "Desing No / Design Name / Weavers Code / Item No / PO ... HS CODE : 540753009011" then 206.00 MT 3.85 $ 793.10 $
If HS digits run into meters (540753009011206,00 MT), split HS=540753009011 qty=206.00. Prefer Design Name (DYER 789). HS may be dotted (54.07.73.00.90.11) in any cell — digits only, TNVED first 10.
Packing Weavers: Customer Name is the goods article, mill Design is not. Two lots of one Design Name with own meters stay two items.
Packing Tosun "Seçme Listesi", no black borders:
  Ürün Kodu = mill/model; Müşteri Kodu = customer article (goods key, SINDRI 162)
  Artikel 1 Top 40.43 ... = group total, 1 = rolls. Genel Toplam = document totals, not an item.
Spec Excel always has a second sheet: "описание" / "Опис" / "Sheet1 (2)" with Articul/артикул and Product name / Наименование товара.
Ipekis: Articul may look like mill codes; ROLL NR/Номер рулона is not the article. Prefer Articul/артикул.
KDF Tekstop: read BOTH spec sheets. Articul like BLOOM (BLOOM); do not mix neighbouring HS columns into the article.
Escada packing sheet name "Ceki" = PACKING LIST / CEKI LISTESI. Goods may start after "DESCRIPTION OF THE GOODS / MAL CINSI".

--- Multi-page PDF invoice / packing (any supplier) ---
A goods table often continues on the next page without repeating the full header.
Page 2+ may start at the next item number. Keep reading until the printed TOTAL.
Two-row headers are common: parent WEIGHT over NETTO / NETTO WITH PRIMARY PACKAGING / BRUTTO.
MODEL / SERIES / ART. is the article. PACKAGE / PACKAG E is places, QUANTITY is pcs.
A stamp or signature may overlap the totals footer; the goods rows above stay readable.
When the upload is PDF-only (no Excel workbook), those pages are the source of truth.

--- What to extract ---
items[]: article, qty or meters, unit, rolls, price, amount, net_weight, gross_weight, description if printed or catalog-matched.
header[]: invoice_no, invoice_date (including title-embedded dates), contract_no, container when visible.
totals[]: printed TOTAL / Genel Toplam / Sub Total, not a sum you invent.
Ignored: letterhead noise (bank, stamps), signatures, addresses, date-only sheets, catalog cards, old INV inside справочник.
"""

from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "scan_examples"


def document_shapes() -> str:
    """Built-in atlas plus optional operator .txt notes in backend/scan_examples/."""
    chunks = [DOCUMENT_SHAPES.strip()]
    folders = [EXAMPLES_DIR, EXAMPLES_DIR / "local"]
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.txt")):
            if path.name.lower().startswith("readme"):
                continue
            text = path.read_text(encoding="utf-8").strip()
            if text:
                chunks.append(f"--- operator example {path.name} ---\n{text}")
    return "\n\n".join(chunks)

