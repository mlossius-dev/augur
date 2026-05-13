#!/usr/bin/env bash
# Augur database restore script.
#
# Downloads a backup from object storage and restores it into Postgres.
# Intended for disaster recovery; NOT for routine operation.
#
# Usage:
#   ./restore.sh <backup-key>
#   e.g. ./restore.sh database/20260301T030000Z/augur.dump
#
# If no key is given, lists the 10 most recent backups and prompts.
#
# Required environment variables: same as backup.sh

set -euo pipefail

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
RESTORE_DIR="${RESTORE_DIR:-/tmp/augur-restore}"

# ── Pre-flight ────────────────────────────────────────────────────────────────

for var in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB \
           OBJECT_STORAGE_ENDPOINT OBJECT_STORAGE_BUCKET \
           OBJECT_STORAGE_ACCESS_KEY OBJECT_STORAGE_SECRET_KEY; do
    if [[ -z "${!var:-}" ]]; then
        echo "ERROR: ${var} is not set" >&2
        exit 1
    fi
done

for cmd in pg_restore aws; do
    if ! command -v "${cmd}" &>/dev/null; then
        echo "ERROR: ${cmd} not found" >&2
        exit 1
    fi
done

# ── Select backup ─────────────────────────────────────────────────────────────

BACKUP_KEY="${1:-}"

if [[ -z "${BACKUP_KEY}" ]]; then
    echo "Available backups (most recent 10):"
    AWS_ACCESS_KEY_ID="${OBJECT_STORAGE_ACCESS_KEY}" \
    AWS_SECRET_ACCESS_KEY="${OBJECT_STORAGE_SECRET_KEY}" \
    aws s3 ls "s3://${OBJECT_STORAGE_BUCKET}/database/" \
        --endpoint-url="${OBJECT_STORAGE_ENDPOINT}" \
        --region="${OBJECT_STORAGE_REGION:-eu-central}" \
        --recursive \
    | sort -r | head -10

    echo ""
    read -rp "Enter the backup key to restore (e.g. database/20260301T030000Z/augur.dump): " BACKUP_KEY
fi

if [[ -z "${BACKUP_KEY}" ]]; then
    echo "ERROR: No backup key provided" >&2
    exit 1
fi

# ── Confirm ───────────────────────────────────────────────────────────────────

echo ""
echo "WARNING: This will DROP and recreate the '${POSTGRES_DB}' database."
echo "Backup: s3://${OBJECT_STORAGE_BUCKET}/${BACKUP_KEY}"
echo ""
read -rp "Type 'yes' to proceed: " CONFIRM

if [[ "${CONFIRM}" != "yes" ]]; then
    echo "Aborted."
    exit 0
fi

# ── Download ──────────────────────────────────────────────────────────────────

mkdir -p "${RESTORE_DIR}"
DUMP_FILE="${RESTORE_DIR}/augur_restore.dump"

echo "Downloading ${BACKUP_KEY} …"
AWS_ACCESS_KEY_ID="${OBJECT_STORAGE_ACCESS_KEY}" \
AWS_SECRET_ACCESS_KEY="${OBJECT_STORAGE_SECRET_KEY}" \
aws s3 cp "s3://${OBJECT_STORAGE_BUCKET}/${BACKUP_KEY}" "${DUMP_FILE}" \
    --endpoint-url="${OBJECT_STORAGE_ENDPOINT}" \
    --region="${OBJECT_STORAGE_REGION:-eu-central}"

echo "Download complete: $(du -sh "${DUMP_FILE}" | cut -f1)"

# ── Restore ───────────────────────────────────────────────────────────────────

echo "Restoring to ${POSTGRES_DB} on ${POSTGRES_HOST}:${POSTGRES_PORT} …"

# Drop existing connections then recreate the database
PGPASSWORD="${POSTGRES_PASSWORD}" psql \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    --dbname="postgres" \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();"

PGPASSWORD="${POSTGRES_PASSWORD}" dropdb \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    "${POSTGRES_DB}"

PGPASSWORD="${POSTGRES_PASSWORD}" createdb \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    "${POSTGRES_DB}"

PGPASSWORD="${POSTGRES_PASSWORD}" pg_restore \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    --dbname="${POSTGRES_DB}" \
    --no-owner \
    --no-privileges \
    --verbose \
    "${DUMP_FILE}"

# ── Clean up ──────────────────────────────────────────────────────────────────

rm -f "${DUMP_FILE}"

echo ""
echo "Restore complete. Run 'augur migrate' to verify schema state."
