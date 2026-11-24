# Agent Factory Platform

> A visual multi-agent dev pipeline: PM → SWE → QA agents pick up tasks dropped into `projects/`,
> orchestrated with **LangGraph** (`StateGraph`, `interrupt` / `Command` for human approval gates),
> backed by the **Claude API**, and streamed live to a factory-floor HUD over WebSocket.
> Built spec-first (spec → plan → tasks) with separate Developer and QA agent sessions — see [`specs/`](specs/).
> Docs are in Traditional Chinese.

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

# 2. 建構並啟動服務（postgres、backend、frontend）
docker compose up --build

# 3. 開啟瀏覽器
open http://localhost:8080
```

前端在 `http://localhost:${FRONTEND_HOST_PORT:-8080}`（React 版 HUD，由非 root 的 Nginx 提供，容器內聽 8080，
只綁 `127.0.0.1`；用 `FRONTEND_HOST_PORT` 改埠）。
瀏覽器打開後選擇 `example-project`，就能看到內建的示範任務跑完整個 PM → SWE → QA
流水線。不需要 `.env`，沒有金鑰時就是 Mock 模式。

服務與埠：

| 服務 | 對外 | 說明 |
| --- | --- | --- |
| `frontend` | `127.0.0.1:8080`（`FRONTEND_HOST_PORT`） | Nginx（nginx-unprivileged，容器內 8080）：提供 React 版畫面，並把 `/api`、`/ws` 轉給 backend |
| `backend` | **不對外** | FastAPI；啟動時（`TASK_STORE=db`）先跑資料庫遷移。healthcheck 變 healthy 後前端才啟動 |
| `postgres` | `127.0.0.1:5433`（`POSTGRES_HOST_PORT`） | PostgreSQL 17，資料存在 volume `pgdata`；本機開發帳密 `factory`／`factory`／`factory`，可用 `POSTGRES_USER`／`POSTGRES_PASSWORD`／`POSTGRES_DB` 覆寫 |

`HUD_BIND_ADDR`（預設 `127.0.0.1`）決定埠綁在哪個位址；要讓區網其他機器連線才需要改。

**直接打 API**：backend 預設不對外。經前端即可：`curl http://localhost:8080/api/health`。
需要從主機直接連 backend 時，加上 override 檔（綁 `127.0.0.1:8001`，用 `BACKEND_HOST_PORT` 改埠）：

```bash
docker compose -f docker-compose.yml -f compose.expose-backend.yml up --build
curl http://localhost:8001/api/health
```

**不想產生任何真實 LLM 費用**（即使 `.env` 有金鑰）：`FORCE_MOCK=true docker compose up --build`，
再用 `curl http://localhost:8080/api/health` 確認回傳 `"force_mock": true`。

關閉服務：

```bash
docker compose down
```

## Mock 模式（免 API key）

**不需要任何 LLM API key 也能完整體驗。** 專案的預設模型就是 Mock；選了 Claude 但伺服器沒有
`ANTHROPIC_API_KEY`、或設了 `FORCE_MOCK=true` 時，也一律改用 Mock。流水線照常運作，agent 輸出會標示
`[MOCK MODE]`；所有 agent 都用 Mock 時，前端 header 也會顯示 `[MOCK MODE]`（原因是 `FORCE_MOCK` 或缺金鑰時一併註明）。這讓你可以在零設定的情況下直接看到範例任務跑完全程。

要接上真實模型，複製環境變數範本、填入 `ANTHROPIC_API_KEY` 後重啟，再到畫面上選模型（見下一節）：

```bash
cp .env.example .env
# 編輯 .env，填入 ANTHROPIC_API_KEY
docker compose up
```

**目前只有 Anthropic（Claude）能真的呼叫。** `.env.example` 裡的 `OPENAI_API_KEY`、`GEMINI_API_KEY` 只是佔位：
OpenAI 的 client 還沒接上真正的 API（呼叫會直接失敗），Gemini 沒有 client（一律退回 Mock）。畫面的模型選單與
API 也只接受 Mock 與 Claude 模型。

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

2. **在畫面上選模型**：header 的「模型」選單是專案預設（`PUT /api/projects/{id}/settings`），選 Claude
   模型前會先跳出確認（提醒會產生費用，並列出目前的預算上限）。可選 `claude-opus-5-5`、`claude-sonnet-5-5`、
   `claude-haiku-5-5`、`claude-fable-5-2`。流水線閒著時立刻生效，有任務在跑時從下一張開始。
   沒有金鑰時仍會改用 Mock，畫面會說明原因。

