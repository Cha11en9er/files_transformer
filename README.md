# files_transformer

Рабочее место таможенного брокера: загружаешь комплект одной поставки (инвойс, пакинг, спецификация, при необходимости справочник и скан), получаешь Excel для декларирования.

## Как устроено

1. **Код** читает все файлы комплекта, находит шапки по подписям колонок, собирает лоты до строки `TOTAL` / `ИТОГО` и склеивает роли в один набор позиций.
2. **Модель** (OpenCode) получает полный промпт и пути к исходникам на диске. Черновик кода для неё подсказка, не истина: по файлам она может поправить поле, выбросить или добавить лот. Число, которого нет ни в черновике, ни в файле, она не выдумывает.
3. **Экспорт** пишет книги по выбранному профилю. Модель файл не верстает.

На сайте: загрузка → граф разбора → таблица / вид «как в Excel» → ZIP.

`package` — места (коробки, рулоны). `quantity` — штуки / метры / пары по смыслу колонки.

## Профили выхода

Профиль меняет только форму Excel. Разбор входа один.

| В UI | Код | Результат |
|------|-----|-----------|
| Три Excel: Инвойс + Пакинг + Спецификация | `18233` | Три книги в ZIP |
| Одна книга: Invoice + Packing + Specification + Описание | `BEIJING` | Одна книга с листами |

Для штучного товара дополнительно собирается лист **ДТ** (колонки TSD), если это уместно по составу поставки. Ткань (метры, рулоны, площадь) и штучный товар выходят разным набором столбцов.

Роль документа берётся из заголовка внутри файла (`INVOICE`, `PACKING LIST`, `Спецификация №`), не из имени файла и не из профиля. Несколько файлов или одна книга с несколькими листами — один комплект строк.

## Структура репозитория

```
backend/                   FastAPI, API, экспорт, отдача UI
frontend/                  Статика сайта
prepare_transform_code/    Разбор Excel/PDF и склейка лотов
deploy/                    systemd и скрипты выкладки
study_tools/               dump_shipment.py — снимок поставки для отладки
```

Локально (в git не входят): `documents/`, `documents_final/` — ваши поставки и эталоны для тестов.

Секреты только в `backend/.env`. PDF, Excel и сканы поставок в репозиторий не коммитятся.

## Локальный запуск

Нужны Python 3.10+ и доступный OpenCode на `:4096` (см. `backend/.env.example`).

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
# поправь OPENROUTER_API_KEY / пароль OpenCode; для локали удобно:
# MODEL_INPUTS_ROOT= (пусто) → backend/data/model_inputs
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Сайт: http://127.0.0.1:8010/

Важные переменные `.env`:

| Переменная | Смысл |
|------------|--------|
| `OPENCODE_URL` | Сервер модели, обычно `http://127.0.0.1:4096` |
| `OPENCODE_MODEL` / `_1` / `_2` | Модели вердикта |
| `MODEL_INPUTS_ROOT` | Куда класть копии загрузок для модели |
| `MODEL_INPUTS_TTL_H` | Через сколько часов чистить `postavka_*` (по умолчанию 24) |
| `ALLOW_SKIP_MODEL` | `0` на бою; `1` только для отладки без модели |
| `VERDICT_PROMPT` | `full` — полный промпт вердикта |
| `OPENCODE_TIMEOUT_S` | `0` — ждать ответ модели без обрыва |

Подробнее: [backend/README.md](backend/README.md), [prepare_transform_code/README.md](prepare_transform_code/README.md), [frontend/README.md](frontend/README.md).

## API (боевой путь)

`POST /api/v1/shipments/` → `prepare_transform_code.analyze` → вердикт по файлам в `MODEL_INPUTS_ROOT/postavka_{uuid}/` → `apply_verdict` → export.

Копии загрузок удаляются после ответа модели и дополнительно чистятся по TTL.

## Сервер

| | |
|--|--|
| Ветка | `prod` |
| Каталог | `/opt/files_transformer_update` |
| Unit | `excel-transformer-update` |
| Порт сайта | **8200** |
| OpenCode | **4096** (не перезапускать вместе с сайтом без нужды) |

Один раз на машине:

```bash
mkdir -p /opt/files_transformer_update/data/model_inputs
```

В `.env` на сервере: `MODEL_INPUTS_ROOT=/opt/files_transformer_update/data/model_inputs`, `ALLOW_SKIP_MODEL=0`, `VERDICT_PROMPT=full`, `OPENCODE_TIMEOUT_S=0`.

Выкладка с рабочей машины:

```powershell
$env:SSH_DEPLOY_PASSWORD='…'
python deploy/deploy_8200.py
```

Перед передачей сервера заказчику очистить загрузки и кэш:

```powershell
$env:SSH_DEPLOY_PASSWORD='…'
python deploy/_clean_server_uploads.py --wipe
```

См. также [deploy/README.md](deploy/README.md).

## Тесты

```powershell
python -m unittest prepare_transform_code.tests.test_shipments
cd backend
python -m pytest
```

Часть тестов читает локальные `documents_final/` и `documents/`. Без этих папок соответствующие кейсы пропускаются или падают.

## Правила разбора

Подписи колонок и склейка лотов описаны в `.cursor/rules/field-anchors.mdc` (для разработки парсера). История разбора шаблонов — `prepare_logic.md`, это внутренний дневник, не инструкция пользователя.
