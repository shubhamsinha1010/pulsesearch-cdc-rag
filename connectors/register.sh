#!/usr/bin/env bash
# Registers (or updates) the Debezium MySQL connector with Kafka Connect.
# Idempotent: safe to run repeatedly.
set -euo pipefail

CONNECT_URL="${CONNECT_URL:-http://localhost:8083}"
CONFIG_FILE="$(dirname "$0")/debezium-mysql.json"
CONNECTOR_NAME="pulsesearch-mysql-connector"

echo "Waiting for Kafka Connect at ${CONNECT_URL} ..."
until curl -sf "${CONNECT_URL}/connectors" >/dev/null; do
  sleep 2
done

echo "Registering connector '${CONNECTOR_NAME}' ..."
# Use the config-only body via PUT for idempotent create-or-update.
CONFIG=$(python3 -c "import json,sys;print(json.dumps(json.load(open('${CONFIG_FILE}'))['config']))")
curl -sf -X PUT \
  -H "Content-Type: application/json" \
  --data "${CONFIG}" \
  "${CONNECT_URL}/connectors/${CONNECTOR_NAME}/config" >/dev/null

echo "Connector status:"
curl -sf "${CONNECT_URL}/connectors/${CONNECTOR_NAME}/status" | python3 -m json.tool
