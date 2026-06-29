# Product: Agent Factory Platform

一個將 AI agents 協作流水線視覺化的開發自動化平台。使用者將專案放入 `projects/` 目錄，系統自動識別專案、初始化 agents、並在偵測到新任務時觸發流水線執行。

## 核心流水線

任務依照以下順序流過三個 agent 站點：

**PM_agent → SWE_agent → QA_agent**

- QA 失敗時任務回送 SWE 重工，最多 3 次，超過則標記 failed
- **DevOps_agent** 獨立運行（不在流水線上），每 30 秒週期性監控系統健康

## 四個內建 Agent 角色

| Agent | 職責 |
|-------|------|
| PM_agent | 分析任務需求、拆解工作項目、產出執行計畫 |
| SWE_agent | 依 PM 規劃撰寫程式碼，重工時接收 QA 失敗報告 |
| QA_agent | 設計與執行測試，pass/fail 判定，失敗時輸出結構化報告 |
| DevOps_agent | 監控 CPU/記憶體/磁碟/服務健康，異常時廣播警告事件 |

## 任務來源

- `projects/{project}/tasks.yaml` — 主要任務格式（含 priority / status）
- `projects/{project}/TODO.md` — 補充來源，解析 `- [ ]` 項目

## LLM 整合

- 支援 OpenAI、Anthropic、Gemini
- 無 API key 時自動切換 Mock 模式，前端顯示 `[MOCK MODE]` 指示器
- Mock 模式下流水線仍完整運作，輸出標示 `[MOCK MODE]`

## 前端

保留原有工廠流水線 sci-fi HUD 動畫風格，透過 WebSocket 即時更新。新增：
- 專案 select box（切換不同 projects）
- DevOps 獨立監控面板（右側）
- Mock mode 指示器、WebSocket 斷線警告
