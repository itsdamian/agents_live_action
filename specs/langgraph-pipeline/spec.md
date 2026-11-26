# Spec：Pipeline 改造成 LangGraph

Status: CONFIRMED（2026-11-16，使用者確認）

> 相關文件：`plan.md`（架構決策）、`tasks.md`（給執行者的任務清單）。
> 本文件所有關於現有程式的描述，以 2026-11-15 讀到的 `backend/core/pipeline.py`（716 行）、`core/runtime.py`、`core/ws_manager.py`、`api/tasks.py`、`agents/base_agent.py`、`core/deliverables.py`、`tests/test_pipeline.py` 為準。

## 1. 背景

`PipelineOrchestrator`（`backend/core/pipeline.py`）用一個 `while True` 迴圈串起 PM → SWE → QA，QA 不過或人工 reject 時回到 SWE 重工（上限 `task.max_reworks`），`approval` 閘門用 `asyncio.Event` 暫停。流程控制全寫在 `process_task()` 一個方法裡，要看懂分支得逐行讀。

使用者的主要目的是**練習 LangGraph**，次要才是功能。把這個流程改寫成 LangGraph 的 `StateGraph` 剛好能用到 LangGraph 的核心概念，而且有一套現成的行為（事件、tasks.yaml 狀態、交付檔）可以拿來對照，確認改寫沒有改到行為。

## 2. 目標

1. **學習**：下列每個 LangGraph 概念在程式中都有明確的落點、`# LangGraph 學習:` 註解，以及至少一個測試示範它的語意：
   - `StateGraph` + `TypedDict` state + reducer（`Annotated[list, operator.add]`）
   - 固定邊（`add_edge`）與條件邊（`add_conditional_edges`）取代 `while` 迴圈
   - `astream(stream_mode="updates")` 驅動 graph 並觀察每個 node 的 state 更新
   - `interrupt()` + `Command(resume=...)` 實作人工核准閘門，包括「resume 後 node 會從頭重跑」的語意
   - checkpointer（`InMemorySaver`；選配階段改 `AsyncSqliteSaver`）與 `thread_id`
   - （選配）`get_stream_writer()` 自訂串流、`get_graph().draw_mermaid()`、LangChain chat model
2. **行為不變**：在 `PIPELINE_ENGINE=langgraph` 下，對外可觀察的行為與 legacy 完全相同：
   - `on_event` 的事件名稱、`data` 的 key 與值、事件順序
   - 寫回 `tasks.yaml` 的狀態序列
   - `deliverables/{task_id}/` 的檔名清單
   - `PipelineOrchestrator` 的公開介面（`enqueue`、`start`、`stop`、`process_task`、`approve`、`reject`、`is_awaiting_approval`、`snapshot`、`queue_size`、`is_budget_paused`、`station_states`、`done_count`、`failed_count`）與 `snapshot()` 的形狀
   - 預算守門（超標時 enqueue 丟棄任務、只發一次 critical `devops:alert`、進行中的任務照常跑完）
3. **可並存、可回退**：環境變數 `PIPELINE_ENGINE=legacy|langgraph`，**預設 `legacy`**，兩套引擎並存，方便對照學習與回退。

## 3. 非目標

- 不改前端（`frontend/`），不改 `core/ws_manager.py`、`api/websocket.py`、`api/tasks.py` 的行為。
- 第一階段不動 LLM 呼叫層（`core/llm_client.py`）與各 agent（`agents/*.py`）。
- 不改 `tasks.yaml` 格式、`Task` model、事件契約。
- 不引入 Postgres、Redis 或其他外部服務；不改前端框架。
- 不改變佇列與排程（`asyncio.PriorityQueue`、單一 worker、優先序 + FIFO）；佇列不放進 graph。
- 不改 DevOps agent（`agents/devops_agent.py`）的獨立監控週期。
- 不在本次把預設引擎切成 `langgraph`（由使用者在驗收後決定）。

## 4. 對使用者原始描述的修正（以程式碼為準）

| 原描述 | 實際程式碼 |
| --- | --- |
| `station:update` 的 status 有 working／done／error／rework／awaiting_approval／idle | 實際**只發** `working`、`done`、`error`、`rework` 四種。`awaiting_approval` 與 `idle` 只寫進 `station_states`（`snapshot()` 讀的那份），**不會**發 `station:update` 事件（`pipeline.py` 的 `_set_station` 只改字典）。 |
| `approve()/reject()` 變成對 graph 的 resume 呼叫 | REST 端點是同步函式，FastAPI 會在 threadpool 的執行緒呼叫 `orchestrator.approve()`，那裡沒有 event loop，也不能和正在跑的 graph 並行。改為：`approve()/reject()` 維持只記錄決定並喚醒等待者，由 event loop 上的 driver 送出 `Command(resume=...)`。詳見 plan D4。 |
| 每站成功後 `write_deliverable` | 正確，但要注意 `write_deliverable` **不是冪等的**：檔名序號（`01_`、`02_`…）取目錄中最大序號 + 1，重跑一次就多一組檔案。這是 interrupt 重跑語意下最需要防的副作用。 |
| （未提及） | `BaseAgent.run()` 失敗時會直接把 `task.status` 設為 `FAILED`（不寫檔），所以 `station:update error` 事件裡的 `task.status` 是 `failed`。SWE 的 prompt 會讀 `task.rework_count`。兩者都代表 agent 必須拿到**同一個** live `Task` 物件，這影響 state 設計（plan D3）。 |
| （未提及） | `projects/example-project/tasks.yaml` 的三個任務目前都是 `status: done`，且該資料夾有使用者尚未 commit 的修改。驗收不能直接改它，要複製一份。 |
| （未提及） | 前端是 vanilla JS（`frontend/js/*.js`），不是 `.kiro/steering/tech.md` 寫的 Vue 3；與本 spec 無關，只是記錄。 |

