# Design Document: Agent Factory Platform

## Overview

將現有純前端的「FACTORY // Agent Assembly Line」視覺化模擬，升級為一個真實驅動 AI agents 的開發自動化平台。後端以 Python FastAPI 實作，透過 WebSocket 將 agent 執行狀態即時推送到前端。整個系統以 Docker Compose 封裝，使用者只需把專案放入 `projects/` 目錄，系統即自動識別並啟動 agents 協作流水線。

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Docker Compose                     │
│                                                     │
│  ┌──────────────────┐    ┌─────────────────────┐   │
│  │  frontend        │    │  backend (FastAPI)  │   │
│  │  (Nginx)         │◄───│                     │   │
│  │  port: 80        │    │  port: 8000         │   │
│  │                  │    │                     │   │
│  │  - index.html    │    │  - REST API         │   │
│  │  - js/           │    │  - WebSocket        │   │
│  │  - css/          │    │  - Agent runner     │   │
│  └──────────────────┘    │  - Task watcher     │   │
│                          └──────────┬──────────┘   │
│                                     │ volume mount  │
│                          ┌──────────▼──────────┐   │
│                          │  ./projects/        │   │
│                          │  (host directory)   │   │
│                          └─────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

資料流向：
1. 使用者在 `projects/` 內新增任務 → 後端 `TaskWatcher` 偵測到變更
2. 新任務進入優先級隊列 → `PipelineOrchestrator` 啟動流水線
3. PM → SWE → QA agents 依序執行（失敗時回送 SWE 重工）
4. `DevOpsAgent` 獨立執行，週期性監控系統健康
5. 每個狀態變更透過 `WebSocketManager` 廣播到前端
6. 前端接收事件並更新流水線動畫

## Components and Interfaces

### Backend Structure

```
backend/
├── main.py                  # FastAPI app entry point
├── config.py                # 環境變數與系統設定
├── requirements.txt
├── api/
│   ├── projects.py          # GET /projects, GET /projects/{id}
│   ├── tasks.py             # GET/POST /projects/{id}/tasks
│   └── websocket.py         # WS /ws/{project_id}
├── agents/
│   ├── base_agent.py        # 所有 agents 的抽象基類
│   ├── pm_agent.py          # PM Agent 實作
│   ├── swe_agent.py         # SWE Agent 實作
│   ├── qa_agent.py          # QA Agent 實作
│   └── devops_agent.py      # DevOps 監控 Agent 實作
├── core/
│   ├── project_scanner.py   # 掃描 projects/ 目錄
│   ├── agent_config.py      # 讀取/建立 agent YAML 配置
│   ├── task_watcher.py      # 監控 tasks.yaml 變更 (watchdog)
│   ├── pipeline.py          # 流水線排程與執行
│   ├── llm_client.py        # LLM API 抽象層 + mock 模式
│   └── ws_manager.py        # WebSocket 連線管理
└── models/
    ├── project.py           # Project Pydantic model
    ├── task.py              # Task Pydantic model
    └── agent.py             # AgentConfig Pydantic model
```

### Frontend Structure（Vue 3 + Vite）

現有的純 vanilla JS 前端重構為 Vue 3，保留所有 CSS 動畫與 sci-fi HUD 視覺風格：

