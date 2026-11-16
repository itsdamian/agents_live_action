# Spec：Agent 工廠現場（Live-action HUD）＋動態編制＋資料地基

Status: CONFIRMED（2026-11-17 使用者確認；第 3 版，2026-11-17：第 2 版＋team lead 審閱缺口（LLM 選擇、強制 Mock、待辦內容刷新、歷史跨重啟、旗標組合）與使用者決定 B1–B5）

> 相關文件：`plan.md`（架構、資料模型、事件契約、API）、`tasks.md`（任務清單）。
> 基底：`dev` 1478392（已含 `feat/langgraph-pipeline` 與 `feat/default-engine-langgraph`：預設引擎 langgraph、`test_api.py` 改 `time.monotonic()`；合併時 419 passed）＋分支 `feat/anthropic-provider`（非串流的 Claude API，見 `specs/anthropic-provider/spec.md`；A1–A3 完成、`anthropic==2.8.0`，A4 只改 `agents/base_agent.py` 的 `retry=` 一行，依例外的 `retryable` 屬性決定是否重試）。本計畫假設 anthropic 分支也已合併。
> 現有程式的描述以 2026-11-16～17 實際讀到的程式碼為準。畫面與互動以使用者確認過的原型 `agent-live-stage.html` 為準。

## 1. 背景

現在的 HUD 是純 HTML/CSS/JS 的科幻儀表板；pipeline 寫死 PM → SWE → QA；所有狀態不是在記憶體（usage、rework 次數、失敗原因、閘門）就是在 `tasks.yaml`（唯一事實來源，被特徵化測試鎖住）。重啟後花費歸零、停在核准閘門的任務無法續跑。

使用者要的是「工廠現場」畫面（每個 agent 是桌前的小角色、便條紙在桌間飛、可新增／移除 agent），以及支撐它的後端：自訂編制、即時細節事件、可查的歷史、匯入專案並由 LLM 建議編制、多條 pipeline 平行。使用者也決定：前端改 React（考慮行動版）、引入 PostgreSQL 與 Redis。主要目的仍是**練習 LangGraph**。

## 2. 目標

1. **畫面**：以原型為準，用 React＋Vite＋TypeScript 重寫前端；responsive，手機（375 px）可用。
2. **資料地基**：PostgreSQL 成為任務、歷史、用量、agent 與 pipeline 編制、匯入工作、編制提案的事實來源；LangGraph checkpointer 改用 Postgres，**核准閘門在後端重啟後可續跑**。`tasks.yaml`／`TODO.md` 降為「新增任務的入口之一」：新任務匯入 DB，之後狀態只寫 DB、不寫回檔案，畫面一律從 DB 讀（使用者已定案，plan D3）。
3. **動態編制（只在 LangGraph 引擎）**：使用者可新增自訂 agent（名字、職責、prompt、make／check、造型、座位）、移除、換座位；check 型輸出可由程式判定的通過／退件。
4. **事件契約向後相容**：既有事件與欄位不刪不改語意；**預設三站編制下既有事件序列逐筆相同**（檔案模式與 DB 模式皆然）。
5. **即時細節**：`agent:phase`（LangGraph custom stream）、`agent:token`（Mock 與 Anthropic 都支援串流）。
6. **便條紙歷史**：完成、失敗、進行中、等核准、待辦都看得到；重啟後仍在。
7. **匯入專案＋LLM 評估編制**：本機資料夾或 GitHub 公開 repo；建議編制必須經使用者確認才生效。
8. **多條 pipeline**：先序列、後平行（兩者都在範圍內）。
9. **三步驟 quickstart 與 Mock 模式不變**：沒有任何 API key 時 `docker compose up --build` 即可跑完整流程。
10. **LLM 選擇**：專案層級預設 LLM（Mock 或 Claude 白名單模型），每位 agent 可跟隨預設或個別指定；畫面上切換並看得到每桌目前的模型。
11. **強制 Mock**：`FORCE_MOCK=true` 時不論 `.env` 有沒有金鑰都只用 Mock；所有驗收與錄製一律在此模式下執行，真實呼叫只出現在明寫「使用者同意、會產生費用」的步驟。

