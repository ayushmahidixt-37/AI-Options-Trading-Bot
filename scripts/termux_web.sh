#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/ayushmahidixt-37/AI-Options-Trading-Bot.git}
APP_DIR=${APP_DIR:-$HOME/AI-Options-Trading-Bot}
WEB_HOST=${WEB_HOST:-127.0.0.1}
WEB_PORT=${WEB_PORT:-8000}
CONFIG_FILE=${CONFIG_FILE:-$APP_DIR/local-bot.env}
DATA_DIR=${DATA_DIR:-$APP_DIR/.termux-data}
CREDENTIALS_FILE=${CREDENTIALS_FILE:-$APP_DIR/credentials.env}
PASSWORD_FILE=${PASSWORD_FILE:-$DATA_DIR/web-password}

if command -v pkg >/dev/null 2>&1; then
  pkg update -y
  pkg install -y python git clang rust libffi openssl
fi

if [[ ! -d ${APP_DIR}/.git ]]; then
  git clone "${REPO_URL}" "${APP_DIR}"
fi

cd "${APP_DIR}"
git pull --ff-only || true

mkdir -p "${DATA_DIR}"
if [[ -z ${OPTIONS_BOT_WEB_PASSWORD:-} ]]; then
  if [[ -s ${PASSWORD_FILE} ]]; then
    OPTIONS_BOT_WEB_PASSWORD=$(cat "${PASSWORD_FILE}")
  else
    OPTIONS_BOT_WEB_PASSWORD=12345
    printf '%s\n' "${OPTIONS_BOT_WEB_PASSWORD}" > "${PASSWORD_FILE}"
  fi
  export OPTIONS_BOT_WEB_PASSWORD
fi

if [[ -z ${OPTIONS_BOT_WEB_PASSWORD} ]]; then
  echo "OPTIONS_BOT_WEB_PASSWORD cannot be empty." >&2
  exit 2
fi
printf '%s\n' "${OPTIONS_BOT_WEB_PASSWORD}" > "${PASSWORD_FILE}"
chmod 600 "${PASSWORD_FILE}"

python -m pip install --upgrade pip setuptools wheel

# Install only what is needed to run the dashboard. The dev extra includes Ruff,
# whose Rust release build can exceed the memory available on Android tablets.
python -m pip install fastapi jinja2 logzero pyotp python-multipart smartapi-python tzdata uvicorn websocket-client
python -m pip install --no-deps -e .

mkdir -p "${DATA_DIR}"
if [[ ! -f ${CREDENTIALS_FILE} ]]; then
  cp credentials.env.example "${CREDENTIALS_FILE}"
fi
chmod 600 "${CREDENTIALS_FILE}"

if [[ ! -f ${CONFIG_FILE} ]]; then
  cp bot.env.example "${CONFIG_FILE}"
fi

# Repair configs left behind by an interrupted older installer as well as new
# configs. Replacing only the documented Linux defaults preserves user edits.
CONFIG_FILE="${CONFIG_FILE}" DATA_DIR="${DATA_DIR}" CREDENTIALS_FILE="${CREDENTIALS_FILE}" python - <<'PY'
import os
from pathlib import Path
path = Path(os.environ["CONFIG_FILE"])
data_dir = Path(os.environ["DATA_DIR"])
credentials_file = Path(os.environ["CREDENTIALS_FILE"])
text = path.read_text(encoding="utf-8")
text = text.replace("/var/lib/ai-options-bot", str(data_dir))
text = text.replace("/etc/ai-options-bot/credentials.env", str(credentials_file))
text = text.replace(str(Path.cwd() / "credentials.env.example"), str(credentials_file))
path.write_text(text, encoding="utf-8")
PY
mkdir -p "${DATA_DIR}"

python -m compileall -q src
python -c "from zoneinfo import ZoneInfo; from SmartApi.smartConnect import SmartConnect; import fastapi, jinja2, uvicorn; import options_bot; ZoneInfo('Asia/Kolkata')"

# Older SmartAPI runs may have written request headers, including the API key,
# to date-based files. The application now disables those unsafe SDK logs.
rm -rf "${APP_DIR}/logs"

if grep -qE '^(ANGEL_API_KEY|ANGEL_CLIENT_CODE|ANGEL_PASSWORD|ANGEL_TOTP_SECRET|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)=[[:space:]]*$' "${CREDENTIALS_FILE}"; then
  echo
  echo "Credentials are not complete yet. Edit this private file, then rerun the script:"
  echo "  nano ${CREDENTIALS_FILE}"
  exit 2
fi

echo
echo "Starting AI Options Trading Bot web UI..."
echo "Open this in tablet Chrome: http://${WEB_HOST}:${WEB_PORT}"
echo "Username: admin"
echo "Password: saved in ${PASSWORD_FILE} (initial default: 12345)"
echo
exec options-bot --config "${CONFIG_FILE}" web --host "${WEB_HOST}" --port "${WEB_PORT}"
