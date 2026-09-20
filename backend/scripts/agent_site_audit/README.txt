# Agent site audit

Прогон живого сайта глазами пользователя: открыть UI, загрузить `вход/`, нажать Обработать, дождаться модели, скачать ZIP, сравнить с `эталон_заказчика/`.

Расхождения только пишутся в `findings.md`. Парсер в этом скрипте не правится.

```text
cd backend
.venv\Scripts\python.exe scripts/agent_site_audit/run_site_audit.py
.venv\Scripts\python.exe scripts/agent_site_audit/run_site_audit.py --limit 2
.venv\Scripts\python.exe scripts/agent_site_audit/run_site_audit.py --only 01,11
.venv\Scripts\python.exe scripts/agent_site_audit/run_site_audit.py --base-url http://127.0.0.1:8012/
```

Артефакты: `scripts/debug_docs/out/agent_site_audit/run_*/`
