# Tasks：Pipeline 改造成 LangGraph

Status: CONFIRMED（2026-11-16，使用者已確認 spec 與 plan，可開始執行）

> 依據：`spec.md`、`plan.md`（皆為 CONFIRMED）。括號內是對應的 spec AC 或 plan 決策／章節。
> 標 **〔選配〕** 的任務不在必做範圍；必做任務（1–15）全部完成後，先停下來問使用者要不要做選配。

## 給執行者的須知

**專案慣例**

- **不要 `git push`、不要 merge 進 `dev`／`main`。** 例外（2026-11-16 team lead 依「Sonnet 執行 + QA 覆核」流程加註）：你在獨立 worktree 的功能分支 `feat/langgraph-pipeline` 上，**每完成一個 task 就 commit 一次**（QA 要靠 commit 驗證）。commit 訊息格式 `Task <N>: <摘要>`，**不要**加 `Co-Authored-By` 或 `Generated with` 之類的行；身分已由 repo-local config 設為 itsdamian，不要改任何 git config。不要用 `git add -A`／`git add .`，只明確列出該 task 改到的檔案；`specs/` 由 team lead 維護，不要 commit。
- 工作目錄裡已有使用者尚未 commit 的修改（`projects/example-project/agents/*.yaml`、`projects/example-project/tasks.yaml`、未追蹤的 `projects/logs/`）。**不要還原、不要修改、不要刪除它們。**
- 不改 `frontend/`、`backend/core/ws_manager.py`、`backend/api/websocket.py`、`backend/api/tasks.py`、`backend/core/llm_client.py`、`backend/agents/`。每個任務完成時都跑一次：`git diff --stat -- frontend backend/core/ws_manager.py backend/api/websocket.py backend/api/tasks.py backend/core/llm_client.py backend/agents` 必須沒有輸出。
- 既有測試檔（`backend/tests/test_*.py`，本計畫新增的除外）**不得修改**。
- 文件用繁體中文；程式碼註解沿用專案風格（英文），但 LangGraph 學習註記用 `# LangGraph 學習: ...`（繁中）。
- 每完成一項，把 `- [ ]` 改成 `- [x]`，並在該項下方縮排加一行「結果：…」（通過的測試數、遇到的差異）。

**開始前先讀**（依序）

1. `specs/langgraph-pipeline/spec.md`、`plan.md`（特別是 D3、D4、D5、§7、§8）
2. `backend/core/pipeline.py`（全檔，716 行）
3. `backend/tests/test_pipeline.py`（測試寫法：`asyncio.run`、`MockLLMClient` 子類別、`_wait_for_gate` 輪詢）
4. `backend/agents/base_agent.py` 的 `run()`、`backend/agents/swe_agent.py` 的 `_build_prompt`
5. `backend/core/deliverables.py` 的 `write_deliverable`（注意序號不是冪等的）
6. `backend/core/runtime.py` 的 `ensure_project`、`_make_event_forwarder`
7. `backend/tests/test_usage_tracker.py` 第 339 行以後（pipeline 的 usage／預算測試）

**如何跑測試**（已實際確認，2026-11-15）

- 虛擬環境在 `backend/.venv`（Python 3.11.15，pytest 9.1.1，pytest-asyncio 1.4.0）。系統的 `python3` 是 3.14 且沒有裝 fastapi，**不要用**；`pytest` 指令也不一定在 PATH 上。
- 一律在 `backend/` 目錄下執行：

  ```bash
  cd backend   # 從 repo 根目錄
  .venv/bin/python -m pytest -q                          # 全部
  .venv/bin/python -m pytest tests/test_pipeline.py -q   # 單檔
  PIPELINE_ENGINE=langgraph .venv/bin/python -m pytest -q   # Task 8 之後才有意義
  ```

- **基準結果（2026-11-15 實跑）**：`312 passed, 1 warning in 2.65s`；其中 `tests/test_pipeline.py` 為 `15 passed`。唯一的 warning 是 `StarletteDeprecationWarning`（starlette testclient 建議改用 httpx2），與本計畫無關，不要處理。
- 測試不得連網、不得呼叫真實 LLM。

**遇到下列情況必須停下來問使用者**（不要自行決定）