## 3. 非目標

- **原生 app**。「行動版」指 responsive web（手機瀏覽器）。日後若要原生 app（例如 React Native），React 的概念與部分純 TypeScript 邏輯（事件 reducer、型別）可轉移，但元件與樣式不能直接共用。
- OpenAI／Gemini provider、tool use、prompt caching（沿用 `specs/anthropic-provider` 的範圍）。
- 私有 GitHub repo、其他 git 主機、zip 上傳；匯入的程式碼不會被執行或被 agent 修改。
- legacy 引擎的新功能（自訂編制、phase／token、多條 pipeline）。
- 使用者帳號、權限、多人同時編輯的衝突合併。
- 把交付檔（`deliverables/`）與 agent log 檔（`logs/`）搬進資料庫（仍是檔案）。
- LLM 路由（多條 pipeline 的選線只用明確指定與規則）。
- 專案資料夾被刪除時自動刪除 DB 中的資料：專案列表以 `projects/` 下的資料夾為準，DB 的列保留（同名資料夾再出現時接回）；清理孤兒資料為選配 O4。
- MVP 不支援「重啟後從中斷的那一站接著跑」：重啟時 `in_progress` 的任務標 failed、可重跑；接續執行是階段 2 之後的獨立項目（plan §12「階段 2b」）。

## 4. 使用者故事

- U1 打開 HUD，PM、SWE、QA 坐在桌前打瞌睡；派一張任務，便條從待辦匣飛到 PM 桌，泡泡逐字顯示目前階段（MVP 是固定的階段文字，例如「讀任務：…」「思考中…（claude-opus-5-5）」；LLM 實際輸出要到階段 2），再到 SWE、QA，最後飛進完成匣，全員歡呼。
- U2 QA 退件：便條變紅飛回 SWE，SWE 發抖，便條角落顯示重做次數。
- U3 派任務時「核准時機」選「全部通過後給我看」（另兩個選項是「不用」「做完後先給我看」；內部值 `none`／`after_qa`／`after_swe` 不變）：QA 通過後舉手、出現「可以／重做」；按「重做」可填意見。
- U4 新增「設計師」（make、貝雷帽、坐在 PM 後面）：角色跳進場自我介紹；下一張任務起便條會經過他。重啟後他還在。
- U5 新增「資安把關」（check）：它退件時便條回到它前面最近的「做事」桌。
- U6 請某角色離開：若他手上有任務，畫面標示「這張做完就離開」。
- U7 點完成匣，展開便條牆：每張便條的標題、結果、經過的 agent、重做次數、失敗原因、花費、完成時間；可篩選；點開看交付檔。重啟後內容不變。
- U8 任務停在核准閘門時重啟後端（或整個 compose），回到畫面閘門仍在；按「可以」後任務從閘門之後繼續，前面的 agent 不會重跑。
- U9（階段 2）泡泡裡是 LLM 正在吐出的字（接 Claude 時是真的串流，Mock 時是假串流）。
- U10 在手機上看同一個畫面：桌子換行排列、按鈕好按、便條牆與詳情以全螢幕面板呈現。
- U11 匯入本機資料夾或貼上 `https://github.com/<owner>/<repo>`，系統提出「建議 4 位：PM、設計師、SWE、QA，理由…」；我修改後按「就這樣」才建立。
- U12 「功能線」與「修 bug 線」兩條 pipeline 一條一層樓，兩張便條同時在不同樓層飛（每條線同時只跑一張）。
- U13 在 header 把專案的模型從「Mock」切到「Claude Sonnet 5.5」（確認對話框提醒會產生費用）；每張桌子的名牌旁顯示目前模型；把資安把關個別指定為「Claude Opus 5.5」；若伺服器沒有 `ANTHROPIC_API_KEY` 或設了 `FORCE_MOCK`，畫面標示「實際以 Mock 執行」與原因。

## 5. 分階段範圍

