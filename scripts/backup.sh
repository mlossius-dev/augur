#!/usr/bin/env bash
# Augur database backup script.
#
# Creates a compressed Postgres dump and uploads it to S3-compatible object
# storage (Hetzner Object Storage or Backblaze B2).
#
# Intended to run as a cron job on the VPS, e.g.:
#   0 3 * * * /opt/augur/scripts/backup.sh >> /var/log/augur-backup.log 2>&1
#
# Required environment variables (all set in .env / docker-compose):
#   POSTGRES_HOST        (default: localhost)
#   POSTGRES_PORT        (default: 5432)
#   POSTGRES_USER
#   POSTGRES_PASSWORD
#   POSTGRES_DB
#   OBJECT_STORAGE_ENDPOINT
#   OBJECT_STORAGE_BUCKET
#   OBJECT_STORAGE_ACCESS_KEY
#   OBJECT_STORAGE_SECRET_KEY
#   OBJECT_STORAGE_REGION    (default: eu-central)
#
# Optional:
#   BACKUP_RETAIN_DAYS   (default: 30 — older remote backups are deleted)
#   BACKUP_DIR           (default: /tmp/augur-backups — local staging)

set -euo pipefail

TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/tmp/augur-backups}"
BACKUP_RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-30}"
DUMP_FILE="${BACKUP_DIR}/augur_${TIMESTAMP}.dump"

echo "[${TIMESTAMP}] Starting Augur database backup"

# ── Pre-flight checks ─────────────────────────────────────────────────────────

for var in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB \
           OBJECT_STORAGE_ENDPOINT OBJECT_STORAGE_BUCKET \
           OBJECT_STORAGE_ACCESS_KEY OBJECT_STORAGE_SECRET_KEY; do
    if [[ -z "${!var:-}" ]]; then
        echo "ERROR: ${var} is not set" >&2
        exit 1
    fi
done

if ! command -v pg_dump &>/dev/null; then
    echo "ERROR: pg_dump not found" >&2
    exit 1
fi

if ! command -v aws &>/dev/null; then
    echo "ERROR: aws CLI not found — install it to upload backups" >&2
    exit 1
fi

# ── Create dump ───────────────────────────────────────────────────────────────

mkdir -p "${BACKUP_DIR}"

PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    --dbname="${POSTGRES_DB}" \
    --format=custom \
    --compress=9 \
    --file="${DUMP_FILE}"

DUMP_SIZE=$(du -sh "${DUMP_FILE}" | cut -f1)
echo "Dump created: ${DUMP_FILE} (${DUMP_SIZE})"

# ── Upload to object storage ──────────────────────────────────────────────────

S3_KEY="database/${TIMESTAMP}/augur.dump"
S3_URI="s3://${OBJECT_STORAGE_BUCKET}/${S3_KEY}"

AWS_ACCESS_KEY_ID="${OBJECT_STORAGE_ACCESS_KEY}" \
AWS_SECRET_ACCESS_KEY="${OBJECT_STORAGE_SECRET_KEY}" \
aws s3 cp "${DUMP_FILE}" "${S3_URI}" \
    --endpoint-url="${OBJECT_STORAGE_ENDPOINT}" \
    --region="${OBJECT_STORAGE_REGION:-eu-central}"

echo "Uploaded: ${S3_URI}"

# ── Clean up local staging file ───────────────────────────────────────────────

rm -f "${DUMP_FILE}"
echo "Local dump removed"

# ── Prune old remote backups ─────────────────────────────────────────────────

CUTOFF=$(date -u -d "${BACKUP_RETAIN_DAYS} days ago" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
         || date -u -v -"${BACKUP_RETAIN_DAYS}"d +"%Y-%m-%dT%H:%M:%SZ")

echo "Pruning backups older than ${BACKUP_RETAIN_DAYS} days (before ${CUTOFF})"

AWS_ACCESS_KEY_ID="${OBJECT_STORAGE_ACCESS_KEY}" \
AWS_SECRET_ACCESS_KEY="${OBJECT_STORAGE_SECRET_KEY}" \
aws s3 ls "s3://${OBJECT_STORAGE_BUCKET}/database/" \
    --endpoint-url="${OBJECT_STORAGE_ENDPOINT}" \
    --region="${OBJECT_STORAGE_REGION:-eu-central}" \
| while read -r date time size key; do
    # Date from ls is ISO; compare lexicographically
    if [[ "${date}T${time}Z" < "${CUTOFF}" ]]; then
        OLD_URI="s3://${OBJECT_STORAGE_BUCKET}/database/${key}"
        echo "  Deleting ${OLD_URI}"
        AWS_ACCESS_KEY_ID="${OBJECT_STORAGE_ACCESS_KEY}" \
        AWS_SECRET_ACCESS_KEY="${OBJECT_STORAGE_SECRET_KEY}" \
        aws s3 rm "${OLD_URI}" \
            --endpoint-url="${OBJECT_STORAGE_ENDPOINT}" \
            --region="${OBJECT_STORAGE_REGION:-eu-central}" || true
    fi
done

echo "[${TIMESTAMP}] Backup complete"
