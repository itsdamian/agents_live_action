# 進度與 QA 紀錄：Pipeline 改造成 LangGraph

由 team lead（personal-workplace-d4）維護。Developer：`ala-developer`（Sonnet）；QA：`ala-qa`（Opus）。分支 `feat/langgraph-pipeline`，基底 `dev` b2fece5。

## 關卡 A（Task 1–6）— QA 判定 PASS（2026-11-16，commit 23f054d）

| Task | commit | 全套測試數 |
|---|---|---|
| 1 基準 | （無） | 312 passed（test_pipeline 15） |
| 2 harness | 46da0a9 | 313 |
| 3 S1–S5 | bb78781 | 318 |
| 4 S6–S10、S12 | c4e69e0 | 326 |
| 5 S11 | 2b6ce3b | 327 |
| 6 legacy 純重構 | 23f054d | 327 |

QA 驗證：範圍只有 4 個檔案、禁改清單無變更；23f054d 連跑 3 次與亂序 5 次皆 327；特徵化檔連跑 20 次皆 14 passed；執行期改 legacy 行為，4 種改法有 3 種被特徵化測試抓到（期望值確為錄製）；7 個閘門攻擊情境在 dev 與 23f054d 結果一致。

Developer 偏離（QA 已確認正當）：新增私有 `_gate_events`；測試輪詢多等 `task:awaiting_approval` 事件。

### 帶進 Task 11 的風險與待補（已寫入派工訊息）
- `stop()` 落在閘門登記後、進入 `_gate_wait` 前，`_approval_gates`／`_awaiting`／`_gate_events`／snapshot 會留殘骸，`approve()` 對死閘門仍回 True（legacy 同樣；langgraph 空窗更大 → runner 的 finally 要清）。
- approve 之後再 cancel，`_approval_decisions` 會殘留（legacy 同樣，目前無害）。
- AC6 原文與 legacy 不符，已修正 spec（見 spec AC6）。
- 尚未覆蓋：AC5 缺「其他執行緒呼叫 reject()」測試；AC6 的 stop() 只測了發事件之後；S12 無法分辨空 note 是否進 context。
- 與本計畫無關：`tests/test_base_agent.py` 每次執行都往 repo 的 `projects/logs/` 寫檔（主目錄未追蹤的 `projects/logs/` 即由此而來）；requirements 沒釘 pytest 版本（實裝 9.2.0／asyncio 1.5.0，與文件的 9.1.1／1.4.0 不同，不影響測試）。

## 關卡 B（Task 7–13）— Developer 完成，QA 覆核中（commit 09193d7）

| Task | commit | 全套測試數（legacy／langgraph 兩種引擎皆同） |
|---|---|---|
| 7 langgraph 安裝＋語意學習測試 | edd50b8 | 335 |
| 8 引擎旗標 | 237f124 | 342 |
| 9 State 與條件邊 | 64a6f2d | 363 |
| 10 無閘門 graph | 6ae439f | 369 |
| 11 閘門 interrupt／Command | 8231e9c | 387 |
| 12 整套在 langgraph 引擎重跑 | （無） | 387，連跑 3 次 |
| 13 學習註記與邊界檢查 | 09193d7 | 388 |

`git diff --stat dev...HEAD`：13 個檔案 +2284/-30（config.py、core/pipeline.py、core/pipeline_graph.py 新增、requirements.txt、docker-compose.yml，其餘為測試檔）。

### Developer 實測：plan §9 待驗證項（皆與 plan 相符）
- node 例外會從 `astream` 原樣拋出（型別與訊息不變）。
- updates 串流停在 interrupt 時，最後一個 chunk 是 `{"__interrupt__": (Interrupt,)}`，串流正常結束。
- `AsyncSqliteSaver.from_conn_string` 是 `@classmethod` + `@asynccontextmanager`，有 `setup()`（只讀原始碼，未安裝；O3 的事）。
- 放進 state 的 pydantic 物件 resume 後變成純 dict。
- 新增的套件：langchain-core 1.8.1、langgraph 1.2.22、langgraph-checkpoint 4.3.2、langgraph-prebuilt 1.1.2、langgraph-sdk 0.4.14、langsmith 0.14.17 等；未升降 fastapi／starlette／pydantic／httpx／websockets。

