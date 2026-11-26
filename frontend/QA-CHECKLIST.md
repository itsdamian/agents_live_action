# 前端 QA 人工檢查清單（QA 關卡 B：AC8–AC11b）

自動化已涵蓋的部分先跑，剩下的靠這份清單目視確認。所有項目都在 **replay 模式**（不需要後端、不會呼叫任何 LLM）完成。

## 0. 先跑自動化

```bash
cd frontend
npm ci
npm run typecheck          # AC8：事件型別與 plan §6 一致
npm run test               # AC8：Vitest（含 reducer 每種事件、守門、元件互動）
npm run build
npm run e2e                # AC10 a–f（Playwright，約 20 秒，會先 build 再用 vite preview）
```

`npm run e2e` 不會自己下載瀏覽器，依序使用：`PW_CHROMIUM_PATH` 指定的執行檔 → 這版 Playwright
預期的 chromium（`npx playwright install chromium`）→ Playwright 快取中已有、最新的 chrome-headless-shell
（較舊的版本也可以）。測試期間只連 `vite preview`，其他網址（含 Google Fonts）一律攔下。

預期：全部通過、無 console error／warning。

## 1. 啟動 replay

```bash
npm run dev     # http://localhost:5173
```

| 網址 | 內容 |
| --- | --- |
| `/?replay=default-run` | 預設三席：一張任務、QA 退件一次、`after_qa` 閘門（6 秒後自動放行）、完成（AC9） |
| `/?replay=default-run&speed=4` | 四倍速 |
| `/?replay=custom-lineup` | 設計師報到、資安把關（Opus）、`pipeline:pending`、`llm:updated`、critical 預算警示 |
| `/?replay=crowded` | 一條線 8 席（其中一席的模型是 Claude 但缺金鑰 → 徽章有虛線與提示） |
| `/?replay=crowded-3x8` | 三條線各 8 席（靜態） |
| `/?replay=gate` | 靜態：QA 桌停在核准閘門（重啟後由 snapshot 還原的情形） |

## 2. AC9 與畫面（對照 `specs/live-action-hud/prototype/agent-live-stage.html`）

- [ ] `default-run`：便條從「待辦」飛到 PM → SWE → QA；QA 退件時便條變紅飛回 SWE、SWE 發抖、便條角落出現重做數字；重做後 QA 通過；QA 桌舉手出現「可以／重做」；放行後便條飛進「完成」、全員歡呼約 1 秒。
- [ ] 角色造型 6 種（眼鏡、耳機、工程帽、貝雷帽、天線、蝴蝶結）與原型一致；色票與深色模式（系統切深色）正常。
- [ ] 泡泡逐字打出目前階段文字（「讀任務：「…」」「思考中…（mock-model）」「交出成果，往下傳」「檢查通過」「退件 → SWE」）。
- [ ] 每桌名牌旁有模型徽章（Mock／Opus／Sonnet／Haiku／Fable）；FORCE_MOCK／缺金鑰時徽章為虛線且有提示（滑過看 title），單純選 Mock 沒有提示。
- [ ] 點角色開「最近的動作」小卡（寬螢幕為桌旁小卡、≤640 px 為全螢幕面板），`Esc` 或「關閉」收起。
- [ ] 沒有水平捲軸、沒有東西疊在一起（375 與 1280 寬各看一次；`crowded`、`crowded-3x8` 也看）。

## 3. AC11 舊區塊逐項（plan §11.7）

- [ ] Header：專案名稱、`[MOCK MODE]`（FORCE_MOCK／缺金鑰時帶原因）、連線狀態（回放中）。
- [ ] 統計列：完成、重做、進行中、今日花費；進行中可點開任務詳情。
- [ ] 派一張任務：核准時機三選項文字為「不用」「做完後先給我看」「全部通過後給我看」（AC11b）；標題空白會提示；送出後出現「看詳情」。
- [ ] 任務詳情五個分頁：概況、日誌、交付檔、各 agent 產出、執行紀錄（replay 只有概況摘要，其餘顯示「還沒有…」）。核准按鈕在等核准的任務上可用。
- [ ] 機房：有警示時按鈕上有圓點（critical 紅、warning 橘、info 藍）；`custom-lineup` 會跳出 critical toast「機房警示：…」並可按「看機房」。
- [ ] 斷線橫幅：以真後端驗（見 §6）；replay 只會在載入失敗時出現（試 `/?replay=nope`）。
- [ ] 便條牆：點「待辦」匣（預設只勾「待辦」）與「完成」匣（預設勾「完成」「失敗」）；五種狀態可切換；每張卡有標題、結果、經過的 agent 小頭像（重做時每站只出現一次）、重做次數、花費、完成時間、失敗原因（最多 40 字）；已被刪除的 agent（設計師、資安把關）仍顯示名字與造型並標「（已離開）」；點卡片開任務詳情。