1. 為了讓測試通過，必須改變事件名稱、事件 `data` 的 key 或值、事件順序、`snapshot()` 的形狀、`approve()`／`reject()` 的簽章或回傳值。
2. 需要新增 `langgraph==1.2.22` 以外的依賴（選配 O3、O4 會明確寫出需要的套件，仍須先問），或 `pip install` 會升級／降級 fastapi、starlette、pydantic、httpx、websockets 的主版本。
3. 需要修改「專案慣例」列出的禁改檔案或既有測試檔。
4. `plan.md` §9 標「已確認」或「待執行者驗證」的 API，實測行為與文件不符。
5. 特徵化測試在 legacy 上跑出的結果與 `plan.md` §6 的描述有**行為層面**的差異（不只是字串細節）。
6. LangGraph 引擎的結果與 legacy 不同，且找不到不改契約的修法。
7. Docker 無法啟動、`.env` 裡有任何 API key（`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY` 有值），或環境中有 `LANGSMITH_*`／`LANGCHAIN_*` 變數。
8. 要把預設引擎切成 `langgraph`、或刪除 legacy 程式碼。
9. 任何單一任務做超過 2 小時仍未達成 done when。
10. 需要 push、或 merge 進 `dev`／`main`。

**執行模式與 QA 關卡**（2026-11-16 team lead 加註）

- Developer（`ala-developer`，Sonnet）與 QA（`ala-qa`，Opus）各自在獨立 iTerm 視窗與 worktree 工作；team lead 是 session `personal-workplace-d4`，用 `SendMessage` 派工與收回報。
- Developer 的 worktree 是 `.claude/worktrees/ala-developer`（分支 `feat/langgraph-pipeline`，基底為 `dev` 的 b2fece5）。`specs/` 在該 worktree 內是指回主目錄的符號連結；`backend/.venv` 是 worktree 內自建的，測試指令照上面的路徑（相對於 worktree 的 `backend/`）。
- 因為 tasks.md 由 team lead 維護，**你不要自己勾選 `[x]`**，改為在回報中列出結果（通過的測試數、遇到的差異），由 team lead 更新。
- **QA 關卡**：關卡 A = Task 1–6 完成後（鎖行為的測試與 legacy 純重構是否真的沒改行為）；關卡 B = Task 7–13 完成後（LangGraph 引擎是否與 legacy 事件契約一致）。到關卡就**停下來回報**，等 team lead 說 QA 通過再繼續。Task 14–15 另行指派。
- Task 15 需要 Docker、`.env`，是主目錄使用者環境的事，不在 worktree 自行執行；做到那一項時回報 team lead。

---

## M0 基準

- [x] 1. 〔Dev〕確認基準：照「如何跑測試」跑一次全套與 `tests/test_pipeline.py` — done when: 結果與上方基準相同（312 passed、15 passed）；若不同，把實際數字寫在「結果：」並停下來問使用者。

## M1 特徵化測試（只在 legacy 上，鎖住現有行為）（D10、AC3）

- [x] 2. 〔Dev〕測試工具 `backend/tests/pipeline_harness.py`（新增，不是 test 檔，pytest 不會收集）（plan §5、§6）。內容：
  - `ScriptedClient(MockLLMClient)`：建構子收 `role`、`name`、`script: list[str | Exception | None]`；第 i 次 `call` 若 `script[i]` 是字串就回傳、是例外就 raise、是 `None` 或超出長度就回 `super().call(...)`；把每次的 prompt 存在 `self.prompts`。
  - `QA_FAIL_REPORT`：與 `tests/test_pipeline.py` 的 `SequencedQAClient` 相同的 FAIL 報告字串。
  - `make_task(task_id="task-001", **overrides)`、`make_project(tmp_path, tasks)`：照 `test_pipeline.py` 的 `_make_task`、`_make_project` 複製（tasks.yaml 也要寫 `approval`，若 task 有設定）。
  - `make_orchestrator(project, *, engine="legacy", pm_client=None, swe_client=None, qa_client=None, usage_tracker=None)` → `(orchestrator, events)`；`events` 是 `on_event` 收到的 `(event, data)` list，`write_status=True`、`write_deliverables=True`。**本任務先 `assert engine == "legacy"` 且不把 engine 傳給建構子**（參數要到 Task 8 才存在）。
  - `event_signature(event, data)`：完全照 plan §5；`signatures(events)` 回傳 list。
  - `install_status_recorder(monkeypatch) -> list[str]`：把 `core.pipeline.write_task_status` 換成包裝函式，記下 `status.value` 後呼叫原函式。
  - `deliverable_names(project, task_id) -> list[str]`：排序後的檔名，目錄不存在回 `[]`。
  - `async run_with_gate_actions(orch, task, actions, on_pause=None) -> Task`：`asyncio.create_task(orch.process_task(task))`；對 `actions` 裡每個 `("approve", "")` 或 `("reject", note)`：輪詢 `orch.is_awaiting_approval(task.id)`（每 5ms，最多 500 次，逾時 `AssertionError`），若給了 `on_pause` 就先呼叫 `on_pause(orch, task)`（用來在暫停時斷言 snapshot），再呼叫 `orch.approve` 或 `orch.reject` 並斷言回傳 True；最後 `await asyncio.wait_for(proc, timeout=10)`。
  — done when: 新增 `tests/test_pipeline_harness_smoke.py` 一個測試，用 harness 跑 S1 並斷言 `result.status is Status.DONE`；`.venv/bin/python -m pytest tests/test_pipeline_harness_smoke.py -q` 通過；全套仍 313 passed。

