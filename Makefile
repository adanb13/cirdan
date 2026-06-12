# Cirdan dev + demo workflow. Run `make` (or `make help`) to list targets.

CIRDAN  ?= .venv/bin/cirdan
CIRDAND ?= .venv/bin/cirdand
PYTHON  ?= python3
COMPOSE := docker compose -f examples/demo/docker-compose.yml
DEMO    := examples/demo
Q       ?= what depends on postgres?

.DEFAULT_GOAL := help
.PHONY: help demo demo-up demo-map demo-incidents demo-query demo-watch demo-logs \
        demo-down demo-clean install test build map serve

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

## --- Demo stack (examples/demo) -------------------------------------------

demo: demo-up demo-map ## Start the demo stack and map it

demo-up: ## Start the demo containers and wait until api is healthy
	$(COMPOSE) up -d
	@echo "waiting for api healthcheck…"
	@until [ "$$(docker inspect demo-api-1 --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ]; do sleep 2; done
	@$(COMPOSE) ps --format 'table {{.Name}}\t{{.Status}}'
	@echo ""
	@echo "next: make demo-map · make demo-incidents · make demo-watch"

demo-map: ## Fingerprint + graph the demo (artifacts in examples/demo/cirdan-out)
	$(CIRDAN) map $(DEMO)

demo-incidents: ## Detection pass + incident list for the demo
	$(CIRDAN) incidents --path $(DEMO)

demo-query: ## Query the demo graph, e.g. make demo-query Q="what broke?"
	$(CIRDAN) query "$(Q)" --path $(DEMO)

demo-watch: ## Stream live demo events (Ctrl-C to stop); try: docker kill demo-redis-1
	$(CIRDAN) watch $(DEMO)

demo-logs: ## Tail logs from all demo containers
	$(COMPOSE) logs --tail=30

demo-down: ## Stop the demo containers
	$(COMPOSE) down

demo-clean: demo-down ## Stop the demo and delete its volumes + cirdan-out
	$(COMPOSE) down -v 2>/dev/null || true
	rm -rf $(DEMO)/cirdan-out

## --- Development ------------------------------------------------------------

install: ## Create .venv and install cirdanops editable with [all,dev]
	$(PYTHON) -m venv --without-pip .venv 2>/dev/null || $(PYTHON) -m venv .venv
	$(PYTHON) -m pip --python .venv/bin/python install -e ".[all,dev]"

test: ## Run the test suite
	.venv/bin/python -m pytest tests/ -q

build: ## Build sdist + wheel into dist/
	.venv/bin/python -m build --no-isolation

map: ## Map this repo itself
	$(CIRDAN) map .

serve: ## Run the always-on daemon against this repo
	$(CIRDAND) serve .
