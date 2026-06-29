# Tech Stack

## Backend

- **Python 3.11+** with **FastAPI**
- **Pydantic v2** — 資料模型與 YAML 配置驗證
- **watchdog** — 監控 `projects/` 目錄文件變更
- **tenacity** — LLM 呼叫 exponential backoff retry
- **asyncio.PriorityQueue** — 任務優先級排序（critical=0, high=1, medium=2, low=3）
- **pytest** — 單元與整合測試
- WebSocket 透過 FastAPI 內建支援實作

## Frontend

- **Vue 3** (Composition API) + **Vite** 作為 bundler
- **Pinia** — 全域狀態管理（取代原有 vanilla JS state store）
- Google Fonts：Orbitron、Share Tech Mono、Rajdhani
- CSS custom properties (`--var`) 管理主題 token，保留原有 sci-fi HUD 風格
- 元件結構：`App.vue` → `ConveyorBelt.vue`、`StationCard.vue`、`DevOpsPanel.vue`、`ProjectSelector.vue`
- WebSocket 連線封裝於 `composables/useWebSocket.js`
- 前端 build 產出靜態檔案，由 Nginx 提供服務

## Infrastructure

- **Docker Compose** — 封裝 frontend（Nginx）+ backend（FastAPI）兩個 service
- **Nginx** — 提供前端靜態文件，反向代理 `/api` 和 `/ws` 到 backend（避免跨域）
- `./projects/` 以 volume mount 方式掛載（雙向同步）
- LLM API key 透過 `.env` 文件或 host 環境變數注入，不寫入映像檔

## 常用指令

```bash
# 啟動整個系統
docker compose up

# 重新 build 後啟動
docker compose up --build

# 關閉（優雅停止）
docker compose down

# 後端測試（在 backend/ 目錄內）
pytest

# 執行單一測試檔
pytest tests/test_pipeline.py -v

# 本地前端開發（不用 Docker）
cd frontend
npm install
npm run dev        # Vite dev server，port 5173

# 本地後端開發（不用 Docker）
cd backend
uvicorn main:app --reload --port 8000
```

## 環境變數

參考 `.env.example`，支援的 LLM 供應商環境變數：
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`

未設定任何 key 時系統自動進入 Mock 模式。
