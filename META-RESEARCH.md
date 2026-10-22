# Meta and hosting research baseline — 22 October 2026

Primary Meta documentation accessed during the audit (public docs only; no app/account was accessed):
- [Instagram Platform overview](https://developers.facebook.com/documentation/instagram-platform/overview)
- [Instagram API with Instagram Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login)
- [Instagram API with Facebook Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login)
- [Content Publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing)
- [Insights](https://developers.facebook.com/documentation/instagram-platform/insights)
- [Meta access tokens](https://developers.facebook.com/documentation/facebook-login/guides/access-tokens)

## Meta facts and implementation blockers

- Instagram Platform eligibility is for professional Business/Creator accounts; consumer personal accounts are not eligible for these documented account-management endpoints.
- Instagram Login does not require a linked Facebook Page and uses the Instagram business permission family. Facebook Login for Business is Page-linked and uses the Instagram/Page permission families; Meta says the operator must have admin-equivalent Page tasks for Page-linked use.
- The Login overview lists Instagram Login scopes including `instagram_business_basic` and `instagram_business_content_publish`; Facebook Login publishing docs list `instagram_basic`, `instagram_content_publish` and `pages_read_engagement`. Insights permissions differ by route. Request only those actually needed and recheck exact current scopes/App Dashboard.
- Standard Access is described for developers/app roles and their own/managed accounts. Serving unrelated accounts generally requires Advanced Access, App Review and Business Verification. Exact account/app eligibility remains unknown.
- The live publishing guide currently states 100 posts per rolling 24 hours in its rate-limit section, but a later carousel limitation states 50 published posts within 24 hours. This is an internal documentation inconsistency; the eventual integration must query `/{IG_ID}/content_publishing_limit` at run time and should apply a conservative local guard until Meta clarifies the applicable threshold. The guide describes media containers (`/{IG_ID}/media`), container readiness/status, then `/{IG_ID}/media_publish`. Media must be retrievable from a public URL at publishing time or uploaded via a supported flow. JPEG is the listed supported image format; carousels allow up to 20 items; Reels use a video-specific flow.
- **Stories conflict:** the current Content Publishing page says “Reels and stories are not supported” but later mentions `media_type=STORIES`. Facebook Login guide says Stories are only available for Business accounts; Instagram Login guide does not settle it. Do not implement Stories until endpoint-specific official confirmation and account test.
- Insights are for professional accounts; metric availability varies; Meta notes some account metrics may not be available below 100 followers and user metrics are retained up to 90 days. Empty result is not necessarily zero.
- Meta overview described short-lived code/token expiry and long-lived token of about 60 days refreshable before expiry; token lifetimes may change or be invalidated. Revalidate Instagram-specific exchange/refresh requirements before implementing. Keep credentials server-side only.
- No account type, Page linkage, permissions, Meta App, access level, OAuth, quota endpoint or publishing endpoint has been checked against The Marvel Library account.

- **Render official docs (rechecked 22 Oct 2026):** [Blueprint spec](https://render.com/docs/blueprint-spec), [Postgres create/connect](https://render.com/docs/postgresql-creating-connecting), [free services limits](https://render.com/docs/free), [pricing](https://render.com/pricing). Blueprints define web, worker, cron, databases and environment groups. Render provides managed Postgres internal URLs for same-region services and paid database backups/PITR. Free web filesystems are ephemeral; free web services spin down after 15 minutes idle and free Postgres expires after 30 days, has no backup support, and is explicitly not for production. Current pricing page shows Pro workspace $25/month plus compute; paid persistent DB/storage and compute are separate charges.
- A minimum illustrative Render production stack with a Pro workspace, two 0.5 CPU/512 MB services, smallest paid 0.1 CPU/256 MB Postgres and 5 GB database storage is **about USD $46/month before S3, bandwidth, tax and alerting add-ons** (compute estimates: ~$7 each web/worker, ~$6 small Postgres, ~$1.20 additional storage beyond included 1 GB, plus $25 Pro workspace). Confirm live prices/plan names in the Render dashboard before provisioning; they vary and no account quote has been obtained.
- **Amazon S3 pricing:** [official pricing](https://aws.amazon.com/s3/pricing/) is usage-based with storage, request, retrieval and transfer components; exact amount depends on region and volume. AWS free-tier credits for eligible new customers are time-limited and are not a production cost assumption. This project’s S3-compatible adapter needs an externally created private bucket and narrowly scoped credentials.
- Render’s currently documented regions include Singapore but not Australia. Proposed service/DB region is Singapore; S3 is set to Singapore as well. This is not Australian data residency. Do not deploy until location is acceptable and current region/prices are verified.

### Candidate production blueprint

`render.yaml.example` describes a paid Singapore Render web service, continuous worker and PostgreSQL instance; it is not named `render.yaml` and therefore does not auto-provision resources. It references secret values as dashboard prompts and hard-sets Meta publishing off. `requirements-production.txt`, `database.py`, `migrations_pg.py`, `media_store.py`, `worker.py`, `webapp.py` and `/healthz` are the configured application components. Object storage bucket and IAM credentials are separate AWS setup and are not provisioned by Render.

### Current implementation caveats

PostgreSQL schema/adapter and the transactional SQLite-copy utility are implemented, but no PostgreSQL server could be run in this iSH environment: local `initdb` exits with `signalfd() failed`. PostgreSQL integration, provider deployment, external S3 connectivity, production authentication review and backup-restore drills therefore remain unverified. Unit tests use a fake DB-API connection; do not equate that with a live PostgreSQL migration test. The worker is still fake-publisher only. Authentication is currently a single configured operator (PBKDF2 hash, DB-backed login throttling/sessions, CSRF); it is not a multi-user/OIDC review product and needs independent security review before public exposure.

### Provider comparisons

No hosting or scheduler is configured for this user. Minis scheduling is process-dependent and unsuitable for closed-app publishing. Prices change with region, usage and plan; figures below are public pricing observations, not quotes.

- **Render**: [pricing](https://render.com/pricing), [Blueprint spec](https://render.com/docs/blueprint-spec), [Postgres connect](https://render.com/docs/postgresql-creating-connecting), [free service limits](https://render.com/docs/free). Best-fit first candidate: paid web service + background worker + same-region managed Postgres. Free web filesystems are ephemeral; free services can spin down and free Postgres expires after 30 days with no backups, so exclude Free for production. Pro workspace currently lists $25/month; illustrative 0.5 CPU/512 MB web and worker about $7 each, small paid Postgres about $6, 5 GB database storage about $1.20 above included 1 GB; total roughly $46/month including Pro, before external S3, transfer and tax. Confirm at provisioning.
- **Fly.io**: [resource pricing](https://docs.fly.io/about/pricing/). Smaller always-on VM prices are lower but more system administration is required. It has Sydney region listed; managed Postgres costs more, and backups/monitoring must be operated. Not selected because Render simplifies web/worker/database management.
- **Railway**: [pricing](https://railway.com/pricing). Usage credits/minimum charge plans and cron/volumes are available, but verify service cost and restore/region properties in a real project before selecting.
- **PythonAnywhere**: [pricing](https://www.pythonanywhere.com/pricing/). Python-focused always-on tasks, but validate task model and PostgreSQL/object-store fit for this architecture.
- **Hetzner Cloud**: [cloud pricing](https://www.hetzner.com/cloud/). VPS can run all code affordably but requires OS patches, firewall, backups, alerts and on-call ownership; no Australia region was listed in the checked page.
- **S3 media**: [AWS S3 pricing](https://aws.amazon.com/s3/pricing/) is usage-based (storage, request, retrieval and transfer). Exact quote depends on region, media volume and traffic; no account or bucket was checked.

### Recommendation and remaining deployment blockers

Render Singapore with Postgres, separate web and polling worker, and a private S3 bucket in Singapore is the least-operational-burden candidate found. Singapore is not Australian data residency. The files `render.yaml.example` and `deployment-plan.example.yml` describe the candidate but have not been validated with Render or deployed. No workspace/bucket/identity account was accessed or created. The actual plan choice, data location acceptability, OAuth/Meta account eligibility and independent security review remain deployment gates.

PostgreSQL adapter/schema migration and S3 storage interface exist in code, but live PostgreSQL integration could not run in this shell: `initdb` failed with `signalfd() failed`. The migration unit tests use a fake DB-API connection only. The worker remains simulation-only. The app uses a single configured operator authentication model and should undergo security review before public exposure.