| 階段 | 內容 | MVP |
| --- | --- | --- |
| 0 前置（已完成／進行中） | 預設引擎 langgraph、`test_api` 用 monotonic（b79d1e3）；非串流 Claude provider（`feat/anthropic-provider`） | — |
| **1a 地基（後端）** | PostgreSQL＋SQLAlchemy 2＋Alembic；repository 介面（檔案實作與 DB 實作並存，`TASK_STORE` 旗標）；任務、用量、歷史進 DB；Postgres checkpointer 與閘門重啟續跑 | ✔ |
| **1b React 舞台（前端，與 1a 平行）** | React＋Vite＋TS 骨架、事件 reducer、replay、工廠現場舞台、桌上核准、行動版 | ✔ |
| **1c 動態編制（後端）＋整合** | agent／pipeline 編制（DB 為主、可匯出匯入 YAML）、`CustomAgent`、動態 graph、`agent:phase`、REST、便條牆；前端接上真後端、切換到 React 版並刪除舊前端 | ✔ |
| 2 | `agent:token`：Mock 假串流＋Anthropic 串流、節流 | |
| 3 | 匯入（本機、GitHub tarball）＋編制規劃 graph（真實 LLM，結構化輸出由程式驗證） | |
| 4 | 多條 pipeline，序列 | |
| 5 | 多條 pipeline，平行（同一程序內多個 worker） | |
| 6 | 拆成 api 與 worker 兩個程序；Redis 負責跨程序事件與控制訊息（使用者 2026-11-17 定案：保留、排最後，不砍） | |

MVP＝1a＋1b＋1c。前端在 1a 期間用錄製／手寫的事件 replay 開發，所以在後端地基完成前就看得到畫面。

範圍說明（使用者 2026-11-17 決定）：
- **MVP 的泡泡只有固定階段文字**（`agent:phase`，例如「讀任務：…」「思考中…（模型）」「交出成果」「退件 → SWE」），**看不到 LLM 實際寫出的內容**；那是階段 2（`agent:token`）。階段順序不變：MVP 之後接階段 2。
- **「平行」的定義**：每條 pipeline 同時各跑一張任務；同一條線內仍一次一張。三條線最多同時三張。
- **重啟時正在跑的任務**：MVP 標 failed、可重跑；「從 checkpoint 接著跑、只重做被打斷的那一站」排在階段 2 之後（階段 2b），屆時處理該站重新計費、交付檔與事件重複、學習註記。
- **LLM 選擇在 MVP（1c）**；切換的生效時機比照編制（閒置立即、否則下一張任務）。

## 6. 與現況不符之處（以程式碼為準）

