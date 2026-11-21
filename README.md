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

金鑰只透過 `.env`（由 `docker compose` 帶入容器）或 host 環境變數注入，不會寫入映像檔。
本機不用 Docker 直接跑後端時不會讀取 `.env`，需自行 `export`（見下方「接上 Claude」）。

## 接上 Claude（Anthropic API）

流水線可以改用真實的 Claude 模型（非串流）。**真實呼叫會產生費用**，請先設定預算上限。

1. **設定金鑰與預算**（金鑰不要 commit，也不要貼進指令歷史）。後端與煙霧腳本**本身不會讀 `.env`**，
   `.env` 只有在 Docker 下才會生效，所以依你的執行方式擇一：

   - **Docker**：寫在 repo 根目錄的 `.env`，`docker compose` 的 `env_file` 會把它帶進容器：

     ```bash
     # repo 根目錄的 .env
     ANTHROPIC_API_KEY=你的金鑰
     PROJECT_BUDGET_USD=2      # 強烈建議：單一專案的花費上限（美元）
     ```

   - **本機直接執行**（`uvicorn`、煙霧腳本）：`.env` **不會**被讀取，要先把變數放進目前的 shell。
     兩種方式擇一（路徑以 `backend/` 為目前目錄）：

     ```bash
     # 方式 A：互動輸入，金鑰不會出現在指令歷史
     read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY
     export PROJECT_BUDGET_USD=2

     # 方式 B：把 repo 根目錄的 .env 載入目前的 shell（.env 須為 KEY=value 格式）
     set -a; source ../.env; set +a
     ```

     不要直接寫 `export ANTHROPIC_API_KEY=sk-...`：那會把金鑰留在 shell 歷史裡。

2. **把 agent 的 YAML 改成 anthropic**（每個專案的 `agents/*.yaml`，各 agent 可分開設定）：

   ```yaml
   llm_provider: anthropic
   llm_model: claude-opus-5-5
   api_key_env: ANTHROPIC_API_KEY
   ```

   沒有金鑰時仍會自動退回 Mock 模式，行為與以前相同。

3. **想省錢就換較便宜的模型**：把 `llm_model` 改成 `claude-sonnet-5-5` 或 `claude-haiku-5-5`
   （例如 PM 與 QA 用便宜的，SWE 用 `claude-opus-5-5`）。模型 ID 不要帶日期後綴。

**行為說明**

- **拒絕後援（fallback）**：`claude-opus-5-5`、`claude-sonnet-5-5`、`claude-fable-5-2` 會自動開啟
  server-side fallback——模型的安全分類器拒絕時，API 會在同一次呼叫內改由後援模型回答。
  `claude-haiku-5-5` 與未知模型不使用此功能。
- **花費是估計值**：用量照 API 回報的 token 數、依內建單價（`core/usage_tracker.py`）計算。
  發生 fallback 時實際由別的模型計費，所以實際花費可能不同；以 Anthropic 帳單為準。
- **預算上限**：超過 `PROJECT_BUDGET_USD`／`DAILY_BUDGET_USD` 時會停止接收新任務並發出 critical 警示。
  每一次呼叫都會計入用量，包括重試的每一次，以及被拒絕（refusal）或被截斷（`max_tokens`）的那次。
- 輸出被截斷或被拒絕時，任務會標示為失敗（不會把殘缺的輸出傳給下一站），且不會自動重試。

**先用煙霧腳本確認連線**（只送一次很短的請求，由你手動執行）：

```bash
cd backend
python scripts/anthropic_smoke.py                       # 預設 claude-opus-5-5
python scripts/anthropic_smoke.py --model claude-haiku-5-5
```

會印出回覆的前 200 字、`stop_reason`、回應的模型、token 用量與估計花費；沒有金鑰時會說明如何設定並以狀態碼 2 結束。

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

## Pipeline 引擎

PM → SWE → QA 的流程控制有兩種實作，用環境變數 `PIPELINE_ENGINE` 選擇：

- `langgraph`（預設）：以 LangGraph `StateGraph` 實作（`backend/core/pipeline_graph.py`），人工核准閘門使用 `interrupt()` + `Command(resume=...)`。
- `legacy`：原本的 `while` 迴圈（`backend/core/pipeline.py`），保留作為退路。

兩者對外行為相同（WebSocket 事件、`tasks.yaml` 回寫、交付檔）。要切回 legacy：

```bash
PIPELINE_ENGINE=legacy docker compose up --build    # Docker
PIPELINE_ENGINE=legacy uvicorn main:app --port 8000  # 本地
```

無效的值會在建立 pipeline 時直接報錯，訊息會列出合法值。

## 本地開發（不用 Docker）

```bash
# 後端
cd backend
uvicorn main:app --reload --port 8000
pytest                    # 執行測試

# 舊前端 frontend/：原生 JS 靜態檔，沒有 build 步驟（由 Nginx 提供，見 docker compose）
# 新前端 web/：見下一節
```

### 新前端 `web/`（React＋Vite＋TypeScript，開發中）

> **舊 `frontend/`（原生 JS 靜態頁，無 build）已凍結**：不修 bug、不加功能，直到新前端達到同等功能後在 F12 一次刪除，
> 並把 `web/` 改名為 `frontend/`。`docker compose` 目前仍提供舊前端。

需要 Node ≥ 24。

```bash
cd web
npm ci
npm run dev               # Vite dev server；/api、/ws 代理到 VITE_BACKEND_URL，預設 http://localhost:8001
npm run typecheck         # tsc -b --noEmit
npm run test              # Vitest（jsdom）
npm run build
npm run e2e               # Playwright 煙霧測試（AC10 a–f），見下
```

不需要後端也能看畫面：開 `http://localhost:5173/?replay=default-run`（另有 `custom-lineup`、`crowded`、
`crowded-3x8`、`gate`；`&speed=4` 加速；`?project=<id>` 指定專案）。後端的埠可用 `BACKEND_HOST_PORT`
或 `VITE_BACKEND_URL` 覆寫。

`npm run e2e` 會先 build，再用 `vite preview` 播放 replay，不連後端、不連網（Google Fonts 也攔下）。
不會自動下載瀏覽器：依序用 `PW_CHROMIUM_PATH`、`npx playwright install chromium` 裝的版本、Playwright
快取裡已有的 chrome-headless-shell。給 QA 的人工檢查清單在 [`web/QA-CHECKLIST.md`](web/QA-CHECKLIST.md)。

## 技術棧

- **後端**：Python 3.11+ / FastAPI / Pydantic v2 / watchdog / WebSocket
- **前端**：目前 `docker compose` 提供的是舊 `frontend/`（原生 JavaScript 靜態檔，無框架、無 build），Nginx 提供靜態檔案並反向代理 `/api`、`/ws`；新前端 `web/` 為 React＋Vite＋TypeScript（開發中，F12 取代舊版）
- **部署**：Docker Compose（frontend + backend 兩個 service）
