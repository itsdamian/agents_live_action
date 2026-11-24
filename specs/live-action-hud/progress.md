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
- **後端 Task 7、9、10 完成**（`feat/hud-backend`，2f9e7b0；未 QA）：
  - Task 7（6073b4d）：七張表、alembic（`backend/alembic.ini`）、`0001_initial_schema`；刻意只有 `task_run_steps.run_id` 有外鍵（`projects` 列代表「已遷移／匯入」，寫任務不能隱含建立它）；`pipelines` 以 partial unique index 保證每專案最多一條 default；`start.sh` 現在會真的跑 `alembic upgrade head`（尚未在 compose 實跑驗證）。
  - Task 9（f8df949）：`core/stores/pg.py` 三個 Pg 實作，Task 8 的契約測試對真 Postgres 全過；下一個任務 id／run_no／step seq 用 `pg_advisory_xact_lock` 序列化（20／12／15 個並行測試無缺號）。
  - Task 10（2f9e7b0）：`PipelineOrchestrator(task_repo=None)`；有 repo 時只寫 repo、不寫 tasks.yaml；None 時路徑不變。`tests/hud_harness.py` 與 `test_characterization_db_store.py`（41 個）以「同一劇本兩種儲存模式各跑一次、逐項相等」驗證 S1–S12；整合版對真 Postgres 跑 S1、S6、S11。既有測試與 28 個特徵化 case 一字未改。
  - 三種設定皆 846 passed、6 skipped；整合測試單獨 59 passed；無 Docker 787 passed、65 skipped。`sqlalchemy[asyncio]==2.1.9`＋`greenlet==3.5.6`（team lead 追加核准）。
  - Developer 回報一次不可重現的測試卡住（約 10 分鐘，恰在額度中斷前後）；QA 若遇到請用 `-o faulthandler_timeout=60`。
- **前端 F8、F9a、F9b、F9c 完成**（`feat/hud-frontend`，1d8b383；尚未 QA）：新增／移除 agent 與板凳、Header（專案、MOCK MODE＋原因、連線狀態、模型選單＋確認對話框）、StatsBar、TaskForm（核准時機文字）、TaskDetail、機房抽屜、Toasts、ConnectionBanner、Sheet。vitest 386 tests、typecheck、build、`npm audit` 0；真 chromium（replay）實測新增／移除、模型確認、派任務、critical toast、手機 Sheet（Tab 鎖定、Esc、捲動鎖）console 乾淨。切 Claude 的確認框顯示「每日 $5.00、專案 $20.00」，取自 snapshot `usage.daily_budget_usd`／`project_budget_usd`（`null` ＝後端沒設定上限，見 `UsageTracker`）。
- **後端 Task 11a、11b、11c、11d 完成**（`feat/hud-backend`，85c8b0c；未 QA）：
  - 11a（6e0a26c）：`core/task_importer.py`，實作 D3 (1) 表每一列（唯一鍵 `(project_id, task_id)`、`source_hash`、pending 時更新內容、已開始或結束則忽略、檔案 status 一律忽略、刪除不影響 DB、yaml 與 TODO.md id 衝突先寫入者勝）；只回傳 `to_enqueue`，不自己 enqueue。`tests/test_task_importer.py` 22 個（InMemory，含並行匯入、真實檔案與無法解析的檔案）；4 種變異皆被抓到。
  - 11b（503c869）：DB 模式啟動順序（匯入不 enqueue → 殘留 in_progress／awaiting_approval 標 failed 並關閉未結束的 run → 從 DB 依優先序 enqueue pending → 啟動 worker，watcher prime 後才啟動）；`TaskWatcher` 新增選用的 `on_file_changed`；`_worker_loop` 的 `_refresh_from_store`（D24）。新增 `test_runtime_db_mode.py`（14）、`test_task_watcher_file_hook.py`（5）、`integration/test_runtime_pg.py`（2，真實 Postgres）；6 種變異皆被抓到；既有測試一字未改且全綠。awaiting_approval 目前標 failed（`gate lost after restart`），Task 14 會改成先嘗試恢復。
  - 11c（1e21db1）：`api/tasks.py` 的列表、詳情、建立在 DB 模式走 repo（檔案模式不變）；`NewTaskRequest` 新增選用 `approval`；新增 `POST /tasks/{tid}/rerun` 與 `GET /tasks/export`。新增 `test_api_db_mode.py`（18）、`integration/test_api_db.py`（2，真實 Postgres，tasks.yaml 位元組與 mtime 不變）。`FORCE_MOCK=true` compose 實跑：容器啟動時 `alembic upgrade head` 成功、示範任務 3／3 在 Postgres 完成、重啟 backend 後不重複 enqueue；已 `down -v` 並刪除映像。既有測試一字未改且全綠。
  - 11d（85c8b0c）：`core/project_migration.py`（`projects` 列不存在時單一交易遷移；in_progress／awaiting_approval 改 failed 並建 imported 的 failed run；失敗整個回滾、下次重試）；新增 `ProjectRepository` Protocol 與 InMemory／Pg 實作；agents 匯入屬 Task 18，未做。新增 `test_project_migration.py`（10）、契約測試（InMemory 與 Pg 各 6）、`integration/test_project_migration_pg.py`（真實 Postgres）；4 種變異皆被抓到。
