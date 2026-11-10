#!/usr/bin/env bash
# Run ON THE SERVER as the frappe user (or with sudo where noted).
# Installs the app on ONE site only. Other sites on the bench are untouched:
# app_include_js/css only load on sites where the app is installed.
#
# Usage: ./deploy.sh <bench-path> <app-source-dir> <site-name>
#   e.g. ./deploy.sh /home/frappe/frappe-bench /tmp/file_converter_app mysite.local
set -euo pipefail

BENCH="${1:?bench path}"
SRC="${2:?path to file_converter_app}"
SITE="${3:?site name (see: ls $BENCH/sites)}"

echo "== Sites on this bench (for reference) =="
ls "$BENCH/sites" | grep -v -E '^(assets|apps.txt|common_site_config.json|apps.json)$' || true

echo "== Converter engines (server-wide, harmless to other sites) =="
sudo apt-get update
sudo apt-get install -y libreoffice-writer libreoffice-calc libreoffice-impress ffmpeg poppler-utils

cd "$BENCH"
echo "== Add app to bench =="
bench get-app file_converter "$SRC"

echo "== Install on target site ONLY =="
bench --site "$SITE" install-app file_converter
bench build --app file_converter
bench --site "$SITE" clear-cache

echo "== Verify =="
bench --site "$SITE" list-apps
echo "Other sites are unchanged. Restart workers (brief blip for all sites):"
echo "  sudo supervisorctl restart all    # or: bench restart"
