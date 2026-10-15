#!/usr/bin/env bash
set -e -o pipefail
DASH="$HOME/Sabeq0/dashboard_final"
[ -f "$DASH/scripts/run_integrated.sh" ] || {
  echo "[ERROR] Run setup_final.sh first"
  exit 1
}
exec bash "$DASH/scripts/run_integrated.sh"
