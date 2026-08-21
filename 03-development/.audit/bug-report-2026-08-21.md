# 對抗式 Bug Hunt 報告 — Gate 3 adversarial_review (re-hunt on ded68d0)

- 日期:2026-08-21(re-hunt #3)
- HEAD(掃描時):`ded68d09aacf82f530a9cb6c2cbe74aecdc71abd`
- 目標清單:`.methodology/bug_hunt_targets.json`(13 high-risk × 3 lens、24 standard × 1 lens)
- Lens:correctness / concurrency / resilience / general
- 結果:沿用前次 14 筆 finding;errors#1 (T-05 緩解) 此次 **resolved**(原 open medium)。
- 阻擋 Gate 的 confirmed critical/high:**4 筆全部 resolved**(3 筆 commit `c1351e5`、1 筆 commit `5c1c2cf`;repro test 全部位於 `03-development/tests/test_bug_hunt_regressions.py`)
- 重跑重點:`runner.py` 把純狀態原語抽到新檔 `service/run_state.py`,行為等價(回歸測試全綠);`app.py` 把 denylist 換成三條 regex 並接 `config.redact`,Linux 絕對路徑現在會被攔下。

## 本次 re-hunt 變動

| id | severity | 變動 | 證據 |
|---|---|---|---|
| `errors#1` | medium → resolved | `app.py:64` denylist 換成 `_INTERNAL_DETAIL_PATTERNS`(traceback / sql / POSIX 絕對路徑 三條 regex),`return redact(message)` 對良性訊息做 secret 過濾。Linux `/home/...` 路徑現已被攔下。 | `_sanitize_detail('/home/deploy/secrets/taskq.db') → 'Internal server error.'`;`test_ac_n2_5_500_error_body_no_stack_sql_or_paths` PASS |
| `runner.py` | refactor | 純常數/dataclass/門控移至 `service/run_state.py`,runner 只保留 I/O 邏輯;release path 保留 | regression `test_bughunt_runner_admission_slot_released_after_completion` PASS |
| `v3_split_results#1` | 仍 resolved | 5c1c2cf 修法未變 | `test_bughunt_v3_downgrade_restores_latest_result_for_multi_run_tasks` PASS |
| `runner#1 / runner#2 / health#1` | 仍 resolved | runner 抽到 run_state.py 後 release/except/OSError 邏輯都在新檔,行為等價 | 4 筆 regression 全 PASS |

## 沿用的 resolved Bug(critical/high,仍阻擋 Gate 但已處理)

| id | severity | 模組 / 位置 | 摘要 |
|---|---|---|---|
| `v3_split_results#1` | critical | `migrations/versions/v3_split_results.py:101-113` | `downgrade()` 子查詢加上 `ORDER BY started_at DESC, id DESC LIMIT 1`,multi-run 任務收斂到最新一筆。commit `5c1c2cf`。 |
| `runner#1` | critical | `service/run_state.py:132-140` | `_AdmissionGate.release()` 從 `submit()` 的 `finally` 區塊被呼叫。commit `c1351e5`。 |
| `runner#2` | high | `service/runner.py` (`_collect_outcome`) | `except (OSError, ValueError)` 攔 spawn 失敗並寫入 `STATE_FAILED`。commit `c1351e5`。 |
| `health#1` | high | `api/health.py:235` | `metrics_route()` 即時讀 `ratelimit_service.denial_count`,不再用 import-time 拷貝。commit `c1351e5`。 |

## 沿用的 open Bug(medium/low,不阻擋 Gate)

| id | severity | 模組 / 位置 | 摘要 |
|---|---|---|---|
| `auth#1` | medium | `service/auth.py:48-60,98-104` | `_is_wrong_key_stub_active` 仍以測試替身 `__name__` 分支;兩條分支皆 → 401,無提權風險。 |
| `ratelimit#1` | medium | `api/deps.py:51-56` | `_enforce_rate_limit` fail-open,且僅在認證成功後才扣桶,未認證洪水可打到 DB。 |
| `tasks#1` | low | `api/tasks.py:108-115` | 只擋 `limit > 200`,負數會傳到 `LIMIT -4`(SQLite 解讀為無上限)。 |

## 威脅模型驗證(SAD §6 STRIDE-lite,8 條;re-verify on ded68d0)

| threat | owner | 緩解有效 | 證據 |
|---|---|---|---|
| T-01 tampering | `service.tasks` | ✅ | `schemas.py:16` regex 在 validator 強制執行 → 422;`run_state`/runner shlex + exec 無 shell |
| T-02 DoS | `service.ratelimit` | ❌ | `ratelimit#1` 仍 open(fail-open + 未認證不限流) |
| T-03 spoofing | `service.auth` | ✅ | `key_repo.py:80` 過濾 revoked;`auth.py` hmac.compare_digest;DB 失敗 → None → 401 |
| T-04 EoP | `api.deps` | ✅ | `tasks.py:127` / `health.py:201` admin gate;`has_scope` 排名比對 → 403 |
| T-05 info disclosure | `errors` | ✅(本次 resolved) | `_sanitize_detail` 三條 regex 攔 traceback/SQL/POSIX 絕對路徑;Linux 路徑不再外洩 |
| T-06 tampering | `service.runner` | ✅ | `runner.execute_command` shlex + exec,全樹無 `shell=` |
| T-07 DoS | `service.runner` | ✅ | `runner.execute_command` timeout + kill+wait;`drain` 有預算 |
| T-08 info disclosure | `config` | ✅ | `config.py:51` repr 遮蔽 userinfo;`session.py:67-71` 壓低 sqlalchemy logger;metrics body 無 settings 插值 |

## Gate 3 阻擋條件檢查

| 條件 | 結果 |
|---|---|
| `.methodology/bug_hunt_report.json` 存在 | ✅ |
| 任何 confirmed critical/high 為 `open` | ❌(4 筆 critical/high 皆 `resolved`,repro test 全部存在) |
| 任何 refuted 缺 `refute_evidence` | ❌(6 筆 refuted 皆有 evidence) |
| `git_sha` 與 HEAD 漂移 | ✅(`ded68d0` 與 HEAD 一致) |

→ **Gate 3 `adversarial_review` 維度可 PASS**(score=100)。

## 修復優先順序(post Gate 3,可選)

1. `ratelimit#1` (medium) — `_enforce_rate_limit` fail-open 改為 fail-closed;認證前加 per-IP 粗粒度 bucket。
2. `auth#1` (medium) — 刪除 `_is_wrong_key_stub_active` 與其分支,改寫 FR-03 測試 stub 不再用 `__name__` 假旗。
3. `tasks#1` (low) — `effective_limit < 1` 也回 422。

## 掃描方法

1. 讀 SAD.md §6 提取 8 筆 STRIDE-lite threat,逐條以「執行攻擊 — 驗證緩解」流程驗證。
2. 對 13 個 high-risk 模組逐一讀源碼,focus 在 mutation_survivors 提示的函式 + threat_model owner_module。
3. 直接執行 `_sanitize_detail` 驗證 T-05 緩解(Linux 路徑、macOS 路徑、SQL、良性訊息),並把 `test_ac_n2_5_500_error_body_no_stack_sql_or_paths` 跑一次確認 RED→GREEN。
4. 跑 `tests/test_bug_hunt_regressions.py` 4 筆 + `tests/test_nfr_spec_coverage.py::test_ac_n2_5_500_error_body_no_stack_sql_or_paths` 1 筆確認所有 resolved 修復無回歸。
