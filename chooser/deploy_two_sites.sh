#!/bin/bash
# Один боевой сайт: ветка prod_update, порт 8200.
# 8100 (ветка prod, 733c735) и выборщик на 8010 больше не поднимаем.
set -euo pipefail
REPO=https://github.com/Cha11en9er/files_transformer.git
DIR=/opt/files_transformer_update
BRANCH=prod_update
PORT=8200
UNIT=excel-transformer-update
ENV_SRC=/opt/files_transformer/backend/.env

if [ ! -d "$DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO" "$DIR"
fi
git -C "$DIR" fetch origin "$BRANCH"
git -C "$DIR" checkout "$BRANCH"
git -C "$DIR" reset --hard "origin/$BRANCH"
mkdir -p "$DIR/backend/.venv"
if [ ! -x "$DIR/backend/.venv/bin/python" ]; then
  python3 -m venv "$DIR/backend/.venv"
fi
"$DIR/backend/.venv/bin/pip" install -q -r "$DIR/backend/requirements.txt"
if [ -f "$ENV_SRC" ] && [ ! -f "$DIR/backend/.env" ]; then
  cp "$ENV_SRC" "$DIR/backend/.env"
fi

cat > "/etc/systemd/system/${UNIT}.service" <<EOF
[Unit]
Description=files_transformer ${BRANCH} :${PORT}
After=network.target

[Service]
WorkingDirectory=${DIR}/backend
Environment=PYTHONPATH=${DIR}
EnvironmentFile=-${DIR}/backend/.env
ExecStart=${DIR}/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$UNIT"
systemctl restart "$UNIT"
systemctl disable --now excel-transformer-prod excel-transformer 2>/dev/null || true
systemctl --no-pager --full status "$UNIT" || true
