# Tasks：Agent 工廠現場（Live-action HUD）＋動態編制＋資料地基

Status: CONFIRMED（2026-11-17 使用者確認；第 3 版，2026-11-17）

> 依據：`spec.md`、`plan.md`（皆 DRAFT 第 3 版，使用者確認後才可開始）。括號內是對應的 AC 或 plan 決策。plan 與 spec 中的「Task N／F N」引用都以本檔為準。
> 標示：〔BE〕後端 Developer、〔FE〕前端 Developer、〔QA〕QA、〔Lead〕team lead、〔使用者〕需要使用者本人。

## 範圍與工作量（誠實估計）

| 里程碑 | 任務數 | 相對工作量 | MVP |
| --- | --- | --- | --- |
| M0 前置與凍結 | 7（2 已完成） | 3% | ✔ |
| M1 鎖行為 | 2 | 3% | ✔ |
| M2 地基（DB、checkpointer、歷史） | 17 | 22% | ✔ |
| M3 React 舞台（與 M1–M2 平行） | 13 | 20% | ✔ |
| M4 動態編制 | 7 | 11% | ✔ |
| M5 API、LLM 選擇、整合、切換前端 | 6 | 8% | ✔ |
| M6 token 串流 | 5 | 5% | |
| M6b 中斷的站接著跑（階段 2b） | 2 | 3% | |
| M7 匯入＋編制規劃 | 11 | 12% | |
| M8 多條 pipeline 序列 | 4 | 5% | |
| M9 平行 | 5 | 4% | |
| M10 拆程序＋Redis（保留，排最後） | 4 | 4% | |
| **合計** | **83** | 100% | **MVP 52 個（含 2 個已完成），約 2/3 工作量** |

**可以砍或延後的**（依建議順序；使用者 2026-11-17 定案：**M10／Redis 與 Playwright 都保留，不在可砍清單內**）：`agents/import` 端點（保留 export）；`tasks/{id}/rerun` 與 `tasks/export`；選配 O1（唯讀狀態檔）、O2（取消待辦）、O4（清孤兒資料）本來就不在 MVP；編制規劃的 `Send` 平行摘要（改單次呼叫）；多條 pipeline 的 `route` 規則（只留明確指定）；點桌子的「最近 5 筆」小卡；檔案模式的 `/history`。

## 給執行者的須知

**分工與分支（三個工作階段＋Lead）**

| 角色 | 工作階段 | 分支／worktree | 可改的範圍 |
| --- | --- | --- | --- |
| 後端 Developer（〔BE〕） | 第一個 | `feat/hud-backend` | `backend/`、`docker-compose.yml`、`compose.expose-backend.yml`、`.gitignore`、`.env.example`、README 的後端段落 |
| 前端 Developer（〔FE〕） | 第二個（新增） | `feat/hud-frontend` | `web/`（F12 起為 `frontend/`）、README 的前端段落；F12 經 Lead 授權改 `docker-compose.yml` 的 frontend 服務 |
| QA（〔QA〕） | 第三個 | 各自的唯讀 worktree | 不改程式；只寫覆核報告（可新增攻擊用的暫時測試，但不 commit） |
| Lead | team lead | — | `specs/`、派工、整合分支 `feat/live-action-hud` |

- 兩個 Developer 平行工作，介面是 plan §4.1、§6（事件）、§7（REST），在 **Task 2 凍結**。凍結前：BE 只做 Task 1、3、3b、4、5；FE 只做 F1。
- Lead 在 `feat/anthropic-provider` 合併回 `dev`（`feat/default-engine-langgraph` 已於 1478392 合併）後，從 `dev` 開兩個 Developer 的 worktree。整合由 Lead 在 QA 關卡通過後處理。

**專案慣例**

- **不要 `git push`、不要 merge。** 每完成一個 task commit 一次：`Task <N>: <摘要>`（前端 `Task F<N>: ...`），**不要**加 `Co-Authored-By`、`Generated with` 之類的行；不要改 git config。不要 `git add -A`／`git add .`。`specs/` 由 Lead 維護，不要 commit。
- 不要碰 `.claude/worktrees/` 底下不屬於你的 worktree。
- 不要還原、修改、刪除使用者未 commit 的檔案（`projects/example-project/**`、`projects/logs/`）。
- **禁改清單**（每個 task 完成時 `git diff --stat <基底> -- <路徑>` 無輸出；例外寫在該 task）：
  - 既有測試檔：`backend/tests/test_*.py`、`backend/tests/pipeline_harness.py`。新測試放新檔；共用工具放新的 `backend/tests/hud_harness.py`（可 import `pipeline_harness`，不得修改它）。
  - `backend/agents/pm_agent.py`、`swe_agent.py`、`qa_agent.py`、`devops_agent.py`；`backend/api/websocket.py`；`backend/core/ws_manager.py`（Task 41 例外）；`backend/core/deliverables.py`（只可新增函式；Task 52 例外）。
  - 舊前端 `frontend/`：F1 起**凍結**，直到 F12 一次刪除（D14）。
  - `.env`、`projects/example-project/**`。
- `backend/core/llm_client.py`、`backend/agents/base_agent.py` 可改，但必須先有 Task 4 與 `feat/anthropic-provider` 的特徵化測試，改完那些測試一字不改仍全綠。
- 文件與學習註記用繁體中文；程式碼註解英文；LangGraph 學習註記 `# LangGraph 學習: ...`。
- 回報中列「結果：」；**不要自己勾選 `[x]`**。

**真實 LLM 與費用**

- 使用者的 `.env` 有真的 `ANTHROPIC_API_KEY` 與預算上限。**所有 Docker 實跑、驗收、錄製一律 `FORCE_MOCK=true`**（plan D22，Task 3b 實作），開始前先 `curl -s http://localhost:${FRONTEND_HOST_PORT:-8080}/api/health` 確認 `"force_mock": true`，否則立刻停止。
- 不得讀取、印出、要求金鑰。會產生費用的真實呼叫只出現在標「〔使用者〕同意、會產生費用」的步驟，且由使用者本人執行。

**如何跑測試**

