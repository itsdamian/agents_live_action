# Implementation Plan

> 依 `roadmap.md` 以商業價值與使用便利性重新排序。
> Phase 0 打通 MVP → Phase 1 真實價值 → Phase 2 便利性深化 → Phase 3 商業化。
> 原 task 10（Vue 3 重構）延後至 Phase 2；Phase 0 前端改用現有 vanilla JS 接 WebSocket。

## 已完成基礎（原 tasks 1–6）

- [x] 1. 建立專案基礎結構與 Docker 環境
  - `backend/`、`frontend/`、`docker-compose.yml`、Dockerfiles、`nginx.conf`（/api、/ws 反向代理）、`.env.example`、`projects/`
  - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [x] 2. 後端核心資料模型與配置
  - `models/task.py`、`models/agent.py`、`models/project.py`、`config.py`（env 掃描 + `is_mock_mode()`）＋ 測試
  - _Requirements: 5.2, 3.4, 4.1, 4.2, 4.3_

- [x] 3. 專案掃描與 Agent 配置管理
  - `core/project_scanner.py`（`scan_projects` / `scan_projects_with_structure`）、`core/agent_config.py`（`load_agent_configs` / `create_default_agents`）＋ 測試
  - _Requirements: 1.1, 1.4, 2.1, 2.2, 3.1–3.5_

- [x] 4. 任務檔案讀寫與監控
  - `core/task_watcher.py`（watchdog 監控、`parse_tasks_yaml` / `parse_todo_md`、`write_task_status` 回寫、`scan_now` / `prime`）＋ 測試
  - _Requirements: 5.1, 5.3, 5.4, 5.5_

- [x] 5. LLM 客戶端抽象層
  - `core/llm_client.py`：`LLMClient` 基類、`MockLLMClient`、`OpenAIClient` / `AnthropicClient` 框架（**真實呼叫尚未接通，見 task 12**）、`get_llm_client` factory ＋ 測試
  - _Requirements: 4.3, 4.4, 4.5_

- [x] 6. 四個 Agent 類別
  - `agents/base_agent.py`（retry、日誌、`AgentResult`）、`pm_agent.py`、`swe_agent.py`（重工 context）、`qa_agent.py`（`is_pass`/`is_fail` 判定）、`devops_agent.py`（週期監控 + `devops:alert`）＋ 測試
  - _Requirements: 2.1, 2.3, 2.4, 2.5, 6.2, 6.3, 9.1, 9.2_

---

## Phase 0：打通 MVP（目標：`docker compose up` 即可看到流水線端到端跑完）

- [x] 7. 實作流水線排程器 `core/pipeline.py`
  - `PipelineOrchestrator` 類：`asyncio.PriorityQueue` 依 priority 排序（critical=0, high=1, medium=2, low=3，同優先級以入列順序 FIFO）
  - `enqueue(task)`（thread-safe，供 TaskWatcher callback 使用）與 async worker loop
  - PM → SWE → QA 依序執行：agent 的 `run(task, context, project_path)` 為同步呼叫，用 `asyncio.to_thread` 包裝避免阻塞 event loop
  - 每站完成後：更新 `task.pm_output/swe_output/qa_output`、`write_task_status` 回寫 YAML、透過 event callback 廣播（`station:update`、`task:created`、`task:completed`、`task:failed`）
  - 重工邏輯：`QAAgent.is_fail(report)` 時回送 SWE（context 帶 `qa_output`），`rework_count += 1`，超過 `max_reworks`（預設 3）標記 failed
  - 任一 agent `AgentResult.success == False` → 任務 failed，不中斷 worker loop
  - `tests/test_pipeline.py`：正常流程、重工後通過、超過重工上限、priority 排序四種情境（用 MockLLMClient / stub agents）
  - _Requirements: 6.1–6.6_

- [x] 8. 實作 WebSocket 管理器與 REST API
- [x] 8.1 `core/ws_manager.py`
  - 管理多個 WS 連線，依 `project_id` 分組；`connect` / `disconnect` / `broadcast(project_id, event, data)`
  - 廣播失敗（連線已死）時自動移除該連線，不拋例外
  - `tests/test_ws_manager.py`（FastAPI TestClient WebSocket 測試）
  - _Requirements: 7.1, 7.2_
- [x] 8.2 REST API endpoints（`api/projects.py`、`api/tasks.py`）
  - `GET /api/projects` — 專案清單（含 mock_mode flag 與 configured providers）
  - `GET /api/projects/{project_id}` — 專案詳情含 agent 配置與狀態快照
  - `GET /api/projects/{project_id}/tasks` — 任務列表（讀 tasks.yaml + TODO.md）
  - `POST /api/projects/{project_id}/tasks` — **新增（原規格未涵蓋，便利性關鍵）**：接收 title/description/priority，產生 id 後寫入 `tasks.yaml`（status: pending），由 TaskWatcher 自然觸發流水線
  - `GET /api/health`
  - `tests/test_api.py`
  - _Requirements: 1.2, 1.3_