| 描述 | 實際與處理 |
| --- | --- |
| `tasks.yaml` 是唯一事實來源、被特徵化測試鎖住 | 使用者定案改為 DB 為事實來源（plan D3）。為了不一次打破測試：先抽 repository 介面，`TASK_STORE=file` 時呼叫路徑與現在**完全相同**（特徵化 harness 的 `install_status_recorder` monkeypatch 的是 `core.pipeline.write_task_status`，所以檔案模式必須仍經由這個名字呼叫）；`TASK_STORE=db` 由新的 DB 版測試把關。檔案模式不在本計畫刪除。 |
| Redis 負責 WS fan-out、佇列、鎖、token 緩衝、預算計數 | 逐項判斷後（plan D8）：在「單一後端程序」下**以上沒有一項需要 Redis**——佇列與鎖用 Postgres（`FOR UPDATE SKIP LOCKED`、advisory lock），預算用 Postgres 交易，token 節流在程序內。Redis 只有在拆成多個程序（階段 6）時才有真正的工作：跨程序的事件 fan-out 與 api→worker 的控制訊息。因此 Redis 排在階段 6（使用者 2026-11-17 定案：保留、排最後，不砍）。 |
| `append_task` 已安全 | 前一份計畫只把 `write_task_status` 改成原子寫入；`append_task` 仍是 `open("w")`，兩者都沒有互斥。檔案模式下補鎖（Task 3）；DB 模式下不再寫 yaml。 |
| 「真實供應商只是佔位」（第 1 版 spec 的說法） | 已不成立：`feat/anthropic-provider` 實作了 `AnthropicClient.call`（`anthropic==2.8.0`，`client.beta.messages.create`／`messages.create`）。串流沿用同一份請求參數。 |
| 匯入的 repo 自帶 `tasks.yaml`／`TODO.md` | `TaskWatcher` 用 `rglob` 遞迴，會把它們當任務派發。匯入內容放 `source/`，watcher 必須忽略。 |
| GitHub「淺層 clone」 | backend 映像 `python:3.11-slim` 沒有 git。已定案用 tarball 下載。 |
| `usage` 與花費 | `UsageTracker` 純記憶體，重啟歸零（預算守門也跟著歸零）；改為 DB 寫入並在啟動時載入。 |
| 前端 `state.js` | 不處理 `usage:update`。React 版 reducer 處理。 |
| `MockLLMClient._role_key` 用子字串猜角色；`deliverables._is_swe_agent` 用 `"swe" in name` | 自訂 agent 明確指定 Mock 角色；後者列風險。 |
| `_enter_rework` 寫死 `station: "swe"`；`station_states` 寫死三鍵；`BaseAgent` 實例不可重入；`_budget_paused` 每個 orchestrator 一份 | 依序在動態編制（參數化）與平行 pipeline（每線獨立 agent 實例、專案層級預算守門）處理。 |
| 基準測試數 | dev 1478392 為 419 passed；anthropic 分支合併後以 Task 1 實測為準。 |
| agent 的 LLM 由 `agents/*.yaml` 的 `llm_provider`／`llm_model`／`api_key_env` 決定 | DB 成為編制來源後改 YAML 不再生效，而第 2 版的 `POST`／`PUT /agents` 又沒有 LLM 欄位——等於無法切到 Claude。第 3 版加入專案預設 LLM 與 agent 個別指定（plan D21）；`api_key_env` 不再開放設定，由 provider 對應固定的環境變數。 |
| 驗收要求「`.env` 有金鑰就停」 | 使用者的 `.env` 已有真的 `ANTHROPIC_API_KEY`。改為 `FORCE_MOCK=true` 執行驗收（plan D22）。 |
| backend 對外開 8000 埠 | 使用者機器的 8000 被佔用；backend 預設不 publish，前端 Nginx 轉發（plan D10）。 |

## 7. 驗收標準（AC）

指令在 `backend/`（後端）或 `frontend/`（前端）下執行。「既有測試檔」＝本計畫開始時已存在的 `backend/tests/test_*.py` 與 `backend/tests/pipeline_harness.py`。「整合測試」＝標 `@pytest.mark.integration`、用 Testcontainers 起 Postgres（或 Redis）的測試：沒有 Docker 時本機略過並註明原因，**QA 關卡必須在有 Docker 的環境實際跑過**。

