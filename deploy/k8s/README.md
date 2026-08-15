# Kubernetes (app tier)
#
# Compose remains the easiest full-stack local demo (MySQL, Redpanda, Debezium, ES).
# These manifests run the **application services** (ingest, worker, api, web) on
# Kubernetes — the gap most backend JDs in Germany ask about.
#
# Layout
#   base/                 Deployments, Services, ServiceAccount, Ingress, ConfigMap
#   overlays/local-kind/  kind + Docker Desktop → host.docker.internal infra
#   overlays/ghcr/        same base, images pulled from GHCR (main branch publishes)
#
# Probes
#   api     liveness `/health`, readiness `/health/ready` (503 if ES down)
#   web     HTTP `/`
#   worker/ingest  HTTP `/metrics` on METRICS_PORT
#
# Quick start (local-kind)
#   1. Start data plane:  docker compose up -d mysql redpanda elasticsearch connect
#   2. Register CDC:      make register
#   3. Build images:      make k8s-build
#   4. Kind cluster:      make k8s-kind-up
#   5. Ingress controller: make k8s-ingress
#   6. Load images:       make k8s-kind-load
#   7. Apply:             make k8s-apply
#   8. Hosts file:        127.0.0.1 pulsesearch.local api.pulsesearch.local
#   9. Open:              http://pulsesearch.local  and  http://api.pulsesearch.local/docs
#
# Or port-forward without Ingress:
#   kubectl -n pulsesearch port-forward svc/api 8000:8000
#   kubectl -n pulsesearch port-forward svc/web 3000:3000
#
# GHCR images (after merge to main)
#   kubectl apply -k deploy/k8s/overlays/ghcr
#   Packages: ghcr.io/shubhamsinha1010/pulsesearch-cdc-rag/{api,worker,ingest,web}
#
# Validate only (no cluster): make k8s-validate