- [x] 3. 〔Dev〕`backend/tests/test_pipeline_characterization.py`（新增）劇本 S1–S5（無閘門）（plan §6）。檔案頂端 `ENGINES = ["legacy"]`，每個測試用 `@pytest.mark.parametrize("engine", ENGINES)`。期望值的取得方式：先在 legacy 上 `print(signatures(events))` 跑一次，**逐行核對**是否符合下面的錨點與 plan §6 的重點，再把結果寫死成 list literal，然後刪掉 print。錨點（由程式碼推得，S1）：
  - 事件 (event, station, status, task.status)：`task:created`(—, —, `pending`) → `station:update`(pm, working, in_progress) → (pm, done) → (swe, working) → (swe, done) → (qa, working) → (qa, done) → `task:completed`(—, —, `done`)。注意 `task:created` 的 `task.status` 是 `pending`，因為事件在寫 `in_progress` **之前**發出。
  - 狀態寫入：`["in_progress", "done"]`；交付檔：`["01_PM_agent.md", "02_SWE_agent.md", "02_SWE_agent_1.py", "03_QA_agent.md"]`。
  每個劇本斷言：簽章序列、狀態寫入序列、交付檔名、`result.status`、`result.rework_count`、`done_count`、`failed_count`、各 client 的 `len(prompts)`。
  — done when: `.venv/bin/python -m pytest tests/test_pipeline_characterization.py -q` 5 passed；全套 318 passed。

- [x] 4. 〔Dev〕同檔加入閘門劇本 S6–S10、S12，外加兩個非劇本測試（plan §6、§7、AC4–AC6）：
  - 每個閘門劇本用 `run_with_gate_actions`，並在 `on_pause` 裡斷言：`orch.snapshot()["awaiting_approval"]` 恰一筆且 `station` 正確、`orch.station_states[閘門站]["status"] == "awaiting_approval"`、tasks.yaml 目前狀態是 `awaiting_approval`。閘門結束後斷言 `snapshot()["awaiting_approval"] == []`。
  - 另外斷言 `task:awaiting_approval` 的次數等於閘門進入次數。
  - S8、S10、S12 斷言 SWE 第二次 prompt 的內容（S8／S10 含 note；S12 依 legacy 實測結果寫，預期不含 `approval_feedback`）。
  - `test_approve_from_other_thread`：`orch.start()`，`enqueue` 一個 after_swe 任務，輪詢到閘門後用 `threading.Thread(target=orch.approve, args=(task.id,))` 核准，`await asyncio.wait_for(orch._queue.join(), 10)`，斷言 `done_count == 1`，最後 `await orch.stop()`。
  - `test_stop_while_waiting_clears_gate`：`orch.start()`，`enqueue` after_swe 任務，輪詢到閘門後 `await orch.stop()`，斷言 `is_awaiting_approval` 為 False、`snapshot()["awaiting_approval"] == []`。
  兩個非劇本測試也用 `ENGINES` 參數化。
  — done when: 該檔 13 passed（5 + 6 + 2）；全套 326 passed。

- [x] 5. 〔Dev〕同檔加入 S11（usage 與預算）：`UsageTracker(project_budget_usd=0.015)`，在 `process_task` 前 `tracker.record(project.name, "seed", "openai", "gpt-4o", 1000, 1000)`（照 `test_usage_tracker.py` 的寫法）— done when: 斷言 `usage:update` 3 次、`devops:alert` 恰 1 次且緊接在第一個 `usage:update` 之後、任務 done、`orch.is_budget_paused()` 為 True；該檔 14 passed；全套 327 passed。