- 後端（worktree 的 `backend/`，venv 為 `backend/.venv`，Python 3.11；**不要**用主目錄或系統 `python3`）：
  ```bash
  .venv/bin/python -m pytest -q                                  # 單元測試：不需要任何服務
  PIPELINE_ENGINE=legacy .venv/bin/python -m pytest -q
  .venv/bin/python -m pytest -q -m integration tests/integration  # 需要 Docker（Testcontainers）；沒有 Docker 會 skip 並印原因
  ```
  基準：`dev` 1478392 為 **419 passed**（QA 記錄）。anthropic 分支合併後由 Task 1 實測，之後以它為準。
- 前端（`web/`）：`npm ci`、`npm run typecheck`、`npm run test`（Vitest）、`npm run e2e`（Playwright）、`npm run dev`（Vite；`?replay=default-run` 不需後端；接後端見 plan §11.3，預設 proxy 到 `localhost:8001`）。Node ≥24（本機 v26.4.0）。
- 測試不得連網、不得呼叫真實 LLM、不得寫入 repo 的 `projects/`。

**必須停下來問**

1. 必須修改既有事件的名稱、data 的 key／值／順序、snapshot 既有 key，或任何既有測試的期望值。
2. 需要修改禁改清單中的檔案（任務內明列的例外除外）。
3. 需要新增 plan §13 以外的依賴，或安裝會升降 fastapi／starlette／pydantic／httpx／websockets 的主版本。
4. plan §15 的 API 實測與文件不符（含 anthropic SDK 的串流細節）。
5. 特徵化（檔案模式或 DB 模式）或 golden mermaid 失敗且找不到不改契約的修法。
6. 凍結後的事件／REST／資料表需要改動。
7. Docker 任務中 `/api/health` 不是 `force_mock: true`；環境有 `LANGSMITH_*`／`LANGCHAIN_*`；任何會發出真實 LLM 請求的動作。
8. 需要連網（Task 33 的手動 `curl -sI` 例外）。
9. 單一任務超過 2 小時未達 done when。
10. 需要 push 或 merge。

**QA 關卡**

| 關卡 | 時點 | 重點 |
| --- | --- | --- |
| A | Task 16 | 檔案模式零差異（既有測試未改且綠）、DB 模式事件逐筆相同、旗標組合、`FORCE_MOCK`、匯入冪等（D3 (1) 逐列）、待辦刷新、既有專案遷移、Repository 契約、Alembic、閘門重啟續跑（含歷史只有一筆 run）、用量持久化（整合測試必須實跑） |
| B | F11 | AC8–AC11b（replay 模式；Playwright） |
| C | Task 23 | 預設編制零差異、自訂編制劇本、live 通道不污染、上游截斷標示 |
| D | Task 27 | MVP：quickstart（AC7）、AC7b、AC12–AC26、React 版在 Docker 上的 AC10／AC11 |
| E | Task 31／F13 | plan §12 階段 2 |
| E2 | Task 53 | plan §12 階段 2b |
| F | F15 | plan §12 階段 3 |
| G | F16 | 階段 4 |
| H | F17 | 階段 5 |
| I | Task 51 | 階段 6 |

---

## M0 前置與凍結

- [x] 0. 〔前一輪〕預設引擎改 `langgraph`（c7df159，前一輪 Task 16；已 QA 通過並合併，dev 1478392）。
- [x] 0b. 〔前一輪〕`test_api.py` 輪詢期限改 `time.monotonic()`（b79d1e3，前一輪 Task 17；同上）。
- [x] 0c. 〔Lead〕確認 `feat/anthropic-provider`（A1–A4）已通過 QA 並合併回 `dev`；開兩個 Developer worktree 與 QA worktree（見「分工與分支」）。— done when: 各 worktree 的 `git log -1` 都是合併後的 `dev`。
- [x] 1. 〔BE〕基準：預設引擎與 legacy 各跑兩次全套、特徵化檔一次 — done when: 「結果：」記錄數字；兩種引擎數字不同時停下來問。
- [x] 2. 〔Lead＋使用者〕凍結：使用者核准 plan §4.1、§6、§7（含 D21 的 LLM 欄位）與 §13 的確切套件版本；Status 改 CONFIRMED — done when: plan 這些章節標「FROZEN（日期）」。
- [x] 3. 〔BE〕檔案模式 `tasks.yaml` 互斥：`core/task_watcher.py` 新增 `_tasks_file_lock(path)`（以 `Path.resolve()` 為 key 的 `threading.Lock` 字典）；`write_task_status`、`append_task` 的讀改寫在鎖內；`append_task` 改 `_atomic_dump_yaml`。新增 `tests/test_tasks_yaml_lock.py`：10 執行緒各 append 5 次、另 10 執行緒對 3 個既有任務輪流寫狀態 50 次 → 任務數 53、狀態為各自最後寫入值；對照組把鎖換成 `nullcontext` 跑 20 次至少一次遺失（不到就加到 100 次並記錄）— done when: 新檔通過；全套綠。
- [x] 3b. 〔BE〕`FORCE_MOCK`（D22、AC7b）：`config.is_force_mock()`；`is_mock_mode()` 在其為真時回 True；`get_llm_client` 在任何金鑰檢查前回 Mock；`/api/health` 加 `force_mock`。新增 `tests/test_force_mock.py`：設了 `ANTHROPIC_API_KEY`＋`FORCE_MOCK=true` 時對 anthropic 設定回 `MockLLMClient`，`AnthropicClient.__init__` 被 monkeypatch 成拋錯也不會被呼叫；`true`／`1`／`yes`／大小寫；未設定時三種選擇規則與現在相同。— done when: 新檔通過；`test_config.py`、`test_llm_client*.py` 未改且綠。

## M1 鎖行為（後端）

- [x] 4. 〔BE〕BaseAgent 特徵化：新增 `tests/test_base_agent_characterization.py`（先印出核對再寫死）：成功／失敗的 `AgentResult` 欄位（`execution_time` 只斷言 ≥0）、失敗時 `task.status`、log 檔頭前 7 行鍵名、`max_retries=3`（`retry_wait_multiplier=0`）時 client 呼叫 3 次、A4 之後依 `retryable` 屬性不重試的行為（照實記錄）。`llm_client` 的特徵化已由 anthropic 分支涵蓋，不重複。— done when: 新檔通過。
- [x] 5. 〔BE〕golden mermaid：`tests/fixtures/pipeline_graph_default.mmd`（目前 `draw_mermaid()` 原文）與 `tests/test_pipeline_graph_golden.py` — done when: 通過；「結果：」記錄邊數（應為 20）。

