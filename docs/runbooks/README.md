# Runbooks

Operational procedures for the PulseSearch pipeline. Each one is written to be
followed under pressure: what the symptom is, which lever to pull, how to
verify, and what will bite you.

| Runbook | Use when |
| --- | --- |
| [backfill-and-replay.md](backfill-and-replay.md) | The index is empty, stale, or wrong and needs rebuilding from the change log — or the mapping itself must change. |
| [dead-letter-queue.md](dead-letter-queue.md) | `pulse_dlq_messages_total` is climbing, or indexing has stalled with write failures. |

Both assume the stack from the repository root `README.md` is running, and both
give Docker Compose and Kubernetes variants of every command.