## M2 Legacy 純重構（不改行為）（plan「Node 職責」）

- [x] 6. 〔Dev〕從 `backend/core/pipeline.py` 抽出共用 helper，legacy 改成呼叫它們，行為一行都不變：
  - `_start_task(task)`：`process_task` 開頭的 `task:created` 事件 + `_set_task_status(IN_PROGRESS)`。
  - `_build_swe_context(task, reject_note) -> dict`：照 legacy（`qa_output` 只在 `task.qa_output is not None` 時加入；`approval_feedback` 只在 `reject_note` 為真值時加入）。
  - `_complete_task(task, deliverables) -> Task`：寫 DONE、`done_count += 1`、發 `task:completed`、log。
  - 把 `_await_approval` 拆成 `_gate_enter(task, station)`（建立 Event、`_approval_gates`、`_awaiting`、`_set_station(awaiting_approval)`、寫狀態、發事件、log）、`_gate_wait(task) -> tuple[str, str]`（`await event.wait()`、`finally` 清兩個 dict、pop `_approval_decisions`，預設 `("approve", "")`）、`_gate_exit(task, station, decision, note)`（`_set_station(idle)`、寫 IN_PROGRESS、發 approved／rejected、log）；`_await_approval` 保留，內容改為依序呼叫三者。
  - 原本的 `process_task` 主體搬到 `_process_task_legacy(task)`，`process_task` 先只是 `return await self._process_task_legacy(task)`。
  — done when: 全套 327 passed（含特徵化測試，**未修改任何期望值**）；`git diff backend/core/pipeline.py` 只有搬移與抽函式，沒有改任何事件內容或順序。

## M3 LangGraph 入門與引擎骨架

- [x] 7. 〔Dev〕安裝 langgraph 並寫語意學習測試（D11、plan §9、AC8、AC9）：
  - `backend/requirements.txt` 新增一段（放在 `# Testing` 之前）：`# Graph-based pipeline engine (PIPELINE_ENGINE=langgraph)` 與 `langgraph==1.2.22`。
  - `.venv/bin/python -m pip install -r requirements.txt`；看輸出，若有升降「停下來問」第 2 點列的套件主版本，停止。
  - 跑 plan §9 的三個驗證指令。
  - 新增 `backend/tests/test_langgraph_semantics.py`（只用玩具 graph，不碰 pipeline），每個測試都加 `# LangGraph 學習:` 註解說明在學什麼：
    1. `test_conditional_edge_routes_by_state`：兩個分支依 state 值走不同 node。
    2. `test_reducer_appends_lists`：`Annotated[list[str], operator.add]` 兩個 node 各回傳一筆，最後有兩筆。
    3. `test_interrupt_node_reruns_from_top_on_resume`：node 內先把外部 list 加一筆、再 `interrupt("q")`；第一次 `ainvoke` 後 list 長 1，`aget_state(config).next` 非空、`.interrupts` 有一筆；`ainvoke(Command(resume="a"), config)` 後 list 長 2（證明重跑），且 node 回傳的值是 `"a"`。
    4. `test_completed_nodes_do_not_rerun_on_resume`：interrupt node 之前的 node 計數器在 resume 後仍為 1。
    5. `test_object_in_state_loses_identity_after_resume`：把一個 pydantic 物件放進 state，resume 後 node 拿到的物件 `is not` 原物件（D3 的理由）；如果實測 `is` 仍成立，把測試改成記錄實際行為，並在「結果：」註記（plan D3 的理由仍成立於持久化 checkpointer）。
    6. `test_node_exception_propagates_from_astream`：node 拋 `ValueError("boom")`，`astream` 迴圈拋出的例外型別與訊息（記錄實際行為；若被包裝，在「結果：」寫出型別）。
    7. `test_updates_stream_shape_on_interrupt`：用 `stream_mode="updates"` 收集 chunk，記錄停在 interrupt 時最後一個 chunk 的形狀（預期含 `"__interrupt__"`），並斷言串流正常結束、不拋例外。
    8. `test_aget_state_next_empty_at_end`：跑到 END 後 `aget_state(config).next == ()`。
  全部用 `asyncio.run` 包 async 函式（與既有測試一致），checkpointer 用 `InMemorySaver()`。
  — done when: 該檔 8 passed；全套 335 passed；`.venv/bin/python -m pip check` 無錯；在「結果：」貼上 `.venv/bin/python -m pip freeze | grep -iE "^lang|ormsgpack|xxhash|websockets|pydantic=="` 的輸出。