```
frontend/
├── Dockerfile              # 兩階段：npm run build → Nginx serve dist/
├── nginx.conf
├── package.json            # vue@3, pinia, vite
├── vite.config.js
├── index.html
└── src/
    ├── main.js             # createApp + Pinia
    ├── App.vue             # 根元件，WebSocket 初始化，整體 layout
    ├── components/
    │   ├── ProjectSelector.vue   # header 的專案 select box
    │   ├── ManagerOffice.vue     # 指揮中心（配額進度、指標、優先級分布）
    │   ├── ConveyorBelt.vue      # 輸送帶動畫，task item 移動
    │   ├── StationCard.vue       # 共用 station card（PM/SWE/QA 各傳入不同 props）
    │   ├── DevOpsPanel.vue       # 獨立監控面板（右側），健康燈號與警告列表
    │   └── MockModeBanner.vue    # [MOCK MODE] header 指示器
    ├── composables/
    │   ├── useWebSocket.js       # WS 連線、指數退避重連、事件派發
    │   └── useProjects.js        # fetch /api/projects，回傳 projects ref
    ├── stores/
    │   ├── pipeline.js           # Pinia：station states、task queue、counters
    │   └── devops.js             # Pinia：DevOps 警告事件、系統健康指標
    └── styles/
        ├── variables.css         # CSS custom properties（直接移植原 styles.css tokens）
        ├── animations.css        # @keyframes（直接移植原 animations.css）
        └── stations.css          # Station 元件樣式（直接移植原 stations.css）
```

原有 CSS 動畫（conveyor scroll、HUD corners、particle effects）直接移植，不需重寫。

### Key Interfaces

#### WebSocket 事件格式

所有後端推送事件統一格式：
```json
{
  "event": "station:update | task:created | task:completed | devops:alert | project:switched",
  "project_id": "my-project",
  "data": { ... }
}
```

`station:update` data:
```json
{
  "station_id": "pm | swe | qa",
  "status": "idle | working | error",
  "current_task": { "id": "...", "title": "...", "priority": "high" },
  "progress": 0.65,
  "queue_size": 3,
  "processed": 12,
  "log": { "message": "Processing task...", "type": "start | done | error" }
}
```

`devops:alert` data:
```json
{
  "level": "info | warning | critical",
  "message": "Container memory usage at 85%",
  "metric": "memory",
  "value": 85
}
```

#### Agent 配置 YAML 格式

```yaml
# agents/PM_agent.yaml
name: PM_agent
role: Project Manager
description: 分析任務需求、拆解工作項目、協調執行順序
llm_provider: openai          # openai | anthropic | gemini | mock
llm_model: gpt-4o-mini
api_key_env: OPENAI_API_KEY   # 對應環境變數名稱
max_retries: 3
system_prompt: |
  You are a senior project manager. Analyze the task and produce
  a structured execution plan with clear acceptance criteria.
```

#### Tasks YAML 格式

```yaml
# tasks.yaml
tasks:
  - id: task-001
    title: "Implement user authentication"
    description: "Add JWT-based auth with refresh token support"
    priority: high              # critical | high | medium | low
    status: pending             # pending | in_progress | done | failed
    created_at: "2026-06-13T10:00:00"
    updated_at: "2026-06-13T10:00:00"
```

## Data Models

### Project
```python
class Project(BaseModel):
    id: str                    # 資料夾名稱
    name: str                  # 顯示名稱
    path: str                  # 絕對路徑
    agents: list[AgentConfig]
    task_count: int
    active_task_count: int
    status: str                # idle | running | error
```

### Task
```python
class Task(BaseModel):
    id: str
    title: str
    description: str
    priority: Literal["critical", "high", "medium", "low"]
    status: Literal["pending", "in_progress", "done", "failed"]
    rework_count: int = 0
    max_reworks: int = 3
    created_at: datetime
    updated_at: datetime
    pm_output: str | None      # PM agent 的規劃輸出
    swe_output: str | None     # SWE agent 的程式碼輸出
    qa_output: str | None      # QA agent 的測試報告
```

### AgentConfig
```python
class AgentConfig(BaseModel):
    name: str
    role: str
    description: str
    llm_provider: str          # "mock" 時強制使用 mock LLM
    llm_model: str
    api_key_env: str
    max_retries: int = 3
    system_prompt: str
```

### PipelineState（記憶體中）
```python
@dataclass
class PipelineState:
    project_id: str
    station_states: dict[str, StationState]   # pm, swe, qa
    task_queue: asyncio.PriorityQueue
    running: bool
```

## Error Handling

