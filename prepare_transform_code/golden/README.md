# golden

Локальный снимок разбора шаблонов для регрессии. Файл `snapshot.json` в git не входит (там реальные стороны и суммы).

Собрать заново из локальных `documents_final/`:

```powershell
python -m prepare_transform_code.golden              # сравнить
python -m prepare_transform_code.golden --update     # записать снимок
```
