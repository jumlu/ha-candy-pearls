#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Süßperlen Harness..."

# Export log level for Python app (app also reads options.json directly)
export LOG_LEVEL=$(bashio::config 'log_level')

exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8099
