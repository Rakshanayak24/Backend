# Operations, SLOs, and recovery

## Targets

Assignment objectives: 99.95% monthly availability (about 22 minutes of error budget per 30-day month), p95 under 200 ms for reads and 500 ms for writes, and capacity for 100k consultations/day. These are service targets; they have not been load-tested in this repository. Use separate dashboards for read/write latency, request volume, 4xx/5xx, 429s, database pool saturation, lock waits, Redis health, and booking conflicts. Alert on sustained SLO burn, readiness failures, DB storage/connection pressure, backup failures, and Redis unavailability.

## Deployment

1. Build an immutable image, run CI and security scans, and sign/publish it to a private registry.
2. Provision managed PostgreSQL with HA, PITR/WAL archiving, private networking and encryption at rest; provision private Redis with authentication and TLS.
3. Configure secrets, explicit CORS origins, log level, external OTLP collector, and ingress restrictions. Do not expose databases, Redis or metrics publicly.
4. Run `alembic upgrade head` as a release job using a migration role, then roll out API instances with a separate restricted runtime DB role.
5. Verify `/health/ready`, synthetic sign-in/search/booking flows, SLO dashboards and rollback readiness.

Compose is for local/demo use. It includes health checks and persistent volumes but does not provide HA, TLS, backups or production-grade secret management.

## Observability

`/metrics` exposes request count and latency histograms, scraped by the included Prometheus service. Request logs include request ID and route-level timing without PHI. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to enable FastAPI, SQLAlchemy, and OTLP trace export to a collector. Correlate traces, logs and metrics by request ID/trace ID at the ingress. Keep metrics labels low cardinality; avoid user IDs, consultation IDs, raw URLs and query values. The outbox worker logs event IDs and event types; its payloads contain resource IDs only. Example latency query: `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{method="GET"}[5m])))`.

## Retries and failure handling

Clients should retry only transient network errors, 429 and selected 5xx responses, using capped exponential backoff with full jitter and `Retry-After`. All booking retries must keep the same `Idempotency-Key`. Treat 409 booking conflicts as a business result, not a transient retry. PostgreSQL transaction failures should be retried at most a small bounded number of times by a service layer; this API currently returns an error and leaves retry policy to the client. Redis rate limiting falls back to process-local counters during a Redis outage, so production alerts must treat Redis loss as degraded protection.

## Backup and disaster recovery

Enable encrypted daily snapshots and continuous WAL archive to an isolated account/region. Retain backups in line with approved clinical record retention and deletion policies. Initial targets: RPO 5 minutes and RTO 1 hour. Quarterly, restore into an isolated environment, verify schema and row counts, run synthetic read/write checks, and record recovery time. Back up secrets separately under controlled access. Do not restore production PHI into developer environments.

## Demo outline (5 minutes)

1. Show `/docs` and explain users, doctors, availability, consultations and prescription resources.
2. Register and sign in a patient; show bearer auth and MFA setup.
3. As admin, grant a doctor role and verify their profile; add a future slot.
4. Book the slot with an idempotency key, replay the same request, then show slot conflict handling.
5. Walk the consultation lifecycle, add a prescription, and show the audit/analytics endpoints and Prometheus metrics.
