#!/bin/sh
# Writes the UI's runtime config from the environment (BL-070): one image, any backend URL.
set -eu
API_URL="${DEVCREW_API_URL:-http://localhost:8080}"
case "$API_URL" in
  *\"*|*\\*) echo "DEVCREW_API_URL must not contain quotes or backslashes" >&2; exit 1 ;;
esac
printf '{ "apiUrl": "%s" }\n' "$API_URL" > /usr/share/nginx/html/config.json