## M2 地基（後端，階段 1a）

- [x] 6a. 〔BE〕依賴與旗標（D2、D5、D6、D23）：`requirements.txt` 加 plan §13 的 1a 套件（Task 2 核准的版本，`==` 釘死；`langgraph-checkpoint-postgres==3.2.1`）；`pip check`。`config.py` 新增 `get_task_store()`（預設 `file`）、`get_database_url()`（錯誤訊息遮蔽密碼）、`get_pipeline_checkpointer()`（預設 `memory`）、`validate_runtime_flags()`（D23 的表）並在 `create_app()` 呼叫。新增 `tests/test_runtime_flags.py`：D23 表每一列一個 case（支援的能建 app、不支援的拋 `ValueError` 且訊息含原因）、`db` 缺 `DATABASE_URL` 的訊息。— done when: 新檔通過；全套數字＝Task 1＋新測試；「結果：」貼 `pip freeze` 差異。
- [x] 6b. 〔BE〕compose 與容器（D10）：postgres 服務（先 `docker pull postgres:17-bookworm` 取得 digest，tag＋digest 同時寫進 compose 與 `backend/tests/integration/images.py`；volume、healthcheck、`127.0.0.1:${POSTGRES_HOST_PORT:-5433}`）；backend：`depends_on` healthy、**移除 `ports`**、環境變數（`TASK_STORE=db`、`PIPELINE_CHECKPOINTER=postgres`、`DATABASE_URL`、`FORCE_MOCK=${FORCE_MOCK:-false}`）、容器內 healthcheck；新增 `compose.expose-backend.yml`；frontend 埠改 `${FRONTEND_HOST_PORT:-...}`（F12 前舊前端仍是 80 埠容器，對外預設 `127.0.0.1:8080:80`）；`backend/scripts/start.sh`（`TASK_STORE=db` 時先 `alembic upgrade head`）並改 Dockerfile CMD；`backend/.dockerignore` 改為 `**/__pycache__/`、`**/*.pyc`；`scripts/acceptance_run.py` 的 `--base-url` 預設改 `http://localhost:8080`；README 的 quickstart 與「直接打 API」段落。— done when: `docker compose config` 中 backend 無 `ports`、postgres 綁 127.0.0.1；加 `-f compose.expose-backend.yml` 後 backend 綁 `127.0.0.1:8001`；`FORCE_MOCK=true docker compose up --build` 後 `/api/health`（經 8080）回 `force_mock: true`（沒有 Docker 就在「結果：」註明交 QA）。
- [x] 6c. 〔BE〕整合測試基礎：`backend/tests/integration/conftest.py`（照 job-agent：session 容器、每測試新 database、無 Docker skip、`CI=true` 失敗）、`test_smoke_postgres.py`（`SELECT 1`）、`pytest.ini`（或 `pyproject`）註冊 `integration` marker，預設執行**包含**它（沒 Docker 時 skip）。— done when: 有 Docker 時 smoke passed、沒 Docker 時顯示 skip 原因。
- [x] 7. 〔BE〕資料表與遷移（plan §4.1，1a 的表：projects、tasks、task_runs、task_run_steps、usage_records、agents、pipelines）：`backend/db/models.py`、`backend/db/session.py`、`backend/alembic.ini`、`backend/db/migrations/`（`env.py` 用同步 psycopg、`include_object` 排除 checkpointer 表）、`0001_initial`。整合測試 `tests/integration/test_migrations.py`：upgrade head、downgrade base、再 upgrade、autogenerate 無差異。— done when: 整合測試通過；單元全套不受影響。
- [x] 8. 〔BE〕Repository 介面與 InMemory 實作（D2；純重構，檔案模式行為不變）：`backend/core/stores/__init__.py`（Protocol：`TaskRepository`、`UsageRepository`、`RunRepository`）、`memory.py`、`file_tasks.py`（`FileTaskRepository` 包裝現有函式）。契約測試 `tests/stores/contract_*.py`（接收 repo factory）＋`tests/test_stores_memory.py`、`tests/test_stores_file.py`（不支援的操作 `pytest.skip`）。— done when: 新測試通過。
- [x] 9. 〔BE〕Postgres 實作：`backend/core/stores/pg.py`（`PgTaskRepository`、`PgUsageRepository`、`PgRunRepository`）；`tests/integration/test_stores_pg.py` 跑同一組契約。— done when: 整合測試通過。
- [x] 10. 〔BE〕Orchestrator 注入 `task_repo`（D2、AC1）：`PipelineOrchestrator.__init__` 加 `task_repo=None`；`_set_task_status` 在 None 時路徑不變。新增 `tests/test_characterization_db_store.py`（InMemory repo 重跑 S1–S12，事件簽章與檔案模式相同、repo 狀態序列等於檔案模式寫入序列；12c 的「先寫狀態再登記閘門」DB 版一個 case）；`tests/integration/test_characterization_pg.py` 對 Pg 跑 S1、S6、S11。— done when: 新測試通過；既有特徵化 28 case 不變。
- [x] 11a. 〔BE〕`core/task_importer.py`（D3 (1)）：純邏輯＋repo，實作表格每一列（唯一鍵、`source_hash`、pending 時更新內容、已開始忽略、檔案 status 忽略、刪除不影響、TODO.md 與 yaml id 衝突）；回傳「新插入且應 enqueue 的任務」清單，不自己 enqueue。`tests/test_task_importer.py`（InMemory；每列 ≥1 個 case）。— done when: 通過。
- [x] 11b. 〔BE〕DB 模式的啟動順序與待辦刷新（D3 (1) 啟動順序、D24、AC2 f、AC7d）：runtime 在 `TASK_STORE=db` 時把 watcher callback 換成「`call_soon_threadsafe` 交回 loop → importer → enqueue」；啟動四步（匯入不 enqueue → in_progress 標 failed → 從 DB enqueue pending → 啟動 watcher 並 `prime()`）；`_worker_loop` 的 `_refresh_from_store`。測試（InMemory）：重啟後同批 pending 只派發一次；in_progress 標 failed（`interrupted by restart`）；佇列中的任務被改標題後 `task:created` 與 PM prompt 用新值；DB 狀態已非 pending 時略過。— done when: 通過；`test_api.py` 等檔案模式測試未改且綠。
- [x] 11c. 〔BE〕DB 模式的任務 REST：`api/tasks.py` 的列表、詳情、建立在 DB 模式走 repo（檔案模式不變）；`NewTaskRequest` 新增可選 `approval`（檔案模式 `append_task` 加有預設值的參數，`none` 時不寫該鍵）；`POST /tasks/{tid}/rerun`、`GET /tasks/export`（可砍）。`tests/integration/test_api_db.py`：API 與改 yaml 兩種建任務 → 跑完 → yaml 逐 byte 與 mtime 不變；UI 建立的任務不寫 yaml；rerun 開新 run。— done when: 通過。
- [x] 11d. 〔BE〕既有專案遷移（D3 (3)、AC2b 的任務部分）：`core/project_migration.py`（`projects` 列不存在時單一交易：建列、任務與狀態、imported 的 `task_runs`、in_progress／awaiting_approval 改 failed）。測試：InMemory 版（四種狀態、TODO.md、id 衝突、交易失敗回滾後下次重試）＋整合版（第二次啟動不再遷移）；以內容與 mtime 驗證檔案未被修改。— done when: 通過。
- [ ] 12. 〔BE〕用量持久化（AC6）：`UsageTracker` 新增可選的 `store`（寫穿，**在回傳之前寫入**，確保 `usage:update` 發出時 DB 已有該列）與 `load_from(store)`；介面與既有行為不變。整合測試：記錄、重建 tracker、彙總與 `budget_status` 相同。— done when: `test_usage_tracker.py` 未改且綠；新測試通過。
- [ ] 13. 〔BE〕Postgres checkpointer（D7）：`core/checkpointing.py`；runtime startup／shutdown 管理生命週期並注入 runner；學習註記。整合測試：`setup()` 後列出實際表名（寫進「結果：」並更新 Task 7 的 `include_object`）、兩次 `setup()` 冪等。— done when: 通過；AC23 的 grep 符合。
- [ ] 14. 〔BE〕閘門重啟續跑（D7、AC5 的流程部分）：`run()` 開頭依 `aget_state` 判斷 resume 或重來；`orch._gate_register`（從 `_gate_enter` 抽出，行為不變）；`runner.resume_waiting`；runtime 恢復流程；學習註記。整合測試 `tests/integration/test_gate_restart.py`：AC5 的事件、client 呼叫次數、交付檔斷言；interrupt 後**立即**關閉再恢復（不穩就改 `durability="sync"` 並記錄）。— done when: 連跑 5 次皆過；特徵化兩模式仍綠。
- [ ] 15a. 〔BE〕`HistoryRecorder`（D20）：開 run、步驟、結束；**跨重啟接續**（找最新未結束 run、`seq` 接續）；花費以 `usage_records` 重新彙總（不累加事件數字）；`seats` 快照（M2 期間由「座位提供者」callable 給預設三站，Task 24 接上真實編制）。單元測試（InMemory）：done、failed、rework、閘門 reject、花費＝usage 加總、重建 recorder 後接續同一 run、`task:created` 時舊的未結束 run 標 superseded；整合測試：AC5 的「只有一筆 run、seq 連續、花費含重啟前」。— done when: 通過。
- [ ] 15b. 〔BE〕歷史 API：`GET /api/projects/{p}/history`（兩種模式；篩選、分頁、counts、`stale`）、snapshot `history_counts`、任務詳情 `runs` 與 `agent_outputs`（`deliverables.py` 新增 `read_agent_outputs`）。測試（InMemory＋整合：重建 runtime 後仍可列出）。— done when: 通過。
- [ ] 16. 〔BE〕`EventBus` 介面（D8）：`core/event_bus.py`（`InProcessEventBus`）；runtime 的 forwarder 改經 bus（recorder 在 bus 之前）；`devops:alert` 仍 `broadcast_all`。— done when: `test_api.py`、`test_ws_manager.py` 未改且綠。

