# 進度與 QA 紀錄：Agent 工廠現場（live-action-hud）

由 team lead（personal-workplace-d4）維護。後端 Developer：`ala-developer`（分支 `feat/hud-backend`）；前端 Developer：`ala-frontend`（分支 `feat/hud-frontend`，`web/`）；QA：`ala-qa`。

## 2026-11-17

- 三份 spec／plan／tasks（第 3 版）使用者確認；`plan.md` §4.1、§6、§7 FROZEN；§7.1a（回應形狀補充）由 team lead 補定義（前端 F2 指出未定義處）。
- 前置：`feat/anthropic-provider` 合併進 dev（e6e5c17）；`dev` 已 push 到 GitHub（51f2608，經 443 埠）。
- **後端 Task 1、3、3b、4、5 完成**（`feat/hud-backend`，2a8d75a；尚未 QA、尚未合併）：
  - 基準 542 passed（兩種引擎相同，特徵化 28）；Task 5 後 613 passed。
  - Task 3 `tasks.yaml` 互斥（59fafb7）：`write_task_status`、`append_task` 讀改寫在鎖內，`append_task` 改原子寫入；對照組去掉鎖 20/20 遺失更新。
  - Task 3b `FORCE_MOCK`（52c5b45）：`get_llm_client` 在任何金鑰檢查前回 Mock；`/api/health` 加 `force_mock`。
  - Task 4 BaseAgent 特徵化（934690f，24 個）、Task 5 golden mermaid（2a8d75a，20 條邊、12 節點）。
  - 備註：全套耗時 13 秒 → 27 秒（Task 3 的鎖測試）；`get_configured_providers` 未改（金鑰仍列在 health 的 providers，只是不被使用）。
- **前端 F1 完成**（`feat/hud-frontend`，1c51a8a）：React 19.3.1、Vite 8.3.8、TypeScript 7.0.2、Vitest 5.1.0 等；typecheck、test、build 通過，`npm audit` 0 個漏洞。F2 進行中（型別檔已寫，因電腦睡眠中斷兩次，已重新指派）。
- 待使用者決定（Task 2 依賴核准）：見 team lead 回報（SQLAlchemy 2.1.9 或 2.0.x、TypeScript 7、`@types/node`、字體）。
- **前端 F2、F3、F4 完成**（`feat/hud-frontend`，ae88554；尚未 QA）：型別／guards／replay fixture（980333f）、reducer／deskMachine／selectors（75e7a7e）、socket／replay／api／liveText／FactoryProvider（ae88554）。vitest 146 tests、typecheck、build 通過；`?replay=default-run` 在 headless chromium 跑完無 console error；`@types/node` 24.19.2 已取代最小宣告。
- 契約釐清 §6.3a（前端提出 7 點，team lead 裁定，後端照此實作）；Task 2 依賴核准記錄在 plan §13。
- 與電腦睡眠有關的中斷：前端工作階段在 05:35 前後兩次被中斷（`API Error: Your computer went to sleep mid-response`），已重新指派；建議長時間工作期間保持清醒。
- **後端 Task 8 完成**（`feat/hud-backend`，3dfabd0；未 QA）：`core/stores/`（TaskRepository、UsageRepository、RunRepository 三個 async Protocol）、InMemory 實作、`FileTaskRepository`（只包裝現有函式，沿用 Task 3 的鎖與原子寫入；檔案模式做不到的操作拋 `NotSupportedInFileMode`）、契約測試（Task 9 的 Pg 實作直接繼承同一份）。663 passed、6 skipped（File 的不支援操作）；6 種變異全抓到；沒有新增套件，`pipeline.py` 沒動。Developer 依 plan D3／D20／D24 推出的最小方法集合，之後只增不改簽章。
- **後端 Task 6a、6b、6c 完成**（`feat/hud-backend`，858ad91；未 QA）：
  - 6a（d5806df）：套件依核准版本釘死（sqlalchemy 2.1.9、alembic 1.20.1、psycopg 3.3.9、psycopg-pool 3.3.3、langgraph-checkpoint-postgres 3.2.1、testcontainers 4.15.0）；fastapi／starlette／pydantic／httpx／websockets／langgraph／anthropic 版本不變；`config.get_task_store()`、`get_pipeline_checkpointer()`、`get_database_url()`（密碼遮蔽）、`validate_runtime_flags()`（D23 八種組合，`create_app()` 啟動時呼叫）。
  - 6b（5e76c1f）：compose 加 postgres（`postgres:17.12-bookworm@sha256:40865bc8…`，compose 與 `tests/integration/images.py` 由測試保證相同）；backend 不 publish port；`compose.expose-backend.yml`；frontend `127.0.0.1:8080`；`start.sh`（有 `alembic.ini` 才跑遷移）；`FORCE_MOCK=true docker compose up --build` 實跑：postgres、backend healthy、`/api/health` 回 `force_mock: true`、示範任務 3/3 完成；已 `down -v`、無殘留。
  - 6c（858ad91）：整合測試基礎（session 容器＋每測試新 database、無 Docker skip、`CI=true` 失敗）。有 Docker 731 passed、6 skipped；無 Docker 727 passed、10 skipped。
  - 環境備註：這台機器預設埠 8000（php）、8080、5433（wcs-postgres 容器）已被佔用；實跑用獨立 compose 專案名與 `FRONTEND_HOST_PORT=18080`、`POSTGRES_HOST_PORT=15433`。docker CLI 要先 `export PATH="/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"`。Task 7 的 `alembic.ini` 必須放在 `backend/` 根目錄，`start.sh` 才找得到。
- **前端 F4b、F5、F6、F7 完成**（`feat/hud-frontend`，4dec3b3；尚未 QA）：§6.3a 調整（8aabacd）、舞台元件（880d157）、便條飛行（b84fa27）、泡泡與桌上核准按鈕（4dec3b3）。vitest 241 tests、typecheck、build、`npm audit` 0；真 chromium 跑四個 replay 無 console error；375／1280 無水平捲軸；便條飛行時 `getAnimations()=1`、reduced-motion 為 0；閘門 Tab→Enter 可核准。team lead 看過截圖（`scratchpad/ala-frontend-shots/`）：與原型一致。與原型落差（皆合理）：加 `border-box`（原型單檔沒設，獨立開會讓泡泡溢出桌寬）；原型的核准按鈕因 `display:flex` 蓋過 `hidden` 一直可見（原型 bug），正式版只在 waiting 顯示；8 席緊湊模式把模型徽章疊在名牌右上角。待辦：核准按鈕目前高 31px，F11 要放大到 44px。
