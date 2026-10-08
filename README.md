# files_transformer

Сервис для таможенного брокера: сотрудник кладёт комплект одной поставки (инвойс, пакинг, спецификация, иногда справочник и скан). Парсер собирает таблицу позиций, модель OpenCode проверяет черновик, на выходе Excel для декларирования.

Неуверенные данные не подставляются молча, а идут на проверку. Правила живут на заголовках колонок и merge, не на имени отправителя.

## Профили

- **18233** (Hangzhou): три книги, инвойс / пакинг / спецификация с цветами.
- **BEIJING** (Goldluck): одна книга, листы Invoice / Packing list / Specification / описание.

Вход Beijing это не один файл. Товарная книга (Invoice+Packing) в основные файлы, `(описание )сводная.xlsx` в зону «Справочник», подписанный PDF в основные файлы.

## Локально

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Сайт: http://127.0.0.1:8010/

Секреты только в `backend/.env`. В git не класть.

## Боевой путь кода

`POST` загрузки → `backend/app/transform/`.

1. `reader.py` / `pdf.py` - сетка, merge раскрыт, `inherited` помечает унаследованное.
2. `extract.py` - заголовки по синонимам, роль листа, стоп на TOTAL / новой секции.
3. `merge.py` - `match_key` (пробелы и дефисы не различают артикул), коммерция с инвойса, веса с пакинга, коды только из однозначного catalog.
4. `service.py` - профиль, шапка, статусы файлов.
5. `export.py` / `export_beijing.py` / `export_style.py` - выгрузка.

Легаси `app/parsing/` в тестах ещё есть. Живой UI идёт через `transform/`. `package` это места, `quantity` это штуки.

## Git

Remote: `https://github.com/Cha11en9er/files_transformer.git`

Боевой сайт: ветка `prod_update`, каталог `/opt/files_transformer_update`, systemd `excel-transformer-update`, порт 8200. OpenCode на :4096 не трогать.

Порт 8100 больше не используется. На нём стояла ветка `prod`, коммит `733c7354d5cde6728a8d12249a9e743ea129262a` («Do not invent RMB when the shipment currency is USD or unknown», 5 октября 2026).