> **QA 關卡 A**（整合測試必須在有 Docker 的環境實跑）。

## M3 React 舞台（前端，階段 1b；F1 可在凍結前開始，F2 起需 Task 2）

- [x] F1. 〔FE〕骨架（D11、D14）：`web/` 以 Vite React-TS 範本建立後整理：`tsconfig` strict、`vite.config.ts`（proxy `/api`、`/ws` 到 `VITE_BACKEND_URL` 或 `localhost:${BACKEND_HOST_PORT:-8001}`，plan §11.3）、Vitest（jsdom、jest-dom setup）、scripts（`dev`、`build`、`preview`、`typecheck`、`test`、`e2e`）、`src/styles/tokens.css`（照抄原型色票與深色模式三段）、`base.css`、空的 `App`。安裝 plan §13 的 1b 套件（Task 2 核准的版本；Playwright 只裝 chromium）。README 前端段落寫明「舊 `frontend/` 已凍結」。— done when: `typecheck`、`test`（一個 render 測試）、`build` 成功；`git diff --stat -- frontend` 無輸出。
- [x] F2. 〔FE〕型別與 replay 腳本：`src/types/events.ts`、`src/types/api.ts`（依凍結的 §6、§7，含 `llm:updated`、AgentView 的 `llm`／`effective_llm`、snapshot 的 `force_mock`／`llm`）；`public/replay/default-run.jsonl`（snapshot → 一張任務經 pm/swe/qa、phase、一次 QA 退件、after_qa 閘門、usage）、`custom-lineup.jsonl`（designer＋security、`agent:joined`、`pipeline:pending`、`llm:updated`）、`crowded.jsonl`（8 席）、`history.json`（30 筆 HistoryItem，含已被刪除 agent 的座位快照）；`src/net/guards.ts`（執行期型別檢查）與測試：每個 fixture 每行都通過 guard。— done when: `test`、`typecheck` 通過。
- [x] F3. 〔FE〕reducer（D12）：`state/reducer.ts`、`state/deskMachine.ts`、`state/selectors.ts`，移植舊 `frontend/js/state.js` 的正規化與計數。測試：每種事件 ≥1；snapshot 重建；亂序；rework 目標進 reject；閘門 waiting；cheer 計時（注入 `now`）；`usage:update`；`llm:updated` 更新各桌 effective 模型；整份 `default-run.jsonl` 餵完的最終狀態快照。— done when: 通過。
- [x] F4. 〔FE〕連線層：`net/socket.ts`（指數退避重連）、`net/replay.ts`、`net/api.ts`（typed，`fetch` 可注入）、`state/liveText.ts`、`state/FactoryProvider.tsx`、`?project=`、`?replay=`。測試：重連退避（假計時器）、replay 依 `t` 排程、api 錯誤轉 `{detail, errors}`、liveText 訂閱。— done when: 通過；`?replay=default-run` 時 console 無錯誤。
- [x] F5. 〔FE〕舞台元件（D13）：`Stage`、`Floor`、`Desk`（名牌旁的模型徽章：Opus／Sonnet／Haiku／Fable／Mock，`mock_reason` 非空時加提示）、`Avatar`（6 種造型）、`Tray`、CSS Modules（原型 keyframes）、`useReducedMotion`、`lib/layout.ts`、`lib/colors.ts`。測試：桌數與順序、`data-state`、6 種造型、徽章文字、layout 純函式（375／1280 × 3／8／24 桌）。— done when: 通過；replay 下與原型並排目視一致（截圖附回報）。
- [x] F6. 〔FE〕便條飛行：`lib/notePath.ts`、`hooks/useNoteFlight.ts`（FLIP＋`element.animate`；reduced motion 直接定位；resize 重新定位）、`Note`。測試：notePath（預設、C2 退件回 swe、閘門 reject）、座標計算純函式。— done when: 通過。
- [x] F7. 〔FE〕泡泡與桌上閘門：`Bubble`（`lib/typewriter.ts`、reduced motion 一次輸出、只顯示最後兩行；MVP 只有 phase 文字）、`GateControls`（可以／重做＋意見、送出中鎖定、409「這張已經不在等你了」、replay 送假事件）。測試：點擊與鍵盤、api 參數、409。— done when: 通過。
- [x] F8. 〔FE〕新增／移除 agent：`AddAgentForm`（plan §4.2 的欄位與限制，另有「模型：跟專案預設／Mock／Claude …」選單）、`Bench`（回座、刪除自訂）、桌上「×」（`PUT /pipelines/main`）、202 標記、`agent:joined` 的 hello。測試：表單驗證、API 呼叫（含 `llm` 欄位）、202 標記。— done when: 通過。
- [x] F9a. 〔FE〕`Header`（專案選擇、`[MOCK MODE]`／`force_mock`／缺金鑰原因、連線狀態、「模型」選單——切到 Claude 前確認對話框「會產生費用」、呼叫 `PUT /settings`、202 時標「下一張開始」）、`StatsBar`（完成、重做、進行中、今日花費）、`ConnectionBanner`、`Toasts`。測試：每個元件 ≥1 互動測試；模型選單的確認對話框與 API 參數。— done when: 通過。
- [x] F9b. 〔FE〕`TaskForm`（「派一張任務」；核准時機選項「不用」「做完後先給我看」「全部通過後給我看」→ `none`／`after_swe`／`after_qa`，AC11b）、`TaskDetail`（log、交付檔下載、核准、各 agent 產出、執行紀錄）。測試：選項文字與送出值、詳情各分頁。— done when: 通過。
- [x] F9c. 〔FE〕`DevOpsDrawer`（機房按鈕、紅點、critical toast）、`Sheet`（≤640 px 全螢幕、返回、Esc、焦點鎖定）。測試：Sheet 鍵盤與焦點、警示分級。— done when: 通過；plan §11.7 每列在 replay 下可操作（回報逐列列出）。
- [x] F10. 〔FE〕便條牆（D20）：`NoteWall`（點匣子開啟、5 種狀態篩選、每頁 50、載入更多、點卡片開 `TaskDetail`、已刪除 agent 以座位快照顯示）。— done when: 通過。
- [ ] F11. 〔FE〕行動版、a11y 與 Playwright（plan §11.5、§11.6、AC10）：精簡模式、≤520／≤640 斷點、觸控區 ≥44 px、`aria-live`、safe-area；`playwright.config.ts`（chromium、`vite preview`）、`tests/e2e/smoke.spec.ts` 覆蓋 AC10 a–f。— done when: `npm run e2e` 全過。