### Developer 回報的偏離與疑慮
- a. Task 13 字面 grep（`grep -rln langgraph . --include=*.py`）不可能只剩 pipeline_graph.py（config.py、pipeline.py 含引擎名稱字串）；改用 import 語句 grep 後只有 pipeline_graph.py。建議把 AC7 的 grep 改為 import 版本。
- b. `docker` 不在 PATH，要用 `/usr/local/bin/docker`（Task 15 要注意）。
- c. `from __future__ import annotations` 搭配函式內定義的 TypedDict，langgraph 解析註解會 NameError（`PipelineState` 在 module 層級故無事）。
- 為了讓 stop() 落在發事件之前的測試在兩種引擎上都過，legacy 與 langgraph 共用的 `_gate_enter` 加了 `try/except BaseException`，取消時呼叫新增的 `_discard_gate()`（只影響取消／失敗路徑；QA 關卡 B 重點檢查是否影響 legacy 可觀察行為）。

### 關卡 B — QA 判定 FAIL（2026-11-16，commit 09193d7），唯一阻擋：AC2 不穩
- 事件契約（S1–S12＋QA 另寫 5 個劇本）、閘門冪等、範圍、套件版本、AC7／AC8、mermaid 邊集合（20 條）全部通過；兩種引擎各 388 passed。
- 阻擋項：`test_api.py` 在 langgraph 引擎下約 13/40 失敗（legacy 0/40；dev 上 legacy 也 1/60）。根因是既有的 `core/task_watcher.py` `write_task_status` 用 `open("w")` 非原子寫入，測試讀到寫到一半的 `tasks.yaml`。QA 用 `tmp + os.replace` 驗證可從 10/30 降到 0/30。
- team lead 決定（2026-11-16）：批准超出 Task 12 範圍的修正，列為 Task 12b：`write_task_status` 改原子寫入，新測試放新檔，不改既有測試。另補特徵化劇本（AFTER_QA reject → 重做 → QA 失敗 → rework，抓「SWE 成功後不清 approval_feedback」的變異，QA 的 6 種變異中唯一漏抓的）。
- QA 其他建議：AC7 的 grep done-when 改為只查 import 語句（`config.py`／`pipeline.py` 只是含引擎名稱字串，沒有 import 洩漏）；QA 環境沒有 docker，Task 8 的 `docker compose config` 未實跑（Developer 實跑過，輸出 `PIPELINE_ENGINE: legacy`）。
- Task 11 對 legacy 的改動：正常流程零變動；唯一差異是 stop() 落在閘門登記後、發事件前時，新版清乾淨（snapshot 0 筆、approve() 回 False），舊版會留殘骸——屬於修正。

