# Runbook: dead-letter queue

How poison messages are isolated, how to inspect them, and how to get their
data back into the index.

**Audience:** whoever is on call for the sync pipeline.
**Key fact to internalise first:** this DLQ has **two producers with opposite
semantics** (§3). Treating them the same is how you create duplicates or
permanent index holes.

---

## 1. What it is

| Property | Value |
| --- | --- |
| Topic | `pulsesearch.dlq` (`KAFKA_DLQ_TOPIC`) |
| Producer | worker only — `DeadLetterQueue` in `services/worker/app/sink.py` |
| Consumers | none. Nothing drains this topic automatically. |
| Message key | the original Kafka message key, preserved |
| Metric | `pulse_dlq_messages_total` |

Envelope written for each message:

```json
{
  "error": "parse: unsupported Debezium op: 'x'",
  "payload": { "...": "the original message value, JSON-parsed" }
}
```

If the original value was not valid UTF-8 JSON, `payload` degrades to
`{"_raw": "<lossy-decoded text>"}`.

**Known limitation:** the envelope records only the error string and the
payload — no source topic, partition, offset, timestamp, or attempt count. That
is enough to diagnose and to hand-replay a message, but not enough to build
safe automated redrive. Enriching it is the natural next change to this
subsystem.

## 2. The design intent

A single unparseable event must never wedge the stream. Rather than retrying a
message that will never succeed, the worker sets it aside and keeps the
pipeline moving. The DLQ is therefore a **bug report queue**, not a work queue:
in normal operation it stays empty, and anything in it means code met data it
did not expect.

## 3. Two failure modes — read this before acting

Both paths publish to the same topic, but their effect on the main stream is
opposite (`services/worker/app/consumer.py`):

### Parse failures — the message is *removed* from the stream

`_parse_message` catches the error, publishes to the DLQ, increments
`pulse_sync_failures_total{stage="parse"}`, and the batch's offsets **are
committed**.

- The DLQ copy is the **only** remaining copy of that change.
- The stream continues; that row simply never reaches Elasticsearch (until a
  later edit to the same row produces a fresh, parseable event).
- Causes: schema drift, a Debezium op the parser does not know, a row missing
  `before`/`after`, a malformed id.

### Write exhaustion — the messages are *duplicated*

After `max_write_retries = 8` attempts with exponential backoff (capped at 15s),
`_write_with_retries` publishes every non-tombstone message in the batch to the
DLQ, increments `pulse_sync_failures_total{stage="write"}`, and then returns
`False` — so offsets are **deliberately not committed** and Kafka redelivers the
batch after a 2s pause.

- The DLQ copies are **forensic duplicates**, not the last copy.
- The worker will keep retrying the same batch, so the DLQ can accumulate many
  copies of the same messages while Elasticsearch is down.
- **Redriving these is wrong** — it would double-write data the main stream is
  already going to reprocess. Fix Elasticsearch; the stream heals itself.
- Causes: ES down, disk watermark hit, mapping conflict, cluster read-only.

Tell them apart by the `error` prefix (`parse:` vs `write:`) or by the
`stage` label on `pulse_sync_failures_total`.

## 4. Detect

```promql
# Anything at all is worth a look — normal is zero.
increase(pulse_dlq_messages_total[15m]) > 0

# Which mode are we in?
sum(increase(pulse_sync_failures_total[15m])) by (stage)
```

A suggested alerting split, matching §3:

| Condition | Severity | Meaning |
| --- | --- | --- |
| `stage="write"` rising | **page** | The sink is broken; indexing has stalled. |
| `stage="parse"` rising | ticket | Data the code cannot handle; events are being dropped. |

There are no alert rules committed yet (`infra/prometheus/prometheus.yml` has no
`rule_files`), so today this is dashboard-driven — the Grafana dashboard has a
"Failures & DLQ rate" panel.

## 5. Inspect

```bash
make dlq-count           # offsets/size of the DLQ topic
make dlq-peek            # print the oldest 20 messages
make dlq-peek N=100      # ...or more
```

Underneath:

```bash
docker compose exec redpanda rpk topic describe pulsesearch.dlq -p
docker compose exec redpanda rpk topic consume pulsesearch.dlq -n 20 -o start
```