- [x] 8. 〔Dev〕引擎旗標（D2、AC1）：
  - `backend/config.py` 新增 `PIPELINE_ENGINES = ("legacy", "langgraph")` 與 `get_pipeline_engine() -> str`（讀 `PIPELINE_ENGINE`，去空白轉小寫，空或未設定回 `"legacy"`，不合法拋 `ValueError`，訊息格式見 plan D2）。
  - `PipelineOrchestrator.__init__` 新增最後一個關鍵字參數 `engine: str | None = None`；`None` 時用 `config.get_pipeline_engine()`，非 `None` 時也要驗證合法；存成 `self.engine`。`engine == "langgraph"` 時才 `from core.pipeline_graph import PipelineGraphRunner` 並建立 `self._graph_runner = PipelineGraphRunner(self)`。
  - `process_task`：`if self.engine == "langgraph": return await self._graph_runner.run(task)`，否則走 legacy。
  - 新增 `backend/core/pipeline_graph.py` 骨架：模組 docstring 說明用途；`class PipelineGraphRunner` 的 `__init__(self, orch)` 存參考，`async def run(self, task)` 先 `raise NotImplementedError("langgraph engine: see specs/langgraph-pipeline/tasks.md Task 10")`。
  - `docker-compose.yml` 的 backend `environment` 加 `- PIPELINE_ENGINE=${PIPELINE_ENGINE:-legacy}`。
  - `tests/pipeline_harness.py` 的 `make_orchestrator` 移除 Task 2 的 assert，改為把 `engine` 傳給建構子。
  - 新增 `backend/tests/test_pipeline_engine_flag.py`：未設定→legacy；`langgraph`→`self.engine == "langgraph"`；`LangGraph `（大小寫、空白）→langgraph；`bogus`→`ValueError` 且訊息含 `legacy, langgraph`；用 `subprocess.run([sys.executable, "-c", "import core.pipeline, sys; print('langgraph' in sys.modules)"], cwd=backend 目錄, env 不含 PIPELINE_ENGINE)` 斷言輸出 `False`（AC7）。用 `monkeypatch.setenv`／`delenv`。
  — done when: 新檔通過；全套（未設定 PIPELINE_ENGINE）全部通過，數量為 335 + 新檔測試數；`docker compose config | grep PIPELINE_ENGINE` 顯示 `PIPELINE_ENGINE: legacy`。

- [x] 9. 〔Dev〕State 與條件邊（純函式）（plan「State schema」、「Graph」）：在 `core/pipeline_graph.py` 加入 `PipelineState`（完全照 plan）、`initial_state(task) -> dict`，以及五個 routing 函式：`route_after_pm`、`route_after_swe`、`route_after_qa`、`route_after_gate`、`route_after_rework`，回傳值是 node 名稱字串或 `END`，邏輯照 plan 的 mermaid 圖。每個函式上方加 `# LangGraph 學習: 條件邊就是一個讀 state、回傳下一個 node 名稱的普通函式`。新增 `backend/tests/test_pipeline_graph_routing.py`，以表格驅動測試每個函式的所有分支（不建 graph、不跑 agent）— done when: routing 測試全部通過，分支覆蓋：pm 2、swe 3、qa 4、gate 3、rework 2，共至少 14 個 case；全套通過。

## M4 LangGraph 引擎

- [x] 10. 〔Dev〕無閘門的 graph（plan「Node 職責」、「Driver」、D3、D5、D6、D9）：
  - 在 `PipelineGraphRunner` 實作 node：`intake`、`pm`、`swe`、`qa`、`rework`、`done`、`fail`（全部 `async def`，呼叫 orchestrator 的共用 helper，回傳 plan 表格中的 state 更新）。`gate_enter`、`gate_wait`、`gate_exit` 先不加；`route_after_swe`／`route_after_qa` 若回傳 `gate_enter`，暫時 `raise NotImplementedError`。
  - `build_graph()`：`StateGraph(PipelineState)`、`add_node`（不設 `retry_policy`）、`add_edge(START, "intake")`、`add_edge("intake", "pm")`、各 `add_conditional_edges`、`add_edge("done", END)`、`add_edge("fail", END)`。
  - `run()`：照 plan「Driver」的程式碼（本任務還不會遇到 interrupt，但 while 迴圈先寫好）。
  - 加上學習註記：StateGraph、reducer、條件邊、astream、checkpointer、thread_id（AC8）。
  - `tests/test_pipeline_characterization.py` 的 `ENGINES` 改成 `["legacy", "langgraph"]`，但閘門相關測試（S6–S10、S12、兩個非劇本測試）用 `pytest.mark.skipif` 或分開的 `ENGINES_NO_GATE` 暫時只跑 legacy，**期望值不得修改**。
  — done when: `.venv/bin/python -m pytest tests/test_pipeline_characterization.py -q` 中 S1–S5、S11 的 langgraph 版本全部通過；全套（legacy 預設）通過。

