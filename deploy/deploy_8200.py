# -*- coding: utf-8 -*-
"""Deploy branch `prod` to :8200. Password only from SSH_DEPLOY_PASSWORD."""
from __future__ import annotations

import os
import subprocess
import sys

HOST = os.environ.get("SSH_DEPLOY_HOST", "87.251.86.53")
USER = os.environ.get("SSH_DEPLOY_USER", "root")
REMOTE = "/opt/files_transformer_update"
BRANCH = "prod"


def _password() -> str:
    env = (os.environ.get("SSH_DEPLOY_PASSWORD") or "").strip()
    if env:
        return env
    raise SystemExit("Set SSH_DEPLOY_PASSWORD in the environment before deploy.")


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
    # After history rewrite, reset --hard is safer than ff-only on the server clone.
    cmd = (
        f"cd {REMOTE} && git fetch origin {BRANCH} && git checkout {BRANCH} "
        f"&& git reset --hard origin/{BRANCH} && git rev-parse --short HEAD "
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
