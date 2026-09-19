"""Ask OpenCode a one-line hello using the same .env as the app."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.opencode_review import ping_opencode, probe_opencode


def main() -> int:
    live = probe_opencode()
    print("health", json.dumps(live, ensure_ascii=False))
    ping = ping_opencode()
    print("ping", json.dumps(ping, ensure_ascii=False))
    return 0 if ping.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