**1a 地基**
- **AC1 兩種儲存模式**：`TASK_STORE=file`（程式預設）下，既有測試檔一行未改、兩種引擎全套綠、特徵化 28 case 全過。`TASK_STORE=db` 下，新增的 DB 版特徵化測試對 S1–S12 的事件簽章與檔案模式**逐筆相同**，且 DB 中的狀態變化序列等於檔案模式的 tasks.yaml 寫入序列。
- **AC2 tasks.yaml 角色與匯入冪等**（`tests/test_task_importer.py`＋整合測試）：DB 模式下 (a) tasks.yaml／TODO.md 的新 id 匯入 DB，status 為 pending 者派發恰一次；(b) pipeline **不寫回** yaml（任務執行前後檔案逐 byte 相同、mtime 不變）；(c) 同一 id 再次出現不重複匯入、不重複派發；(d) 同一 id 內容被改過：DB 中仍為 pending 時更新 title／description／priority／approval／max_reworks，已開始或結束時不變；檔案中的 status 一律忽略；(e) 從檔案刪除任務不影響 DB；(f) 重啟後不重複派發（同一批 pending 只 enqueue 一次）、in_progress 的任務標 failed（`interrupted by restart`）；(g) UI 建立的任務直接寫 DB、不寫 yaml。檔案模式下 `append_task` 與 `write_task_status` 併發 20 個執行緒無遺失。
- **AC2b 既有專案遷移**：一個有 tasks.yaml（含 pending、done、failed、in_progress 各一）與自訂 `agents/*.yaml` 的專案第一次在 DB 模式啟動：所有任務與狀態進 DB；done／failed 各有一筆 `imported` 的歷史；in_progress 變 failed（`interrupted before migration to database`）；pending 被派發；tasks.yaml、TODO.md、`agents/*.yaml` 內容與 mtime 不變；第二次啟動不再遷移。
- **AC3 Repository 契約**：同一組契約測試對 InMemory 與 Postgres 兩種實作都通過（Postgres 為整合測試）。
- **AC4 遷移**：`alembic upgrade head` 在空資料庫成功、`alembic downgrade base` 成功；`alembic check`（或 autogenerate 產生空 diff）證明 model 與 migration 一致；LangGraph checkpointer 的表不在 Alembic 管理範圍。
- **AC5 閘門重啟續跑**：（整合測試）after_swe 任務停在閘門 → 關閉 runtime → 以同一個資料庫建立新 runtime → snapshot 的 `awaiting_approval` 有該任務 → `approve` → 任務 done；PM、SWE 沒有重跑（client 呼叫次數）、交付檔沒有重複、`task:awaiting_approval` 在重啟後**不重發**、事件 `task:approved` 之後的序列與未重啟時相同；該任務在 `task_runs` **只有一筆**、`task_run_steps` 的 `seq` 連續（重啟前後不斷號）、`cost_usd` 與 tokens 含重啟前的用量（等於 `usage_records` 該期間的加總）。
- **AC6 用量持久化**：重啟後 `usage_summary` 與預算暫停狀態與重啟前相同。
- **AC7 quickstart**：乾淨的機器上 `docker compose up --build`（不需 `.env`）→ postgres 與 backend healthcheck 變 healthy → 開 `http://localhost:8080` 跑完 example 任務（Mock）。資料庫遷移在 backend 啟動時自動執行。`docker compose config` 中 backend **沒有** `ports`；postgres 只綁 127.0.0.1；frontend 埠可用 `FRONTEND_HOST_PORT` 改；加上 `-f compose.expose-backend.yml` 時 backend 綁 `127.0.0.1:${BACKEND_HOST_PORT:-8001}`。
- **AC7b 強制 Mock**：`FORCE_MOCK=true` 且環境有 `ANTHROPIC_API_KEY` 時，`get_llm_client` 對 anthropic 設定回傳 `MockLLMClient`、`AnthropicClient` 從未被建構（測試以 monkeypatch 讓其建構子拋錯）、`is_mock_mode()` 為真、`/api/health` 與 snapshot 的 `force_mock` 為真；staffing 也走 Mock。未設 `FORCE_MOCK` 時行為與現在相同。
- **AC7c 旗標組合**：plan D23 表中標「不支援」的 `PIPELINE_ENGINE` × `TASK_STORE` × `PIPELINE_CHECKPOINTER` 組合在啟動時拋 `ValueError`（訊息列出原因與可用組合）；支援的組合各有一個啟動測試。
- **AC7d 待辦內容刷新**：DB 模式下任務已在程序內佇列中、尚未開始時，tasks.yaml 改了它的標題與描述 → worker 開始處理時用的是新內容（`task:created` 的 `task.title` 與 PM 收到的 prompt 都是新值）；DB 中狀態已不是 pending（例如被重跑邏輯或手動處理改掉）時 worker 略過該項。

