# study_tools

Локальные утилиты для разбора поставок. В git только рабочий скрипт; вывод и распаковка в `out/` не коммитятся.

## dump_shipment.py

Печатает клетки Excel, слитые диапазоны и текст PDF. Логику разбора сюда не переносить.

```powershell
python study_tools/dump_shipment.py "documents/7_pravka/Дегон"
python study_tools/dump_shipment.py "documents/7_pravka/Дегон" --rows 80 --pdf-pages 1 --no-pdf
python study_tools/dump_shipment.py --headers documents_final documents/4_pravka
```

Снимок каждый раз перезаписывает `study_tools/out/dump.txt`. Zip распаковывается в `study_tools/out/unzip`.
