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

## Боевой путь кода (ветка prod_update_v2)

`POST /api/v1/shipments/` → `prepare_transform_code.analyze` (черновик) → OpenCode-вердикт по исходным файлам на диске → export по профилю.

1. Код читает xls/xlsx/pdf и собирает лоты (`prepare_transform_code`).
2. Копии загрузок кладутся в `MODEL_INPUTS_ROOT/postavka_{uuid}/` (на сервере обычно `/opt/files_transformer_update/data/model_inputs`).
3. Модель получает полный промпт (`verdict.py`) + абсолютные пути; PDF/сканы могут идти вложениями, Excel через tool `read`. JPEG-кадры листов для модели не строятся.
4. Выгрузка: `export.py` / `export_beijing.py` / шаблоны 18233.

Легаси `app/transform/` и `POST /recognize` остаются в коде, живой UI идёт через prepare. `package` это места, `quantity` это штуки.

## Git

Remote: `https://github.com/Cha11en9er/files_transformer.git`

Боевой сайт: ветка `prod_update_v2`, каталог `/opt/files_transformer_update`, systemd `excel-transformer-update`, порт 8200. OpenCode на :4096 не трогать.
Один раз: `mkdir -p /opt/files_transformer_update/data/model_inputs` и в `.env` строка `MODEL_INPUTS_ROOT=...`.

Порт 8100 больше не используется. На нём стояла ветка `prod`, коммит `733c7354d5cde6728a8d12249a9e743ea129262a` («Do not invent RMB when the shipment currency is USD or unknown», 5 октября 2026).
