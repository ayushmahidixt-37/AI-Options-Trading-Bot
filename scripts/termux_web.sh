#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/ayushmahidixt-37/AI-Options-Trading-Bot.git}
APP_DIR=${APP_DIR:-$HOME/AI-Options-Trading-Bot}
WEB_HOST=${WEB_HOST:-127.0.0.1}
WEB_PORT=${WEB_PORT:-8000}
CONFIG_FILE=${CONFIG_FILE:-$APP_DIR/local-bot.env}
DATA_DIR=${DATA_DIR:-$APP_DIR/.termux-data}

if [[ -z ${OPTIONS_BOT_WEB_PASSWORD:-} ]]; then
  echo "Enter a local web UI password for username admin. It will not be shown:"
  read -r -s OPTIONS_BOT_WEB_PASSWORD
  echo
  export OPTIONS_BOT_WEB_PASSWORD
fi

if [[ -z ${OPTIONS_BOT_WEB_PASSWORD} ]]; then
  echo "OPTIONS_BOT_WEB_PASSWORD cannot be empty." >&2
  exit 2
fi

if command -v pkg >/dev/null 2>&1; then
  pkg update -y
  pkg install -y python git clang rust libffi openssl
fi

if [[ ! -d ${APP_DIR}/.git ]]; then
  git clone "${REPO_URL}" "${APP_DIR}"
fi

cd "${APP_DIR}"
git pull --ff-only || true

python -m pip install --upgrade pip setuptools wheel
if ! python -m pip install -e '.[dev]'; then
  echo "Standard install failed; installing Rust/Python build backend helpers and retrying." >&2
  python -m pip install --upgrade maturin
  python -m pip install --no-build-isolation -e '.[dev]'
fi

mkdir -p "${DATA_DIR}"
if [[ ! -f ${CONFIG_FILE} ]]; then
  cp bot.env.example "${CONFIG_FILE}"
  python - <<'PY'
import os
from pathlib import Path
path = Path(os.environ["CONFIG_FILE"])
data_dir = Path(os.environ["DATA_DIR"])
text = path.read_text(encoding="utf-8")
text = text.replace("/var/lib/ai-options-bot", str(data_dir))
text = text.replace("/etc/ai-options-bot/credentials.env", str(Path.cwd() / "credentials.env.example"))
path.write_text(text, encoding="utf-8")
PY
fi

python -m compileall -q src tests
pytest -q

echo
echo "Starting AI Options Trading Bot web UI..."
echo "Open this in tablet Chrome: http://${WEB_HOST}:${WEB_PORT}"
echo "Username: admin"
echo "Password: the password you entered"
echo
exec options-bot --config "${CONFIG_FILE}" web --host "${WEB_HOST}" --port "${WEB_PORT}"
