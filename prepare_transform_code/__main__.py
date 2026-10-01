import json
import sys
from pathlib import Path

from prepare_transform_code.shipment import analyze


def main(argv):
    if len(argv) != 2:
        print("usage: python -m prepare_transform_code <папка поставки>")
        return 2
    result = analyze(Path(argv[1]))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
