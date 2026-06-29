# Requirements Document

## Introduction

將現有的「FACTORY // Agent Assembly Line」前端視覺化頁面升級為一個真正可驅動 AI agents 開發專案的平台。使用者只需將專案放入 `projects/` 資料夾，系統會自動讀取 agent 配置、建立預設 agents、並在有新任務進來時觸發流水線執行。後端以 Python FastAPI 為主，前端保留工廠流水線動畫風格，並透過 WebSocket 即時顯示 agent 執行狀態。

## Requirements

### Requirement 1: 專案管理

**User Story:** As a 使用者, I want 將專案資料夾放入 `projects/` 目錄後系統自動識別, so that 我不需要手動在系統中登錄專案。

#### Acceptance Criteria

1. WHEN 使用者將任意資料夾放入 `projects/` 目錄 THEN 系統 SHALL 自動掃描並識別該資料夾為一個專案。
2. WHEN 前端頁面載入 THEN 系統 SHALL 透過 API 取得所有可用專案清單並顯示在 select box 中。
3. WHEN 使用者在 select box 中選擇一個專案 THEN 系統 SHALL 切換顯示該專案的 agents 狀態與任務流水線。
4. WHEN `projects/` 目錄中有新增或移除的專案資料夾 THEN 系統 SHALL 在下次 API 查詢時反映最新狀態。

### Requirement 2: Agent 角色定義

**User Story:** As a 使用者, I want 系統內建四個專業 agent 角色自動協作, so that 專案從需求規劃到環境監控都有對應的 agent 負責。

#### Acceptance Criteria

1. WHEN 系統初始化一個專案 THEN 系統 SHALL 建立以下四個預設 agent 角色：
   - `PM_agent`：專案經理，負責分析任務需求、拆解工作項目、追蹤執行進度，並協調 SWE 與 QA agents 的工作順序。
   - `SWE_agent`：資深開發工程師，負責根據 PM 的規劃撰寫程式碼、解決技術問題。
   - `QA_agent`：資深 QA 工程師，負責設計測試案例、執行測試、回報缺陷並驗證修復結果。
   - `DevOps_agent`：DevOps 工程師，獨立於主流水線之外，持續監控系統整體穩定度與環境健康狀態，並在偵測到問題時主動記錄並警示。
2. WHEN 任務進入流水線 THEN 系統 SHALL 依照 PM → SWE → QA 的順序執行，DevOps_agent 不參與流水線排程。
3. WHEN PM_agent 分析完任務 THEN 系統 SHALL 讓 PM_agent 輸出任務摘要與執行計畫，作為後續 agents 的上下文。
4. WHEN QA_agent 測試失敗 THEN 系統 SHALL 將失敗報告回傳給 SWE_agent 進行修復，並由 PM_agent 記錄重工狀態。
5. WHEN DevOps_agent 偵測到系統異常（如容器崩潰、資源超限、服務不可達）THEN 系統 SHALL 讓 DevOps_agent 記錄事件並在前端監控面板顯示警告。

### Requirement 3: Agent 配置管理

**User Story:** As a 使用者, I want 系統自動讀取或建立 agent 配置文件, so that 我可以客製化每個專案的 agents 或使用預設配置快速開始。

#### Acceptance Criteria

1. WHEN 系統掃描到專案資料夾 THEN 系統 SHALL 在該資料夾內尋找 `agents/` 子目錄下的 agent 配置 YAML 文件。
2. WHEN 專案內不存在 `agents/` 目錄或無配置文件 THEN 系統 SHALL 自動建立四個預設配置文件：`agents/PM_agent.yaml`、`agents/SWE_agent.yaml`、`agents/QA_agent.yaml`、`agents/DevOps_agent.yaml`。
3. WHEN agent 配置文件存在 THEN 系統 SHALL 解析文件並根據配置初始化對應的 agent 實例。
4. WHEN 讀取 agent 配置 THEN 系統 SHALL 支援以下欄位：agent 名稱、角色描述、使用的 LLM 模型、環境變數名稱參照、系統提示詞（system prompt）、以及最大任務並發數。
5. IF agent 配置文件格式錯誤 THEN 系統 SHALL 記錄錯誤並回退使用預設配置，同時在前端顯示警告訊息。

### Requirement 4: LLM 模型整合與環境變數管理

**User Story:** As a 使用者, I want 透過環境變數配置 LLM API 金鑰, so that 我可以彈性切換不同的 AI 模型而不修改程式碼。

#### Acceptance Criteria

