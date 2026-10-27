# Security checklist and threat model

## Data classification

| Class | Examples | Handling |
| --- | --- | --- |
| Public | Verified doctor specialty and approved profile text | Return only fields approved for directory listing; bound search inputs |
| Internal | Operational metrics, deployment configuration | Restrict to staff; metrics endpoint should be network restricted at ingress |
| Personal | Name, email, phone, appointment times | TLS in transit; database/storage encryption at rest; least-privilege access |
| Sensitive health data | Consultation reason/notes, prescription | TLS, encrypted managed storage and backups, strict ownership/RBAC, audited access, retention controls |
| Secrets | Password hashes, JWT and MFA encryption keys | Passwords Argon2 hashed; other keys kept in a secret manager and rotated; never commit `.env` |

The application relies on TLS at ingress and provider-level encryption at rest. Column-level encryption for health content and key management are deployment decisions not implemented in this baseline. Do not use production health data in development or test fixtures.

## Attack surface and controls

| Threat | Mitigation in code / deployment |
| --- | --- |
| Credential stuffing, weak passwords | Minimum 12 characters, Argon2id password hashes, generic login failures, per-IP rate limit; add breached-password screening and adaptive lockout at identity edge |
| Token theft or forgery | HS256 signed, short-lived (15 minute default), issuer checked, algorithm allow-listed; use managed asymmetric signing keys and rotation in production |
| Privilege escalation / IDOR | Server-side role and owner checks on doctor, consultation and prescription resources; admin-only role and verification operations |
| Appointment double booking | PostgreSQL row lock, slot uniqueness, transactional state update, required user-scoped idempotency key |
| Injection and malformed requests | Pydantic validation and SQLAlchemy parameterized queries; bounded search, pagination, body field sizes |
| Cross-origin abuse / browser injection | Explicit CORS origin list, security response headers; deploy CSP and CSRF protection if cookie auth is introduced |
| MFA secret disclosure | TOTP secrets encrypted with Fernet key, returned once on setup, six-digit time-based verification with ±30 second drift; store encryption key outside app. Rate limit enrollment and login |
| Data exfiltration via logs | No request bodies, passwords, tokens, clinical notes, or prescriptions are logged; audit details remain minimal |
| Denial of service | Redis shared rate limiter, bounded API inputs, health endpoints; add edge WAF, concurrency caps and database statement timeouts |
| Dependency/supply-chain attack | CI runs `pip-audit`, Bandit and Ruff; pin production images by digest and produce an SBOM/sign images in release pipeline |
| Insider misuse / repudiation | Append-style audit events include actor, action, resource and address; restrict DB access and export logs to immutable centrally managed storage |

## Security checklist before a real deployment

- [ ] Configure TLS for ingress, PostgreSQL, Redis, metrics and traces; enforce HSTS at ingress.
- [ ] Put DB, Redis, JWT signing and MFA encryption secrets in a cloud secret manager; rotate keys and document incident revocation.
- [ ] Replace bootstrap credentials immediately; disable bootstrap configuration after first provision.
- [ ] Restrict admin accounts, require MFA for all privileged users, review role assignments, and add an emergency access process.
- [ ] Restrict `/metrics`, `/docs`, and `/openapi.json` to trusted network/auth in production.
- [ ] Add database least-privilege accounts, managed encryption at rest, encrypted backups, PITR and tested restores.
- [ ] Define health-data retention/deletion, consent, export, breach response, and jurisdictional compliance requirements with counsel.
- [ ] Add audit-log immutability and alert on admin role changes, failed logins, unusual reads, and MFA resets.
- [ ] Scan container images and dependencies continuously; review SBOM, image provenance, base image updates, and migration diffs.
- [ ] Pen-test authorization boundaries and booking races; load test p95 targets using realistic peak concurrency.

This checklist is a threat-model starting point, not a claim of regulatory certification. The service does not process cardholder data; payment provider tokens/references only are modeled.
