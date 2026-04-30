#!/usr/bin/env bash
# Backup automatico SQLite DB (P3.13 SIGNAL_ROADMAP).
#
# Strategy: rotating daily backup. Mantiene ultimi 7 daily + ultimi 4
# weekly + ultimi 12 monthly. Backup directory `data/backups/` (gitignored).
#
# Usage:
#   ./scripts/backup_db.sh                # backup interactive
#   ./scripts/backup_db.sh --quiet        # no output (cron-friendly)
#
# Cron daily 03:00 (no market hours):
#   0 3 * * * cd /path/to/propicks-ai-framework && ./scripts/backup_db.sh --quiet

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_FILE="$REPO_ROOT/data/propicks.db"
BACKUP_DIR="$REPO_ROOT/data/backups"

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

log() {
  [[ $QUIET -eq 0 ]] && echo "$@"
}

err() {
  echo "[ERROR] $@" >&2
}

# Pre-check
if [[ ! -f "$DB_FILE" ]]; then
  err "DB file non trovato: $DB_FILE"
  exit 1
fi

mkdir -p "$BACKUP_DIR"/{daily,weekly,monthly}

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
DOW=$(date +%u)              # 1-7 (Mon-Sun)
DOM=$(date +%d)              # 01-31

# Daily backup (sempre)
DAILY_FILE="$BACKUP_DIR/daily/propicks_${TIMESTAMP}.db"
log "[backup] daily → $DAILY_FILE"
sqlite3 "$DB_FILE" ".backup '$DAILY_FILE'"
SIZE=$(du -h "$DAILY_FILE" | cut -f1)
log "[backup] saved ($SIZE)"

# Weekly: domenica
if [[ $DOW -eq 7 ]]; then
  cp "$DAILY_FILE" "$BACKUP_DIR/weekly/propicks_${TIMESTAMP}.db"
  log "[backup] weekly snapshot saved"
fi

# Monthly: primo giorno mese
if [[ $DOM -eq 01 ]]; then
  cp "$DAILY_FILE" "$BACKUP_DIR/monthly/propicks_${TIMESTAMP}.db"
  log "[backup] monthly snapshot saved"
fi

# Rotation: tieni ultimi N
prune_keep() {
  local dir="$1" keep="$2"
  cd "$dir"
  if (( $(ls -1 | wc -l) > keep )); then
    ls -1t | tail -n +$((keep + 1)) | xargs rm -f
    log "[rotation] $dir pruned (keep=$keep)"
  fi
}

prune_keep "$BACKUP_DIR/daily" 7
prune_keep "$BACKUP_DIR/weekly" 4
prune_keep "$BACKUP_DIR/monthly" 12

log "[backup] done. Total backups:"
log "  daily:   $(ls -1 "$BACKUP_DIR/daily" | wc -l | tr -d ' ')"
log "  weekly:  $(ls -1 "$BACKUP_DIR/weekly" | wc -l | tr -d ' ')"
log "  monthly: $(ls -1 "$BACKUP_DIR/monthly" | wc -l | tr -d ' ')"