- [x] 11. 〔Dev〕閘門：`interrupt()` + `Command(resume)`（D4、D7、§7、§8、AC4–AC6）：
  - 加 `gate_enter`（呼叫 `orch._gate_enter`）、`gate_wait`（**只有** `interrupt({"task_id": ..., "station": ...})` 與回傳 `decision`／`decision_note`，函式最上方註解寫明規則 I1）、`gate_exit`（呼叫 `orch._gate_exit`，reject 時回傳 `rework_reason` 與 `approval_feedback`）。移除 Task 10 的 `NotImplementedError`。
  - driver 的 interrupt 分支照 plan：`aget_state` → `orch._gate_wait(task)` → `Command(resume={"decision": ..., "note": ...})`。
  - 加學習註記：interrupt、Command（AC8）。
  - 移除 Task 10 對閘門測試的 langgraph 排除。
  — done when: `tests/test_pipeline_characterization.py` 全部劇本與兩個非劇本測試在兩種引擎下都通過（共 28 個 test case），**期望值未修改**；`.venv/bin/python -m pytest -q` 全綠。

- [x] 12. 〔Dev〕整套在 LangGraph 引擎上重跑（AC2）：`PIPELINE_ENGINE=langgraph .venv/bin/python -m pytest -q` — done when: 全部通過，且通過數與未設定 `PIPELINE_ENGINE` 時相同；`git diff --quiet -- backend/tests/test_pipeline.py backend/tests/test_api.py backend/tests/test_usage_tracker.py` 成立。若有失敗，只能改 `core/pipeline_graph.py` 或 `core/pipeline.py`；需要改測試或契約時依「停下來問」第 1、3 點處理。

- [x] 13. 〔Dev〕學習註記與邊界檢查（AC7、AC8）：
  - 新增 `backend/tests/test_pipeline_graph_shape.py`：建一個 langgraph 引擎的 orchestrator，`orch._graph_runner._graph.get_graph().draw_mermaid()` 的字串包含全部 10 個 node 名稱（intake、pm、swe、qa、gate_enter、gate_wait、gate_exit、rework、done、fail）。把該 mermaid 輸出貼在「結果：」，與 plan 的圖人工比對，有差異就註記。
  - 執行並記錄：`grep -n "LangGraph 學習:" core/pipeline_graph.py`（需涵蓋 StateGraph、reducer、條件邊、astream、interrupt、Command、checkpointer、thread_id）；`grep -rln "langgraph" . --include=*.py | grep -v "^./tests/" | grep -v "^./.venv/"` 只輸出 `./core/pipeline_graph.py`。
  — done when: 新測試通過；兩個 grep 的結果符合；「專案慣例」的禁改檔案 `git diff --stat` 無輸出。

## M5 驗收

