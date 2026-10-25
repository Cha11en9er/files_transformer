# deploy

## systemd

- `excel-transformer.service` — шаблон unit (путь/порт подставь под контур).
- `opencode.service` — сервис модели; на боевом контуре обычно уже запущен на :4096.

Боевой сайт сейчас: каталог `/opt/files_transformer_update`, порт **8200**, unit `excel-transformer-update`, ветка `prod`.

## deploy_8200.py

Тянет ветку с origin, перезапускает unit, проверяет `/api/health`.

```powershell
$env:SSH_DEPLOY_PASSWORD='…'
python deploy/deploy_8200.py
```

Пароль не коммитить. Скрипт читает его из env или (для локальной отладки) из файла терминала Cursor.
