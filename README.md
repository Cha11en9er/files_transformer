# files_transformer

Сервис для таможенного брокера. Сотрудник загружает комплект одной поставки (инвойс, пакинг, спецификация, иногда справочник и скан). Код собирает таблицу позиций, модель проверяет черновик по исходным файлам, на выходе Excel для декларирования.

Неуверенные данные не подставляются молча — уходят на проверку. Правила опираются на подписи колонок и склейку лотов, не на имя отправителя.

## Профили выхода

Профиль задаёт только форму Excel. Чтение входа одно и то же.

| Профиль | Выход |
|---------|--------|
| **18233** (Hangzhou) | Три книги: инвойс, пакинг, спецификация с цветами |
| **BEIJING** (Goldluck) | Одна книга, листы Invoice / Packing / Specification / описание |
| **TSD** | Лист для ДТ |

На входе граница ролей размыта: несколько файлов или одна книга с листами — один комплект строк. Роль берётся из заголовка внутри документа.

## Структура репозитория

```
backend/                  FastAPI, UI, экспорт
frontend/                 Статика сайта (отдаётся бэкендом)
prepare_transform_code/   Разбор и склейка поставок
deploy/                   systemd unit и скрипт выкладки
study_tools/              dump_shipment.py — снимок поставки для разбора
roadmap/                  Дневник работ (для команды)
documents/                Локальные поставки (в git не входят)
documents_final/          Локальные эталоны шаблонов (в git не входят)
```

Секреты только в `backend/.env`. PDF, Excel и сканы поставок в git не кладутся.

## Локальный запуск

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Открой http://127.0.0.1:8010/

Подробнее: [backend/README.md](backend/README.md), [prepare_transform_code/README.md](prepare_transform_code/README.md).

## Боевой путь

`POST /api/v1/shipments/` → `prepare_transform_code.analyze` (черновик) → вердикт модели по файлам на диске → export по профилю.

1. Код читает xls/xlsx/pdf и собирает лоты.
2. Копии загрузок кладутся в `MODEL_INPUTS_ROOT/postavka_{uuid}/`.
3. Модель получает промпт (`verdict.py`) и пути к исходникам; числа, которых нет ни в черновике, ни в файле, не выдумывает.
4. Выгрузка: шаблоны 18233 / Beijing / TSD.

`package` — места, `quantity` — штуки.

## Сервер

| | |
|--|--|
| Remote | `https://github.com/Cha11en9er/files_transformer.git` |
| Боевая ветка | `prod` (код с линии prod_update_v2) |
| Каталог | `/opt/files_transformer_update` |
| Unit | `excel-transformer-update` |
| Порт | **8200** |

OpenCode на :4096 не трогать. Один раз:

```bash
mkdir -p /opt/files_transformer_update/data/model_inputs
```

В `.env`: `MODEL_INPUTS_ROOT=...`, `ALLOW_SKIP_MODEL=0`, `VERDICT_PROMPT=full`.

Выкладка с рабочей машины (пароль только в env сессии):

```powershell
$env:SSH_DEPLOY_PASSWORD='…'
python deploy/deploy_8200.py
```

## Тесты

```powershell
python -m unittest prepare_transform_code.tests.test_shipments
cd backend; python -m pytest
```

Тесты поставок ожидают локальные `documents_final/` и `documents/`. Без них часть кейсов пропускается.

## Документация по полям

- Правила подписей и склейки: `.cursor/rules/field-anchors.mdc`
- История разбора шаблонов: `prepare_logic.md` (внутренняя, для доработки парсера)
