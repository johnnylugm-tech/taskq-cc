# 對抗式 Bug Hunt 報告 — Gate 3 adversarial_review

- 日期:2026-08-20
- HEAD(掃描時):`c1351e5`
- 目標清單:`.methodology/bug_hunt_targets.json`(13 high-risk × 3 lens、23 standard × 1 lens)
- Lens:correctness / concurrency / resilience / general
- 結果:13 筆 finding — 7 confirmed(1 critical、2 high、3 medium、1 low)、6 筆威脅模型驗證通過(refuted)
- 阻擋 Gate 的 confirmed critical/high:3 筆,全部 **resolved**(commit `c1351e5` + repro test)

## 掃描摘要(module × severity)

| 模組 | critical | high | medium | low |
|---|---|---|---|---|
| service.runner | 1 | 1 | — | — |
| api.health | — | 1 | — | — |
| app(errors 邊界) | — | — | 1 | — |
| service.auth | — | — | 1 | — |
| service.ratelimit / api.deps | — | — | 1 | — |
| api.tasks | — | — | — | 1 |

## 已確認並修復(阻擋 Gate)

### 1. `runner#1`(critical)— 併發閘門只減不加,submit 永久拒收
`service/runner.py:127`。`_AdmissionGate.try_admit()` 只對 `_remaining` 遞減,全模組沒有任何遞增路徑;`_get_gate()`(`runner.py:143`)僅在 cap 值改變時重建。因此 `TASKQ_MAX_CONCURRENT` 實際是「行程生命週期總量」而非併發上限。

實測(cap=2,四次**循序** submit,每次都 await 到完成):`['done','done','queued','queued']` — 第 3、4 次在完全沒有 in-flight 的情況下被拒,且不會重試,等同於永久性的靜默執行中斷。

修復:新增 `release()`,在 `submit()` 的 `finally` 呼叫(成功/失敗/取消都會歸還)。

### 2. `runner#2`(high)— 無法 spawn 的指令讓任務永遠卡在 `running`
`service/runner.py:236`。`_collect_outcome` 只攔 `asyncio.TimeoutError`,但 `create_subprocess_exec` 對不存在的執行檔會拋 `FileNotFoundError`(OSError)。例外在 `record_result` / `update_status`(`runner.py:276-285`)之前逃逸 → 沒有 `task_results` 列、狀態永遠停在 `running`;經 `POST /v1/tasks/{id}/run` 時例外死在 fire-and-forget task 裡,客戶端完全無感。

實測:`run_task(..., '/nonexistent/binary arg')` → `FileNotFoundError`,狀態 `running`,run rows `0`。

修復:`except (OSError, ValueError)` → `STATE_FAILED`,OS 錯誤訊息寫入 `stderr_tail`。

### 3. `health#1`(high)— `/v1/metrics` 的 `rate_limit_denials` 永遠是 0
`api/health.py:52` 在 import 時複製了 `ratelimit_service.denial_count` 的**值**;`ratelimit.record_denial()`(`ratelimit.py:31`)重新綁定的是 service 模組的 global,無法影響已複製的 int。FR-09 這個觀測欄位形同失效。

實測:兩次 `record_denial()` 後 `ratelimit.denial_count == 2`,而 metrics body 仍是 `0`。

修復:handler 改讀 `ratelimit_service.denial_count`(即時值)。

> 三者的 repro test 皆先 RED 後 GREEN:`03-development/tests/test_bug_hunt_regressions.py`;全套 207 test 通過。

## 已確認、不阻擋 Gate(留檔追蹤)

| id | severity | 位置 | 摘要 |
|---|---|---|---|
| `errors#1` | medium | `app.py:64` | T-05 緩解只是 3 個子字串黑名單(`Traceback`/`SQL`/`/Users`)。Linux 部署路徑(`/home/...`、`/opt/...`)會原樣回傳到 500 body。實測 sanitizer 對 `/home/deploy/secrets/taskq.db` 完全不遮蔽。建議改為白名單:500 一律回固定字串,原始訊息只進 log。 |
| `auth#1` | medium | `auth.py:48-99` | 生產程式碼依 `get_active_by_hash.__name__ == "_stub_active"` 分支 —— 測試替身的識別字被編譯進了認證決策。因兩個回傳值在 `deps.py:87` 都導向 401,無提權風險,故列 medium。 |
| `ratelimit#1` | medium | `deps.py:51` | T-02 緩解不完整:bucket 例外一律 fail-open 放行;且 `_enforce_rate_limit` 排在 `_resolve_or_raise` 之後,未認證洪水完全不受限,而每次嘗試仍會開一個 session 查 `key_repo`。 |
| `tasks#1` | low | `tasks.py:109` | 只檢查 `limit > 200`,未擋負數;`limit=-5` 在 SQLite 下的 `LIMIT -4` 等於不限筆數。 |

## 威脅模型驗證(SAD §6 STRIDE-lite,8 條)

每條都實際嘗試攻擊,而非只確認「看起來有防禦碼」。

| threat | 緩解有效 | 證據 |
|---|---|---|
| T-01 tampering | ✅ | `schemas.py:16` 注入字元黑名單在 validator(`:45-52`)強制執行 → 422;縱深防禦:`runner.py:190-191` shlex + exec 無 shell |
| T-02 DoS | ❌ | 見 `ratelimit#1`:fail-open + 未認證階段無限流 |
| T-03 spoofing | ✅ | `key_repo.py:74` 過濾 `revoked_at`;`auth.py:101` `hmac.compare_digest`;偽造 key 無 sha256 preimage → 401 |
| T-04 EoP | ✅ | `tasks.py:126` / `health.py:198` admin gate;`has_scope("write","admin")` = 2>=3 False → 403;未知 scope 排名 0 亦拒 |
| T-05 info disclosure | ❌ | 見 `errors#1`:黑名單只涵蓋 macOS 路徑前綴 |
| T-06 tampering | ✅ | `runner.py:190` shlex.split + `:191` create_subprocess_exec,全樹無 `shell=`;`;` `rm` 只成為 argv 字面 token |
| T-07 DoS | ✅ | `runner.py:272` timeout fallback、`:198` wait_for、`:205`/`:214` kill+wait 收屍、`:390` drain 有預算 |
| T-08 info disclosure | ✅ | `config.py:49` repr 遮蔽 userinfo;`session.py:67-71` 壓低 `sqlalchemy.engine.create` logger;metrics body 不插值任何 settings |

## 修復優先順序

1. (已修)`runner#1`、`runner#2`、`health#1`
2. `errors#1` — 500 detail 改白名單(部署到 Linux 前必須處理)
3. `ratelimit#1` — fail-closed + 認證前的粗粒度限流
4. `auth#1` — 移除測試替身分支
5. `tasks#1` — 補 `limit < 1` 的 422

## 掃描方法

`bug-hunt-targets` 產生目標清單(CRG hub + mutation survivor + SAD §6 威脅模型種子)→ 對 13 個 high-risk 模組逐一全文閱讀並套用 correctness / concurrency / resilience 三 lens、23 個 standard 模組套 general lens → 每筆 finding 以「refuter 預設不成立」的標準覆核,只有能寫出具體觸發輸入且**實際執行重現**的才標 confirmed → 阻擋級 finding 先寫 RED repro test 再最小修復到 GREEN。
