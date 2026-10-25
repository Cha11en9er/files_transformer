# Backend

FastAPI-приложение: загрузка комплекта поставки, вызов `prepare_transform_code`, вердикт модели, выгрузка Excel по профилю.

## Запуск

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Сайт: http://127.0.0.1:8010/

Секреты только в `.env`. Пример переменных — `.env.example`.

## Структура

- `app/api` — HTTP API
- `app/services` — оркестрация поставки, вызов модели, экспорт
- `app/transform` — легаси-путь (живой UI идёт через prepare)
- `tests` — unit/API тесты бэкенда

Боевой путь: `POST /api/v1/shipments/` → `prepare_transform_code.analyze` → вердикт → export.