> **QA 關卡 B**。

## M4 動態編制（後端，階段 1c）

- [ ] 17. 〔BE〕`backend/models/lineup.py`（plan §4.2）：`LOOKS`、`AGENT_ID_RE`、`RESERVED_IDS`、`Seat`、`Lineup`、`DEFAULT_LINEUP`、`BUILTIN_SEATS`、`AgentView`（含 `llm`、`effective_llm`）、`validate_agent_fields`、`derive_agent_id`、`validate_lineup`、`validate_llm_choice`（D21 白名單；`api_key_env` 出現即錯）。`tests/test_lineup_model.py`：id 合法 5 例、不合法 ≥10 例、display_name 控制字元、derive、validate_lineup、validate_llm_choice（白名單、未知 model、provider 非 mock／anthropic、帶 `api_key_env`）；白名單每個 model 在 `MODEL_PRICES` 有價格。— done when: 通過。
- [ ] 18. 〔BE〕`LineupRepository`（D4）：Protocol＋InMemory＋Pg（表已在 0001）；`core/lineup_yaml.py`（`agents` 表沒有該專案列時從 `agents/*.yaml` 匯入——含 D21 的 LLM 對應表與 `migration_notes`；`export_yaml`；`import_yaml` 整份取代、全部合格才寫）。契約測試兩種實作；YAML 來回測試；LLM 對應表每列一個 case；匯入不修改 yaml（mtime 與內容）。— done when: 單元與整合測試通過。
- [ ] 19. 〔BE〕`CustomAgent`、`parse_verdict`、上游截斷、Mock 角色（D17、plan §4.4、AC15、AC26）：`agents/custom_agent.py`（含 `truncate_upstream` 與截斷標記）；`llm_client.py` 的 `role_key`、`make`／`check` renderer、`MOCK_CHECK_FAIL_FIRST`、`get_llm_client(..., mock_role_key=None)`。`tests/test_custom_agent.py`（截斷：未超過時 prompt 逐字不變、超過時有標記且 log 檔含該行）、`tests/test_verdict.py`（≥10 case 含 injection）；自訂 agent 設成 anthropic 時以假 SDK 物件跑一次。— done when: 通過；Task 4 與 anthropic 分支的特徵化測試未改且綠。
- [ ] 20. 〔BE〕路由工廠（plan §5.3）：`make_seat_router`、`make_gate_router`、`make_rework_router`、`*_paths`、`gate_seat_index`、`prev_maker`；`route_after_*` 不動。`tests/test_lineup_routing.py`：與 `route_after_*` 的等價表、C1–C6 分支表、預設 path_map、含 `-` 的 node 名稱可 compile 與 `draw_mermaid`（§15 待驗證項）。— done when: 通過。
- [ ] 21. 〔BE〕`build_lineup_graph`（plan §5.1、§5.2）：自訂 seat node、內建 node 的 context 擴充、`_enter_rework(..., station=...)`、graph 快取、`with_phases` 先直接回傳 node。— done when: golden、特徵化（兩模式、兩引擎）、shape、routing 全綠；`core/pipeline.py` 的 diff 只有 `_enter_rework` 參數與 Task 10／11b／14 的改動。
- [ ] 22. 〔BE〕Orchestrator 編制（D17、AC14、AC18）：`lineup`、`extra_agents`、`in_flight_task_id`、`request_lineup`、`_pending_lineup`（同步 `pipelines.pending_seats`）、legacy 收到非預設編制時改用預設並 warning。`hud_harness.py`（`ScriptedCustomClient`、`make_lineup`）與 `tests/test_dynamic_lineup.py`（plan §9 C1–C6：先印出、逐行核對錨點、再寫死；`request_lineup` 的 now／next_task）。— done when: 通過；legacy 下動態劇本以 skipif 標示。
- [ ] 23. 〔BE〕live 通道與 `agent:phase`（D16、AC13、AC24）：`on_live_event`、`_emit_live`、編制事件、`with_phases`、`phase_text()`、driver 的多 stream mode；學習註記。`tests/test_live_events.py`：有／無 live 時 `on_event` 相同（S1、S2、S6、C2）、每站 3 個 phase、verdict 欄位、只用 updates 時 writer 不拋錯（§15 待驗證項）、`phase_text` 表格、編制事件 data 形狀。— done when: 通過。

