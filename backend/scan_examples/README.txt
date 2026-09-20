Примеры раскладки для модели (OpenCode).

Каждый прогон подмешивает в промпт встроенный атлас (колонки, merge, TOTAL) плюс все .txt из этой папки и из local/.

Как добавить свой пример
1. Создай файл backend/scan_examples/local/имя.txt (папку local git не коммитит).
2. Опиши форму документа, не конкретный артикул и не имя поставщика.
   Пиши: какие колонки бывают, что такое места vs количество, двустрочная шапка,
   пустой MODEL / «-» / n/a, две партии с разным qty при одном описании.
3. Копируй числа только как образец вида строки, не как эталон «подставь как в прошлом файле».
4. 15-40 строк достаточно. Не клади сюда целый инвойс и не клади PDF.
5. Перезапусти excel-transformer, чтобы новый txt попал в промпт.

Пример абзаца

Goods table: № | CODE (HS) | DESCRIPTION | MODEL / SERIES / ART. | PACKAGE | QTY | NETTO | BRUTTO | PRICE | AMOUNT
MODEL may be blank or "-". DESCRIPTION is then the match key across invoice, packing and spec.
PACKAGE = places (boxes/rolls). QTY = pieces. Two QTY/AMOUNT pairs = two lots.
Stop at TOTAL. Stacked header WEIGHT / NETTO is one net_weight column.

Не писать: «если шары / Bestway / файл NH-26001002».
