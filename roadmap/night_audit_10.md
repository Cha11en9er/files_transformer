# Ночной аудит 10 поставок

Стартовал пока пользователь спит. Цель: загрузить 10 поставок на http://87.251.86.53:8200/, смотреть склейку, выгрузку, тайминги и чинить скорость.

## План китов

1. `10_pravka/Светильники` (18233, Excel) — та, что крутилась 12+ мин
2. `10_pravka/Генератор` (18233, Excel)
3. `10_pravka/Датчик` (BEIJING, PDF)
4. `10_pravka/Грибы` (BEIJING, PDF)
5. `10_pravka/продукты питания` (BEIJING, PDF)
6. `я_тестирую/01_ханчжоу_18233_626-1`
7. `я_тестирую/11_beijing_goldluck_003`
8. `я_тестирую/12_beijing_goldluck_002`
9. `я_тестирую/02_ханчжоу_18233_626-2`
10. `я_тестирую/13_bestway_way04`

Скрипт: `backend/scripts/night_site_audit.py`. Артефакты: `backend/scripts/debug_docs/out/night_site_audit/`.

## Правки скорости (в коде, нужен deploy на сервер)

| Что | Зачем |
|-----|--------|
| `FAST_RULES` вместо полного трактата на known+Excel | меньше токенов и tool-циклов |
| `OPENCODE_MODEL_FAST` / qwen без `:high` | не ждать grok:high на простой сверке |
| Без `OPENCODE_MODEL_2` на known Excel | вторая модель не нужна, если суммы сошлись |
| `VERDICT_SYSTEM_FAST` + «макс 3 read» | модель не читает весь xlsx по кругу |
| `OPENCODE_TIMEOUT_S=0` | ждать ответ, не «не ответила вовремя» |

Deploy (на сервере, SSH с этой машины нет):

```bash
cd /opt/files_transformer_update && git pull && sudo systemctl restart excel-transformer-update
```

В `.env` желательно: `OPENCODE_TIMEOUT_S=0`, `OPENCODE_MODEL_FAST=openrouter/qwen/qwen3.8-max-0902` (или как у тебя qwen).

## Результаты прогонов

Ниже дописывает скрипт по мере выполнения.