### LLM 呼叫失敗
- 使用 tenacity 做 exponential backoff retry（最多 3 次）
- 超過 retry 上限 → 任務標記 `failed`，記錄錯誤到 log，不中斷流水線
- 若環境變數不存在 → 啟動時自動切換為 mock 模式並在前端顯示 `[MOCK MODE]`

### Task 文件解析失敗
- YAML 格式錯誤 → 記錄 warning，跳過該任務，繼續處理其他任務
- 文件不存在 → 建立空的 `tasks.yaml` 範本

### Agent 配置錯誤
- 欄位缺失 → 以預設值填充並記錄 warning
- 完全無法解析 → 回退使用 hardcoded 預設配置

### WebSocket 斷線
- 前端自動重連，間隔 2s → 5s → 10s（指數退避上限 10s）
- 重連後向後端請求當前專案完整狀態快照（REST API）

### 重工上限
- 預設 `max_reworks: 3`，可在 agent 配置中覆寫
- 超過上限 → 任務標記 `failed`，前端顯示紅色失敗動畫

## Testing Strategy

### Backend Unit Tests（pytest）
- `test_project_scanner.py` — 測試目錄掃描、配置自動建立
- `test_agent_config.py` — 測試 YAML 解析、錯誤回退邏輯
- `test_task_watcher.py` — 測試新任務偵測、狀態回寫
- `test_pipeline.py` — 測試流水線排程、優先級排序、重工邏輯
- `test_llm_client.py` — 測試 mock 模式、環境變數偵測
- `test_agents.py` — 各 agent 的 mock LLM 輸入輸出驗證

### Integration Tests
- `test_api.py` — REST API 端點測試（FastAPI TestClient）
- `test_websocket.py` — WebSocket 事件推送測試

### Frontend Tests
- 現有前端邏輯改以 mock WebSocket 事件驅動，可用 Playwright 做 E2E 視覺驗證

### Mock LLM
所有測試預設使用 mock LLM（不需要真實 API 金鑰），mock 回應依 agent 角色回傳固定格式的假輸出，確保流水線邏輯可以完整跑通。

## DevOps Agent 設計

DevOps_agent 獨立運行，不在流水線上，以週期性任務模式執行：

```
監控週期（每 30 秒）：
  1. 檢查後端服務健康（記憶體、CPU、uptime）
  2. 檢查 projects/ 目錄權限與空間
  3. 偵測失敗任務堆積（超過閾值發出 warning）
  4. 透過 ws_manager 廣播 devops:alert 事件
```

前端 DevOps 監控面板（右側獨立 panel）顯示：
- 系統健康燈號（綠/黃/紅）
- 最近 5 條警告訊息
- 整體系統 uptime

## Docker Compose 架構

```yaml
services:
  backend:
    build: ./backend
    ports: ["8000:8000"]
    volumes:
      - ./projects:/app/projects   # 雙向同步
    env_file: .env
    
  frontend:
    build: ./frontend              # Nginx serving static files
    ports: ["80:80"]
    depends_on: [backend]
```

前端 Nginx 同時反向代理 `/api` 和 `/ws` 到後端，避免跨域問題。

## 目錄結構（完整）

```
/ (專案根目錄)
├── docker-compose.yml
├── .env.example
├── .env                    (gitignored)
├── projects/               (volume mount)
│   └── my-project/
│       ├── agents/
│       │   ├── PM_agent.yaml
│       │   ├── SWE_agent.yaml
│       │   ├── QA_agent.yaml
│       │   └── DevOps_agent.yaml
│       ├── tasks.yaml
│       ├── TODO.md         (可選)
│       └── logs/
│           └── 20260613_120000_PM_agent_task-001.log
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   ├── config.py
│   ├── api/
│   ├── agents/
│   ├── core/
│   └── models/
└── frontend/               (現有靜態檔案)
    ├── Dockerfile
    ├── nginx.conf
    ├── index.html
    ├── js/
    └── css/
```
