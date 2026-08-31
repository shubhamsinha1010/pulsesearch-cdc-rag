.DEFAULT_GOAL := help
COMPOSE := docker compose
CONNECT_URL ?= http://localhost:8083
GROUP ?= pulsesearch-sync
TOPIC ?= pulse.pulsesearch.pages
DLQ_TOPIC ?= pulsesearch.dlq
ES_URL ?= http://localhost:9200
ES_INDEX ?= pages
# Seek target for replay-from: RFC3339 timestamp or epoch milliseconds.
TS ?= start
# Number of DLQ messages to print.
N ?= 20

.PHONY: help up down build logs ps register status replay replay-from recreate-index clean \
	dlq-count dlq-peek test smoke bench eval \
	lint lint-fix format-check validate-configs bandit security audit ci \
	k8s-validate k8s-build k8s-kind-up k8s-kind-load k8s-ingress k8s-apply k8s-delete

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

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

# Kafka rejects offset changes for a group with active members, so the consumer
# must be stopped for the seek to take effect. See docs/runbooks/backfill-and-replay.md.
replay: ## Replay CDC from the start of the topic (stops/starts the worker)
	$(COMPOSE) stop worker
	$(COMPOSE) exec redpanda rpk group seek $(GROUP) --to start --topics $(TOPIC)
	$(COMPOSE) start worker

replay-from: ## Replay CDC from TS=<RFC3339|epoch-ms> (e.g. make replay-from TS=2026-08-30T10:00:00Z)
	$(COMPOSE) stop worker
	$(COMPOSE) exec redpanda rpk group seek $(GROUP) --to $(TS) --topics $(TOPIC)
	$(COMPOSE) start worker

recreate-index: ## Delete the Elasticsearch index (worker recreates it) — DESTRUCTIVE
	curl -s -X DELETE $(ES_URL)/$(ES_INDEX) && echo "" && $(COMPOSE) restart worker

dlq-count: ## Show dead-letter topic partitions and offsets
	$(COMPOSE) exec redpanda rpk topic describe $(DLQ_TOPIC) -p

dlq-peek: ## Print the oldest N dead-letter messages (make dlq-peek N=50)
	$(COMPOSE) exec redpanda rpk topic consume $(DLQ_TOPIC) -n $(N) -o start

clean: ## Stop and remove volumes (DESTRUCTIVE)
	$(COMPOSE) down -v

test: ## Run unit tests for each service (isolated to avoid package clashes)
	cd services/common && python -m pytest -q --ignore=tests/test_es_upsert_search_smoke.py
	cd services/worker && python -m pytest -q
	cd services/api && python -m pytest -q
	cd services/ingest && python -m pytest -q

smoke: ## ES upsert → BM25 smoke (needs ES_URL; skips if ES is down)
	cd services/common && python -m pytest -q tests/test_es_upsert_search_smoke.py

bench: ## Retrieval latency benchmark: BM25 vs vector vs hybrid vs RAG (stack must be up)
	python scripts/bench_retrieval.py

eval: ## Live search-accuracy probe (stack must be up)
	python scripts/eval_search_accuracy.py

lint: ## Ruff lint + format check on services/ and scripts/
	ruff check services scripts
	ruff format --check services scripts

lint-fix: ## Auto-fix Ruff issues and format
	ruff check --fix services scripts
	ruff format services scripts

format-check: ## Ruff format check only
	ruff format --check services scripts

validate-configs: ## Parse every checked-in YAML/JSON config
	python scripts/validate_configs.py

bandit: ## Bandit security scan (medium+ severity)
	bandit -c bandit.yaml -r services --severity-level medium

security: bandit ## Alias for bandit

audit: ## pip-audit service requirement files
	pip-audit -r services/api/requirements.txt
	pip-audit -r services/worker/requirements.txt
	pip-audit -r services/ingest/requirements.txt
	pip-audit ./services/common

ci: ## Local stand-in for the main CI quality gates
	$(MAKE) lint
	$(MAKE) bandit
	$(MAKE) validate-configs
	$(MAKE) test

k8s-validate: ## Render kustomize and validate manifests (needs kubectl + kubeconform)
	kubectl kustomize deploy/k8s/base >/dev/null
	kubectl kustomize deploy/k8s/overlays/local-kind >/dev/null
	kubectl kustomize deploy/k8s/overlays/ghcr >/dev/null
	@command -v kubeconform >/dev/null \
		&& kubectl kustomize deploy/k8s/overlays/local-kind | kubeconform -summary -ignore-missing-schemas \
		&& kubectl kustomize deploy/k8s/overlays/ghcr | kubeconform -summary -ignore-missing-schemas \
		|| echo "kubeconform not installed — kustomize render OK"

k8s-build: ## Build app images tagged :local for kind
	docker build -f services/api/Dockerfile -t pulsesearch-api:local .
	docker build -f services/worker/Dockerfile -t pulsesearch-worker:local .
	docker build -f services/ingest/Dockerfile -t pulsesearch-ingest:local .
	docker build -f web/Dockerfile \
		--build-arg NEXT_PUBLIC_API_URL=http://api.pulsesearch.local \
		--build-arg NEXT_PUBLIC_WS_URL=ws://api.pulsesearch.local \
		-t pulsesearch-web:local web

k8s-kind-up: ## Create a local kind cluster named pulsesearch
	kind create cluster --name pulsesearch || true
	kubectl cluster-info --context kind-pulsesearch

k8s-ingress: ## Install ingress-nginx into the kind cluster (idempotent)
	kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/kind/deploy.yaml
	kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=180s

k8s-kind-load: ## Load :local images into kind
	kind load docker-image pulsesearch-api:local --name pulsesearch
	kind load docker-image pulsesearch-worker:local --name pulsesearch
	kind load docker-image pulsesearch-ingest:local --name pulsesearch
	kind load docker-image pulsesearch-web:local --name pulsesearch

k8s-apply: ## Apply local-kind overlay (Compose infra via host.docker.internal)
	kubectl apply -k deploy/k8s/overlays/local-kind
	kubectl -n pulsesearch get pods,svc,ingress

k8s-delete: ## Delete the pulsesearch namespace from the cluster
	kubectl delete namespace pulsesearch --ignore-not-found
