# Project Structure

```
/ (repo root)
├── docker-compose.yml
├── .env.example              # 所有支援的環境變數範本
├── .env                      # 本地 secret（gitignored）
│
├── projects/                 # volume mount，使用者放專案的地方
│   └── {project-name}/
│       ├── agents/
│       │   ├── PM_agent.yaml
│       │   ├── SWE_agent.yaml
│       │   ├── QA_agent.yaml
│       │   └── DevOps_agent.yaml
│       ├── tasks.yaml        # 主要任務定義
│       ├── TODO.md           # 可選，補充任務來源
│       └── logs/             # agent 執行日誌，自動生成
│           └── {timestamp}_{agent}_{task_id}.log
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py               # FastAPI entry point，掛載所有 router
│   ├── config.py             # 環境變數掃描、mock mode 判定
│   ├── api/
│   │   ├── projects.py       # GET /api/projects, GET /api/projects/{id}
│   │   ├── tasks.py          # GET /api/projects/{id}/tasks
│   │   └── websocket.py      # WS /ws/{project_id}
│   ├── agents/
│   │   ├── base_agent.py     # 抽象基類，含 retry 與 log 寫入邏輯
│   │   ├── pm_agent.py
│   │   ├── swe_agent.py
│   │   ├── qa_agent.py
│   │   └── devops_agent.py   # 獨立週期監控，不在流水線上
│   ├── core/
│   │   ├── project_scanner.py  # 掃描 projects/ 目錄
│   │   ├── agent_config.py     # 讀取/建立 agent YAML 配置
│   │   ├── task_watcher.py     # watchdog 監控 tasks.yaml 變更
│   │   ├── pipeline.py         # PipelineOrchestrator，流水線排程
│   │   ├── llm_client.py       # LLM 抽象層 + MockLLMClient
│   │   └── ws_manager.py       # WebSocket 連線管理，依 project_id 分組廣播
│   ├── models/
│   │   ├── project.py
│   │   ├── task.py
│   │   └── agent.py
│   └── tests/
│       ├── test_models.py
│       ├── test_config.py
│       ├── test_project_scanner.py
│       ├── test_agent_config.py
│       ├── test_task_watcher.py
│       ├── test_llm_client.py
│       ├── test_pm_agent.py
│       ├── test_swe_agent.py
│       ├── test_qa_agent.py
│       ├── test_devops_agent.py
│       ├── test_pipeline.py
│       ├── test_ws_manager.py
│       └── test_api.py
│
└── frontend/
    ├── Dockerfile            # 兩階段：Vite build → Nginx serve
    ├── nginx.conf            # 反向代理 /api 和 /ws 到 backend:8000
    ├── package.json
    ├── vite.config.js
    ├── index.html            # Vite entry
    └── src/
        ├── main.js           # Vue app entry，掛載 Pinia
        ├── App.vue           # 根元件，layout 與 WebSocket 初始化
        ├── components/
        │   ├── ProjectSelector.vue   # 專案 select box
        │   ├── ManagerOffice.vue     # 指揮中心面板（配額/指標/優先級）
        │   ├── ConveyorBelt.vue      # 輸送帶動畫與 task item
        │   ├── StationCard.vue       # PM/SWE/QA station card（共用元件）
        │   ├── DevOpsPanel.vue       # DevOps 獨立監控面板
        │   └── MockModeBanner.vue    # [MOCK MODE] 指示器
        ├── composables/
        │   ├── useWebSocket.js       # WebSocket 連線、重連邏輯
        │   └── useProjects.js        # GET /api/projects 資料取得
        ├── stores/
        │   ├── pipeline.js           # Pinia store：流水線狀態、station states
        │   └── devops.js             # Pinia store：DevOps 警告與健康指標
        ├── assets/
        │   └── fonts.css             # Google Fonts import
        └── styles/
            ├── variables.css         # CSS custom properties（主題 token）
            ├── animations.css        # @keyframes 動畫
            └── stations.css          # Station card 元件樣式
```

## 重要慣例

- 新的 backend 模組放在對應的 `api/`、`agents/`、`core/`、`models/` 目錄下，不要放在 `backend/` 根目錄
- 每個新模組都應有對應的 `tests/test_{module}.py`
- 前端新元件放在 `frontend/src/components/`，共用邏輯抽成 composable
- `projects/` 目錄內的任何內容都由系統自動管理，不應 hardcode 路徑
- Agent 日誌統一由 `base_agent.py` 寫入，不要在各 agent 子類別重複實作
