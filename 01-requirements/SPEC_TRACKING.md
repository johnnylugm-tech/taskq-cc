# Specification Tracking Matrix — taskq-api

> Human-readable specification tracking view for the canonical `SPEC.md` (v1.0.0, 2026-07-30).
> Source: `SPEC.md` (project root) → transcribed into `01-requirements/SRS.md` as the per-FR/NFR machine-readable body.
> This file is a tracking companion, NOT the SSOT — the SSOT is `SPEC.md` and `quality_manifest.json`.
> Status is machine-refreshed from `build_traceability` at `advance-phase`; do not hand-edit Status.

## Project Info
- Project Name: taskq-api
- Version: v1.0.0
- Canonical spec: `SPEC.md` v1.0.0 (2026-07-30)
- SRS authoring: 01-requirements/SRS.md (APPROVED, 2026-08-19)
- Phase: 1 (Requirements)
- Language: Python 3.11 (FastAPI + SQLAlchemy 2.x + Alembic)

## Specification Status

> **The Status column is machine-refreshed** — `advance-phase` overwrites each
> FR/NFR's Status from `build_traceability`'s live code/test scan (DRAFT while no
> code/module exists, IN_PROGRESS once code/module exists, VERIFIED once
> code+test exist). The authoritative status is that scan / `quality_manifest.json`,
> NOT this hand-filled cell. Fill the semantic columns (Spec Description /
> Intent Class / Decision Framework / Notes); leave Status to refresh itself
> (a hand-edit is overwritten on the next advance).