### 關卡 B 修正輪（2026-11-16）— Developer 完成，QA 複驗中（commit 1f1f731）
- Task 12b d1bb456：`write_task_status` 改原子寫入（tmp + fsync + os.replace），新測試檔 `test_task_watcher_atomic_write.py`（8 個）。`test_api` NoneType 失敗：新寫法 0/200、舊寫法 42/200。
- Task 12c a6a2912（team lead 批准，超出 12b 範圍）：`_gate_enter` 改成先寫 AWAITING_APPROVAL 狀態、成功後才登記閘門再發事件。原因：原子寫入較慢，放大了既有 `tests/test_pipeline.py` 閘門測試的競爭（`is_awaiting_approval()` 為 True 就立刻讀 tasks.yaml）；60 次量測：舊寫法 1、原子寫入含 fsync 39、不含 fsync 21、含 fsync＋重排 0。唯一可觀察差異：閘門「變成可見」的時間點從寫檔前移到寫檔後；事件名稱／data／順序、狀態寫入序列、snapshot 內容不變。
- Task 13b 1f1f731：補特徵化劇本（AFTER_QA reject → 重做 → QA 失敗 → rework），變異「SWE 成功後不清 approval_feedback」在兩種引擎都抓得到（注意 QA agent 會重新格式化失敗報告，斷言用 "Result: FAIL"）。
- 全套兩種引擎各 3 次 398 passed；耗時約 3 秒 → 約 13 秒（fsync 與新測試 300 次寫入）。
- 已知殘留 flake（未修，超出範圍）：legacy 引擎 `test_api` 100 次中 2 次失敗（test_list_deliverables_after_pipeline_run、test_approve_resumes_gated_task_to_done，訊息未保存；累計約 480 次另見過 1 次任務停在 `pending`、watcher 沒派發）；全套 20 次中 1 次 test_approve_resumes。Developer 推測是測試自己寫檔＋輪詢＋watcher 單次解析的競爭，未確證；交 QA 判斷根因。
- 疑似掛住：全套曾有一次前景指令超過 300 秒（當時指令是 `pytest | grep | head` 的迴圈、legacy 引擎、12c 之前），之後 180＋250＋40 次（含 faulthandler_timeout）都沒重現，無 stack dump；與本次改動無證據關聯，QA 複驗時多跑幾輪確認。

### 關卡 B 複驗 — QA 判定 CONCERN、無阻擋（2026-11-16，commit 1f1f731）
- 事件契約：14 個特徵化劇本＋QA 的 N1–N5，兩種引擎 data 與 prompt 逐筆相同；6 種變異全部被抓到；兩種引擎各 25 次依序＋2 次亂序皆 398 passed；掛住 50 次（faulthandler 120 秒）無法重現。
- test_api flake（失敗／執行）：dev legacy 6/60（全是 NoneType）；1f1f731 legacy 0/180、langgraph 0/180；4–6 個迴圈並行時各 2/200。殘留根因：既有 `tests/test_api.py` 的輪詢期限用 `time.time()`（:134、155、218、357、389），系統時鐘前跳時期限提早到；與 12b／12c 無關。修法（改 `time.monotonic()`）要動既有測試檔，待使用者決定。
- 12c 攻擊情境（寫檔期間 approve／stop、寫檔拋例外、連續兩個閘門任務）兩種引擎一致，且比 dev 乾淨；但 12c 沒附測試 → 派 Task 12d 補。
- 風險：`os.replace` 在 Linux inotify 只送 moved，`TaskWatcher` 沒有 `on_moved`（macOS 會補 modified）→ 派 Task 12e，Task 15（Docker）前要解決。
- `test_concurrent_readers_always_see_complete_yaml` 單一測試約 5 秒，占全套三分之一以上（未處理）。

### 關卡 B 收尾（2026-11-16）— Developer 完成，尚未經 QA 複驗（HEAD 01fd1f7）
- Task 12d 3764d9a：12c 的單元測試（兩種引擎共 4 個 case）；把 pipeline.py 還原成 12c 之前，4 個全失敗。
- Task 12e 01fd1f7：`TaskWatcher.on_moved`（只看 dest_path 是否為 tasks.yaml／TODO.md），新測試檔 7 個測試；dev 上本來就漏掉 rename 存檔。重複派發檢查：pipeline 寫回狀態送 moved 事件不會重複 enqueue（`_seen_ids` 只增不減；真實 Observer 端對端驗過）。
- 兩種引擎各 3 次 409 passed；`git diff --stat dev...HEAD` 16 個檔案 +2816/-45。
- 下一步：Task 14–15（驗收腳本、Docker 驗收），QA 驗收時一併複驗 12d／12e。

