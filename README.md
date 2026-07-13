# Agent Factory Platform

將 AI agents 協作流水線視覺化的開發自動化平台。把專案放進 `projects/` 目錄，
系統會自動識別專案、初始化 agents，並在偵測到新任務時觸發流水線執行，透過工廠
流水線風格的即時 HUD 介面呈現每個 agent 的工作狀態。

任務依序流過三個站點：**PM_agent → SWE_agent → QA_agent**（QA 失敗會回送 SWE
重工，最多 3 次）。**DevOps_agent** 獨立於流水線之外，週期性監控系統健康。

## Quickstart（三步驟）

只要有 Docker，三個步驟就能跑起來：

```bash
# 1. 取得程式碼
git clone <this-repo-url>
cd agents_live_action

# 2. 建構並啟動前後端服務
docker compose up

# 3. 開啟瀏覽器
open http://localhost
```

前端在 http://localhost（Nginx，port 80），後端 API 在 http://localhost:8000。
瀏覽器打開後選擇 `example-project`，就能看到內建的示範任務跑完整個 PM → SWE → QA
流水線。

關閉服務：

```bash
docker compose down
```

## Mock 模式（免 API key）

**不需要任何 LLM API key 也能完整體驗。** 當偵測不到任何供應商金鑰時，系統會自動
進入 Mock 模式：流水線照常運作，agent 輸出會標示 `[MOCK MODE]`，前端 header 也會
顯示 `[MOCK MODE]` 指示器。這讓你可以在零設定的情況下直接看到範例任務跑完全程。

要接上真實模型，複製環境變數範本並填入金鑰後重啟：

```bash
cp .env.example .env
# 編輯 .env，填入 OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY 其中之一
docker compose up
```

金鑰只透過 `.env` 或 host 環境變數注入，不會寫入映像檔。

## 專案結構

`./projects/` 以 volume mount 掛載進容器，主機與容器雙向同步。每個子資料夾即是一個
專案：

```
projects/
└── example-project/
    ├── tasks.yaml        # 任務定義（本專案已內建 3 個示範任務）
    ├── agents/           # agent 配置，首次掃描時自動建立
    └── logs/             # agent 執行日誌，自動生成
```

想新增自己的專案，只要在 `projects/` 底下建立一個資料夾並放入 `tasks.yaml` 即可，
系統會自動識別並補齊缺少的 `agents/` 預設配置。

### tasks.yaml 格式

```yaml
tasks:
  - id: task-001
    title: "任務標題"
    description: "任務描述"
    priority: high        # critical | high | medium | low
    status: pending       # pending | in_progress | done | failed
    created_at: "2026-01-01T09:00:00"
    updated_at: "2026-01-01T09:00:00"
```

新出現的 `status: pending` 任務會自動送入流水線；任務狀態更新後會同步回寫到
`tasks.yaml`。也可以用 `TODO.md` 的 `- [ ]` 項目作為補充任務來源。

## 本地開發（不用 Docker）

```bash
# 後端
cd backend
uvicorn main:app --reload --port 8000
pytest                    # 執行測試

# 前端
cd frontend
npm install
npm run dev               # Vite dev server，port 5173
```

## 技術棧

- **後端**：Python 3.11+ / FastAPI / Pydantic v2 / watchdog / WebSocket
- **前端**：Vue 3 + Vite + Pinia，Nginx 提供靜態檔案並反向代理 `/api`、`/ws`
- **部署**：Docker Compose（frontend + backend 兩個 service）
