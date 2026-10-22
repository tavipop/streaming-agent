# Local dev services. Agent tasks (tests, run, format) live in build.sh.

# Prefer the `docker compose` plugin, fall back to standalone docker-compose.
COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)

.PHONY: phoenix-up phoenix-down phoenix-logs

phoenix-up: ## Start Arize Phoenix (UI + OTLP receiver at http://localhost:6006)
	$(COMPOSE) up -d phoenix
	@echo "Phoenix UI: http://localhost:6006"
	@echo "Export traces: OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:6006/v1/traces"

phoenix-down: ## Stop Phoenix (trace data is kept in the phoenix-data volume)
	$(COMPOSE) down

phoenix-logs: ## Follow Phoenix logs
	$(COMPOSE) logs -f phoenix
