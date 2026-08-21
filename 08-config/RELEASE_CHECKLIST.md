# RELEASE_CHECKLIST

## Pre-Release Checks
- [ ] All P1-P7 phases completed and artifacts generated.
- [ ] CI pipeline fully passed.
- [ ] Final Sign Off approved.
- [ ] Production environment provisioned.
- [ ] Rollback plan documented.

## Human Context (P8 append)
> The Pre-Release Checks above are the framework-generated baseline. The
> items below are human-owned release runbooks / contacts and must be
> appended by the P8 reviewer; they do not live in any framework-generated
> artefact. Gate 4 PASS proof, `quality_manifest.composite_score`, FR
> coverage, and git tag/hash are emitted by the framework into
> `.methodology/p8_release/` and the Phase Truth report — not into this
> checklist — so there is nothing framework-side to preserve here beyond
> the Pre-Release Checks above.

### Deployment runbook
- Runbook URL: `https://runbooks.internal/taskq/release-procedure` (canonical; mirrors `infra/runbooks/taskq/release-procedure.md` in the `infra` repo, kept in sync by `runbook-lint` in CI).
- Step-by-step: pre-flight (config + secrets check) → image build → schema migrations → staged rollout (canary 10% → 50% → 100%) → post-release monitoring window (T+0 → T+24 h).

### Rollback owner & on-call
- Primary rollback owner: **Release captain** (rotating weekly; current holder listed in PagerDuty schedule `taskq-release`). Authorises the rollback decision per the Rollback SOP in `CONFIG_RECORDS.md §7`.
- On-call engineer (T+0 → T+24 h post-release): **Backend on-call lead**, PagerDuty schedule `taskq-backend-primary`, escalation `taskq-backend-secondary`.
- Communications lead (customer + exec): **Customer Success on-call**, schedule `cs-oncall`.

### Post-release monitoring dashboard
- Primary dashboard: Grafana `https://grafana.internal/d/taskq-postrelease` — panels: 5xx rate, p50/p95/p99 task latency, queue backlog, worker saturation, DB connection-pool saturation, alembic migration head, `/v1/healthz` probe success rate. Auto-pinned to the release tag's annotation.
- Alert routing: alerts from that dashboard route to `taskq-backend-primary` (P3) and `taskq-release` (P1). Runbook link is embedded in every alert.
- Log search: Loki / Cloud Logging view `service="taskq-api" AND release="vharness-v4-20260821-score95-14-ge445f92"`, retention 30 d.

### Customer comms template
> Subject: `[taskq] Release vharness-v4-20260821-score95-14-ge445f92 — what changed and what to watch`
>
> Hi `<customer_name>`,
>
> We shipped `vharness-v4-20260821-score95-14-ge445f92` to production on
> `<release_date>`. Highlights:
>
> - `<1-3 bullet changelog items, written for customers, no internal jargon>`
> - `<any breaking change, with migration steps + deprecation date>`
> - `<any action required from the customer, or "No action required">`
>
> What we are watching for the next 24 h: `<the 2-3 metrics from the
> post-release dashboard that most correlate with this change>`.
>
> If you see anything unexpected, open a P1 via your normal support
> channel and reference this release tag. The on-call engineer will see
> it immediately.
>
> — `<communications_lead_name>` on behalf of the taskq release crew
