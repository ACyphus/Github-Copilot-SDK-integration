#!/usr/bin/with-contenv bashio

GITHUB_TOKEN=$(bashio::config 'github_token')
CONNECTION_TOKEN=$(bashio::config 'connection_token')

if bashio::var.is_empty "${GITHUB_TOKEN}"; then
    bashio::log.fatal "No GitHub token configured."
    exit 1
fi

if bashio::var.is_empty "${CONNECTION_TOKEN}"; then
    bashio::log.fatal "No bridge connection token configured."
    exit 1
fi

export COPILOT_GITHUB_TOKEN="${GITHUB_TOKEN}"
export COPILOT_CONNECTION_TOKEN="${CONNECTION_TOKEN}"

INTERNAL_PORT=8001
EXTERNAL_PORT=8000
MAX_RETRIES=5
RETRY_DELAY=5

cleanup() {
    if [ -n "${SOCAT_PID:-}" ]; then
        kill "${SOCAT_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

bashio::log.info "Starting authenticated bridge proxy on port ${EXTERNAL_PORT}..."
socat \
    "TCP-LISTEN:${EXTERNAL_PORT},fork,reuseaddr" \
    "TCP:127.0.0.1:${INTERNAL_PORT}" &
SOCAT_PID=$!

ATTEMPT=1
while true; do
    bashio::log.info "Starting GitHub Copilot runtime (attempt ${ATTEMPT})..."
    copilot \
        --headless \
        --port "${INTERNAL_PORT}" \
        --disable-builtin-mcps \
        --no-auto-update \
        --log-level info
    EXIT_CODE=$?

    if [ "${EXIT_CODE}" -eq 0 ]; then
        bashio::log.info "GitHub Copilot runtime exited normally."
        exit 0
    fi

    if [ "${ATTEMPT}" -ge "${MAX_RETRIES}" ]; then
        bashio::log.fatal \
            "GitHub Copilot runtime failed after ${MAX_RETRIES} attempts."
        exit "${EXIT_CODE}"
    fi

    bashio::log.warning \
        "GitHub Copilot runtime exited with code ${EXIT_CODE}; retrying."
    sleep "${RETRY_DELAY}"
    ATTEMPT=$((ATTEMPT + 1))
done
