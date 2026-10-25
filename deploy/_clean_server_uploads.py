# -*- coding: utf-8 -*-
"""One-shot: list and wipe shipment upload dirs on the deploy host. Password from env."""
from __future__ import annotations

import os
import sys

HOST = os.environ.get("SSH_DEPLOY_HOST", "87.251.86.53")
USER = os.environ.get("SSH_DEPLOY_USER", "root")

DIRS = [
    "/opt/files_transformer_update/data/model_inputs",
    "/opt/files_transformer_update/backend/uploads",
    "/opt/files_transformer_update/backend/exports",
    "/opt/files_transformer_update/backend/data/model_inputs",
    "/opt/files_transformer_update/backend/data/vision_cache",
    "/opt/files_transformer/data/model_inputs",
    "/opt/files_transformer/backend/uploads",
    "/opt/files_transformer/backend/exports",
    "/opt/files_transformer/backend/data/model_inputs",
    "/opt/files_transformer/backend/data/vision_cache",
]

LIST_CMD = r"""
set -e
echo '=== before ==='
for d in %(dirs)s; do
  if [ -d "$d" ]; then
    echo "-- $d"
    du -sh "$d" 2>/dev/null || true
    echo -n "dirs: "; find "$d" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l
    echo -n "files: "; find "$d" -type f 2>/dev/null | wc -l
  else
    echo "-- missing $d"
  fi
done
echo '=== .env paths ==='
grep -E 'MODEL_INPUTS|UPLOAD|EXPORT|VISION' /opt/files_transformer_update/backend/.env 2>/dev/null || true
grep -E 'MODEL_INPUTS|UPLOAD|EXPORT|VISION' /opt/files_transformer/backend/.env 2>/dev/null || true
""" % {"dirs": " ".join(DIRS)}

WIPE_CMD = r"""
set -e
echo '=== wipe ==='
for d in %(dirs)s; do
  if [ -d "$d" ]; then
    # keep the root dir, remove contents (postavka_*, files, caches)
    find "$d" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    echo "cleaned $d"
    mkdir -p "$d"
    # restore gitkeep if this is a tracked empty marker path
    touch "$d/.gitkeep" 2>/dev/null || true
  fi
done
echo '=== wipe /tmp leftovers ==='
rm -rf /tmp/parse_* /tmp/ocr_pdf_* /tmp/excel_transformer.tar \
  /tmp/_probe_files.py /tmp/_agent.sh /tmp/_srv_fix.sh /tmp/_watch_model.py \
  /tmp/deploy_one_site.sh /tmp/deploy_two_sites.sh 2>/dev/null || true
echo '=== after ==='
for d in %(dirs)s; do
  if [ -d "$d" ]; then
    echo -n "$d -> "
    du -sh "$d" 2>/dev/null
    echo -n "  remaining entries: "
    find "$d" -mindepth 1 2>/dev/null | wc -l
  fi
done
echo -n 'tmp leftovers: '
ls /tmp 2>/dev/null | grep -E 'parse_|ocr_pdf_|excel_transformer|probe|deploy_' || echo none
""" % {"dirs": " ".join(DIRS)}


def main() -> None:
    try:
        import paramiko
    except ImportError:
        import subprocess

        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko

    password = (os.environ.get("SSH_DEPLOY_PASSWORD") or "").strip()
    if not password:
        raise SystemExit("Set SSH_DEPLOY_PASSWORD")

    do_wipe = "--wipe" in sys.argv
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=password, timeout=30)
    cmd = WIPE_CMD if do_wipe else LIST_CMD
    _stdin, stdout, stderr = client.exec_command(cmd, timeout=180)
    sys.stdout.write(stdout.read().decode("utf-8", "replace"))
    err = stderr.read().decode("utf-8", "replace")
    if err:
        sys.stderr.write(err)
    code = stdout.channel.recv_exit_status()
    client.close()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
