#!/usr/bin/env bash

# Ensure the agent keeps running continuously by restarting it whenever it exits.
# This script is intended to be launched via `nohup run_codagent.sh &` and may
# also be invoked by cron to guarantee the agent is running.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="${RUN_CODAGENT_PYTHON_SCRIPT:-"${SCRIPT_DIR}/codagent_mccay.py"}"
LOG_DIR="${RUN_CODAGENT_LOG_DIR:-"${SCRIPT_DIR}/logs"}"
LOG_FILE="${LOG_DIR}/codagent_mccay.log"
LOCK_FILE="${RUN_CODAGENT_LOCK_FILE:-"${SCRIPT_DIR}/run_codagent.lock"}"
RESTART_DELAY="${RUN_CODAGENT_RESTART_DELAY:-5}"
MAX_RESTARTS="${RUN_CODAGENT_MAX_RESTARTS:-}"

if [[ -n "${MAX_RESTARTS}" && ! "${MAX_RESTARTS}" =~ ^[0-9]+$ ]]; then
  echo "$(date -Is) [ERROR] RUN_CODAGENT_MAX_RESTARTS must be a non-negative integer." | tee -a "${LOG_FILE}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"

if [[ ! -f "${PYTHON_SCRIPT}" ]]; then
  echo "$(date -Is) [ERROR] Unable to locate codagent_mccay.py at ${PYTHON_SCRIPT}." | tee -a "${LOG_FILE}"
  exit 1
fi

exec 200>"${LOCK_FILE}"
if ! flock -n 200; then
  echo "$(date -Is) [INFO] run_codagent.sh is already running. Exiting." | tee -a "${LOG_FILE}"
  exit 0
fi

cleanup() {
  flock -u 200 || true
  rm -f "${LOCK_FILE}"
}

on_terminate() {
  echo "$(date -Is) [INFO] Received termination signal. Cleaning up." | tee -a "${LOG_FILE}"
  cleanup
  exit 0
}

trap cleanup EXIT
trap on_terminate SIGINT SIGTERM

echo "$(date -Is) [INFO] run_codagent.sh started." | tee -a "${LOG_FILE}"

restart_count=0

while true; do
  echo "$(date -Is) [INFO] Launching codagent_mccay.py." | tee -a "${LOG_FILE}"
  python3 "${PYTHON_SCRIPT}" >>"${LOG_FILE}" 2>&1
  exit_code=$?
  restart_count=$((restart_count + 1))

  echo "$(date -Is) [WARN] codagent_mccay.py exited with status ${exit_code}." | tee -a "${LOG_FILE}"

  if [[ -n "${MAX_RESTARTS}" && "${restart_count}" -ge "${MAX_RESTARTS}" ]]; then
    echo "$(date -Is) [INFO] Reached maximum restart limit (${MAX_RESTARTS}). Exiting." | tee -a "${LOG_FILE}"
    break
  fi

  echo "$(date -Is) [WARN] Restarting in ${RESTART_DELAY}s." | tee -a "${LOG_FILE}"
  sleep "${RESTART_DELAY}"
done
