#!/bin/bash
# Instala un cron job diario (6am) para sincronizar badges desde tu Mac.
# Requiere: pip install requests beautifulsoup4 boto3
# Ejecutar una sola vez: bash scraper/setup_cron.sh

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$(which python3)"
CRON_CMD="0 6 * * * cd $SCRIPT_DIR && $PYTHON scraper/run_local.py >> /tmp/mapache_badge_sync.log 2>&1"

# Add if not already present
(crontab -l 2>/dev/null | grep -v "run_local.py"; echo "$CRON_CMD") | crontab -
echo "Cron instalado: $CRON_CMD"
echo "Logs en: /tmp/mapache_badge_sync.log"
