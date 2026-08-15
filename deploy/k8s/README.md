# Kubernetes (app tier)
#
# Compose remains the easiest full-stack local demo (MySQL, Redpanda, Debezium, ES).
# These manifests run the **application services** (ingest, worker, api, web) on
# Kubernetes — the gap most backend JDs in Germany ask about.
#
# Layout
#   base/                 in-cluster DNS (mysql, redpanda, elasticsearch)
#   overlays/local-kind/  kind + Docker Desktop → host.docker.internal infra
#
# Quick start (local-kind)
#   1. Start data plane:  docker compose up -d mysql redpanda elasticsearch connect
#   2. Register CDC:      make register
#   3. Build images:      make k8s-build
#   4. Kind cluster:      make k8s-kind-up
#   5. Load images:       make k8s-kind-load
#   6. Apply:             make k8s-apply
#   7. Port-forward:      kubectl -n pulsesearch port-forward svc/api 8000:8000
#
# Validate only (no cluster): make k8s-validate