3. **想省錢就讓個別 agent 用較便宜的模型**：新增 agent 的表單可以選「模型」（預設跟著專案）；已存在的
   agent（含內建的 PM／SWE／QA）用 `PUT /api/projects/{id}/agents/{agent}` 帶
   `{"llm": {"provider": "anthropic", "model": "claude-haiku-5-5"}}`，`"llm": null` 表示跟著專案。
   模型 ID 不要帶日期後綴。

   以上需要資料庫模式（Docker 預設）。檔案模式（本機直接跑 `uvicorn`、沒設 `TASK_STORE=db`）下，
   模型仍讀各專案 `agents/*.yaml` 的 `llm_provider`／`llm_model`／`api_key_env`，畫面上的切換會被拒絕（409）。

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

新出現的 `status: pending` 任務會自動送入流水線。也可以用 `TODO.md` 的 `- [ ]` 項目作為補充任務來源。

任務狀態存在哪裡，看 `TASK_STORE`：

- **資料庫模式**（`TASK_STORE=db`，`docker compose` 固定用這個）：PostgreSQL 是任務、歷史、花費、agent 編制的
  事實來源。`tasks.yaml`／`TODO.md` 只是新增任務的入口：新的 id 會匯入成 `pending`，檔案裡的 `status`
  一律忽略，狀態**不會**回寫到 `tasks.yaml`。
- **檔案模式**（`TASK_STORE=file`，本機直接跑 `uvicorn` 時的預設）：`tasks.yaml` 就是事實來源，狀態更新會回寫進去。

## Pipeline 引擎

PM → SWE → QA 的流程控制有兩種實作，用環境變數 `PIPELINE_ENGINE` 選擇：

- `langgraph`（預設）：以 LangGraph `StateGraph` 實作（`backend/core/pipeline_graph.py`），人工核准閘門使用 `interrupt()` + `Command(resume=...)`。
- `legacy`：原本的 `while` 迴圈（`backend/core/pipeline.py`），保留作為退路。

兩者對外行為相同（WebSocket 事件、任務狀態的寫入、交付檔）。要切回 legacy：

```bash
PIPELINE_ENGINE=legacy PIPELINE_CHECKPOINTER=memory docker compose up --build    # Docker
PIPELINE_ENGINE=legacy uvicorn main:app --port 8001  # 本地
```

legacy 引擎不能修改編制與模型（新增／移除 agent、切換模型會回 409），這些需要 `langgraph`＋`TASK_STORE=db`。

無效的值會在建立 pipeline 時直接報錯，訊息會列出合法值。`legacy` 不使用 checkpointer，所以 Docker 下
要一併設 `PIPELINE_CHECKPOINTER=memory`（`postgres` 只支援 `langgraph` 引擎；不支援的旗標組合會在啟動時報錯）。

## 本地開發（不用 Docker）

```bash
# 後端（埠用 8001，與前端 Vite dev server 預設的代理目標一致）
cd backend
uvicorn main:app --reload --port 8001
pytest -m "not integration"   # 單元測試，全部離線
pytest -m integration         # 需要 Docker（Testcontainers 啟動真的 Postgres）；沒有 Docker 時略過

# 前端 frontend/：見下一節
```

要用別的埠，啟動前端時用 `BACKEND_HOST_PORT=<埠>` 或 `VITE_BACKEND_URL=http://localhost:<埠>` 指過去。
直接跑 `uvicorn` 時預設是檔案模式（`TASK_STORE=file`），編制與模型不能在畫面上改（見上面「任務狀態存在哪裡」）。

### 前端 `frontend/`（React＋Vite＋TypeScript）

舊的原生 JavaScript 前端已在 F12 移除，`frontend/` 現在就是 React 版；`docker compose` 會 build 它
（多階段：Node build → nginx-unprivileged 提供 `dist/`）。需要 Node ≥ 24。

```bash
cd frontend
npm ci
npm run dev               # Vite dev server；/api、/ws 代理到 VITE_BACKEND_URL，預設 http://localhost:8001
npm run typecheck         # tsc -b --noEmit
npm run test              # Vitest（jsdom）
npm run build
npm run e2e               # Playwright 煙霧測試（AC10 a–f），見下
```

不需要後端也能看畫面：開 `http://localhost:5173/?replay=default-run`（另有 `custom-lineup`、`crowded`、
`crowded-3x8`、`gate`、`recorded-mvp`（Task 26 從真後端錄的）；`&speed=4` 加速；`?project=<id>` 指定專案）。

`npm run e2e` 會先 build，再用 `vite preview` 播放 replay，不連後端、不連網（Google Fonts 也攔下）。
不會自動下載瀏覽器：依序用 `PW_CHROMIUM_PATH`、`npx playwright install chromium` 裝的版本、Playwright
快取裡已有的 chrome-headless-shell。設 `E2E_BASE_URL=http://localhost:8080` 時不起 `vite preview`，改對已在跑的
build（例如 Docker 的 nginx）跑同一份測試。

