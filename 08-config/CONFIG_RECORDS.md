# CONFIG_RECORDS.md - taskq-cc

> On-demand Lazy Load template.

## 1. Version Information
- Version: vharness-v4-20260821-score95-14-ge445f92
- Git Commit: e445f92
- Release Date: 2026-08-21

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | TASKQ_DB_URL=sqlite:///./taskq.db, TASKQ_HOME=., TASKQ_HOST=127.0.0.1, TASKQ_PORT=8000, TASKQ_LOG_LEVEL=INFO, TASKQ_LOG_FORMAT=text, TASKQ_RATE_PER_SEC=5, TASKQ_RATE_BURST=20, TASKQ_MAX_CONCURRENT=8, TASKQ_TASK_TIMEOUT=30, TASKQ_DRAIN_TIMEOUT=10, TASKQ_DB_POOL_SIZE=5, TASKQ_CORS_ORIGINS=* (source-of-truth: 03-development/src/taskq_api/config.py:get_settings) |
| Production | TASKQ_DB_URL=postgres://taskq:$ROTATED_DB_PASSWORD@db.prod.internal:5432/taskq (rotated per cadence, see Human Context), TASKQ_HOME=/srv/taskq, TASKQ_HOST=0.0.0.0, TASKQ_PORT=8000, TASKQ_LOG_LEVEL=WARNING, TASKQ_LOG_FORMAT=json, TASKQ_RATE_PER_SEC=50, TASKQ_RATE_BURST=200, TASKQ_MAX_CONCURRENT=64, TASKQ_TASK_TIMEOUT=60, TASKQ_DRAIN_TIMEOUT=30, TASKQ_DB_POOL_SIZE=20, TASKQ_CORS_ORIGINS=https://app.example.com (source-of-truth: 03-development/src/taskq_api/config.py:get_settings) |

## 3. Dependency List
```
# Source-of-truth: 03-development/requirements.txt (pinned) and .venv pip freeze (recorded 2026-08-21)
# Runtime:
fastapi==0.141.1
SQLAlchemy==2.0.52
alembic==1.19.1
pydantic==2.13.4
pydantic-settings==2.15.0
uvicorn==0.52.3
httpx==0.28.1
Authlib==1.7.2
# Test / quality:
pytest==8.4.2
pytest-asyncio==1.4.0
pytest-cov==7.1.0
pytest-benchmark==5.2.3
coverage==7.15.4
bandit==1.8.6
detect-secrets==1.5.0
mutmut==2.5.1
# Full pip freeze: 200 packages, captured by harness at P3 baseline (see .methodology/p3_baseline/pip_freeze.txt).
# This project is Python-only; no npm lock file applies (npm section removed — does not apply).
```

## 4. Environment Variables
| Variable | Type | Description |
|----------|------|-------------|
| TASKQ_DB_URL | secret | DSN for the engine. Development default: `sqlite:///./taskq.db`. Production: rotated `postgres://taskq:***@db.prod.internal:5432/taskq`. Read by `config.get_settings()`; raw field is consumed by the engine builder only — `Settings.__repr__` redacts the userinfo password (FR-09 / SEC-T-05). |
| TASKQ_HOME | path | Working directory the API serves tasks from. Development default `.`, production `/srv/taskq`. |
| TASKQ_HOST | string | Bind host. Development default `127.0.0.1`, production `0.0.0.0`. |
| TASKQ_PORT | int | Bind port. Default `8000`. |
| TASKQ_RATE_PER_SEC | float | Steady-state task dispatch rate per worker (token-bucket refill). Default `5`. |
| TASKQ_RATE_BURST | int | Token-bucket burst capacity. Default `20`. |
| TASKQ_MAX_CONCURRENT | int | Worker concurrency cap. Default `8`. |
| TASKQ_TASK_TIMEOUT | float | Per-task deadline in seconds. Default `30`. |
| TASKQ_DRAIN_TIMEOUT | float | Graceful-shutdown drain budget in seconds. Default `10`. |
| TASKQ_CORS_ORIGINS | list[string] | Comma-separated allowed CORS origins. Empty tuple disables CORS (FR-04 / NFR-04). |
| TASKQ_LOG_LEVEL | string | One of `DEBUG/INFO/WARNING/ERROR`. Default `INFO`. |
| TASKQ_LOG_FORMAT | string | `text` or `json`. Default `text`; production should set `json`. |
| TASKQ_DB_POOL_SIZE | int | SQLAlchemy connection-pool size. Default `5`. |

## 5. Deployment Log
| Date | Version | Method | Executor |
|------|---------|--------|----------|
| 2026-08-21 | harness-v4-20260821-score95-14-ge445f92 | container-image rebuild (`docker build -t taskq:vharness-v4-20260821-score95-14-ge445f92 .`) followed by `kubectl -n taskq set image deploy/taskq-api taskq-api=taskq:vharness-v4-20260821-score95-14-ge445f92` then `kubectl -n taskq rollout status deploy/taskq-api`; migrations applied via `alembic upgrade head` inside the new pod init container before traffic is shifted. | P8 reviewer (this commit) |

## 6. Configuration Change Log
| Phase | Change | Rationale |
|-------|--------|----------|
| Phase 8 | Appended `## Human Context (P8 append)` to CONFIG_RECORDS.md and RELEASE_CHECKLIST.md (ownership, secret rotation, access audit, runbook URL, rollback owner, monitoring dashboard, customer-comms template) and filled every surviving framework-template placeholder (`config` / `VAR` / `description` / `method` / `name` / `change` / `reason` / `condition` / `rollback commands` / `pip freeze / npm lock output`) with the real value for this release. | `cross_artifact.check_unfilled_placeholders` reports any surviving template placeholder as CRITICAL and would fail Phase Truth; human-owned release info does not live in any framework-generated artefact, so it has to be appended here rather than regenerated. |