- [x] 14. 〔Dev〕驗收用腳本（新增 `backend/scripts/`，不被 pytest 收集）（AC10）：
  - `prepare_accept_project.py --source <example-project 路徑> --project-dir <目標> --status done|pending`：在目標建立（或覆寫）`tasks.yaml`，內容是 source `tasks.yaml` 中 id 不是 `task-gate` 的任務（example-project 有三個；id、title、description、priority 照抄，status 用參數值）加一個 `{"id": "task-gate", "title": "Acceptance gated task", "description": "Exercise the approval gate.", "priority": "low", "approval": "after_swe", "status": <參數值>}`；目標沒有 `agents/` 時從 source 複製。**不得寫入 source。**
  - `compare_ws_events.py a.jsonl b.jsonl`：讀兩個 JSONL（每行 `{"event", "data"}`），忽略 `snapshot` 事件，用 plan §5 的 `event_signature`（複製函式，不 import tests）比較；相同 exit 0，不同時印出第一個差異的索引與兩邊簽章並 exit 1。
  - `acceptance_run.py --project lg-accept --out <檔案> [--base-url http://localhost:8000] [--timeout 180]`：
    1. 以 `websockets.connect(f"ws://.../ws/{project}")` 連線（websockets 16；若 API 不同，改用 `websockets.asyncio.client.connect` 並在「結果：」註記），收到第一則 `snapshot`。
    2. 以 `subprocess.run(["docker", "compose", "exec", "-T", "backend", "python", "scripts/prepare_accept_project.py", "--source", "/app/projects/lg-accept", "--project-dir", "/app/projects/lg-accept", "--status", "pending"], cwd=repo 根目錄（`Path(__file__).resolve().parents[2]`）, check=True)`（source 與目標同一個檔案是刻意的：腳本先讀完再寫） 在容器內改寫 tasks.yaml（在容器內寫檔，watchdog 一定收得到事件；從 macOS 主機改 bind mount 的檔案不保證觸發）。
    3. 持續接收事件寫進 JSONL；收到 `task:awaiting_approval` 且 task id 為 `task-gate` 時：第一次 `POST /api/projects/{project}/tasks/task-gate/reject`，body `{"note": "acceptance: please add validation"}`；第二次 `POST .../approve`（HTTP 呼叫用 `await asyncio.to_thread(httpx.post, ...)`）。
    4. 收到 4 個 `task:completed`／`task:failed` 後結束，exit 0；逾時 exit 2。
  — done when: `compare_ws_events.py` 有一個小測試 `backend/tests/test_compare_ws_events.py`（相同檔案 exit 0、改一個 status 後 exit 1，用 `tmp_path` 與 `subprocess`）通過；`prepare_accept_project.py` 在 `tmp_path` 上跑一次產生 4 個任務（同一測試檔驗證）；全套通過。

- [ ] 15. 〔Dev + 使用者〕Docker 驗收（AC10）。在 repo 根目錄執行：
  1. 前置檢查：`docker info` 成功；`grep -cE '^(OPENAI|ANTHROPIC|GEMINI)_API_KEY=.+' .env` 輸出 `0`（`.env` 不存在也可）；`env | grep -E '^(LANGSMITH|LANGCHAIN)_'` 無輸出。任何一項不符就停下來問使用者，**不要修改 `.env`**。
  2. legacy 回合：
     ```bash
     backend/.venv/bin/python backend/scripts/prepare_accept_project.py --source projects/example-project --project-dir projects/lg-accept --status done
     PIPELINE_ENGINE=legacy docker compose up --build -d
     # 輪詢直到 backend 就緒
     until curl -sf http://localhost:8000/api/projects >/dev/null; do sleep 1; done
     cd backend && .venv/bin/python scripts/acceptance_run.py --project lg-accept --out /tmp/lg-accept-legacy.jsonl; cd ..
     docker compose down
     rm -rf projects/lg-accept
     ```
     （若 `/tmp` 不可寫，改用其他暫存目錄，並在「結果：」記錄路徑。）
  3. langgraph 回合：同上，`PIPELINE_ENGINE=langgraph`，輸出 `/tmp/lg-accept-langgraph.jsonl`；跑之前用 `docker compose exec backend env | grep PIPELINE_ENGINE` 確認是 `langgraph`。
  4. `backend/.venv/bin/python backend/scripts/compare_ws_events.py /tmp/lg-accept-legacy.jsonl /tmp/lg-accept-langgraph.jsonl` exit 0。
  5. 〔使用者，建議〕`PIPELINE_ENGINE=langgraph docker compose up -d` 後開 `http://localhost`，選 lg-accept 專案，在 HUD 上手動 reject／approve 一個 gated 任務，確認畫面與 legacy 一致。
  6. 收尾：`docker compose down`、`rm -rf projects/lg-accept`；`git status --short` 與任務開始前相比，只多了本計畫新增／修改的檔案（`projects/example-project` 的狀態與開始前相同）。
  — done when: 步驟 4 exit 0；兩個 JSONL 各有 4 個終止事件（3 個 completed 加 task-gate 的 completed）且 task-gate 各有 2 次 `task:awaiting_approval`；「結果：」記錄兩個 JSONL 的事件數。完成後**停下來**，請使用者決定：是否切換預設引擎、是否做選配任務。

