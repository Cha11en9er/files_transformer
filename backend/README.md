# Backend

FastAPI: загрузка комплекта, вызов `prepare_transform_code`, вердикт OpenCode, экспорт Excel, отдача `frontend/`.

## Запуск

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

http://127.0.0.1:8010/ — секреты только в `.env` (образец `.env.example`).

## Структура

| Путь | Назначение |
|------|------------|
| `app/api` | HTTP API (`/api/v1/shipments/…`) |
| `app/services` | Оркестрация, model_inputs, вердикт, export 18233 / Beijing / TSD |
| `app/parsing` | Вспомогательный разбор шапки (легаси-слой рядом с prepare) |
| `app/transform` | Старый путь `POST /recognize`; живой UI им не пользуется |
| `data/model_inputs` | Локальные копии загрузок для модели (если `MODEL_INPUTS_ROOT` пуст) |
| `tests` | Тесты API и экспорта |

Боевой путь: create shipment → analyze → files to model → apply_verdict → export ZIP.
