#!/usr/bin/env bash
# Nightly backup for a docker-compose deployment: Postgres (devcrew-pgdata), the
# storyforge-data volume (ChromaDB + JSON job registries + SQLite checkpoints +
# users.json + .jwt_secret, all under /data), and .env (lives in the source tree, not a
# volume). Run from the repo root (where docker-compose.yml lives), e.g. via cron:
#   0 2 * * * /opt/usp-ai-apps/scripts/backup.sh >> /var/log/usp-ai-apps-backup.log 2>&1
#
# Restore drill (test this before relying on it):
#   docker compose down
#   docker run --rm -v storyforge-data:/data -v <backup-dir>:/backup alpine \
#     sh -c "rm -rf /data/* && tar xzf /backup/storyforge-data.tar.gz -C /"
#   docker compose up -d devcrew-postgres
#   gunzip -c <backup-dir>/devcrew-postgres.sql.gz | docker compose exec -T devcrew-postgres psql -U devcrew devcrew
#   docker compose up
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/usp-ai-apps}"
BACKUP_DIR="$BACKUP_ROOT/$(date +%F)"
# Set to an rsync destination (e.g. user@backup-host:/backups/usp-ai-apps/) to ship
# backups off this box. Left unset by default so a fresh checkout doesn't fail with no
# destination configured -- set it in the environment before relying on this in prod.
OFFBOX_TARGET="${USP_BACKUP_OFFBOX_TARGET:-}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

echo "==> Dumping devcrew-postgres"
docker compose exec -T devcrew-postgres \
  pg_dump -U devcrew devcrew | gzip > "$BACKUP_DIR/devcrew-postgres.sql.gz"

echo "==> Archiving storyforge-data volume"
docker run --rm \
  -v storyforge-data:/data:ro \
  -v "$BACKUP_DIR":/backup \
  alpine tar czf /backup/storyforge-data.tar.gz -C / data

echo "==> Backing up .env"
cp usp-ai-ba/backend/.env "$BACKUP_DIR/.env.backup"

if [ -n "$OFFBOX_TARGET" ]; then
  echo "==> Shipping to $OFFBOX_TARGET"
  rsync -a "$BACKUP_DIR" "$OFFBOX_TARGET"
else
  echo "==> USP_BACKUP_OFFBOX_TARGET not set -- backup stays local at $BACKUP_DIR only"
fi

echo "==> Pruning backups older than ${RETENTION_DAYS} days"
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} +

echo "==> Backup complete: $BACKUP_DIR"
