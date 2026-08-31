# Runbook: backfill and replay

How to rebuild the Elasticsearch index from the change log, in whole or in
part, without taking the pipeline down or corrupting data.

**Audience:** whoever is on call for the sync pipeline.
**Estimated time:** 2 minutes to start a replay; minutes to hours for it to
catch up, depending on how much log you rewind.

---

## 1. Mental model

```
MySQL (system of record)
  └─ binlog ─→ Debezium (Kafka Connect) ─→ Kafka topic pulse.pulsesearch.pages
                                              └─→ worker ─→ Elasticsearch
```

There are **two independent rewind levers**, and picking the wrong one is the
most common mistake:

| Lever | What it replays | Use when |
| --- | --- | --- |
| **Consumer-group offset reset** | Events still retained in the Kafka topic | The index is wrong/empty but the change log is intact. Cheap, fast, no load on MySQL. |
| **Debezium re-snapshot** | Rebuilds the topic from MySQL itself | The topic has aged out, was deleted, or you need rows that predate CDC. Expensive; hits MySQL. |

Reach for the offset reset first. Only re-snapshot if the log genuinely no
longer contains what you need (see §6).

## 2. Why replay is safe

Replaying is not a destructive operation here, by design:

- Every upsert is **version-guarded**. The worker sets the document `version`
  from the Debezium `ts_ms` and writes with
  `version_type=external_gte` (`services/worker/app/handlers.py`,
  `services/common/pulsesearch_common/es_client.py`). Re-delivering an old
  event is a no-op — Elasticsearch rejects the write rather than regressing a
  newer document.
- Deletes are **soft** tombstones (`deleted: true`) written through the same
  version guard, so they survive replay too.
- Offsets are committed only **after** a batch is durably written
  (`services/worker/app/consumer.py`), so Kafka gives at-least-once and the
  version guard makes indexing effectively-once.

Net effect: replaying the entire topic converges the index to the same state.
It costs CPU (re-embedding) and time, not correctness.

## 3. Decide what you actually need

| Symptom | Action |
| --- | --- |
| Index empty or partially populated, log intact | Full replay — §4 |
| Bad window (e.g. a bug shipped between 10:00–12:00) | Time-bounded replay — §5 |
| Changed `EMBEDDING_MODEL` or `EMBEDDING_DIMENSIONS` | Recreate the index mapping first — §7 — then full replay |
| Topic deleted / retention expired / need pre-CDC rows | Re-snapshot from MySQL — §6 |
| A handful of poison messages | Not a replay. See [dead-letter-queue.md](dead-letter-queue.md) |

Before anything, capture the current state so you can tell whether you helped:

```bash
curl -s localhost:8000/health/ready            # documents count
curl -s "localhost:9200/pages/_count?pretty"   # raw ES count
docker compose exec redpanda rpk group describe pulsesearch-sync
```

The `LAG` column in `rpk group describe` is the number you will watch during
catch-up.

## 4. Full replay (Docker Compose)

```bash
make replay
```

That target stops the worker, seeks the consumer group to the start of the
topic, and starts the worker again:

```bash
docker compose stop worker
docker compose exec redpanda rpk group seek pulsesearch-sync --to start \
  --topics pulse.pulsesearch.pages
docker compose start worker
```

**The worker must be stopped first.** Kafka will not let you move offsets for a
group that has active members; seeking against a live consumer either fails or
is immediately overwritten by the running member's committed offsets.

Then watch it drain:

```bash
docker compose logs -f worker
watch -n5 'docker compose exec -T redpanda rpk group describe pulsesearch-sync'
```

Done when `LAG` returns to ~0 and the document count has stopped climbing.
Re-embedding is the bottleneck; expect throughput in the low hundreds of
documents/second on a laptop CPU.

## 5. Time-bounded (partial) replay

Cheaper than a full replay when you know the damaged window. `rpk` accepts a
timestamp as the seek target:

```bash
make replay-from TS=2026-08-30T10:00:00Z
```

which is:

```bash
docker compose stop worker
docker compose exec redpanda rpk group seek pulsesearch-sync \
  --to 2026-08-30T10:00:00Z --topics pulse.pulsesearch.pages
docker compose start worker
```

`TS` accepts an RFC3339 timestamp or epoch milliseconds. There is no "stop at"
bound — the worker replays from that point forward to the head, which is
normally what you want.

Note the interaction with §2: replaying a window **cannot** undo a *newer*
correct write, because the version guard rejects the older event. If your bug
wrote *newer* bad data, a replay of older events will not fix it — you need to
recreate the index (§7) and replay in full.

## 6. Re-snapshot from MySQL (topic gone or aged out)

Only when the Kafka log can no longer supply the events. Two retention limits
apply, and both are easy to trip in a demo environment:

- MySQL binlog: `binlog-expire-logs-seconds=86400` (24h) in
  `docker-compose.yml` — Debezium cannot re-read changes older than this.
- Kafka topic retention: whatever Redpanda defaults to for
  `pulse.pulsesearch.pages`.

If the rows still exist in MySQL, the cleanest path is a fresh snapshot:

```bash
# 1. Remove the connector and its stored offsets, then re-register it.
curl -s -X DELETE http://localhost:8083/connectors/pulsesearch-mysql-connector
make register
make status
```

Debezium takes a new consistent snapshot of the `pages` table and republishes
it, after which the worker consumes it normally. A snapshot re-reads the whole
table, so expect load on MySQL and a burst of Kafka traffic.

If you want a *targeted* re-snapshot without dropping the connector, Debezium
supports signal-based incremental snapshots — preferable in production, but it
requires a signalling table that this project does not yet provision.

## 7. Recreate the index mapping

Needed when the mapping itself must change — most commonly a different
embedding model or dimension count, which the worker refuses to index into a
mismatched `dense_vector` field.

```bash
make recreate-index    # DELETE the index; worker recreates it on restart
make replay            # then refill it from the log
```

This is **destructive**: the index is dropped and search returns nothing until
the replay catches up. `recreate-index` honours `ES_URL` and `ES_INDEX`, so
double-check you are pointed at the right cluster:

```bash
make recreate-index ES_URL=http://localhost:9200 ES_INDEX=pages
```

## 8. Kubernetes equivalents

`make replay` shells into a Compose container, so it does **not** work against
a cluster deployment. The sequence is the same — stop the consumer, move the
offsets, start the consumer:

```bash
# 1. Drain the consumer group (an HPA does not manage the worker, so this holds).
kubectl -n pulsesearch scale deployment/worker --replicas=0
kubectl -n pulsesearch rollout status deployment/worker --timeout=120s
```

With the `local-kind` overlay, Redpanda still runs under Compose on the host,
so seek from there:

```bash
docker compose exec redpanda rpk group seek pulsesearch-sync --to start \
  --topics pulse.pulsesearch.pages
```

If Kafka runs **inside** the cluster, use a throwaway pod instead:

```bash
kubectl -n pulsesearch run rpk-admin --rm -it --restart=Never \
  --image=redpandadata/redpanda:v24.1.7 --command -- \
  rpk group seek pulsesearch-sync --to start \
    --topics pulse.pulsesearch.pages --brokers redpanda:9092
```

Then restore the worker and watch it catch up:

```bash
kubectl -n pulsesearch scale deployment/worker --replicas=1
kubectl -n pulsesearch logs -f deployment/worker
```

To recreate the index from inside the cluster:

```bash
kubectl -n pulsesearch exec deployment/api -- \
  python -c "import urllib.request as u; \
r=u.Request('http://elasticsearch:9200/pages', method='DELETE'); \
print(u.urlopen(r).status)"
kubectl -n pulsesearch rollout restart deployment/worker
```

## 9. Verify

A replay is finished and healthy when all of these hold:

```bash
# Lag back to ~0
docker compose exec redpanda rpk group describe pulsesearch-sync

# Document count stable and non-zero
curl -s localhost:8000/health/ready

# Freshness recovered — p95 sync latency back to seconds, not minutes
curl -s localhost:9090/api/v1/query \
  --data-urlencode 'query=histogram_quantile(0.95, sum(rate(pulse_sync_latency_seconds_bucket[5m])) by (le))'

# No new failures during the replay
curl -s localhost:9090/api/v1/query \
  --data-urlencode 'query=sum(increase(pulse_sync_failures_total[15m])) by (stage)'

# Search actually returns results
python scripts/eval_search_accuracy.py
```

Note that `pulse_sync_latency_seconds` measures *source commit → indexed*, so
during a replay of old events it legitimately spikes to the age of those
events. Judge it after catch-up, not during.

## 10. Gotchas

| Gotcha | Why it bites |
| --- | --- |
| Seeking with the worker running | Kafka refuses offset changes for groups with active members; the seek silently does nothing useful. Always stop the consumer. |
| Expecting replay to erase bad newer data | `external_gte` rejects older events. Recreate the index (§7) instead. |
| Replaying to fix poison messages | Those messages will fail again identically. Fix the code first — see [dead-letter-queue.md](dead-letter-queue.md). |
| Changing the embedding model without recreating the index | The worker refuses to index into a `dense_vector` with mismatched dims. |
| Assuming the binlog is infinite | 24h retention in this project; past that only rows still in MySQL can be recovered, via §6. |
| Replaying with `SUMMARY_ENRICHMENT=true` on a large backlog | Every document re-triggers a Wikipedia summary fetch. Consider disabling enrichment for the duration of a big replay. |