> **QA 關卡 C**。

## M5 API、LLM 選擇、整合、切換前端（階段 1c）

- [ ] 24. 〔BE〕Runtime 接線（AC21）：DB 模式下 roster 與編制讀 `LineupRepository`、建 `CustomAgent`、`on_live_event` → `EventBus`、`LiveState`、recorder 的座位提供者接真實編制、snapshot 新 key（`engine`、`task_store`、`force_mock`、`agents`、`pipelines`、`live`；`llm` 由 Task 25b 填）。`tests/test_runtime_lineup.py`（InMemory）：snapshot 既有 key 與值和改動前錄的相同、新 key 正確。— done when: 通過；`test_api.py` 未改且綠。
- [ ] 25a. 〔BE〕REST：agents 與 pipelines（plan §7.1、AC16–AC19）：`api/agents.py`（agents CRUD 含 `llm` 欄位、export／import、pipelines）。`tests/test_api_agents.py`（InMemory）＋`tests/integration/test_api_agents_pg.py`：所有 422／409（含 `api_key_env`）、legacy 或檔案模式 409、202 延後、WS 收到 `agent:joined`／`pipeline:updated`、不合格輸入時 DB 無殘留。— done when: 通過。
- [ ] 25b. 〔BE〕LLM 選擇（D21、AC25）：`resolve_llm` 純函式；`GET`／`PUT /settings`；設定或 agent 的 `llm` 變更時重建 agent 實例並走 `request_lineup`（200／202）；`llm:updated` 事件；snapshot `llm`；staffing 之後會用的 `project_llm_client()` helper。測試：`resolve_llm` 表格（agent／project 來源、四種 `mock_reason`）、PUT 後下一張任務以假 SDK 物件驗證送出的 `model`、個別指定不受影響、202 時機、缺金鑰時 effective 為 Mock。— done when: 通過。
- [ ] 26. 〔BE＋使用者〕Docker 實跑與錄製（**`FORCE_MOCK=true`**）：`FORCE_MOCK=true docker compose up --build` → 確認 `/api/health`（經 8080）`force_mock: true` → 用 curl（經 8080）建 check 型自訂 agent（`MOCK_CHECK_FAIL_FIRST=true`）、把專案預設 LLM 設成 `claude-sonnet-5-5`（驗證畫面與 snapshot 顯示 `mock_reason: force_mock`，實際不呼叫）→ 建兩個任務（一個 after_qa）→ `backend/scripts/record_ws.py`（base URL 預設 `http://localhost:8080`；閘門先 reject 再 approve）錄事件 → 讓一個 after_swe 任務停在閘門時 `docker compose restart backend`，確認續跑且歷史只有一筆 run → 轉成 replay 格式交 Lead（給 FE 放 `web/public/replay/recorded-mvp.jsonl`）。只用 `projects/hud-accept/`，結束後刪除。— done when: 錄到 `agent:phase`、`agent:joined`、`llm:updated`、自訂 station 的 rework、閘門、終止事件；重啟後閘門續跑成功；`GET /history` 在重啟後仍有資料。
- [ ] F12. 〔FE〕接真後端並切換（D14、AC22）：跑 `recorded-mvp.jsonl` replay 並修正落差（落差若在後端，回報 Lead，不改後端）；`web/Dockerfile`（多階段、釘 digest）、`web/nginx.conf`（8080、`/api`、`/ws`、SPA fallback）；**同一個 commit**：刪除舊 `frontend/` 全部檔案、`git mv web frontend`、改 `docker-compose.yml` 的 frontend 服務（Lead 授權；`127.0.0.1:${FRONTEND_HOST_PORT:-8080}:8080`）、更新 README。— done when: `git ls-files frontend` 只有 React 專案檔；`FORCE_MOCK=true` 的 Docker 上 AC10 與 AC11 通過。
- [ ] 27. 〔Lead＋使用者〕MVP 驗收：Lead 把兩分支合到 `feat/live-action-hud`（不 push、不進 dev）；乾淨環境 quickstart（AC7）；使用者在 `FORCE_MOCK=true` 下操作：新增設計師與資安、派任務、桌上 reject／approve、移除進行中的 agent、切換模型（看到「實際以 Mock 執行」）、閘門中重啟、手機寬度操作、看便條牆。〔使用者〕同意、會產生費用（選做）：取消 `FORCE_MOCK`，把專案預設設成 `claude-haiku-5-5`，跑一張任務確認真實呼叫與花費顯示。— done when: spec AC1–AC26 每條都有證據（測試名稱或截圖）；使用者決定進入階段 2。

