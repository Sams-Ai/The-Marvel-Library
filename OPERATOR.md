# Operator and deployment guide — The Marvel Library

## Local development

```sh
cd /var/minis/workspace/marvel-library
python3 auth_setup.py       # first use; local interactive credentials, never share .env.local
python3 webapp.py           # local dev server at 127.0.0.1:8080
```

Use the UI for search/story integrity, ideas/campaign label, caption versions, JPEG media, review batches, calendar, history and pause. Approval is tied to the exact content-version ID/hash; editing creates a new version and invalidates approval. An external story source change is detected by `ops.py check-sources` and flags associated drafts.

Local CLI tools remain useful:

```sh
python3 app.py import-vault --vault /var/minis/mounts/Spiderweb
python3 ops.py search fury
python3 ops.py show tml-0001
python3 ops.py check-sources
python3 app.py export tml-0001 --out exports/tml-0001.zip
python3 ops.py verify-export exports/tml-0001.zip
```

Local job queue tests/simulation: `python3 worker.py --once` or `python3 worker.py`. The worker never calls Meta. `SIM-...` outcomes are fake. Do not treat them as posts.

## Backups and migration

1. Back up source SQLite before migration and verify it:
   `python3 db_tools.py backup marvel_library.sqlite3 backups/pre-pg.sqlite3`
2. Create an empty Render Postgres instance in the same region as the services. Restrict it to the internal service network where possible; keep the connection URL in the host secret manager.
3. From a trusted migration environment, set `DATABASE_URL` in that process environment to the target DSN; do not include it in command arguments or chat. Run:
   `python3 migrate_sqlite_to_postgres.py --sqlite marvel_library.sqlite3`
4. The migration refuses non-empty target app tables, copies known source tables in one PostgreSQL transaction, and checks table row counts and story IDs/raw/canonical hashes. It does not modify SQLite.
5. Compare all table counts, records, approval batches and audit rows; run the test and integrity checks. Keep SQLite and backup immutable until verified and accepted.
6. Rollback before live use: stop services and point the app back to the preserved SQLite copy or restore PostgreSQL from the verified pre-migration backup. Do not run a destructive reverse migration.

Migration has **not** run successfully against real PostgreSQL here. A disposable local server was unavailable: the previous `initdb` attempt failed in this iSH kernel with `signalfd() failed`; there is no running database, Docker/Podman, or `TEST_POSTGRES_URL` configured. The PostgreSQL test is prepared in `.github/workflows/postgres.yml`; it uses an ephemeral `postgres:16` service with trust authentication restricted to that throwaway CI service, and a job-scoped DSN with no password. No real credentials or repository secrets are used for the test DB.

### Run the PostgreSQL CI workflow

This working directory has no Git repository/remote configured and Git is not installed in this shell, so I could not trigger GitHub Actions. Once this project is pushed to a private GitHub repository:

1. Commit `.github/workflows/postgres.yml`, `test_database.py`, `database.py`, `migrations_pg.py`, `requirements-production.txt` and the related source to the default branch.
2. The workflow runs on push/PR changes to those paths. For a manual run, open **Actions → PostgreSQL integration → Run workflow** (the workflow now has `workflow_dispatch`).
3. Wait for the PostgreSQL service health check, then inspect job `postgres-integration` and command `python -m unittest -v test_database`.
4. Require the integration test `test_schema_copy_rollback_repeat_and_populated_target_rejection` to be **executed and pass, not skipped**. Confirm it verifies rollback after a partial copy, schema versions 1 and 2, all 14 app-table counts, story hashes, content version, batch approval, job, attempt, audit/auth data, source-file SHA-256 unchanged, and rejection of a populated destination without changing counts.
5. If the job fails, inspect the first failing assertion/traceback. `connection refused`/health-check failure means inspect the service logs/readiness; migration SQL/FK errors mean fix the migration; count/hash mismatches mean stop and investigate—do not rerun against or overwrite any non-empty target. Keep the SQLite source and backup unchanged.

The workflow uses a disposable PostgreSQL 16 service and sets `TEST_POSTGRES_URL` to its local URL. The Postgres container is configured for trust authentication only within that isolated ephemeral service; no real/test password or repository secret is committed or required. Do not reuse this trust-auth setup for any non-CI database.

## Render staging sequence (no account configured)

1. Review `render.yaml.example`; copy to `render.yaml` only in a private repository after selecting region, plan and data location. Blueprint sync may create paid services; review the Render change summary before accepting.
2. Create private S3-compatible bucket separately (current template uses Singapore); block public access, enable versioning/lifecycle as decided, scope credentials to that bucket and use TLS. Provide `OBJECT_STORAGE_BUCKET`, `OBJECT_STORAGE_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` as host secrets/env, never source control.
3. Configure `WEB_PASSWORD_HASH` using `auth_setup.py` in a trusted environment; set `WEB_SESSION_KEY` as a random high-entropy secret. Blueprint generates one session key by default; ensure the admin password verifier is entered as a secret.
4. Build services from `requirements-production.txt`, set `COOKIE_SECURE=1` in the web service, use the private Postgres URL, keep live Meta disabled, and use `/healthz` for service health.
5. Run database backup, SQLite→PostgreSQL migration, compare records/hashes, verify export, run tests. Upload synthetic JPEGs only. Confirm private storage and signed URL behavior without making Meta requests.
6. Verify web auth, login lockout, session revocation, CSRF, exact-batch approval, pause from both services, scheduler lease recovery, retry limits, DB outage health and restore procedure.
7. Observe JSON logs and manually configure Render alert notifications. No alert webhook integration is currently coded.

## Secrets and security

- Local `.env.local` is gitignored and mode 0600. Production credentials belong in a provider secret manager/environment. Never put tokens in DOCX, exports, logs, repo or chat.
- Auth is one configured admin, PBKDF2-hashed password, signed cookie with DB-backed revocation, CSRF, and DB-backed failed-login throttling. It is not multi-user or OIDC. Require an independent security review, rate-limit/abuse review and an account recovery plan before public exposure.
- DB error logs record error type and identifiers, not DSNs. S3 objects are private; generated presigned URLs expire within 60–900 seconds.
- The worker retries only classified transient errors (bounded); ambiguous external publishing outcomes require explicit reconciliation before any live publisher is implemented.

## Current status / human actions still required

No cloud account, S3 bucket, Render services, live database, or Meta app/account connection exists. Before a safe staging deploy: choose/authorise Render and AWS accounts; accept Singapore data residency; create private S3 bucket and IAM credentials; verify current costs; run a real PostgreSQL migration test; review the single-admin security posture. Before live Instagram publishing: separately verify a professional account and exact Meta scopes/access, complete official OAuth/app review as applicable, implement/independently test the Meta adapter and obtain explicit activation approval. None of these actions was taken.

Unofficial Marvel fan work: do not imply affiliation or endorsement; get legal advice before commercial use.
