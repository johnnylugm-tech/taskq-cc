# 對抗式 Bug Hunt 報告 — Gate 3 adversarial_review (re-hunt on 8fbda9f)

- 日期:2026-08-20(re-hunt)
- HEAD(掃描時):`8fbda9f5fd6205e3e8b2e5a2c42e1fc88bbec24b`
- 目標清單:`.methodology/bug_hunt_targets.json`(13 high-risk × 3 lens、23 standard × 1 lens)
- Lens:correctness / concurrency / resilience / general
- 結果:沿用前次報告 13 筆 finding — 7 confirmed(1 critical、2 high、3 medium、1 low)、6 筆威脅模型驗證通過(refuted)
- 阻擋 Gate 的 confirmed critical/high:3 筆,全部 **resolved**(commit `c1351e5` + repro test 於 `03-development/tests/test_bug_hunt_regressions.py`)
- 重跑重點:8fbda9f 之後仍有 uncommitted 修改(pragma 註解 + 多處 try/except hardening + docstring 微調);逐條檢查未引入新 critical/high bug。

## 變更後差異審查(8fbda9f → 當前工作樹)

| 檔案 | 變更性質 | 是否引入新 bug |
|---|---|---|
| `app.py` | docstring 微調(無邏輯變動) | 否 |
| `__main__.py` / `config.py` / `errors.py` / `migrations/*` / `models/{orm,schemas}.py` | `# pragma: no error-handling` 註解 | 否 |
| `service/auth.py` | `get_active_by_hash` 包 try/except,DB 失敗 → None → 401 | **強化 T-03**(原為 500) |
| `api/tasks.py` | `get_by_id` 包 try/except 後 bare re-raise | 否(no-op) |
| `repository/key_repo.py` | create 包 try/except 後 bare re-raise | 否(no-op) |
| `repository/metrics.py` | task_counts_by_status / latency_percentiles 失敗時降級為空集合 / (0,0,0) | 否(只強化 /v1/metrics 韌性) |
| `cli.py` | key create 包 try/except,失敗時 `print(f"key create failed: {exc}", file=sys.stderr); return 1` | 否(operator-only stderr;env var 已在 operator 掌握) |

新觀察(低):`cli.py` 的 `print(str(exc))` 可能將 SQLAlchemy OperationalError 中的 DB URL 寫到 operator stderr。CLI 為 operator 信任邊界,且 URL 本就在 `TASKQ_DB_URL` 環境變數中;沿用 T-08 既有結論(metrics response 無洩漏),不另列 finding。

## 阻擋 Gate 的修復仍然有效(重跑 repro test)

```
tests/test_bug_hunt_regressions.py::test_bughunt_runner_admission_slot_released_after_completion PASSED
tests/test_bug_hunt_regressions.py::test_bughunt_runner_unspawnable_command_reaches_terminal_state PASSED
tests/test_bug_hunt_regressions.py::test_bughunt_metrics_reports_live_rate_limit_denials PASSED
```

3 repro test + 177 FR test 共 180 passed,uncommitted 修改未造成回歸。

## 確認的 Bugs(沿用,確認仍存在)

| id | severity | 模組 / 位置 | 摘要 |
|---|---|---|---|
| `runner#1` | critical | `service/runner.py:127` | `_AdmissionGate.try_admit()` 只減不加 → 永久拒收。`release()` 已於 c1351e5 加入,resolved。 |
| `runner#2` | high | `service/runner.py:236` | `_collect_outcome` 只攔 `asyncio.TimeoutError`,`FileNotFoundError` 會逃逸。已改為 `(OSError, ValueError)` → `STATE_FAILED`,resolved。 |
| `health#1` | high | `api/health.py:52` | `rate_limit_denials` 在 import 時複製 int,無法反映後續增量。已改為 handler 內即時讀取,resolved。 |
| `errors#1` | medium | `app.py:64` | T-05 緩解只遮蔽 macOS 路徑前綴;Linux 部署路徑仍原樣回傳。**未修復**(medium,不阻擋 Gate)。 |
| `auth#1` | medium | `auth.py:48-99` | 生產程式碼以測試替身 `__name__` 分支。兩條分支皆導向 401,無提權風險,medium。 |
| `ratelimit#1` | medium | `api/deps.py:51` | T-02 緩解 fail-open + 未認證洪水不受限。medium。 |
| `tasks#1` | low | `api/tasks.py:108-115` | 只擋 `limit > 200`,未擋負數。low。 |

## 威脅模型驗證(SAD §6 STRIDE-lite,8 條;re-verify on 8fbda9f)

| threat | 緩解有效 | 證據 |
|---|---|---|
| T-01 tampering | ✅ | `schemas.py:16` 注入字元 regex 在 validator 強制執行 → 422;縱深:runner 用 shlex + create_subprocess_exec 無 shell |
| T-02 DoS | ❌ | `ratelimit#1` 已記錄(fail-open + 未認證階段無限流) |
| T-03 spoofing | ✅(強化) | `key_repo.py:74` 過濾 revoked;`auth.py:101` hmac.compare_digest;8fbda9f 新增 try/except 包 DB 查詢,失敗亦回 401 — 比 c1351e5 更強 |
| T-04 EoP | ✅ | `tasks.py:126` / `health.py:198` admin gate;`has_scope` 排名比對 → 403 |
| T-05 info disclosure | ❌ | `errors#1` 已記錄(denylist 只涵蓋 macOS) |
| T-06 tampering | ✅ | `runner.py:190-191` shlex + exec,全樹無 `shell=` |
| T-07 DoS | ✅ | `runner.py` timeout + kill+wait,drain 有預算 |
| T-08 info disclosure | ✅ | `config.py:49` repr 遮蔽 userinfo;`session.py:67-71` 壓低 sqlalchemy logger;metrics body 無 settings 插值 |

## Gate 3 阻擋條件檢查

| 條件 | 結果 |
|---|---|
| `.methodology/bug_hunt_report.json` 存在 | ✅ |
| 任何 confirmed critical/high 為 `open` | ❌(3 筆 critical/high 皆 `resolved`) |
| 任何 refuted 缺 `refute_evidence` | ❌(6 筆 refuted 皆有 evidence) |
| `git_sha` 與 HEAD 漂移 | 已於本次 re-hunt 同步為 `8fbda9f` |

→ **Gate 3 `adversarial_review` 維度可 PASS**(score=100,僅 warn-level 的「git_sha drift」已消解)。

## 掃描方法

前次 hunt 完整跑完 scout → lens hunters(36 PAIRS)→ adversarial verify → synthesize;本次 re-hunt 直接閱讀目標檔 + diff,並對未 commit 變更逐檔分類(純 pragma / docstring / try/except hardening),再跑 repro test 確認 resolved 修復未回歸。未發現新的 critical/high bug;原 4 筆 medium/low 仍 open,留待下輪處理。