| ID | Spec Description | Intent Class | Decision Framework | Status | Notes |
|----|------------------|--------------|--------------------|--------|-------|
| FR-01 | 任務資源 CRUD API: POST/GET(單筆)/GET(列表, cursor 分頁)/DELETE /v1/tasks; 驗證失敗 422, 未知 id 404, 列表 limit 預設 50 上限 200, N+1 防護 | Functional — CRUD contract | Integration via AC-1.1..AC-1.7 + SPEC §8 #4/#7/#14; HTTP-level assertions + SQLAlchemy event-listener count | DRAFT | §3 FR-01; AC IDs AC-1.1..AC-1.7; implementation modules `taskq_api.service.tasks` + `taskq_api.api.tasks` |
| FR-02 | 任務執行端點: POST /v1/tasks/{id}/run → 202; `asyncio.create_subprocess_exec(*shlex.split(command))` 禁 `shell=True`; 結果寫入 `task_results`; GET /v1/tasks/{id}/runs 歷史 | Functional — process execution contract | Integration via AC-2.1..AC-2.5 + SPEC §8 #16/#25; grep gate for `shell=True` + PID enumeration | DRAFT | §3 FR-02; AC IDs AC-2.1..AC-2.5; implementation module `taskq_api.service.runner` |
| FR-03 | API Key 認證: `X-API-Key` header; SHA-256 hashed storage; `hmac.compare_digest` constant-time compare; `key create` 一次性印明文; `revoked_at` 失效; `/healthz`+`/readyz` 免認證 | Functional — authentication contract | Integration + unit via AC-3.1..AC-3.6 + SPEC §8 #5/#18; CLI invocation + DB plaintext-absence check | DRAFT | §3 FR-03; AC IDs AC-3.1..AC-3.6; modules `taskq_api.service.auth` + `taskq_api.api.deps` + `taskq_api.__main__` |
| FR-04 | Scope 授權: `read < write < admin` 階層; 不足回 403, body 不洩漏資源存在性; 單一 FastAPI dependency 判定點 | Functional — authorisation contract | Integration via AC-4.1..AC-4.3 + SPEC §8 #6; static route-dependency introspection | DRAFT | §3 FR-04; AC IDs AC-4.1..AC-4.3; single-dependency invariant enforced |
| FR-05 | 流量控制: per-token token bucket (`TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC`); 超限 429 + `Retry-After`; DB 內狀態以 row-level lock 單一交易更新; `/healthz`+`/readyz` 免限 | Functional — rate limiting contract | Integration + unit via AC-5.1..AC-5.3 + SPEC §8 #9; SQLAlchemy `SELECT ... FOR UPDATE` instrumentation + bucket-exhaustion integration test | DRAFT | §3 FR-05; AC IDs AC-5.1..AC-5.3; modules `taskq_api.service.ratelimit` + `taskq_api.repository.rate_repo` |
| FR-06 | 持久化層與交易邊界: 全部存取經由 `repository/`; 每個請求一個 Session 由 context manager 包; 禁字串拼接 SQL; 顯式 `selectinload`/`joinedload` 防 N+1; `pool_size` + `pool_pre_ping` | Functional — persistence/layering contract | Integration + grep gate via AC-6.1..AC-6.5 + SPEC §8 #14/#17/#21; `lint-imports` + SQLAlchemy event-listener + engine-config unit | DRAFT | §3 FR-06; AC IDs AC-6.1..AC-6.5; layering rule enforced by `.importlinter` (NFR-06) |
| FR-07 | Schema Migration (Alembic 三步演進): v1 base, v2 tags 多對多 + name unique, v3 拆 `result_json` 到 `task_results` 含資料搬遷; 全部有 `downgrade`; upgrade→write→downgrade -1→upgrade 逐欄相同 | Functional — migration contract | Integration via AC-7.1..AC-7.5 + SPEC §8 #12/#13; **real SQLite file** per NFR-09 (no in-memory mock); offline-SQL round-trip | DRAFT | §3 FR-07; AC IDs AC-7.1..AC-7.5; revisions v1/v2/v3 in `migrations/versions/`; round-trip is acceptance focus |
| FR-08 | 非同步執行器: `asyncio.TaskGroup` 管理背景; graceful drain 至 `TASKQ_DRAIN_TIMEOUT`; `TASKQ_MAX_CONCURRENT` 排隊; timeout 殺子進程 (`process.kill()` + `await process.wait()`); `CancelledError` 向上傳播 | Functional — async runner contract | Integration + unit via AC-8.1..AC-8.5 + SPEC §8 #25; PID enumeration + AST check for `TaskGroup` (no bare `gather`/fire-and-forget) | DRAFT | §3 FR-08; AC IDs AC-8.1..AC-8.5; cancellation semantics critical |
| FR-09 | 健康檢查與可觀測性: `/healthz` 200; `/readyz` 200 需 DB 可用 + `alembic current == head`, 否則 503; `/v1/metrics` 需 admin | Functional — health/observability contract | Integration via AC-9.1..AC-9.3 + SPEC §8 #10/#11; closed-DB + downgraded-migration assertions for 503 paths | DRAFT | §3 FR-09; AC IDs AC-9.1..AC-9.3; "deploy-without-migration" must fail closed |
| FR-10 | 錯誤契約 (RFC 7807): 全部非 2xx 為 `application/problem+json`, 欄位 `type`/`title`/`status`/`detail`/`instance`/`correlation_id`; detail 不含 SQL/堆疊/路徑; `X-Correlation-Id` header + log | Functional — error contract | Integration via AC-10.1..AC-10.5 + SPEC §8 #5-#10/#19; status-code sweep + detail allowlist | DRAFT | §3 FR-10; AC IDs AC-10.1..AC-10.5; module `taskq_api.errors` |
| NFR-01 | 效能與查詢效率: `GET /v1/tasks/{id}` p95 < 30 ms, list p95 < 80 ms (10k 筆); 列表端點 SQL 陳述數常數 (N+1 防護) | Non-functional — performance (`dimension: performance`) | `pytest-benchmark` + SQLAlchemy event-listener via AC-N1.1..AC-N1.3 + SPEC §8 #14/#15 | DRAFT | §4 NFR-01; AC IDs AC-N1.1..AC-N1.3 |
| NFR-02 | HTTP 與資料層安全: 禁 `shell=True`/`eval(`/`exec(`; 禁 SQL 字串拼接; API key 雜湊 + `hmac.compare_digest`; 403 不洩漏資源存在; 錯誤 body 不含內部; CORS 預設全拒; `bandit` 0/0 | Non-functional — security (`dimension: security`) | grep gate + integration + `bandit` CI via AC-N2.1..AC-N2.7 + SPEC §8 #6/#16/#17/#18/#19/#23 | DRAFT | §4 NFR-02; AC IDs AC-N2.1..AC-N2.7 |
| NFR-03 | 錯誤處理、交易與非同步正確性: 交易 context manager; 禁裸 `except:` / `except Exception: pass`; `CancelledError` 傳播; DB 失敗 `/readyz` 503; timeout 殺子進程; migration 失敗 rollback | Non-functional — error_handling (`dimension: error_handling`); SAB `type: reliability` | `ast-error-handling` + integration via AC-N3.1..AC-N3.6 + SPEC §8 #10/#11/#25 | DRAFT | §4 NFR-03; AC IDs AC-N3.1..AC-N3.6 |
| NFR-04 | 敏感資料遮蔽: `stdout_tail`/`stderr_tail`/log/error body 寫入或送出前以正則遮蔽 (`sk-...\|token=...\|Bearer ...\|postgres://...`); DB URL 密碼不出現於日誌 / 錯誤 / metrics; key 明文只印一次 | Non-functional — security (`dimension: security`); SAB `type: security` | unit redaction test + log/metric sink scan via AC-N4.1..AC-N4.3 + SPEC §8 #20 | DRAFT | §4 NFR-04; AC IDs AC-N4.1..AC-N4.3; complements (does not replace) `secrets_scanning` per NFR-99-01 |
| NFR-05 | 文件覆蓋: 公開函式/類別 100% docstring 且含 `[FR-XX]`/`[NFR-XX]` 引用; 每個 API 端點在 `/openapi.json` 帶 `summary` + `description` | Non-functional — documentation (`dimension: documentation`) | `ast-docstrings` CI + OpenAPI shape test via AC-N5.1 + AC-N5.2 | DRAFT | §4 NFR-05; AC IDs AC-N5.1, AC-N5.2 |
| NFR-06 | 架構分層契約: `.importlinter` 必存, `api > service > repository > models` 分層 + `config`/`errors` independence; 禁 `repository` 以外 `import sqlalchemy`; `lint-imports` exit 0; 禁止降級 contract | Non-functional — architecture_constraints (`dimension: architecture_constraints`); SAB `type: layering` | `import-linter` CI + negative import test via AC-N6.1..AC-N6.4 + SPEC §8 #21 | DRAFT | §4 NFR-06; AC IDs AC-N6.1..AC-N6.4; no degradation permitted |
| NFR-07 | 依賴與授權合規: `requirements.txt` `==` 釘版; `requirements.lock` 鎖 transitive; allowlist = MIT/BSD-2-Clause/BSD-3-Clause/Apache-2.0/PSF; 全樹掃描; `08-config/SBOM.json` 含 `name`/`version`/`license`/`direct\|transitive` | Non-functional — license_compliance (`dimension: license_compliance`); SAB `type: licensing` | `pip-licenses --with-system` + SBOM shape test via AC-N7.1..AC-N7.4 + SPEC §8 #22 | DRAFT | §4 NFR-07; AC IDs AC-N7.1..AC-N7.4 |
| NFR-08 | 變異測試: `harness_config.json` `features.mutation_testing: true`; `mutmut` score ≥ 70 over `service/` + `repository/`; 範圍限定需註記理由 | Non-functional — mutation_testing (`dimension: mutation_testing`); SAB `type: mutation` | `mutmut run` + `results` parse via AC-N8.1..AC-N8.3 + SPEC §8 #24 | DRAFT | §4 NFR-08; AC IDs AC-N8.1..AC-N8.3 |
| NFR-09 | 驗證真實性 (零 skip 鐵律): `pytest` `skipped=0`; 每個測試至少一個 assert; 禁 `--ignore`/`-k`/`--deselect`/`collect_ignore`; FR-07 migration 以真實 SQLite 檔案測試不得降級; `TRACEABILITY_MATRIX.md` 的 `VERIFIED` 須來自實際執行 | Non-functional — test_assertion_quality (`dimension: test_assertion_quality`); SAB `type: testability` | `ast-assertions` + pytest gate + matrix generator via AC-N9.1..AC-N9.5 + SPEC §8 #1 | DRAFT | §4 NFR-09; AC IDs AC-N9.1..AC-N9.5; round-3 special clause |
| NFR-10 | 整合覆蓋: `03-development/tests/integration/` 行覆蓋 ≥ 80%; 整合測試以 `httpx.AsyncClient(ASGITransport(app))` 驅動, 不得直接呼叫 handler; 涵蓋 401/403/404/409/422/429/503 + migration 往返 + rate limit 觸發/恢復 + graceful drain | Non-functional — integration_coverage (`dimension: integration_coverage`); SAB `type: integration` | `pytest-cov-integration` + suite enumeration via AC-N10.1..AC-N10.3 + SPEC §8 #3 | DRAFT | §4 NFR-10; AC IDs AC-N10.1..AC-N10.3 |
| NFR-11 | 可讀性: MI ≥ 80; 函式 CC ≤ 10; 單檔 ≤ 400 行; 單目錄 ≤ 15 檔; API handler ≤ 40 行 (業務邏輯下沉到 `service/`) | Non-functional — readability (`dimension: readability`); SAB `type: maintainability` | `readability-v2` (radon-mi) + filesystem gate via AC-N11.1..AC-N11.3 | DRAFT | §4 NFR-11; AC IDs AC-N11.1..AC-N11.3 |
| NFR-12 | 系統驗證目標: `Makefile` `verify-system` 串接 `upgrade head` → tests → `/healthz`+ `/readyz` smoke → `downgrade base` + `upgrade head`; exit 0 且 stdout 含 `verify-system: PASS` | Non-functional — execute_verification_target (`dimension: execute_verification_target`); SAB `type: verifiability` | `make verify-system` exit + stdout grep via AC-N12.1 + AC-N12.2 + SPEC §8 #27 | DRAFT | §4 NFR-12; AC IDs AC-N12.1, AC-N12.2 |

## Completeness Check

| Check | Expected | Actual | Status |
|-------|----------|--------|--------|
| FR coverage in tracking matrix | 10 (FR-01..FR-10) | 10 | Done |
| NFR coverage in tracking matrix | 12 (NFR-01..NFR-12) | 12 | Done |
| FR/NFR headings in SRS.md match SPEC.md | 22 / 22 | 22 / 22 | Done |
| Every FR/NFR carries a `Status` cell | 22 / 22 | 22 / 22 | Done (all DRAFT pre-implementation; machine-refreshed by `build_traceability` at `advance-phase`) |
| Every FR/NFR carries an AC cross-reference | 22 / 22 | 22 / 22 | Done |
| Decision Framework references SPEC §8 acceptance commands | 22 / 22 | 22 / 22 | Done |

## Update log

| Date | Change | By |
|------|--------|----|
| 2026-08-19 | Initial creation (Round 1, populated from APPROVED `SRS.md` transcribed from canonical `SPEC.md` v1.0.0 2026-07-30) | Agent A — Requirements Engineer |
