# Amrutam Care API

A reference backend for a telemedicine product. It implements account registration and sign-in, TOTP MFA, role based access, doctor directory and availability, idempotent consultation booking, consultation state transitions, prescriptions, audit events, admin reporting, rate limiting, health endpoints, Prometheus metrics, and a transactional outbox worker for asynchronous domain events.

## Run locally

Requirements: Python 3.11+; Docker Compose for the full PostgreSQL and Redis stack.

```powershell
Copy-Item .env.example .env
# Set unique JWT_SECRET, MFA_ENCRYPTION_KEY, POSTGRES_PASSWORD and REDIS_PASSWORD in .env
docker compose up --build
```

API docs: <http://localhost:8000/docs>. Readiness and liveness are at `/health/ready` and `/health/live`; Prometheus scrapes `/metrics`. The API and outbox worker use PostgreSQL; Redis provides shared rate limiting. For a quick local run, install with `pip install -e '.[dev]'`; the default SQLite database is intended for development only.

## Bootstrap and usage

Set `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` before first startup to create an administrator. This bootstrap is idempotent; remove the password from deployment configuration after initialization. Register a user at `POST /v1/auth/register`, sign in at `POST /v1/auth/login`, then send the returned bearer token. An admin assigns the `doctor` role with `PATCH /v1/admin/users/{user_id}/role`; that user creates a doctor profile and an admin verifies it. Patients book with `POST /v1/consultations` and a unique `Idempotency-Key` header. Create availability in ISO 8601 UTC or explicit offsets.

MFA enrollment: call `POST /v1/auth/mfa/setup`, add its one-time secret to an authenticator, then confirm using `POST /v1/auth/mfa/confirm?code=123456`. Subsequent login requests include `mfa_code`. Configure `MFA_ENCRYPTION_KEY` with a Fernet key; rotate by decrypting/re-encrypting stored MFA secrets during a controlled migration.

## Schema and migrations

OpenAPI is generated live at `/openapi.json`; the checked-in `openapi.json` is the API contract snapshot. Apply schema changes with `alembic upgrade head`. ORM auto-create is enabled for developer startup; disable this path and apply reviewed migrations before production rollout. Generate a new migration with `alembic revision --autogenerate -m "description"`, review it, and commit it.

## Tests, lint, and security tools

```powershell
pip install -e '.[dev]'
ruff check app tests migrations
pytest -q
bandit -q -r app
pip-audit
```

GitHub Actions runs these checks on pushes and pull requests. See [docs/architecture.md](docs/architecture.md), [docs/security-threat-model.md](docs/security-threat-model.md), and [docs/operations.md](docs/operations.md).

## Operational limits

This repository supplies a production-oriented baseline, not a clinical go-live certification. Payments, actual telehealth media, asynchronous notifications, external identity federation, backup automation and cloud key management need provider integrations and environment-specific review. The payment table intentionally stores no card or bank data. Before production, set up managed database HA, TLS at ingress and to data stores, tested restores, a secret manager, distributed tracing export, dashboards and alert policies; follow the release checklist in the operations document.
