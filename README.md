# The Marvel Library — current application status

This continues the original project. The Obsidian source note and DOCX stories were not modified. The SQLite catalogue remains the local development authority; stories `tml-0001` and `tml-0002` retain their original source file and canonical text hashes.

## Run locally

```sh
cd /var/minis/workspace/marvel-library
python3 auth_setup.py       # once, interactive; writes private .env.local
python3 webapp.py           # local dev server at http://127.0.0.1:8080
```

The interface supports story search/detail/integrity, idea creation, campaigns, caption versioning, JPEG upload, multi-post exact-version batch approval, scheduling, calendar, job history and global pause. Authenticated user actions are audited. The login is a **single configured operator**, DB-backed revocable session, PBKDF2 password verifier, CSRF protection and persistent failed-login throttling. Before public production exposure, security-review authentication, identity recovery, multi-user roles and operational controls.

The local dev server is bound to localhost. Production WSGI command in the Render example uses Gunicorn. `/healthz` exposes only a generic DB-connectivity result for provider health checks.

## Local workflow

```sh
python3 app.py import-vault --vault /var/minis/mounts/Spiderweb
python3 ops.py search watch
python3 ops.py show tml-0001
python3 ops.py check-sources
python3 ops.py idea --promotion story --story tml-0001 --caption 'Unapproved draft'
python3 ops.py edit ig-0001 --caption 'Updated proposal' --media /path/to/photo.jpg
python3 app.py ready ig-0001
python3 app.py approve-batch ig-0001 --approver local-label
python3 app.py schedule ig-0001 --at '2027-01-01T09:00:00+10:00'
```

The web review queue supports approval of multiple selected posts in a single immutable-version batch. The CLI `--approver` remains an unauthenticated label; use UI approvals for actor-linked audit.

## Database, migration and backups

- SQLite remains for local use. Schema migrations are versioned (`migrations.py`); version 2 adds persistent sessions and login throttling. A verified backup made before this migration is in `backups/before-auth-hardening-20261022T010740Z.sqlite3`.
- PostgreSQL adapter and additive migrations are in `database.py` and `migrations_pg.py`. The safe copy utility is `migrate_sqlite_to_postgres.py`; it opens SQLite read-only, requires empty target tables, transfers records in FK order inside one PostgreSQL transaction, checks row counts and compares story IDs/hashes. It does not alter the source SQLite file.
- Prepare a verified SQLite backup before any migration:
  ```sh
  python3 db_tools.py backup marvel_library.sqlite3 backups/pre-pg.sqlite3
  python3 db_tools.py restore-as-new backups/pre-pg.sqlite3 recovery.sqlite3
  ```
- For production PostgreSQL use Render paid database automated backups/PITR and periodically follow provider logical export/restore documentation. Restore to a separate DB, compare IDs/hashes/counts, then switch only after verification. Never overwrite source DB during migration.

## Candidate Render staging

`render.yaml.example` defines a paid Singapore web service, continuous worker and PostgreSQL; it is not active `render.yaml` and cannot provision anything until deliberately copied/linked. It references no literal credentials. It expects S3-compatible private object storage, configured separately through secret variables. `requirements-production.txt` lists Gunicorn, psycopg, boto3 and Pillow.

Research estimates about **USD $46/month** for a Render Pro workspace, two small paid services, smallest paid Postgres and 5 GB DB storage; S3, bandwidth, taxes and usage extras are additional. This is a public price-based estimate, not a quote. Free Render services are specifically unsuitable (ephemeral filesystem, sleeping web service; free Postgres expires after 30 days and lacks backups). Render has no Australia region in the reviewed list; candidate Singapore region means non-Australian data residency. Review `META-RESEARCH.md` and confirm prices/region before provisioning.

## Current release boundary

- PostgreSQL and SQLite code, persistent queue adapter, retry/lease behavior, database session storage, and private S3-compatible media adapter are implemented locally.
- **A live PostgreSQL integration was not testable here:** local PostgreSQL `initdb` failed with `signalfd() failed`. The integration test is prepared in `.github/workflows/postgres.yml` using an isolated PostgreSQL 16 service and covers rollback, repeated migration, populated-target rejection, all application-table row counts and story hashes. That workflow has not run yet. Adapter/schema unit tests use a fake DB-API; staging remains blocked until CI or another disposable real Postgres run passes.
- Render blueprint YAML passed validation against Render’s official JSON schema (`https://render.com/schema/render.yaml.json`), but no Render CLI/API validation or account-level deployment test was performed.
- Worker publishing is permanently fake in this release; `SIM-…` results are never Instagram posts. `meta_adapter.py` always raises and no OAuth or API calls are made.
- Staging needs a private Render project, a private S3 bucket/IAM role or tightly scoped credentials, a high-entropy operator password hash/session key, successful PG migration validation, and a reviewed security posture. No account/service has been created or deployed.
- Meta eligibility, login route, permissions, App Review, token refresh and account type are unverified. Stories remain excluded while Meta docs conflict.

See `OPERATOR.md`, `ARCHITECTURE.md`, `META-RESEARCH.md`, and `render.yaml.example`.
