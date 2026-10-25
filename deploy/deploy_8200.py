# -*- coding: utf-8 -*-
"""One-shot deploy to :8200. Password from env or agent terminal file. Not committed."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

HOST = "87.251.86.53"
USER = "root"
REMOTE = "/opt/files_transformer_update"
BRANCH = "prod"
TERMS = Path(
    r"C:\Users\Miroslav\.cursor\projects\c-repos-YouDo-vlad-bit-it-files-transformer\terminals"
)


def _password() -> str:
    env = (os.environ.get("SSH_DEPLOY_PASSWORD") or "").strip()
    if env:
        return env
    for path in sorted(TERMS.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"SSH_DEPLOY_PASSWORD='([^']+)'", text)
        if match:
            return match.group(1)
    raise SystemExit("SSH_DEPLOY_PASSWORD missing")


def main() -> None:
    try:
        import paramiko
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko

    password = _password()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=password, timeout=30)
    cmd = (
        f"cd {REMOTE} && git fetch origin {BRANCH} && git checkout {BRANCH} "
        f"&& git pull --ff-only origin {BRANCH} && git rev-parse --short HEAD "
        f"&& systemctl restart excel-transformer-update && sleep 3 "
        f"&& systemctl is-active excel-transformer-update "
        f"&& curl -m 5 -s http://127.0.0.1:8200/api/health"
    )
    _stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    client.close()
    sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