---

## 選配

> 以下每一項開始前都要先問使用者。全部只能**新增**檔案，或修改 `core/pipeline_graph.py`、`core/pipeline.py`、`core/runtime.py`、`main.py`、`requirements.txt`、`.gitignore`；仍不得修改「專案慣例」的禁改檔案。

- [ ] O1. 〔Dev〕〔選配〕`get_stream_writer()` 自訂串流（D5 放棄的方案，拿來學）：在 `pm`、`swe`、`qa` node 開頭用 `get_stream_writer()` 送 `{"kind": "progress", "node": ..., "task_id": ...}`；driver 改用 `stream_mode=["updates", "custom"]`，custom chunk 只寫 `logger.debug`，**不進 `on_event`**。加學習註記說明多種 stream mode 時 chunk 是 `(mode, payload)` tuple（先以測試驗證這個形狀）。— done when: 新測試用 `caplog` 斷言每個站點都有一筆 progress debug log；特徵化測試兩種引擎仍全綠（證明事件契約未變）。

- [ ] O2. 〔Dev〕〔選配〕流程圖唯讀端點：`core/pipeline_graph.py` 加 `def pipeline_mermaid() -> str`（建一個不連 orchestrator 的 graph 只為了畫圖，或用 dummy runner）；新增 `backend/api/pipeline_graph.py`，`GET /api/pipeline/graph` 回 `{"mermaid": "..."}`；在 `main.py` 註冊 router。HUD 要顯示這張圖需要改前端，**屬於前端改動，必須另外問使用者**，本任務不做。— done when: `tests/test_pipeline_graph_api.py` 用 TestClient 斷言 200 且 `mermaid` 含 `gate_wait`；`langgraph` 的 import 仍只在 `core/pipeline_graph.py`。

- [ ] O3. 〔Dev〕〔選配〕`AsyncSqliteSaver` 讓重啟後可續跑閘門（D6、plan §9）。需要新依賴 `langgraph-checkpoint-sqlite==3.1.3`（會帶入 `aiosqlite`、`sqlite-vec`）——**先問使用者**。開始前請使用者確認以下設計，或給出修改：
  - DB 檔：`<project>/.langgraph/checkpoints.sqlite`，`.gitignore` 加 `projects/*/.langgraph/`。
  - `PIPELINE_CHECKPOINTER=memory|sqlite`（預設 memory）。
  - 用法：先寫一個測試驗證 `AsyncSqliteSaver.from_conn_string(path)` 是否為 async context manager、需不需要 `await saver.setup()`，再決定 runner 的生命週期（在 `start()` 開、`stop()` 關）。
  - `run()` 開頭改為：若該 thread 有未回答的 interrupt，就不刪除，改走 resume 流程；否則照舊刪除。
  - 重啟恢復：`FactoryRuntime.ensure_project` 之後，對 tasks.yaml 中 `status: awaiting_approval` 的任務，若 checkpoint 有未回答的 interrupt，就重建 live `Task`（從 tasks.yaml 讀，再用 checkpoint state 補 `pm_output`／`swe_output`／`qa_output`／`rework_count`），重新登記閘門（不重發 `task:awaiting_approval`，這是新行為，需使用者同意），並在背景 task 中等待決定後 resume。
  — done when: 測試：跑到閘門 → 關閉 orchestrator → 用同一個 DB 建新 orchestrator 並恢復 → `approve()` → 任務 done，且交付檔沒有重複、PM／SWE agent 沒有重跑。

- [ ] O4. 〔Dev〕〔選配〕LangChain chat model：新增 `backend/core/langchain_client.py`，`class LangChainLLMClient(LLMClient)` 包一個 `langchain_core.language_models.BaseChatModel`，`call(prompt, system_prompt)` 送 `[SystemMessage, HumanMessage]` 取 `.content`，有 `usage_metadata` 時填 `last_usage`。**不修改** `core/llm_client.py` 與 agents；只在測試中把 `LangChainLLMClient(FakeListChatModel(responses=[...]))` 傳給某個 agent 的 `llm_client`。`init_chat_model`（需要 `langchain` 套件與各供應商套件）**必須先問使用者**再做。— done when: `tests/test_langchain_client.py` 以 `FakeListChatModel` 跑一次 S1，事件簽章與 legacy 相同（僅輸出文字不同不影響簽章）；`pip check` 無錯。