- **前端 F10 完成**（`feat/hud-frontend`，7b3ad29；尚未 QA）：便條牆（篩選、分頁、已刪除 agent 以座位快照顯示）與預算文字調整。新增 `NoteWall.tsx`／`NoteWall.module.css`、`useHistory.ts`、`historyFilter.ts`；改 `App.tsx`、`Header.tsx`、`PanelsContext.tsx`、`Avatar.tsx`、`net/api.ts`。新增／更新測試：`NoteWall.test.tsx`、`historyFilter.test.ts`、`App.test.tsx`、`Header.test.tsx`。commit message 未提供測試數字。

## 2026-11-21

- 派 `ala-qa` 期中覆核後端（85c8b0c）與前端（7b3ad29）。
- **期中 QA（ala-qa）**：
  - 後端 @ 85c8b0c：**CONCERN**。兩種引擎 935 passed、6 skipped；integration 71 passed；禁改清單無差異。Task 9、11b、11d 各一個問題，其餘 PASS。
  - 前端 @ 7b3ad29：**CONCERN**。vitest 41 檔、412 tests；typecheck、build、audit 0；型別與 §6／§6.3a／§7.1a 逐一比對無缺漏。F9c 缺 §11.7 小卡，其餘 PASS。
- **後端 QA 修正**（`feat/hud-backend`，5ca788b；未複驗）：54f4c52 初始化失敗時 create／rerun 回 503；5352841 新 id 一律以 pending 匯入、同檔重複 id 保留第一筆；5ca788b Pg／InMemory 遷移一致（`run_no` 接續、pending 不建 run）。兩種引擎 942 passed、6 skipped；integration 72 passed。
- **前端 QA 修正與補做**（`feat/hud-frontend`，d7b531b；未複驗）：fe1441a §11.7 桌上小卡（使用者決定補做；只從開頁後的事件累積）；dd64187 counts 校正；8cbf4f0 replay 回 `next_task`；b2f20ec 找不到 replay；02e8e2b 篩選全取消；7a56f4c 分頁去重；536986b aria-label；b9ba717 README；d7b531b fixture `mock_reason`。vitest 45 檔、461 tests。注意：後端 Task 25b 的 `resolve_llm` 必須在 FORCE_MOCK 時回 `mock_reason: force_mock`（plan 第 208 行），前端已照此實作。
- **使用者裁定（已寫入 plan D3）**：TODO.md 打勾的新項目略過不匯入；遷移時 DB 已有的任務不另建 imported run。
- **未 commit 的進行中工作**：後端 worktree 有 Task 12（用量持久化）WIP，已核對與 stash ccd2d8e 相同；前端 F11 WIP 在 stash 2614293（與 fe1441a 後的 `App.tsx`、`Desk.module.css` 衝突）。stash 堆疊為所有 worktree 共用，一律以 SHA `apply`，不要 `pop`。
- 公開 repo 準備（fdfb54a，dev 與 main 同步，尚未 push）：MIT LICENSE、README 英文簡介、清除絕對路徑、`.gitignore` 加 `projects/logs/`；GitHub description／topics 已設。使用者決定：**MVP 完成後再 push 並轉 public**。
- `projects/logs/` 來源：`tests/test_base_agent.py` 的 `test_call_llm_uses_configured_system_prompt` 未給 projects_dir，`BaseAgent._resolve_logs_dir` 退回 `<repo>/projects`；且 scanner 的 `_IGNORED_NAMES` 只有 `.gitkeep`，會把 `projects/logs/` 當成專案。待排入任務。
- **前端 F11 與關卡 B**（`feat/hud-frontend`）：F11（81b38de，套回 11/17 的 WIP）。關卡 B @ 81b38de 判 CONCERN（7 項：斷線公告洗版、AC10c 斷言無效、觸控量測漏 input、精簡模式 × 被徽章遮、間距 6 px、e2e 離線漏 WS／同源 /api、重複公告）→ 修正 651fd8b…9ec653c → **複驗 PASS @ 9ec653c**（vitest 47 檔 484；e2e 46，`--repeat-each=3` 138）。排入 F12：crowded fixture 加短名 agent（讓徽章 `pointer-events:none` 有 e2e 覆蓋）；重連後若有閘門在等就公告。已知限制：replay 下入口匣「待辦 0」／便條牆 10（replay 共用 `history.json`；live 模式一致）。

