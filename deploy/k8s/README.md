# Kubernetes (app tier)

Compose remains the easiest full-stack local demo (MySQL, Redpanda, Debezium,
Elasticsearch). These manifests run the **application services** — ingest,
worker, api, web — on Kubernetes.

## Layout

| Path | Contents |
| --- | --- |
| `base/` | Deployments, Services, ServiceAccount, ConfigMap, Ingress, HPAs, PDBs |
| `overlays/local-kind/` | kind + Docker Desktop → Compose infra via `host.docker.internal` |
| `overlays/ghcr/` | same base, images pulled from GHCR (published on merge to main) |
| `optional/` | CRD-dependent extras (ServiceMonitor); applied separately |

## Scope: app tier only

There are deliberately **no manifests for MySQL, Redpanda, Kafka Connect,
Elasticsearch, Prometheus or Grafana**. Running those as hand-written
StatefulSets is how you get a data-loss incident; in a real cluster they belong
to operators (ECK, Strimzi, kube-prometheus-stack) or managed services (RDS,
MSK, Elastic Cloud). Locally, Compose already provides them, and the
`local-kind` overlay points the app pods at that data plane.

## Probes

| Workload | Startup | Liveness | Readiness |
| --- | --- | --- | --- |
| `api` | `/health`, up to 180s | `/health` | `/health/ready` — 503 when ES is down |
| `web` | `/`, up to 60s | `/` | `/` |
| `worker` | `/metrics` on 9100, up to 300s | `/metrics` | none — see below |
| `ingest` | `/metrics` on 9100, up to 120s | `/metrics` | none — see below |

Two decisions worth knowing:

**Startup probes instead of long initial delays.** `api` and `worker` import
torch and sentence-transformers and the worker also waits for the ES index, so
boot takes tens of seconds. A `startupProbe` absorbs that window, which lets
liveness stay aggressive afterwards instead of a large `initialDelaySeconds`
permanently hiding real hangs.

**No readiness probe on `worker`/`ingest`.** Neither receives client traffic.
Their Services exist only so a `ServiceMonitor` can scrape them, and marking a
struggling consumer "not ready" would remove its metrics endpoint from
scraping at exactly the moment you need the data.

Caveat, stated honestly: the worker's metrics server starts *before* the model
load and index wait, so its probes prove the process is alive, not that the
consumer is caught up. For that, watch `pulse_sync_latency_seconds` and
consumer-group lag — see
[`docs/runbooks/backfill-and-replay.md`](../../docs/runbooks/backfill-and-replay.md).

## Security posture

The namespace enforces the **`restricted` Pod Security Standard**, so the
securityContext blocks cannot silently rot: every pod runs `runAsNonRoot` as
uid/gid 10001 (matching `USER appuser` / `USER nextjs` in the Dockerfiles), with
`allowPrivilegeEscalation: false`, all capabilities dropped, and the
`RuntimeDefault` seccomp profile.

`readOnlyRootFilesystem` is enabled only on `ingest`, which has no ML
dependencies. `api` and `worker` need a writable baked Hugging Face cache
(`HF_HOME=/opt/hf`) for tokenizer lock files, and Next.js writes to
`.next/cache`, so those three opt out with a comment rather than pretending.

## Scaling and disruption

- `api` and `web` are autoscaled on CPU (`base/hpa.yaml`) and have
  `PodDisruptionBudget`s using `maxUnavailable: 1` — `minAvailable: 1` would
  deadlock node drains at a single replica.
- `worker` is **not** autoscaled: useful parallelism is capped by the CDC
  topic's partition count, and CPU is a poor proxy for consumer lag. Scale by
  hand, or with a lag-aware autoscaler (KEDA).
- `ingest` is pinned to **exactly one replica** with `strategy: Recreate`. The
  Wikimedia SSE firehose has no consumer-group semantics, so a second reader
  would duplicate every event into MySQL.

HPAs need metrics-server. Without it they report `<unknown>` and simply do not
scale, which is harmless.

## Quick start (local-kind)

```bash
docker compose up -d mysql redpanda elasticsearch connect  # 1. data plane
make register                                              # 2. register CDC
make k8s-build                                             # 3. build :local images
make k8s-kind-up                                           # 4. kind cluster
make k8s-ingress                                           # 5. ingress-nginx
make k8s-kind-load                                         # 6. load images into kind
make k8s-apply                                             # 7. apply overlay
```

Then add to `/etc/hosts`:

```
127.0.0.1 pulsesearch.local api.pulsesearch.local
```

and open <http://pulsesearch.local> and <http://api.pulsesearch.local/docs>.

Without an Ingress controller, port-forward instead:

```bash
kubectl -n pulsesearch port-forward svc/api 8000:8000
kubectl -n pulsesearch port-forward svc/web 3000:3000
```

## Secrets

`base/kustomization.yaml` intentionally omits `secret.example.yaml`. Applying
`base/` alone leaves the `envFrom.secretRef` unresolved and pods stay in
`CreateContainerConfigError` until a `pulsesearch-secrets` Secret exists. The
`local-kind` overlay includes the example (empty `GROQ_API_KEY`, so RAG falls
back to extractive answers). For a real deployment, copy it and set the key, or
use a `secretGenerator` / External Secrets Operator — do not commit the value.

## TLS

`base/ingress.yaml` ships HTTP-only with a commented `tls:` block and
cert-manager annotation. With cert-manager installed, uncomment both and point
DNS at the controller's external IP.

## GHCR images (after merge to main)

```bash
kubectl apply -k deploy/k8s/overlays/ghcr
```

Packages: `ghcr.io/shubhamsinha1010/pulsesearch-cdc-rag/{api,worker,ingest,web}`.
The overlay pins `:latest`; for reproducible rollouts, repin to the
`sha-<short>` tag CI also publishes.

## Prometheus Operator (optional)

```bash
kubectl apply -f deploy/k8s/optional/servicemonitor.yaml
```

Kept out of `base/` because `ServiceMonitor` is a CRD and applying it without
the operator fails. The Deployments also carry `prometheus.io/*` annotations,
which is all an annotation-scraping Prometheus needs.

## Validate without a cluster

```bash
make k8s-validate   # kustomize render + kubeconform schema validation
```
