#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

APP_DIR=/opt/ai-options-bot
CONFIG_DIR=/etc/ai-options-bot
DATA_DIR=/var/lib/ai-options-bot
LOG_DIR=/var/log/ai-options-bot

id ai-options-bot >/dev/null 2>&1 || \
  useradd --system --home-dir "${DATA_DIR}" --shell /usr/sbin/nologin ai-options-bot
install -d -o root -g ai-options-bot -m 0750 "${CONFIG_DIR}"
install -d -o ai-options-bot -g ai-options-bot -m 0750 "${DATA_DIR}" "${LOG_DIR}"

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/.venv/bin/python" -m pip install "${APP_DIR}"

if [[ ! -e ${CONFIG_DIR}/bot.env ]]; then
  install -o root -g ai-options-bot -m 0640 "${APP_DIR}/bot.env.example" "${CONFIG_DIR}/bot.env"
fi
if [[ ! -e ${CONFIG_DIR}/credentials.env ]]; then
  install -o root -g ai-options-bot -m 0640 "${APP_DIR}/credentials.env.example" "${CONFIG_DIR}/credentials.env"
fi
install -o root -g root -m 0644 "${APP_DIR}/deploy/ai-options-bot.service" /etc/systemd/system/
systemctl daemon-reload

echo "Edit ${CONFIG_DIR}/credentials.env and ${CONFIG_DIR}/bot.env."
echo "Then run: sudo systemctl enable --now ai-options-bot"