- [x] 8.3 WebSocket endpoint（`api/websocket.py`）
  - `WS /ws/{project_id}` — 連線後先推送當前狀態快照，之後即時推送事件
  - 整合 `PipelineOrchestrator` 事件 callback 與 `DevOpsAgent.on_alert` → `ws_manager.broadcast`
  - _Requirements: 7.1, 7.6, 7.8_

- [x] 9. 整合 `main.py` 啟動流程
  - FastAPI lifespan：`scan_projects_with_structure` → 對缺 `agents/` 的專案 `create_default_agents` → 每專案建立 orchestrator（或單一 orchestrator 多專案）→ `TaskWatcher(callback=enqueue)` 啟動 + `scan_now()` 撿起既有 pending 任務 → `DevOpsAgent.start()`
  - 掛載 api routers 與 ws endpoint；CORS 設定（開發時 vite dev server 用）
  - startup log 列出偵測到的 LLM providers 與 mock mode 狀態
  - shutdown：停止 TaskWatcher、DevOpsAgent、worker loops（graceful，Requirement 8.6）
  - _Requirements: 1.1, 2.2, 4.1, 4.3, 4.4, 8.6_

- [x] 10. 前端接上後端（vanilla JS，暫緩 Vue 重構）
  - 將根目錄 `index.html` / `css/` / `js/` 改造後移入 `frontend/`（nginx 直接 serve 靜態檔，`frontend/Dockerfile` 改為 nginx copy，不需 node build）
  - 移除 `js/simulation.js` 假資料驅動，新增：
    - `js/api.js` — `GET /api/projects`、`GET .../tasks` fetch helpers
    - `js/ws.js` — WS 連線 `/ws/{project_id}`、指數退避重連（2s→5s→10s）、事件派發到 state
  - header 加專案 select box（接 `GET /api/projects`），切換時重連 WS 並重置流水線顯示
  - station cards / conveyor / activity log 改由 WS 事件驅動；保留輸送帶動畫
  - `[MOCK MODE]` header 指示器（依 `/api/projects` 回傳 flag）；WS 斷線警告橫幅
  - _Requirements: 1.2, 1.3, 7.1–7.8_

- [x] 11. 範例專案與 Quickstart
  - `projects/example-project/`：`tasks.yaml`（2–3 個示範任務，status: pending）
  - 更新 README：三步驟 Quickstart（clone → `docker compose up` → 開 http://localhost）
  - 端到端驗證：無 API key（mock mode）下 `docker compose up`，瀏覽器可看到範例任務跑完全流程
  - _Requirements: 8.1–8.5_

---

## Phase 1：真實價值（mock → 真的幫使用者做事）

- [ ] 12. 接通真實 LLM API
  - 補完 `AnthropicClient.call`（`anthropic` SDK）與 `OpenAIClient.call`（`openai` SDK），加入 timeout 與 rate-limit/5xx 錯誤分類（retry 已由 BaseAgent tenacity 處理）
  - `requirements.txt` 加入 SDK 依賴；`get_llm_client` 已有的 key 偵測邏輯不變
  - 回傳中附帶 token usage（見 task 16 的資料需求）：`call` 改回傳含 `text` + `usage` 的結構或另存 `last_usage`
  - 整合測試：以環境變數存在與否驗證 factory 行為；真實呼叫用 respx/mock HTTP 測試
  - _Requirements: 4.1, 4.2, 4.5_

- [x] 13. Agent 產出落地成檔案
  - `core/deliverables.py`：`write_deliverable(project_path, task_id, agent_name, content)` → `projects/{name}/deliverables/{task_id}/{NN}_{agent}.md`（SWE 程式碼區塊另存對應副檔名檔案）
  - pipeline 每站成功後寫入產出；`task:completed` 事件附 deliverables 路徑清單
  - `GET /api/projects/{id}/tasks/{task_id}/deliverables` — 列出並可下載產出檔案
  - _商業理由：使用者拿得到成果，價值才成立_

- [x] 14. UI 新增任務表單
  - 前端加「NEW TASK」按鈕 → 表單（title、description、priority select）→ `POST /api/.../tasks`
  - 成功後 toast 提示 + 任務出現在流水線 queue（經由 TaskWatcher 觸發，無需前端輪詢）
  - 錯誤處理：後端驗證失敗顯示訊息
  - _商業理由：消除「必須手改 YAML」這個最大使用門檻_