**1b 前端**
- **AC8 前端測試**：`npm run test`（Vitest）全綠；reducer 對每一種事件至少一個測試；事件型別（TypeScript）與 plan §6 凍結的 schema 一致（`npm run typecheck` 通過）。
- **AC9 replay**：`npm run dev` 後以 `?replay=default-run` 開啟，不需要後端即可完整演出（預設編制、退件、閘門、完成）。
- **AC10 行動版與動態**（Playwright 煙霧測試，plan §11.6）：
  - a. 375×812 與 1280×800 兩種 viewport 下 `document.documentElement.scrollWidth <= innerWidth`（無水平捲軸）。
  - b. 所有可點元素（閘門按鈕、×、匣子、便條卡、主要按鈕）的點擊區域 ≥ 44×44 CSS px（測試量 `getBoundingClientRect`，含 padding 的觸控區）。
  - c. `reducedMotion: "reduce"` 下：角色無 CSS 動畫（`getAnimations().length === 0`）、便條無飛行過場、泡泡整段出現。
  - d. 一條線 8 位與 3 條線 × 8 位時，桌子不重疊（兩兩 bounding box 不相交）、全部在可視寬度內。
  - e. 便條牆與任務詳情在 ≤640 px 時是全螢幕面板，可用返回／關閉按鈕離開。
  - f. 鍵盤：閘門按鈕可 Tab 到並以 Enter 觸發；焦點可見。
- **AC11 舊區塊對應**：header（專案選擇、MOCK、連線狀態）、建立任務、任務詳情（log、交付檔下載、核准）、DevOps 警示、斷線橫幅在 React 版都有（plan §11.7 對照表逐項驗）。
- **AC11b 核准時機文字**：任務表單的選項文字為「不用」「做完後先給我看」「全部通過後給我看」，送出的值為 `none`／`after_swe`／`after_qa`（元件測試）。

**1c 動態編制與整合**
- **AC12 預設編制零差異**：預設編制下 LangGraph `draw_mermaid()` 與改動前錄的 golden 逐字相同；特徵化 28 case 在兩種儲存模式、兩種引擎都過。
- **AC13 live 通道不污染**：有／無 `on_live_event` 時 `on_event` 簽章序列相同；每站至少 3 個 `agent:phase`。
- **AC14 自訂編制劇本**：plan §9 C1–C6 的事件簽章、rework 次數、失敗原因符合錄製期望值。
- **AC15 check 判定**：`parse_verdict` 只認最後 3 個非空行中最後的 `VERDICT: PASS|FAIL`；≥10 個測試 case（含 injection 樣本）。
- **AC16 編制持久化與 YAML**：建立的自訂 agent 存在 DB；重啟後仍在；`GET .../agents/export` 產生的 YAML 再 `POST .../agents/import` 到另一個空專案，roster 與座位相同；既有 `agents/*.yaml` 在專案第一次啟動時匯入 DB（不修改 yaml 檔）。
- **AC17 輸入驗證**：id 規則、保留字、`display_name` 控制字元、長度上限；不合格時回 422／409 且 DB 無殘留列、檔案系統無新檔。
- **AC18 座位變更時機**：閒置時立即生效（200）；有任務在跑時 202、進行中任務照原編制跑完、下一張才套用。
- **AC19 legacy／檔案模式**：`PIPELINE_ENGINE=legacy` 或 `TASK_STORE=file` 時，編制寫入端點回 409 並說明原因；讀取端點正常。
- **AC20 便條牆**：`GET /history` 的篩選（5 種狀態、pipeline）、分頁、計數正確；資料來自 DB；重啟後不變；某 agent 被刪除後，含它的舊便條仍顯示其名字與造型（來自 `task_runs.seats` 快照）。
- **AC21 snapshot 相容**：既有 key 與值不變（預設編制 `pipeline.stations` 仍為 `{"pm","swe","qa"}`）；新增 `engine`、`task_store`、`force_mock`、`llm`、`agents`、`pipelines`、`live`、`history_counts`。
- **AC22 切換前端**：compose 的 frontend 服務改為多階段 build（Node 建置 → Nginx），舊 `frontend/*.js`、`css/` 已刪除，`git ls-files frontend` 只剩 React 專案的檔案；AC10、AC11 在 Docker 上再驗一次。
- **AC23 import 邊界**：`grep -rlE "^\s*(from|import) langgraph" backend --include=*.py`（排除 tests 與 .venv）只列出 `core/pipeline_graph.py`、`core/checkpointing.py`（階段 3 起加 `core/staffing_graph.py`）；`PIPELINE_ENGINE=legacy` 時 import `core.pipeline`、`core.runtime` 不載入 langgraph。
- **AC25 LLM 選擇**：`PUT /settings` 把專案預設設為 Claude 白名單模型後，`llm` 為 null 的 agent 下一張任務起以該模型建立 client（以假 SDK 物件驗證送出的 `model`）；個別指定的 agent 不受影響；不在白名單的 model、或 provider 不是 `mock`／`anthropic` → 422；請求帶 `api_key_env` → 422；沒有金鑰時 effective 為 Mock 且 `mock_reason == "missing_key"`；有任務在跑時回 202、下一張才生效；廣播 `llm:updated`；白名單每個 model 在 `usage_tracker.MODEL_PRICES` 都有價格（測試）。既有專案遷移時 YAML 的 LLM 設定依 plan D21 表對應。
- **AC26 上游截斷標示**：上游產出超過 6000 字時，prompt 中該段以「（以下內容已截斷，原長 N 字）」標示，且該行出現在 agent 的 log 檔中（log 會記錄完整 prompt）；未超過時 prompt 與未實作截斷時逐字相同（測試）。
- **AC24 學習註記**：`# LangGraph 學習:` 涵蓋：動態建圖、`get_stream_writer`、多 stream mode tuple、node 名稱保留字元、Postgres checkpointer、`thread_id` 跨重啟、`aget_state().interrupts` 用於恢復、`durability`。