> **QA 關卡 D**（MVP 結束）。

## M6 階段 2：token 串流（Mock＋Anthropic）

- [ ] 28. 〔BE〕`LLMClient.stream` 基底與 `MockLLMClient.stream`（D15；經過 `self.call`、8 字元一塊、`MOCK_STREAM_CHUNK_DELAY_MS`）。測試：拼回等於 `call`、`last_usage` 相同、覆寫 `call` 的 scripted client 經 stream 仍生效。— done when: 通過；特徵化綠。
- [ ] 29. 〔BE〕`AnthropicClient.stream`（D15）：**先讀已安裝 anthropic SDK 的原始碼**，把 `messages.stream`／`beta.messages.stream` 的參數、`text_stream` 的內容（是否排除 thinking）、`get_final_message()`、fallback 在串流中的表現、串流中途的例外型別寫進「結果：」，與 plan §15 不符就停下來問。實作沿用 `_build_request`、stop_reason 檢查、`_usage_from`。測試用假 SDK 物件：參數逐 key、refusal／max_tokens 在部分輸出後拋出、usage 加總、不連網。— done when: 通過。
- [ ] 30. 〔BE〕`BaseAgent.run(..., *, on_token=None)`、`_run_station(..., *, on_token=None)`（只在非 None 時傳）。測試：有／無 sink 的 `AgentResult` 相同；第一次嘗試失敗時 sink 收到 attempt 1、2。— done when: 通過。
- [ ] 31. 〔BE〕token pump 與 `agent:token`（D16）：thread-safe queue、可注入時鐘、節流、上限、`final`／`truncated`。`tests/test_token_events.py`：事件數 ≤40、`seq` 遞增、`final` 恰一、拼接等於輸出（未截斷）、`on_event` 不變；Mock 與假 Anthropic 各一。— done when: 通過。
- [ ] F13. 〔FE〕`agent:token` 進 `liveText`、新 `attempt` 清空、error 時標「中斷」、reduced motion 每 500 ms 更新；`public/replay/tokens.jsonl`。— done when: 測試通過。

> **QA 關卡 E**。

## M6b 階段 2b：重啟後從中斷的那一站接著跑（plan §12）

- [ ] 52. 〔BE〕交付檔冪等（前置）：`core/deliverables.py` 新增以 `(task, station, run_no, attempt)` 決定檔名的寫法（**本任務可改 `deliverables.py`**，舊函式保留供檔案模式與既有測試）；DB 模式的 seat node 改用新寫法。測試：同一 `(station, run, attempt)` 寫兩次檔案不增加；檔案模式既有交付檔測試未改且綠。— done when: 通過。
- [ ] 53. 〔BE〕續跑：啟動時 in_progress 任務若 checkpoint 的 `next` 為 seat node 就 `astream(None, config)` 續跑（否則照 MVP 標 failed）；歷史步驟標「重啟後重做」、新的 `usage_records`；學習註記（`durability`、pending writes、`astream(None, ...)`、與 interrupt 恢復的差異）。整合測試：SWE 執行中關閉 runtime → 重啟 → PM 不重跑、SWE 重跑一次、任務 done、交付檔無重複、歷史一筆 run、花費含兩次 SWE。— done when: 連跑 5 次通過。

> **QA 關卡 E2**。

## M7 階段 3：匯入＋編制規劃

- [ ] 32. 〔BE〕`TaskWatcher` 忽略第一層為 `source` 的路徑（`_process_file_if_watched`、`_existing_task_files`）；新測試檔（scan_now 與真實 Observer）。— done when: 通過；既有 watcher 測試綠。
- [ ] 33. 〔BE〕GitHub 下載手動驗證＋網址解析：先 `curl -sI https://codeload.github.com/octocat/Hello-World/tar.gz/master`、`curl -sI https://github.com/octocat/Hello-World/archive/HEAD.tar.gz`、`curl -s https://api.github.com/repos/octocat/Hello-World | grep default_branch`，結果寫進「結果：」（與 plan §8 假設不同就停下來問）；`core/importer.py` 的 `parse_github_url`（≥15 正反例測試）。— done when: 通過。
- [ ] 34. 〔BE〕本機匯入（plan §8）：`resolve_local_source`、`copy_local_tree`、`config.get_import_root()`；compose 唯讀掛載 `${IMPORT_ROOT:-./imports}`、`.gitignore` 加 `imports/`。測試：`..`、絕對路徑、symlink 逃逸（目錄與檔案）、`.env`、`node_modules`、檔數／單檔／總量上限、失敗不留目錄。— done when: 通過。
- [ ] 35. 〔BE〕GitHub tarball：`download_github_tarball`（transport 可注入、手動轉址白名單、30 MB／60 秒）、`extract_tarball`（只取 regular file、去第一層、記 sha、`..`／絕對路徑／連結／裝置檔略過）。測試以 `httpx.MockTransport` 與測試內產生的惡意 tar.gz；以 monkeypatch `socket` 證明不連網。— done when: 通過。
- [ ] 36. 〔BE〕匯入工作：`imports` 表 migration（0002）、`ImportRepository`、`ImportService`（Semaphore、`.import-<uuid>`、rename、`ensure_project`、`import:progress`）、`api/imports.py`。測試（InMemory＋整合）：成功、衝突 409、`IMPORT_ROOT` 未設 409、失敗無殘留、匯入的 repo 含 pending 任務時佇列為空。— done when: 通過。
- [ ] 37. 〔BE〕掃描：`core/staffing_scan.py`（純函式、總量上限）與三種假 repo 的測試。— done when: 通過。
- [ ] 38. 〔BE〕提案 schema 與 Mock 規則：`models/staffing.py`（`extra="forbid"`）、`validate_proposal`、`extract_json_block`、`mock_proposal`；測試（Mock 結果逐字、≥10 種不合格、JSON 擷取邊界）。— done when: 通過。
- [ ] 39. 〔BE〕編制規劃 graph（D19）：`staffing_proposals` 表（0003）、`ProposalRepository`、`core/staffing_graph.py`、`StaffingService`（Postgres checkpointer、thread_id；LLM 用 Task 25b 的 `project_llm_client()`）；學習註記（`Send`、map-reduce、重試迴圈、可編輯 interrupt、再次 interrupt）。測試：Mock 停在 review 且無寫入；scripted LLM 3 次壞 JSON → fallback；accept 寫入；不合格修改再停（§15 待驗證項）；reject；沒有文件跳過 summarize；injection 樣本不影響 provider；專案預設改為 Claude（假 SDK）時 propose 用該 model；整合測試：停在 review 時重建 service 仍可 accept。— done when: 通過；AC23 grep 加上 `staffing_graph.py`。
- [ ] 40. 〔BE〕staffing API 與事件（plan §7.2）；測試（start → awaiting_review → accept → applied；重複 decision 409；預算暫停 409；同專案第二個 start 409）。— done when: 通過。
- [ ] F14. 〔FE〕匯入對話框（本機下拉＋子路徑、GitHub 網址＋分支、`lib/importRules.ts` 與後端同規則並測試、進度、完成後切換專案）。— done when: 測試通過；`FORCE_MOCK=true` 的 Docker 下完成一次本機匯入。
- [ ] F15. 〔FE〕提案審查（候選桌子半透明、理由與 fallback 標記、逐位編輯（含模型）、增刪、排序、422 錯誤留在畫面、接受後 `agent:joined`）。— done when: 測試通過；`FORCE_MOCK=true` 的 Docker 下完成「匯入 → 評估 → 修改 → 接受」。