## 4. 新增／移除 agent 與模型（F8、F9a）

- [ ] 「新增 agent」：名字、負責的事、指示、工作方式、造型、座位、模型；各欄位錯誤訊息為繁中（名字 >14 字、負責 >24 字、指示 >2000 字、重名、把關排第一席、一條線已 8 席、自訂已 16 位）。
- [ ] 模型下拉只有「跟專案預設／Mock」加上後端 `llm.models` 給的清單（replay 中是 Opus／Sonnet／Haiku／Fable 四個），沒有別的模型。
- [ ] 新增成功：角色跳進場自我介紹（報到）、有 toast。桌上「×」請他離開 → 到「板凳」；板凳上可「回座」，自訂的可「刪除」（會先確認）。
- [ ] Header「模型」選單：切到 Claude 先出現確認框「會產生費用，目前預算上限：每日 … 、專案 …」，取消不會切換；沒有任何預算上限時顯示「目前沒有設定預算上限，建議在後端設定 PROJECT_BUDGET_USD」。

## 5. 行動版與無障礙（AC10 的人工補充）

在 375×812（Chrome DevTools 的 iPhone 模式）：

- [ ] 便條牆、任務詳情、新增 agent、派任務、機房都是全螢幕面板，左上「返回」可離開，`Esc` 也行；開著時背景不能捲動、Tab 出不去。
- [ ] 所有按鈕好按（至少 44×44）：閘門「可以／重做」、桌上「×」、兩個匣子、主要按鈕、分頁、篩選。
- [ ] 瀏海／底部橫條機型：內容不被 safe-area 遮住（Toast、面板底部）。
- [ ] 作業系統開啟「減少動態效果」：角色不動、便條直接跳到位、泡泡整段一次出現。

鍵盤（桌面）：

- [ ] `Tab` 可到閘門「可以」，`Enter` 放行；焦點框清楚可見；「重做」`Enter` 展開意見欄、`Esc` 收起。
- [ ] 螢幕閱讀器（VoiceOver）：閘門等待、退件、新同事報到、任務完成／失敗會被播報；toast 與斷線橫幅會被朗讀。

## 6. 接真後端（QA 關卡 D 才需要；此處僅供參考）

```bash
cd frontend
BACKEND_HOST_PORT=8001 npm run dev      # /api、/ws 代理到 http://localhost:8001
```

後端以 `FORCE_MOCK=true` 啟動，並先確認 `curl -s http://localhost:8001/api/health` 回 `"force_mock": true`。停掉後端再開起來，確認「重新連線中…（第 n 次，約 s 秒後）」與橫幅、恢復後自動回到正確狀態。

## 7. 已知限制（不是缺陷）

- replay 模式沒有後端：任務詳情只有摘要；專案選擇是純文字；新增／移除／切模型／核准等互動由前端送「假事件」，檔案本身的時間軸照常往下走（例如在放行前先按「可以」，檔案稍後仍會播一次放行）。
- 便條牆在 replay 讀的是共用的 `public/replay/history.json`（30 張、10 張待辦），與各 replay 的任務無關；
  所以「待辦」匣（佇列長度，例如 0）和便條牆的「待辦 10」數字不同。開便條牆後「完成」匣與統計列會依
  history 的 counts 校正（§6.3a-7），「待辦」匣不會（它顯示的是佇列）。
- 階段 2 以後的事件（`agent:token`、`staffing:*`、`import:progress`）：`agent:token` 只進 liveText 暫存、畫面尚未顯示；其餘會被安靜丟棄。