**階段 2–6** 的 AC 在 plan §12 列出，開始各階段前併入本文件。

## 8. 使用者決定紀錄

已定案（2026-11-17）：`tasks.yaml` 定位＝DB 為事實來源（plan D3）；GitHub 匯入用 tarball；`IMPORT_ROOT` 預設 `./imports` 唯讀掛載；LLM 路由延後；前端 React；平行 pipeline 在範圍內；引入 PostgreSQL。

同日追加定案（使用者回覆「Redis 保留排最後，其他照建議」）：

| # | 問題 | 決定 |
| --- | --- | --- |
| 1 | agent 編制存哪 | DB 為主；第一次啟動從 `agents/*.yaml` 匯入；提供 YAML 匯出／匯入端點（plan D4）。取代第 1 版「存成 YAML」的決定。 |
| 2 | 階段 6（拆程序＋Redis） | **要做，排最後，不砍。** |
| 3 | 新增依賴清單（plan §13） | 1a 與 1b 需要的套件一次核准（Task 2 時列出確切套件與版本給使用者過目）；其餘階段開始時再核准。 |
| 4 | Playwright | 加為前端開發依賴，只用於對應 AC10 的 3–5 個煙霧測試。 |

同日（第 3 版）使用者回覆 team lead 審閱：

| # | 問題 | 決定 |
| --- | --- | --- |
| 5 | 重啟時 `in_progress` 的任務 | MVP 標 failed＋可重跑；「從 checkpoint 接著跑、只重做被打斷的那一站」列為階段 2 之後的獨立項目（階段 2b） |
| 6 | 「平行」的定義 | 每條 pipeline 同時各跑一張；同一條線內一次一張 |
| 7 | 泡泡內容 | MVP 只有固定階段文字；LLM 實際輸出在階段 2；階段順序不變 |
| 8 | backend 對外埠 | 預設不 publish，由前端 Nginx 轉發；需要時以 `compose.expose-backend.yml` 開在 `127.0.0.1:${BACKEND_HOST_PORT:-8001}` |
| 9 | 核准時機的畫面文字 | 「不用」「做完後先給我看」「全部通過後給我看」；內部值不變 |

尚待使用者做的事：看過本文件 §5 的範圍與 plan §6（事件）、§7（REST）的介面後，確認整份 spec（Status 由 DRAFT 改 CONFIRMED）才開始派工。
