# Implementation Plan

- [ ] 1. 建立專案基礎結構與 Docker 環境
  - 建立 `backend/`、`frontend/` 目錄結構
  - 撰寫 `docker-compose.yml`（backend + frontend 兩個 service）
  - 撰寫 `backend/Dockerfile` 與 `frontend/Dockerfile`
  - 建立 `frontend/nginx.conf`，設定反向代理 `/api` 和 `/ws` 到 backend
  - 建立 `.env.example`，列出 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY` 等環境變數欄位
  - 建立 `projects/` 目錄（含 `.gitkeep`）
  - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [ ] 2. 實作後端核心資料模型與配置
- [ ] 2.1 建立 Pydantic 資料模型
  - 撰寫 `backend/models/task.py`：Task model，含 priority/status enum
  - 撰寫 `backend/models/agent.py`：AgentConfig model，含所有 YAML 欄位
  - 撰寫 `backend/models/project.py`：Project model
  - 撰寫對應 unit tests（`tests/test_models.py`）
  - _Requirements: 5.2, 3.4_

- [ ] 2.2 實作 `config.py` 環境變數管理
  - 掃描已知 LLM 供應商環境變數，回傳哪些已設定
  - 實作 `is_mock_mode()` 函式：沒有任何 API key 時回傳 True
  - 撰寫 `tests/test_config.py` 驗證偵測邏輯
  - _Requirements: 4.1, 4.2, 4.3_

- [ ] 3. 實作專案掃描與 Agent 配置管理
- [ ] 3.1 實作 `core/project_scanner.py`
  - 掃描 `projects/` 目錄，回傳所有子資料夾作為專案列表
  - 驗證每個專案目錄結構，偵測 `agents/`、`tasks.yaml`、`TODO.md` 是否存在
  - 撰寫 `tests/test_project_scanner.py`
  - _Requirements: 1.1, 1.4_

- [ ] 3.2 實作 `core/agent_config.py`
  - 讀取 `agents/*.yaml`，使用 Pydantic 解析，格式錯誤時回退預設值並記錄 warning
  - 實作 `create_default_agents(project_path)`：自動建立四個預設 YAML 配置文件（PM、SWE、QA、DevOps）
  - 撰寫 `tests/test_agent_config.py`，涵蓋正常解析、格式錯誤回退、自動建立三個情境
  - _Requirements: 2.1, 2.2, 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ] 4. 實作任務檔案讀寫與監控
- [ ] 4.1 實作 `core/task_watcher.py`
  - 使用 `watchdog` 監控 `projects/` 下的 `tasks.yaml` 和 `TODO.md` 變更
  - 偵測到新 `status: pending` 任務時，觸發 callback 把任務送入流水線隊列
  - 解析 `TODO.md` 的 `- [ ]` 項目，轉換為 Task 物件
  - 撰寫 `tests/test_task_watcher.py`（模擬文件變更事件）
  - _Requirements: 5.1, 5.3, 5.4_

- [ ] 4.2 實作任務狀態回寫
  - 在 `core/task_watcher.py` 新增 `write_task_status(project_path, task_id, status)` 函式
  - 任務狀態更新後，同步回寫到 `tasks.yaml`
  - 撰寫回寫邏輯的 unit test
  - _Requirements: 5.5_

- [ ] 5. 實作 LLM 客戶端抽象層
  - 撰寫 `core/llm_client.py`：定義 `LLMClient` 抽象基類與 `call(prompt, system_prompt)` 介面
  - 實作 `MockLLMClient`：依 agent 角色回傳固定格式的假輸出，輸出中標示 `[MOCK MODE]`
  - 實作 `OpenAIClient`、`AnthropicClient` 框架（保留 API 呼叫位置但可暫時只啟用 mock）
  - 實作 `get_llm_client(agent_config)` factory 函式：無 API key 時自動回傳 MockLLMClient
  - 撰寫 `tests/test_llm_client.py`，使用 mock 模式驗證輸出格式
  - _Requirements: 4.3, 4.4, 4.5_

- [ ] 6. 實作四個 Agent 類別
- [ ] 6.1 實作 `agents/base_agent.py`
  - 定義 `BaseAgent` 抽象類，含 `process(task, context)` 介面與日誌寫入邏輯
  - 實作 retry 邏輯（tenacity，最多 max_retries 次）
  - 實作 log 寫入：輸出到 `projects/{project}/logs/{timestamp}_{agent}_{task_id}.log`
  - _Requirements: 9.1, 9.2, 6.5_

- [ ] 6.2 實作 `agents/pm_agent.py`
  - 繼承 BaseAgent，實作 `process()`：呼叫 LLM 產生任務分析與執行計畫
  - 輸出格式包含任務摘要與子步驟，作為後續 agents 的 context
  - 撰寫 `tests/test_pm_agent.py`（使用 MockLLMClient）
  - _Requirements: 2.3, 6.2_

- [ ] 6.3 實作 `agents/swe_agent.py`
  - 繼承 BaseAgent，接收 PM 輸出作為 context，呼叫 LLM 產生程式碼
  - 重工時接收 QA 的失敗報告作為 context 補充
  - 撰寫 `tests/test_swe_agent.py`（含重工情境）
  - _Requirements: 2.1, 6.2, 6.3_

- [ ] 6.4 實作 `agents/qa_agent.py`
  - 繼承 BaseAgent，接收 SWE 輸出作為 context，產生測試報告，決定 pass/fail
  - fail 時輸出結構化失敗報告供 SWE 使用
  - 撰寫 `tests/test_qa_agent.py`（含 pass 和 fail 兩種情境）
  - _Requirements: 2.4, 6.3, 6.4_

- [ ] 6.5 實作 `agents/devops_agent.py`
  - 實作週期性監控邏輯（每 30 秒）：CPU/記憶體使用率、失敗任務數量、磁碟空間
  - 異常時產生 `devops:alert` 事件（level: info/warning/critical）
  - 撰寫 `tests/test_devops_agent.py`（模擬異常觸發警告）
  - _Requirements: 2.5_

- [ ] 7. 實作流水線排程器
  - 撰寫 `core/pipeline.py`：`PipelineOrchestrator` 類
  - 實作 `asyncio.PriorityQueue` 依 priority 排序（critical=0, high=1, medium=2, low=3）
  - 實作 PM → SWE → QA 流水線執行，每個步驟完成後廣播 WebSocket 事件
  - 實作重工邏輯：QA fail 時將任務回送 SWE，超過 max_reworks 標記 failed
  - 撰寫 `tests/test_pipeline.py`，覆蓋正常流程、重工流程、超過重工上限三種情境
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [ ] 8. 實作 WebSocket 管理器與 REST API
- [ ] 8.1 實作 `core/ws_manager.py`
  - 管理多個 WebSocket 連線，依 `project_id` 分組廣播
  - 實作 `broadcast(project_id, event, data)` 方法
  - 撰寫 `tests/test_ws_manager.py`（使用 FastAPI TestClient 的 WebSocket 測試）
  - _Requirements: 7.1, 7.2_

- [ ] 8.2 實作 REST API endpoints
  - `GET /api/projects` — 回傳所有專案清單
  - `GET /api/projects/{project_id}` — 回傳單一專案詳情含 agent 狀態快照
  - `GET /api/projects/{project_id}/tasks` — 回傳任務列表
  - `GET /api/health` — 系統健康檢查
  - 撰寫 `tests/test_api.py`（FastAPI TestClient）
  - _Requirements: 1.2, 1.3_

- [ ] 8.3 實作 WebSocket endpoint
  - `WS /ws/{project_id}` — 客戶端連線後推送當前狀態快照，後續即時推送事件
  - 整合 `PipelineOrchestrator` 與 `DevOpsAgent` 的事件廣播
  - _Requirements: 7.1, 7.6, 7.8_

- [ ] 9. 整合 `main.py` 與啟動流程
  - 撰寫 `backend/main.py`：初始化 FastAPI app，掛載所有 router
  - 啟動時執行：`ProjectScanner` 掃描 → 建立缺失 agent 配置 → 啟動 `TaskWatcher` → 啟動 `DevOpsAgent` 週期任務
  - 在 startup log 中列出已偵測到的 LLM providers 與 mock mode 狀態
  - _Requirements: 1.1, 2.2, 4.1, 4.3, 4.4_

- [ ] 10. 建立 Vue 3 前端專案並移植現有樣式
- [ ] 10.1 初始化 Vue 3 + Vite 專案結構
  - 建立 `frontend/package.json`，依賴：`vue@3`、`pinia`、`vite`、`@vitejs/plugin-vue`
  - 建立 `vite.config.js`，設定 `/api` 和 `/ws` proxy 到 backend（開發環境用）
  - 建立 `frontend/Dockerfile`（兩階段：node build → nginx serve）
  - 移植現有三個 CSS 文件到 `src/styles/`（variables.css、animations.css、stations.css）
  - 建立 `src/main.js` 掛載 Vue app 與 Pinia
  - _Requirements: 8.1_

- [ ] 10.2 實作 Pinia stores
  - 撰寫 `stores/pipeline.js`：station states（pm/swe/qa）、task queue counters、mock mode flag
  - 撰寫 `stores/devops.js`：DevOps 警告列表、系統健康指標（cpu/memory/disk）
  - _Requirements: 7.2, 7.3, 7.8_

- [ ] 10.3 實作 WebSocket composable 與專案資料取得
  - 撰寫 `composables/useWebSocket.js`：WS 連線到 `/ws/{project_id}`、指數退避重連（2s→5s→10s）、收到事件後更新對應 Pinia store
  - 撰寫 `composables/useProjects.js`：fetch `GET /api/projects`，回傳 projects ref
  - _Requirements: 1.2, 7.1, 7.6, 7.7_

- [ ] 10.4 實作 Vue 元件
  - 撰寫 `ProjectSelector.vue`：呼叫 useProjects，渲染 select box，切換時觸發 WS 重連
  - 撰寫 `StationCard.vue`：接收 station props，渲染狀態燈、進度條、queue count、activity log
  - 撰寫 `ConveyorBelt.vue`：依 pipeline store 渲染 task items 位置與移動動畫
  - 撰寫 `ManagerOffice.vue`：配額進度條、指標數字、優先級分布條
  - 撰寫 `DevOpsPanel.vue`：健康燈號（green/yellow/red）、最近 5 條警告列表
  - 撰寫 `MockModeBanner.vue`：pipeline store 的 mock mode flag 控制顯示
  - 撰寫 `App.vue`：組合所有元件，初始化 useWebSocket，斷線時顯示警告橫幅
  - _Requirements: 1.2, 1.3, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_