## 2026-11-23

- **後端 Task 12–16 完成**（`feat/hud-backend`）：b861ea9／088c6fc（11-21 裁定）、5753698 Task 12、5fda972 Task 13（checkpointer 表：`checkpoint_migrations`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes`）、c22a0da／436e830 Task 14、bfb42d9（`durability="sync"` 註解，使用者裁定維持 sync）、165b2a3 Task 15a（commit 曾被 auto-mode 擋下：改了分支新增的 `test_project_migration.py` 一條斷言；使用者核准後由 Lead commit）、1f87bd3 Task 15b、07bcf75 Task 16。
- **QA 關卡 A @ 07bcf75：FAIL**——gate_enter 的 pending writes 已存、該步 checkpoint 未存時重啟，`next=()` 被判定閘門遺失、任務標 failed（Pg 4/4 重現）。其餘各項 PASS；負載下閘門重啟 50/50。
- 修正 40ab08e（看 `snapshot.tasks`、已有 writes 時直接 `astream(None)`）、8fab092（同步 engine `connect_timeout=5`；寫入失敗維持只記 log）、f7aab1d（新端點 `async def`、繁中 detail）、06f9ba6（AC6 Pg 重啟預算測試）。
- **關卡 A 複驗 @ 06f9ba6：PASS**。兩種引擎 1077 passed、6 skipped；integration 96；無 Docker 981＋102 skipped。QA 的重現法對照組在 07bcf75 會重現、06f9ba6 為 0；負載下 50/50，累計約 1,100 次 sweep 重啟與 2,000 輪兩任務重啟，閘門遺失 0 次。
- Lead 裁定：15b 的 7 個判斷維持；檔案模式 `rework_count` 維持回任務本身的值（不改測試）；reject 後馬上 approve 回 409 為正確語意；approve 後立即關閉導致重啟後需再按一次＝已知限制（結果安全）。
- **待辦（非阻擋）**：`PgUsageStore.add` 在 event loop 上同步 INSERT，`connect_timeout` 只管建立連線，連上後卡住仍可能卡 loop；寫入失敗時 D20「`usage:update` 前 DB 已有該列」不成立，重啟後預算總額會少那一筆。建議改 `await asyncio.to_thread(...)`＋keepalives，並保留失敗紀錄補寫。`limit>200` 的 422 為 FastAPI 預設英文 detail。
- **QA 關卡 C @ aac1e9e：FAIL**。四項重點（預設編制零差異、C1–C6、live 不污染、上游截斷）PASS；問題：(1) `request_lineup` 未驗證且先改狀態再 compile，不合法編制污染狀態、之後每張任務失敗；(2) `(pm, qa)`／`(pm,)` 加閘門 `KeyError: 'gate_station'`；(3) 閘門恢復到續跑之間 `request_lineup` 回 `now`，舊 checkpoint 用新 graph 續跑；(4) 測試寫入 repo `projects/logs/`。Developer 8 個判斷：6 合理，2 交使用者。
- 修正 bdba3fd（先驗證、先 compile 再換狀態）、5815c44（內建 pm 設 `gate_station`；依 D17 這兩種編制合法）、2c40d83（tmp_path）。langgraph 1430 passed、6 skipped；legacy 1404、32 skipped；integration 116。
- **使用者裁定（已寫入 plan D4、D17、§4.4、§5.1）**：pending 編制等續跑的閘門任務跑完才套用；Mock check 把 `approval_feedback` 視同退件意見；退回內建 PM 時 PM 收到非空的 `approval_feedback`／`review_feedback`；DevOps 不匯入 agents 表（D4 文字修正）。
- 裁定實作：5d4c1d8（D17）、adf7224（§4.4：閘門 reject 後自訂 check 的 context 加 `reviewer_note`，prompt 出現 `[退件意見]`；真實 LLM 的 check 也會看到審核意見，已告知使用者）、27ddbe6（§5.1）。
- **關卡 C 複驗 @ 27ddbe6：CONCERN**——#1–#4 皆修好、三裁定符合；僅剩空意見 reject 仍耗兩次重做 → dcc487b（空白意見用「（審核者沒有留下意見）」）。Lead 抽查新測試＋特徵化＋golden 36 passed；Developer 回報 langgraph 1444 passed、6 skipped；legacy 1406、44 skipped；integration 117。**關卡 C 結案，M4 完成。**
- 另記：dev 既有 `tests/test_base_agent.py::test_call_llm_uses_configured_system_prompt` 每次全套都寫一個 PM log 到 repo 的 `projects/logs/`（禁改清單內；MVP 後處理）。

## 2026-11-24

- **M5 完成於整合分支** `feat/live-action-hud` @ 59deda0：3fca0ff／adc6e76 Task 24、ca996e5 Task 25a、4e3e0bd Task 25b、8810163…6f0aa03 Task 26（FORCE_MOCK Docker 實跑、`record_ws.py`、`recorded-mvp.jsonl`）、e801be8／6c24c8d／fda8fb9／cd05449 F12（切換前端：舊 `frontend/` 刪除、`web` → `frontend`、nginx-unprivileged 8080）；修正 d13752c（rework_count 寫回 DB）、a739d69（compose 傳 `MOCK_CHECK_FAIL_FIRST`）、687f70c（log 同秒排序）、3231d05／e6e51c4／59deda0（DB 模式 done／failed 計數一律由 DB 算，使用者同意選項 (a)）。
- **QA 關卡 D @ 59deda0（Docker，FORCE_MOCK）：CONCERN**。langgraph 1603、legacy 1479、integration 124 passed；vitest 499、e2e 50（另對 Docker nginx 跑 50）、live smoke 12。Task 27 操作清單全部走通（截圖在 session scratchpad `qa-gate-d/`）；AC1–AC26 中 25 條 PASS，AC18 CONCERN（移除的 agent 要等下一張開始才離開）。全程 `force_mock: true`，jobagent 容器未碰，`projects/` 已還原。
- 其他問題：shell 有 `FORCE_MOCK=true` 時 10 個既有測試失敗（環境變數未隔離）；status history 顯示 agent id；完成匣 aria-label 漏失敗數；README 6 處與現況不符。QA 另指出兩處測試期望值改動（compose 埠 80→8080、503 訊息改繁中）不在授權清單：皆為本分支新增的測試檔、隨功能變更，Lead 認可並已告知使用者。
- **使用者裁定（已寫入 plan D17、spec AC18）**：pending 編制在進行中任務結束時套用。修正列為 Task 26a。
- 已知限制（非 FAIL，維持）：replay 入口匣「待辦 0／便條牆 10」；approve 後立即關閉需再按一次；reject 後馬上 approve 409；同秒 log 檔名覆寫（MVP 後）；`test_base_agent` 寫 `projects/logs/`（MVP 後）；三行版面只在 replay；Mock 下不觸發 DevOps 警示。
- **Task 26a**（整合分支）：d043ead（conftest 清 `FORCE_MOCK`）、7a09696（任務結束時套用 pending 編制）、d8b8f68／cac34da（狀態紀錄與日誌顯示名稱、完成匣 aria-label 含失敗數）、5e0d149（README）、ee73ffc（`.env.example`，Lead；標題用 `Task 26a:` 卻帶 Co-Authored-By，保留不改）。
- **複驗 @ ee73ffc：CONCERN**——AC18 PASS；QA 找到 59deda0 就有的 bug：閘門等待中刪除自訂 agent → 重啟 → 核准，任務 `internal pipeline error`（agent 列立即刪除，重啟後座位找不到）。修正 093e888（被刪的 agent 記在 `projects.settings.leaving_agents`，等不含它的編制套用時才刪列；匯入同理）、0ed3f66（日誌 `display_name`）。
- **複驗 @ 0ed3f66：CONCERN**——5 步驟重現 PASS；剩：閘門等待中改 `kind` 再重啟會失敗、任務結束清理未持專案鎖、匯出含離開中的 agent。**Lead 裁定**：桌上 agent 在任務忙碌時（進行中、閘門、重啟後待續跑）改 `kind` → 409（匯入整筆 409）。修正 a042475（`kind` 409、專案鎖改為同一 asyncio task 可重入並由清理 hook 取得、匯出排除離開中的 agent 並寫 `pending_seats or seats`）。
- **複驗 @ a042475：PASS**。langgraph 1627 passed、legacy 1482 passed（151 skipped）、integration 126；vitest 503、e2e 50、e2e:live 11＋1 skipped。`_ProjectLock` 審查無 deadlock 路徑；race_probe 記憶體與 DB 一致。**關卡 D 結案，進入 Task 27 使用者驗收。**
- 待辦（MVP 後，低風險）：`core/pipeline.py:327-341` 任務結束套用 pending 時，`get_pipeline`／`save_pipeline` 在取得專案鎖之前，期間若有新的編制變更，DB 會被舊座位覆寫（QA 以延遲 1 秒重現，實際窗口數毫秒）；建議整段套用持鎖。畫面上沒有改 `kind` 或匯入的入口（只有 API）。
