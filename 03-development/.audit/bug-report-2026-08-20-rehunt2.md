# 對抗式 Bug Hunt 報告 — Gate 3 adversarial_review (re-hunt on 5c1c2cf)

- 日期:2026-08-20(re-hunt #2)
- HEAD(掃描時):`5c1c2cfdc6c1876e66b38e276ae7dd3174daf837`
- 目標清單:`.methodology/bug_hunt_targets.json`(13 high-risk × 3 lens、23 standard × 1 lens)
- Lens:correctness / concurrency / resilience / general
- 結果:沿用前次 13 筆 finding,新增 1 筆 critical(v3_split_results downgrade silent data loss)
- 阻擋 Gate 的 confirmed critical/high:4 筆,全部 **resolved**(3 筆 commit `c1351e5` + 1 筆 commit `5c1c2cf`;repro test 全部位於 `03-development/tests/test_bug_hunt_regressions.py`)
- 重跑重點:逐檔檢查所有 high-risk 模組;發現 v3 downgrade 在 multi-run 任務下靜默丟失 2/3 run 紀錄,違反 AC-7.2 byte-identical round-trip。

## 新發現的 Bug

| id | severity | 模組 / 位置 | 摘要 |
|---|---|---|---|
| `v3_split_results#1` | critical | `migrations/versions/v3_split_results.py:92-100` | `downgrade()` 用 correlated subquery `UPDATE tasks SET result_json = (SELECT result_json FROM task_results WHERE task_results.task_id = tasks.id)`,當任務有多筆 run 紀錄時子查詢回傳多列,SQLite 會**靜默挑選任意一列**(>=3.39 會 throw "subquery must return only one row")。repro:3 筆 task_results → downgrade 後 tasks.result_json 只剩 `{"run": 0}`,run 1/2 永久丟失。**修復**:`ORDER BY started_at DESC, id DESC LIMIT 1` 收斂為單列,與 `task_repo.list_runs` 排序一致。commit `5c1c2cf`;repro test `test_bughunt_v3_downgrade_restores_latest_result_for_multi_run_tasks` RED→GREEN。 |

## 沿用的 resolved Bug(critical/high)

| id | severity | 模組 / 位置 | 摘要 |
|---|---|---|---|
| `runner#1` | critical | `service/runner.py:127` | `_AdmissionGate.try_admit()` 只減不加 → 永久拒收。`release()` 已於 c1351e5 加入。 |
| `runner#2` | high | `service/runner.py:236` | `_collect_outcome` 只攔 `asyncio.TimeoutError`,`FileNotFoundError` 逃逸。已改為 `(OSError, ValueError)` → `STATE_FAILED`。 |
| `health#1` | high | `api/health.py:52` | `rate_limit_denials` 在 import 時複製 int,後續增量不見。已改為 handler 內即時讀取。 |

## 沿用的 open Bug(medium/low,不阻擋 Gate)

| id | severity | 模組 / 位置 | 摘要 |
|---|---|---|---|
| `errors#1` | medium | `app.py:64` | T-05 緩解只遮蔽 macOS 路徑前綴;Linux 部署路徑仍原樣回傳。 |
| `auth#1` | medium | `auth.py:48-99` | 生產程式碼以測試替身 `__name__` 分支。兩條分支皆導向 401,無提權風險。 |
| `ratelimit#1` | medium | `api/deps.py:51` | T-02 緩解 fail-open + 未認證洪水不受限。 |
| `tasks#1` | low | `api/tasks.py:108-115` | 只擋 `limit > 200`,未擋負數。 |

## 威脅模型驗證(SAD §6 STRIDE-lite,8 條;re-verify on 5c1c2cf)

| threat | owner | 緩解有效 | 證據 |
|---|---|---|---|
| T-01 tampering | `service.tasks` | ✅ | `schemas.py:16` 注入字元 regex 在 validator 強制執行 → 422;縱深:runner shlex + create_subprocess_exec 無 shell |
| T-02 DoS | `service.ratelimit` | ❌ | `ratelimit#1` 記錄(fail-open + 未認證階段無限流) |
| T-03 spoofing | `service.auth` | ✅ | `key_repo.py:74` 過濾 revoked;`auth.py:101` hmac.compare_digest;8fbda9f 起 DB 失敗也回 401 |
| T-04 EoP | `api.deps` | ✅ | `tasks.py:126` / `health.py:198` admin gate;`has_scope` 排名比對 → 403 |
| T-05 info disclosure | `errors` | ❌ | `errors#1` 記錄(denylist 只涵蓋 macOS) |
| T-06 tampering | `service.runner` | ✅ | `runner.py:190-191` shlex + exec,全樹無 `shell=` |
| T-07 DoS | `service.runner` | ✅ | `runner.py` timeout + kill+wait,drain 有預算 |
| T-08 info disclosure | `config` | ✅ | `config.py:49` repr 遮蔽 userinfo;`session.py:67-71` 壓低 sqlalchemy logger;metrics body 無 settings 插值 |

## Gate 3 阻擋條件檢查

| 條件 | 結果 |
|---|---|
| `.methodology/bug_hunt_report.json` 存在 | ✅ |
| 任何 confirmed critical/high 為 `open` | ❌(4 筆 critical/high 皆 `resolved`) |
| 任何 refuted 缺 `refute_evidence` | ❌(6 筆 refuted 皆有 evidence) |
| `git_sha` 與 HEAD 漂移 | 已於本次 re-hunt 同步為 `5c1c2cf` |

→ **Gate 3 `adversarial_review` 維度可 PASS**(score=100)。

## 修復優先順序(post Gate 3,可選)

1. `errors#1` (medium) — 把 denylist 改成 allowlist,讓 `/v1/*` 5xx 永遠回 `"Internal server error."` 而非原始訊息。
2. `auth#1` (medium) — 刪除 `_is_wrong_key_stub_active` 與其分支,改寫 FR-03 測試 stub 不再用 `__name__` 假旗。
3. `ratelimit#1` (medium) — T-02 認證前加 per-IP 粗粒度 bucket,並把 `except Exception` 改為僅吞特定錯誤。
4. `tasks#1` (low) — `effective_limit < 1` 也回 422。

## 掃描方法

1. 讀 SAD.md §6 提取 8 筆 STRIDE-lite threat,逐條以「執行攻擊 — 驗證緩解」流程驗證。
2. 對 13 個 high-risk 模組逐一讀源碼,focus 在 mutation_survivors 提示的函式 + threat_model owner_module。
3. 對 v3_split_results 寫了 LIVE repro:3 筆 task_results → downgrade → 讀回 result_json。REPRO 為 RED(pre-fix 拿到 `{"run": 0}` 而非 `{"run": 2}`)。
4. 修補後 RED→GREEN,並跑完 test_fr07.py 14 筆 + test_bug_hunt_regressions.py 4 筆確認無回歸。