Group the errors before theorising:

```bash
docker compose exec -T redpanda rpk topic consume pulsesearch.dlq -n 500 -o start -f '%v\n' \
  | python3 -c "import json,sys,collections; \
c=collections.Counter(json.loads(l)['error'].split(':')[0:2][0] + ':' + \
json.loads(l)['error'].split(':')[1][:60] for l in sys.stdin if l.strip()); \
[print(n, e) for e, n in c.most_common()]"
```

In Kubernetes, substitute a throwaway pod for `docker compose exec`:

```bash
kubectl -n pulsesearch run rpk-admin --rm -it --restart=Never \
  --image=redpandadata/redpanda:v24.1.7 --command -- \
  rpk topic consume pulsesearch.dlq -n 20 -o start --brokers redpanda:9092
```

## 6. Resolve

### If `stage="write"` (ES was down)

1. Fix Elasticsearch — check `curl -s localhost:9200/_cluster/health?pretty`,
   disk watermarks, and worker logs for the underlying error.
2. Do nothing to the DLQ. The uncommitted batch is redelivered automatically
   and the version-guarded upsert makes reprocessing safe.
3. Confirm recovery: `pulse_docs_indexed_total` climbing again, lag falling.
4. Optionally purge the forensic copies once you are done with them (§7).

### If `stage="parse"` (code met unexpected data)

1. Read one payload and reproduce it in a test. Add it as a case against
   `DebeziumEventParser` in `services/worker/tests/test_handlers.py` — that is
   the durable fix; a redrive without a code change just fails again.
2. Ship the parser fix and roll out the worker.
3. Recover the dropped data. **Prefer replay over redrive:**

   ```bash
   make replay          # or a time-bounded: make replay-from TS=<when it started>
   ```

   Replaying from the source topic is safer than redriving the DLQ, because the
   source topic holds the original, unmodified events with correct ordering and
   `ts_ms` versions, and the version guard makes the whole thing idempotent.
   Redrive should only be a last resort — when the source topic has aged out and
   the DLQ copy is genuinely the only one left (see §6 of
   [backfill-and-replay.md](backfill-and-replay.md)).

### Manual redrive (last resort)

There is intentionally **no automated redrive tool**: with no offset metadata in
the envelope (§1) and no idempotency key beyond the payload itself, an
unattended redrive loop risks amplifying a bad batch. When you genuinely need it,
extract the payloads and republish them to the source topic after the parser is
fixed:

```bash
# Inspect first — dry run.
docker compose exec -T redpanda rpk topic consume pulsesearch.dlq -n 50 -o start -f '%v\n' \
  | python3 -c "import json,sys; [print(json.dumps(json.loads(l)['payload'])) for l in sys.stdin if l.strip()]" \
  | tee /tmp/redrive.ndjson | head

# Republish only after reviewing /tmp/redrive.ndjson.
cat /tmp/redrive.ndjson | while read -r line; do
  printf '%s\n' "$line" \
    | docker compose exec -T redpanda rpk topic produce pulse.pulsesearch.pages
done
```

Caveats: this drops the original message key (so Debezium's per-row ordering
guarantee is lost), and only messages whose `payload` is real JSON survive the
round trip — `{"_raw": ...}` entries must be handled by hand.

## 7. Purge

Once the incident is closed and you no longer need the evidence:

```bash
# Trim the whole DLQ topic.
docker compose exec redpanda rpk topic trim-prefix pulsesearch.dlq --to-end

# Or delete and let the worker's producer recreate it on next publish.
docker compose exec redpanda rpk topic delete pulsesearch.dlq
```

Purging loses the only copy of any `parse:` failures, so recover or replay
first (§6).

## 8. Post-incident checklist

- [ ] Parser or sink fix merged, with a regression test for the payload that
      broke it.
- [ ] Dropped rows recovered via replay, and document count verified.
- [ ] `pulse_dlq_messages_total` flat again.
- [ ] DLQ purged, or a note left explaining why it is being kept.
- [ ] If the envelope's missing metadata slowed you down, consider adding
      topic/partition/offset/timestamp to `DeadLetterQueue.publish`.