`npm run e2e:live` 是另一份煙霧測試（`tests/live/`），經前端的 nginx 打真後端：派任務、閘門退件再核准（鍵盤）、
新增／移除 agent、切換模型的確認、任務詳情（日誌、交付檔下載）、機房、手機寬度、reduced motion。**只對
`FORCE_MOCK=true` 的後端跑**（測試開頭先查 `/api/health`，不是就停）。**前提是乾淨的專案**：測試會在專案裡
新增任務與 agent，對已經跑過、留有任務或自訂 agent 的資料庫重跑不保證通過，所以每次都用新的 compose 專案起、
結束時 `down -v` 把 volume 一起刪掉：

```bash
FORCE_MOCK=true FRONTEND_HOST_PORT=18080 POSTGRES_HOST_PORT=15433 docker compose -p hud-live up --build -d
LIVE_BASE_URL=http://localhost:18080 LIVE_COMPOSE_PROJECT=hud-live npm run e2e:live  # 後者讓斷線橫幅測試可停／啟 backend
docker compose -p hud-live down -v
```

給 QA 的人工檢查清單在 [`frontend/QA-CHECKLIST.md`](frontend/QA-CHECKLIST.md)。

## 技術棧

- **後端**：Python 3.11 / FastAPI / Pydantic v2 / watchdog / WebSocket；流程控制用 LangGraph（`StateGraph`、`interrupt`／`Command`）；真實模型用官方 `anthropic` SDK
- **前端**：`frontend/` 為 React＋Vite＋TypeScript；Docker 裡由 nginx-unprivileged（8080）提供 build 後的靜態檔，並反向代理 `/api`、`/ws`
- **部署**：Docker Compose（postgres、backend、frontend 三個 service）
- **測試**：全部離線、不呼叫真實 LLM。後端 pytest 約 1,490 個單元測試＋124 個 integration 測試（需要 Docker）；
  前端 Vitest 502 個、Playwright 煙霧測試 50 個（`npm run e2e`，replay）＋12 個（`npm run e2e:live`，真後端）

## 目前狀態與路線圖

已完成（`dev` 分支）：

- PM → SWE → QA 流水線、QA 退件重工、人工核准閘門、花費追蹤與預算上限、交付檔下載
- **LangGraph 引擎**（預設）：與原本的 `while` 迴圈引擎事件逐筆相同，可用 `PIPELINE_ENGINE` 切換對照
- **真實的 Claude API**（非串流）：重試分類、拒絕與截斷的處理、每一次呼叫（含失敗）都計入用量

**Agent 工廠現場**（`specs/live-action-hud/`）的 MVP 在 `feat/live-action-hud` 分支，等待驗收、尚未併入 `dev`：

- 每個 agent 是坐在桌前的小角色，便條紙在桌子之間傳遞；前端是 React＋Vite＋TypeScript，支援手機寬度
  - 畫面原型（假資料，可直接操作）：<https://claude.ai/artifact/Pyp7eHAp4MGq6MeviUsxcg>
  - 原型原始檔：[`specs/live-action-hud/prototype/agent-live-stage.html`](specs/live-action-hud/prototype/agent-live-stage.html)
- 動態編制：在畫面上新增、移除 agent，有任務在跑時等這張結束才套用
- 模型選擇：專案預設＋個別 agent 的 Claude 模型，`FORCE_MOCK` 與缺少金鑰時改用 Mock 並在畫面上說明
- PostgreSQL 是任務、歷史、花費、agent 編制的事實來源；核准閘門在重啟後可續跑（LangGraph Postgres checkpointer）

之後的階段（尚未開始）：LLM 輸出逐字串流、匯入專案並由 LLM 建議 agent 編制、多條 pipeline（序列與平行）、
拆成 api／worker 兩個程序＋Redis。

## 設計文件

採 spec-driven 流程（spec → plan → tasks），每份計畫在 `specs/<名稱>/`：

| 計畫 | 狀態 | 內容 |
| --- | --- | --- |
| [`specs/langgraph-pipeline/`](specs/langgraph-pipeline/) | 完成 | 把流程控制改寫成 LangGraph；含逐關卡的 QA 紀錄（`progress.md`） |
| [`specs/anthropic-provider/`](specs/anthropic-provider/spec.md) | 完成 | 接上 Claude API |
| [`specs/live-action-hud/`](specs/live-action-hud/) | MVP 待驗收（`feat/live-action-hud`）；後續階段未開始 | 工廠現場畫面（React）、PostgreSQL、動態編制、模型選擇；之後是串流、匯入、多條 pipeline |

## 開發方式

這是個人學習專案，主要目的是練習 LangGraph。設計與實作由作者主導、搭配
[Claude Code](https://claude.com/claude-code) 協作完成：每份計畫先寫 spec／plan／tasks，
再由 Developer 與 QA 兩個 agent 工作階段分關卡實作與覆核（定義在 `.claude/agents/`）。

## 安全注意事項

- `.env` 內的 API key **不可以進版控**（已列在 `.gitignore`）。只有 `.env.example` 會被追蹤。
- 沒有使用者帳號與權限控管，設計上只在本機使用；不要把服務直接暴露在公開網路上。

## 授權

[MIT](LICENSE)
