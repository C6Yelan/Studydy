#!/bin/sh
set -eu
# 此服務不公開帳號或 cookie；每次啟動以記憶體內的新密鑰簽署內部狀態。
export SEARXNG_SECRET="$(/usr/local/searxng/.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
exec /usr/local/searxng/entrypoint.sh