- [x] 15. UI 任務結果檢視
  - 任務點擊 → modal/side panel 顯示：各站輸出（pm/swe/qa_output）、狀態歷程、log 檔內容、deliverables 清單
  - `GET /api/projects/{id}/tasks/{task_id}` 回傳完整任務詳情；`GET .../logs` 列出 log 檔
  - _商業理由：不用翻資料夾，結果在 UI 一眼可見_

- [x] 16. 成本追蹤與預算上限
  - `core/usage_tracker.py`：記錄每次 LLM 呼叫的 provider/model/input/output tokens 與估算成本（模型單價表），累計到 per-task 與 per-project
  - 任務詳情與 DevOps 面板顯示累計花費；`usage:update` WS 事件
  - `.env` 支援 `DAILY_BUDGET_USD` / per-project budget；超過即暫停 enqueue 並發 `devops:alert`（critical）
  - _商業理由：沒有花費控管，沒人敢把真 API key 交給自動化系統_

---

## Phase 2：使用便利性深化（可控、省心、天天用）

- [x] 17. Human-in-the-loop 核准關卡
  - Task model 加 `approval`（none / after_swe / after_qa）與 `awaiting_approval` 狀態；pipeline 在關卡暫停
  - `POST .../tasks/{id}/approve`、`POST .../tasks/{id}/reject`（帶備註，reject 走重工路徑）
  - 前端核准 UI（任務卡上 approve/reject 按鈕 + 備註欄）
  - _商業理由：信任是自動化產品的採用前提_

- [ ] 18. 任務歷史與統計儀表板（SQLite 持久化）
  - `core/history_db.py`（SQLite + aiosqlite）：任務執行紀錄、每站耗時、重工次數、成本
  - YAML 仍是任務入口與現況真相；DB 只負責歷史與統計
  - `GET /api/projects/{id}/stats`：成功率、平均耗時、重工率、成本趨勢（近 7/30 天）
  - 前端 Manager Office 面板改顯示真實統計
  - _商業理由：留存數據 = 改善依據 = 對主管展示的 ROI 證明_

- [ ] 19. Agent 配置 UI 化
  - `GET/PUT /api/projects/{id}/agents/{agent_name}` — 讀寫 agent YAML（model、system_prompt、max_retries）
  - 前端 agent 設定 modal；PUT 後熱重載該專案 agents
  - _商業理由：不用開編輯器，降低客製門檻_

- [ ] 20. 通知整合
  - `core/notifier.py`：任務完成/失敗/等待核准時發 Slack incoming webhook（`SLACK_WEBHOOK_URL`）；架構預留 email
  - 可設定通知等級（all / failures_only / approvals_only）
  - _商業理由：使用者不需要盯著畫面_

- [ ] 21. 前端重構為 Vue 3（原 task 10 全部內容移到此）
  - 依原規格 10.1–10.4 執行：Vite + Pinia scaffold、stores（pipeline/devops）、composables（useWebSocket/useProjects）、元件（ProjectSelector、StationCard、ConveyorBelt、ManagerOffice、DevOpsPanel、MockModeBanner、App）
  - 此時 UI 已含表單、詳情、核准流、儀表板，重構開始回收成本
  - _Requirements: 原 10.1–10.4 全部_

---

## Phase 3：商業化擴張（依市場回饋挑選，不必全做）

- [ ] 22. Git 整合（差異化關鍵）
  - SWE agent 在 `projects/{name}/` 內實際建 branch、寫檔、commit；QA agent 真的執行測試命令而非只評估文字
  - 可選 GitHub 整合：開 PR、把 QA 報告寫成 PR comment
  - _商業理由：從「產生建議」升級為「交付可合併的程式碼」，最強付費理由_

- [ ] 23. 多使用者與權限
  - 登入（先做簡單 token/OAuth）、專案層級權限、API token 管理
  - _商業理由：進入團隊市場的門票_

- [ ] 24. 專案/Agent 模板庫
  - 常見專案類型（web app、爬蟲、資料分析）的 agent 組合與任務模板；`POST /api/projects`（從模板建立專案）
  - _商業理由：降低新使用者啟動成本_

- [ ] 25. CI / 對外 API 整合
  - API token 驗證的對外 REST + webhook（任務完成回呼）；文件化 API
  - _商業理由：平台成為工作流的一環而非孤島_

- [ ] 26. 部署與收費分層
  - 單機 Docker 版維持開源引流；SaaS/授權版承載進階功能（多使用者、統計、Git 整合）
  - _商業理由：建立收費依據_