### Task 14 — Developer 完成（2026-11-16，commit d921cb1）
- 新增 `backend/scripts/` 三支驗收腳本與 `tests/test_compare_ws_events.py`（5 個測試）；兩種引擎各 3 次 414 passed。
- 本機（uvicorn、Mock 模式）兩種引擎各跑一次：都是 52 個事件＋1 個 snapshot、4 個 task:completed、task-gate 2 次 awaiting_approval。比對唯一差異：第一個 `task:created` 的 `queue_size`（legacy 5 次為 [3,0,0,0,0]，langgraph [3,3,3,3,3]）。原因是取樣時序：watcher 一次派發 4 個任務，legacy 的 worker 常搶在其餘 enqueue 之前取樣；langgraph 啟動路徑多了 await。legacy 自己也不穩定，不是契約差異。
- team lead 決定（Task 14b）：驗收腳本比對時把 `task:created.queue_size` 排除在簽章外，改印警告；測試用的特徵化簽章不變。這偏離 plan §5 的簽章規則，僅限驗收腳本。
- Task 15 的使用者指令已由 Developer 整理（docker 要用 `/usr/local/bin/docker`；必須 `--build`）。
- Task 14b ce9f47f：驗收比對排除 `task:created.queue_size`、改印警告（3 個新測試）。兩種引擎各 3 次 417 passed；本機驗收 3/3 比對 exit 0（identical: 52 events）。待辦：Task 15 Docker 驗收。

### 關卡 B 收尾複驗 — QA 判定 PASS（2026-11-16，commit 01fd1f7）
- 12e：真實 Observer＋兩種引擎，rename 存檔、移走再移回、連續 15 次 replace、pipeline 自己寫回狀態，每個任務都恰好派發 1 次。Linux inotify（Docker python:3.11-slim）實測：12e 之前三個情境都是 0 次派發（風險屬實），01fd1f7 都是 1 次。
- 12d：把 `_gate_enter` 換回舊順序，4 個案例全失敗。兩種引擎 409 passed；test_api 各 60 次 0 失敗。
- 仍未處理：既有 `test_api.py` 用 `time.time()` 算期限的時鐘跳動 flake（待使用者決定）。

### Task 15 Docker 驗收 — 通過（2026-11-16，team lead 代跑，HEAD ce9f47f）
- 在 worktree 執行（無 `.env` → Mock 模式）；主機 port 8000 被其他程式佔用，後端改對外 18000（scratchpad 的 compose override，未改 repo 內檔案）；只啟動 backend 服務，**前端容器與 HUD 畫面未驗**。
- legacy 與 langgraph 各一輪：`acceptance_run` exit 0，各 52 個事件＋1 個 snapshot、4 個 task:completed、2 次 task:awaiting_approval、0 個 task:failed；容器內 `PIPELINE_ENGINE` 值正確。`compare_ws_events.py`：`identical: 52 events`，exit 0，無 queue_size 警告。
- 發現：後端啟動時也會處理 `projects/example-project` 裡的 pending 任務，改到 worktree 的 `tasks.yaml` 並產生 `deliverables/`（違反「example-project 不變」的收尾條件）。已在 worktree 還原（主目錄未受影響）。驗收步驟應改成用獨立的 projects 目錄。
- 沒有 `.dockerignore`，`backend/.venv` 會被 COPY 進映像（未處理）。

### 合併與後續（2026-11-16～17）
- `feat/langgraph-pipeline` 合併進 dev（merge commit 4bb70ba，417 passed）。
- Task 16（預設引擎改 langgraph，c7df159）、`backend/.dockerignore`（065281f）、驗收腳本說明（a4e349a）、Task 17（`test_api.py` 改 `time.monotonic()`，b79d1e3；使用者明確同意改這一個既有測試檔）。QA 判定 PASS：三種設定各 3 次 419 passed；test_api 兩種引擎各 60 次、6 個迴圈並行共 300 次皆 0 失敗（上一輪 4/400）；Docker build 與容器啟動實測通過。已合併進 dev。
- 未處理的小問題：`backend/.dockerignore` 的 `__pycache__/`、`*.pyc` 只排除根目錄一層，應改成 `**/__pycache__/`、`**/*.pyc`。
