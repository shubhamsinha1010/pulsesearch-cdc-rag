.DEFAULT_GOAL := help
COMPOSE := docker compose
CONNECT_URL ?= http://localhost:8083
GROUP ?= pulsesearch-sync
TOPIC ?= pulse.pulsesearch.pages

.PHONY: help up down build logs ps register status replay recreate-index clean test

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the full stack
	$(COMPOSE) up -d --build

down: ## Stop the stack (keep volumes)
	$(COMPOSE) down

build: ## Build all images
	$(COMPOSE) build

logs: ## Tail logs for all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

register: ## (Re)register the Debezium connector
	CONNECT_URL=$(CONNECT_URL) bash connectors/register.sh

status: ## Show Debezium connector status
	curl -s $(CONNECT_URL)/connectors/pulsesearch-mysql-connector/status | python3 -m json.tool

replay: ## Replay CDC by resetting the sync consumer group to earliest
	$(COMPOSE) exec redpanda rpk group seek $(GROUP) --to start --topics $(TOPIC)
	$(COMPOSE) restart worker

recreate-index: ## Delete the Elasticsearch index (worker recreates it)
	curl -s -X DELETE http://localhost:9200/pages && echo "" && $(COMPOSE) restart worker

clean: ## Stop and remove volumes (DESTRUCTIVE)
	$(COMPOSE) down -v

test: ## Run unit tests for each service (isolated to avoid package clashes)
	cd services/common && python -m pytest -q
	cd services/worker && python -m pytest -q
	cd services/api && python -m pytest -q