## 5. 驗收標準（AC）

每條都要能用指令或測試驗證。指令一律在 `backend/` 目錄下執行。

- **AC1 預設與旗標**：未設定 `PIPELINE_ENGINE` 時引擎為 `legacy`；設為 `langgraph` 時使用 LangGraph；其他值在建構 `PipelineOrchestrator` 時拋出 `ValueError`，訊息列出合法值。（`tests/test_pipeline_engine_flag.py`）
- **AC2 全套測試兩種引擎都綠**：`.venv/bin/python -m pytest -q` 與 `PIPELINE_ENGINE=langgraph .venv/bin/python -m pytest -q` 都全數通過，通過數 ≥ 基準 312 加上新增測試數，且既有測試檔（`tests/test_pipeline.py`、`tests/test_api.py`、`tests/test_usage_tracker.py` 等）**未被修改**（`git diff --quiet -- backend/tests/test_pipeline.py backend/tests/test_api.py backend/tests/test_usage_tracker.py`）。
- **AC3 特徵化測試**：`tests/test_pipeline_characterization.py` 對 plan §6 列出的 12 個劇本（S1–S12），在兩種引擎下分別斷言：事件簽章序列（plan §5 定義）、`tasks.yaml` 狀態寫入序列、交付檔名清單、最終 `Task` 欄位、`done_count`／`failed_count`，與 legacy 寫死的期望值完全相同。
- **AC4 interrupt 冪等**：帶閘門的劇本中，每次進入閘門恰好發一次 `task:awaiting_approval`、寫一次 `awaiting_approval` 狀態；resume 之後交付檔沒有重複；agent 的呼叫次數與 legacy 相同。
- **AC5 跨執行緒核准**：在 `orchestrator.start()` 啟動 worker 的情況下，從另一個 `threading.Thread` 呼叫 `approve()`／`reject()` 能讓任務繼續，兩種引擎行為相同。
- **AC6 snapshot 一致**：任務停在閘門時，`snapshot()["awaiting_approval"]` 恰有一筆、`station_states[閘門站]["status"] == "awaiting_approval"`、`is_awaiting_approval()` 為 True；決定送出後，`snapshot()["awaiting_approval"]` 與 `is_awaiting_approval()` 立刻復原，`station_states[閘門站]` 則要等閘門退出（`_gate_exit`，worker 恢復執行時）才回到 idle（2026-11-16 依關卡 A QA 實測的 legacy 行為修正：原文寫「三者同時復原」，與 legacy 不符，而本計畫的目標是行為不變）。`stop()` 在閘門等待中被呼叫時，`_awaiting` 與 `_approval_gates` 都被清空。
- **AC7 介面與邊界不變**：`git diff --quiet -- frontend backend/core/ws_manager.py backend/api/websocket.py backend/api/tasks.py backend/core/llm_client.py backend/agents` 成立（選配任務只能新增檔案，同樣不得修改上列路徑；需要修改時先問使用者）。`langgraph` 只在 `backend/core/pipeline_graph.py` 與測試中被 import（`grep -rln "langgraph" backend --include=*.py | grep -v "^backend/tests/" | grep -v "^backend/.venv/"` 只輸出 `backend/core/pipeline_graph.py`），且 legacy 引擎不 import 它（`PIPELINE_ENGINE` 未設定時 `import core.pipeline` 後 `"langgraph" not in sys.modules`）。
- **AC8 學習註記**：`grep -rn "LangGraph 學習:" backend/core/pipeline_graph.py` 至少涵蓋 StateGraph、reducer、條件邊、astream、interrupt、Command、checkpointer、thread_id 八個主題；`tests/test_langgraph_semantics.py` 有一個測試證明「resume 後含 `interrupt()` 的 node 從頭重跑」。
- **AC9 相依版本固定**：`backend/requirements.txt` 有 `langgraph==1.2.22`（2026-11-15 PyPI 最新穩定版）；`pip check` 無衝突。
- **AC10 Docker 驗收**：`docker compose up --build` 在 Mock 模式（`.env` 沒有任何 API key）下，用 example-project 的複本跑完三個示範任務，`PIPELINE_ENGINE=legacy` 與 `PIPELINE_ENGINE=langgraph` 兩次擷取到的 WebSocket 事件簽章序列相同（`scripts/compare_ws_events.py` 退出碼 0）；另加一個 `approval: after_swe` 的任務透過 REST reject 一次再 approve，兩種引擎的簽章也相同。

## 6. 開放問題

| 問題 | 本 spec 的預設 | 何時再確認 |
| --- | --- | --- |
| 驗收後是否把預設引擎切到 `langgraph`、何時刪 legacy | 不切、不刪 | AC10 通過後由使用者決定 |
| 選配任務做哪些 | 全部列出，標示選配，執行者做完必做項目後先停下來問 | 必做任務完成後 |
| 選配 O3（SqliteSaver）重啟後要不要自動恢復停在閘門的任務 | 要，這也是 interrupt + 持久化 checkpointer 真正的價值 | 開始 O3 前 |