> **QA 關卡 F**。

## M8 階段 4：多條 pipeline（序列）

- [ ] 41. 〔BE〕多條編制與選線：`validate_lineup` 放寬到 ≤3 條、恰一條 default；`route` 規則；`tasks.pipeline_id`；選線純函式與表格測試；envelope `pipeline_id`（**本任務可改 `core/ws_manager.py`**：`make_event`／`broadcast` 加有預設值的參數，None 時不加 key）。— done when: `test_ws_manager.py` 未改且綠；新測試通過。
- [ ] 42. 〔BE〕orchestrator 依任務選線、graph 快取、`current_pipeline_id`、snapshot `pipelines[].stations`；測試兩條線交錯派發的事件與 envelope。— done when: 通過。
- [ ] 43. 〔BE〕`POST`／`DELETE /pipelines`、任務建立的 `pipeline`；測試。— done when: 通過。
- [ ] F16. 〔FE〕樓層（一條線一層、手機分頁切換、有動靜的分頁圓點）、同一 agent 多層分身、便條牆依線分段、3×8 桌版面（AC10 d）。— done when: 測試與 e2e 通過。

> **QA 關卡 G**。

## M9 階段 5：平行（每條線同時各一張；同一條線內一次一張）

- [ ] 44. 〔BE〕`ProjectPipelines` 外觀物件：每條線一個 orchestrator（各自佇列與 worker）、`enqueue` 選線、`approve`／`reject` 找持有閘門的線、合併 snapshot（`pipeline` 仍為 default 線）。測試（InMemory）：既有介面行為不變。— done when: 通過；`test_api.py` 綠。
- [ ] 45. 〔BE〕每線獨立 agent 實例、watcher／importer 的 callback 改走外觀物件、啟動恢復依 `pipeline_id` 分派、`_refresh_from_store` 在各線 worker 都生效。— done when: 測試通過。
- [ ] 46. 〔BE〕`ProjectBudgetGuard`（sticky、只發一次 critical）；用量寫入以 `pg_advisory_xact_lock` 序列化。— done when: 兩線同時超標只一則 alert 的測試通過。
- [ ] 47. 〔BE〕整合測試：兩線各一個閘門任務同時停住並分別 approve；兩線 50 個任務無狀態遺失；同一條線同時只有一張在跑；共用 `AsyncPostgresSaver` 的併發（§10.2 待驗證項）。— done when: 連跑 5 次通過。
- [ ] F17. 〔FE〕多張便條同時飛（每層一張）、reducer 依 envelope `pipeline_id` 分流的測試。— done when: 通過。

> **QA 關卡 H**。

## M10 階段 6：拆程序＋Redis（使用者定案保留、排最後；開始前 Lead 依當時程式碼展開任務）

- [ ] 48. 〔BE〕設計確認：snapshot 由 api 向 worker 請求或由 worker 寫入 Redis key（plan §10.3）——寫成短提案給 Lead／使用者選；`gate_decisions` 表（0004）。— done when: 決定寫回 plan。
- [ ] 49. 〔BE〕redis 服務（釘 digest、不對外、healthcheck）、`redis` 依賴、`RedisEventBus`、`tests/integration` 的 Redis 容器 fixture。— done when: 整合測試：publish → subscribe 轉 WS。
- [ ] 50. 〔BE〕`ControlBus`、`worker` 服務（同映像不同指令）、api 只留 REST／WS；approve 寫 `gate_decisions` 後 publish，worker 啟動與重連時補讀。— done when: 整合測試：worker 重啟期間送出的 approve 在其起來後生效。
- [ ] 51. 〔BE＋使用者〕Docker 實跑（`FORCE_MOCK=true`）：兩程序下錄的事件簽章與單程序（Task 26 的錄製）相同（`compare_ws_events.py`）；api 重啟期間 worker 繼續跑。— done when: 比對 exit 0。

> **QA 關卡 I**。

---

## 選配（不在 MVP；開始前先問）

- [ ] O1. 〔BE〕唯讀狀態檔（plan §17 O1）：DB 狀態節流後輸出成 `projects/<p>/tasks.status.yaml`，檔頭註明自動產生；測試：watcher 不會匯入它、tasks.yaml 不被修改。
- [ ] O2. 〔BE＋FE〕取消待辦任務（plan §17 O2）：`DELETE /tasks/{tid}`（只限 pending）、佇列項目略過；便條卡片上的「取消」。
- [ ] O4. 〔BE〕清除孤兒專案資料（plan §17 O4）：`scripts/prune_projects.py --dry-run／--yes`；測試。