1. WHEN 系統啟動 THEN 系統 SHALL 掃描已知的 LLM 供應商環境變數（如 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY`）並偵測哪些已設定。
2. WHEN 使用者查詢可用模型 THEN 系統 SHALL 只回傳對應環境變數已設定的 LLM 供應商與模型選項。
3. WHEN agent 執行時環境變數未設定 THEN 系統 SHALL 使用測試模式（mock LLM）產生模擬回應，確保流水線可正常運作。
4. WHEN 系統運行在測試模式 THEN 系統 SHALL 在 agent 日誌和前端介面中清楚標示為 `[MOCK MODE]`。
5. WHEN agent 配置文件參照一個環境變數 THEN 系統 SHALL 在初始化時驗證該環境變數是否存在並在缺失時給予明確提示。

### Requirement 5: 任務格式與管理

**User Story:** As a 使用者, I want 透過 YAML tasks 文件定義要執行的任務, so that 我可以用簡單易讀的格式管理專案的工作項目。

#### Acceptance Criteria

1. WHEN 系統掃描專案 THEN 系統 SHALL 在專案根目錄尋找 `tasks.yaml` 或 `tasks/` 目錄下的 YAML 文件。
2. WHEN 任務文件存在 THEN 系統 SHALL 解析每個任務的以下欄位：`id`、`title`、`description`、`priority`（critical/high/medium/low）、`status`（pending/in_progress/done/failed）。
3. WHEN 系統偵測到 `TODO.md` 文件 THEN 系統 SHALL 解析其中的 `- [ ]` 格式項目作為補充任務來源。
4. WHEN 任務文件中有新增 `status: pending` 的任務 THEN 系統 SHALL 自動觸發流水線將任務送入 agent 處理隊列。
5. WHEN 任務狀態更新 THEN 系統 SHALL 同步回寫到對應的 YAML 文件，使文件始終反映最新狀態。

### Requirement 6: Agent 流水線執行

**User Story:** As a 使用者, I want 新任務進來時自動觸發 agents 協作的流水線, so that 我不需要手動啟動每個 agent 的處理流程。

#### Acceptance Criteria

1. WHEN 偵測到新的 pending 任務 THEN 系統 SHALL 依序將任務送入 PM_agent → SWE_agent → QA_agent 的流水線。
2. WHEN 任務進入某個 agent 站點 THEN 系統 SHALL 呼叫對應 agent 處理任務，並將前一個 agent 的輸出作為上下文傳入。
3. WHEN QA_agent 判定任務失敗 THEN 系統 SHALL 將任務與失敗報告回送至 SWE_agent 重工，並通知 PM_agent 記錄狀態。
4. WHEN 重工次數超過配置上限（預設 3 次）THEN 系統 SHALL 將任務標記為 failed 並通知前端。
5. WHEN 任何 agent 執行過程中發生例外 THEN 系統 SHALL 捕捉錯誤、記錄至日誌，並將任務標記為 failed 而不中斷整體流水線。
6. WHEN 多個任務同時 pending THEN 系統 SHALL 根據任務 priority 排序，優先處理 critical > high > medium > low。

### Requirement 7: 即時前端視覺化

**User Story:** As a 使用者, I want 在保有工廠流水線動畫風格的前端介面中即時看到 agent 執行狀態, so that 我可以直觀監控每個專案的開發進度。

#### Acceptance Criteria

1. WHEN 後端 agent 狀態更新 THEN 系統 SHALL 透過 WebSocket 將更新推送到前端，延遲不超過 500ms。
2. WHEN 前端接收到 WebSocket 更新 THEN 系統 SHALL 更新對應 station card 的狀態、進度條、任務名稱，以及 activity log。
3. WHEN 前端渲染流水線 THEN 系統 SHALL 顯示三個流水線 station cards（PM_agent、SWE_agent、QA_agent）加上一個獨立的 DevOps_agent 監控面板。
4. WHEN 使用者切換 select box 中的專案 THEN 系統 SHALL 重置流水線顯示並載入該專案當前的 agent 狀態。
5. WHEN 任務在各站點之間移動 THEN 系統 SHALL 保留現有的輸送帶動畫效果。
6. WHEN agent 運行於測試模式 THEN 前端 SHALL 在 header 顯示 `[MOCK MODE]` 指示器。
7. WHEN 後端 WebSocket 連線中斷 THEN 前端 SHALL 顯示連線狀態警告並自動嘗試重連。
8. WHEN DevOps_agent 發出警告事件 THEN 前端 SHALL 在 DevOps 監控面板以醒目方式顯示警告訊息與系統健康指標。

### Requirement 8: Docker 容器化部署

**User Story:** As a 使用者, I want 整個系統以 Docker 方式建立和運行, so that 我不需要在本機手動安裝 Python 依賴或配置環境。

#### Acceptance Criteria

1. WHEN 使用者執行 `docker compose up` THEN 系統 SHALL 自動建構並啟動後端 API 服務與前端靜態服務。
2. WHEN Docker 容器啟動 THEN 系統 SHALL 將本機的 `projects/` 目錄以 volume mount 方式掛載到容器內，確保專案資料不隨容器消失。
3. WHEN Docker 容器啟動 THEN 系統 SHALL 從 `.env` 文件或 host 環境變數讀取 LLM API 金鑰，不將任何金鑰寫入映像檔。
4. WHEN 使用者需要新增環境變數 THEN 系統 SHALL 提供 `.env.example` 範本文件列出所有支援的環境變數及說明。
5. WHEN 容器內的 `projects/` 資料有變更 THEN 系統 SHALL 確保變更同步反映到 host 本機目錄（雙向同步）。
6. WHEN 使用者執行 `docker compose down` THEN 系統 SHALL 優雅地關閉所有服務，不遺留孤立的 agent 進程。

### Requirement 9: 執行日誌

**User Story:** As a 使用者, I want 每個 agent 的執行記錄被保存到各專案的 logs 資料夾, so that 我可以事後查閱每個任務的處理詳情。

#### Acceptance Criteria

1. WHEN agent 開始處理任務 THEN 系統 SHALL 在 `projects/{project_name}/logs/` 目錄建立對應的日誌文件，命名格式為 `{timestamp}_{agent_name}_{task_id}.log`。
2. WHEN agent 產生輸出（包含 mock 輸出）THEN 系統 SHALL 將輸入 prompt、輸出回應、執行時間寫入日誌文件。
3. WHEN 前端 activity log 顯示訊息 THEN 系統 SHALL 同步將相同訊息保存到日誌文件中。