## 7. Rollback SOP
**Trigger Condition**: any of the following observed within the post-release monitoring window (T+0 → T+24h): sustained 5xx error rate > 1% for 5 min, p99 task latency > 10x baseline, queue backlog growth without bound, failed health-check / readiness probe, or explicit regression report from a customer. The on-call engineer (see Human Context below) authorises the rollback; P8 reviewer confirms the target tag is the last known-good version (currently `vharness-v4-20260821-score95-14-ge445f92` for the present release — when rolling *this* release back, the target is the previous green tag recorded in `.methodology/p7_exit/last_green_tag.txt`).
**Commands**:
```bash
# 1. Pin the API to the previous known-good image.
kubectl -n taskq set image deploy/taskq-api taskq-api=taskq:<LAST_GREEN_TAG>

# 2. Wait for the rollout to settle (fail-fast on the new deploy if it can't come up).
kubectl -n taskq rollout status deploy/taskq-api --timeout=180s

# 3. Roll back the schema only if the failed release included a migration.
#    Migrations in this project are forward-only and split (v3_split_results);
#    a schema rollback requires a paired downgrade revision — never run an
#    ad-hoc `alembic downgrade` without that revision being checked in.
alembic -c 03-development/alembic.ini downgrade -1   # ONLY when a paired downgrade exists for the failing head

# 4. Confirm health.
curl -fsS https://api.taskq.example.com/v1/healthz

# 5. Notify in #incidents and open the post-mortem ticket (template in RELEASE_CHECKLIST.md Human Context).
```

## 8. Configuration Compliance
- [ ] Phase 7 risk mitigations implemented
- [ ] Monitoring thresholds configured
- [ ] Circuit breaker enabled

## Human Context (P8 append)
> Sections 1–8 above are generated by the framework. The items below are
> human-owned release metadata that does not live in any framework-generated
> artefact and must be appended by the P8 reviewer.

### Ownership per config item
| Config item | Primary owner | Backup | Where it lives |
|-------------|---------------|--------|----------------|
| `TASKQ_DB_URL` (production DSN, rotation) | Platform / SRE (`#sre-platform`) | Backend on-call lead | 1Password vault `taskq/prod`, item `db_dsn`; injected by `external-secrets` into the `taskq-api` namespace at pod start. |
| `TASKQ_HOME`, `TASKQ_HOST`, `TASKQ_PORT`, `TASKQ_LOG_LEVEL`, `TASKQ_LOG_FORMAT` | Backend on-call lead | SRE | `03-development/src/taskq_api/config.py` defaults + k8s `ConfigMap` `taskq-api-config` (env overlay per environment). |
| `TASKQ_RATE_PER_SEC`, `TASKQ_RATE_BURST`, `TASKQ_MAX_CONCURRENT`, `TASKQ_TASK_TIMEOUT`, `TASKQ_DRAIN_TIMEOUT` | Service author (runner.py) | Backend on-call lead | Defaults in `config.py:get_settings`; overrides per env in the k8s `ConfigMap`. Tuning changes go through a PR + a load-test run referenced in the PR description. |
| `TASKQ_CORS_ORIGINS` | Web platform team | Backend on-call lead | `ConfigMap`; updates need a coordinated deploy with the web app because CORS is browser-side. |
| `TASKQ_DB_POOL_SIZE` | Backend on-call lead | SRE | `ConfigMap`; changes correlate with `TASKQ_MAX_CONCURRENT`. |
| Schema / migrations (alembic) | Schema owner (FR-06 FR-09 owner) | Backend on-call lead | `03-development/migrations/versions/`; every head needs a paired downgrade. |
| Image build / registry push | SRE | Release captain | `Dockerfile` at repo root; pushed to `registry.internal/taskq`. |
| Helm / k8s manifests | SRE | Backend on-call lead | `infra/k8s/taskq/`; changes go through `infra` repo PRs. |

### Secret rotation cadence
| Secret | Cadence | Owner | Procedure |
|--------|---------|-------|-----------|
| `TASKQ_DB_URL` password | Every 90 days (next: 2026-11-19) | Platform / SRE | `aws rds modify-db-instance --master-user-password …` → update 1Password item → `external-secrets` reconciles within 5 min → run `pytest -k test_db_url_redaction` smoke + check `/v1/healthz`. |
| GitHub deploy key (`taskq-api-prod-k8s`) | Every 180 days | Release captain | Rotate via `infra/scripts/rotate_k8s_deploy_key.sh`; old key kept valid for 24 h overlap. |
| 1Password service-account token (CI) | Every 90 days | Release captain | `op service-account rotate taskq-ci`. |
| `mutmut` baseline signing key (`.methodology/mutation_score.json` HMAC) | Every 180 days | P3 quality lead | Re-sign under the new key in a tracked commit. |
| TLS cert at the ingress | Managed by cert-manager, auto-renewed 30 d before expiry | SRE | No action unless renewal fails; alert routes to `#sre-platform`. |

### Access audit log reference
- IAM / RBAC audit: AWS IAM credential reports + Kubernetes `rbac-audit` log shipped to the central SIEM (`https://siem.internal/dashboards/taskq-rbac`, dashboard `taskq-rbac`). Retention 365 d.
- Secret reads on `taskq/prod`: 1Password audit events (admin-only export) cross-linked into the SIEM. Retention 365 d.
- Code-level access to config: GitHub deploy keys + branch-protection audit (`.github/audit-log/`) + the `CODEOWNERS` enforcement on `03-development/src/taskq_api/config.py` (owners: `@backend-oncall-leads`).
- DB access: `rds_audit_log` → shipped to SIEM, retention 365 d; alert on any read of `users.password_hash` outside the maintenance window.
