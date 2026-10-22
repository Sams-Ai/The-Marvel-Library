# Current architecture and deployment boundary

```text
Spiderweb Obsidian vault (iPhone, canonical DOCX)
  └── read-only import / selected manifest export

Local development                 Candidate Render staging
SQLite + WSGI dev UI              Gunicorn web service + PostgreSQL
+ SQLite job queue                + worker polling same DB (leases, retries)
+ local/S3 media adapter          + private S3-compatible media bucket
                                  + logs/health checks, Meta adapter disabled

No hosted resources are currently provisioned.
```

## Implemented

- Local SQLite catalogue and versioned migration set (v1 project tables; v2 persistent login sessions and login-attempt throttling).
- PostgreSQL adapter and additive PostgreSQL schema/migrations; database-copy command opens SQLite read-only, checks target emptiness, transfers known tables in dependency order in one transaction, verifies row counts and story IDs/source hashes.
- Responsive authenticated web UI with a configured single operator, PBKDF2 password verifier, signed/expiring cookie, DB-backed session revocation, login CSRF and throttling, authenticated CSRF, exact-version batch approval and audit events.
- SQLite/PostgreSQL-compatible job claim flow with PostgreSQL `FOR UPDATE SKIP LOCKED`, leases, bounded transient retry, duplicate constraints, global pause and audit trail. Publisher implementation remains fake only.
- Local and S3-compatible private media interface, validated JPEG decode/type/size, content hashes, metadata checks and short-lived signed GET URLs.
- Structured JSON logs with conservative redaction, `/healthz`, SQLite backup/restore-as-new tool and staging Render blueprint example.

## Not verified / not live

- PostgreSQL could not start in this environment; `initdb` failed with `signalfd() failed`. Adapter/migration tests use a fake DB-API, and the opt-in real-Postgres integration test is skipped unless `TEST_POSTGRES_URL` points at a dedicated disposable DB. Thus no claim of a successful real SQLite→PostgreSQL migration.
- Render YAML syntax parsed locally but was not validated by Render CLI/API or deployed. Services need a private Git repository, paid account/settings and external S3 bucket/IAM. Region is Singapore, not Australia.
- No external alerts are wired. Worker errors are stored in jobs and emitted as JSON logs; alert routing is a dashboard/operator setup item.
- Current operator identity is one configured admin. Independent security review, recovery and role design are required before public exposure.
- `meta_adapter.py` remains disabled; no account link, OAuth, API access, Insights or real Instagram publishing exists. The worker always produces fake outcomes.

## Boundaries and trust

The external service reads only deliberately exported/synced data; it must not assume access to the phone-mounted vault. S3 objects are private and media URLs are short-lived. Meta credentials must be server-side secrets only, but no Meta secrets are configured. Render health checks use a non-sensitive DB connectivity endpoint. Never deploy publicly before configuring the secrets, service access, backups, alerts and security review.
