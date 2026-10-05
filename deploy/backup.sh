#!/bin/sh
# Daily database backup with rotation. Install with cron, for example:
#   30 2 * * * /opt/transit/deploy/backup.sh >> /var/log/transit-backup.log 2>&1
# Restore into an empty database:
#   gunzip -c FILE | docker compose -f docker-compose.prod.yml exec -T db psql -U transit transit
set -eu
cd "$(dirname "$0")"
DIR="${BACKUP_DIR:-/var/backups/transit}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
mkdir -p "$DIR"
FILE="$DIR/transit-$(date +%Y%m%d-%H%M).sql.gz"
docker compose -f docker-compose.prod.yml exec -T db pg_dump -U transit --no-owner transit | gzip > "$FILE.part"
mv "$FILE.part" "$FILE"
find "$DIR" -name 'transit-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) backed up to $FILE ($(du -h "$FILE" | cut -f1))"
