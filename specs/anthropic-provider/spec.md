# Spec＋Tasks：接上真實的 Claude API（Anthropic provider）

Status: CONFIRMED（2026-11-16，使用者決定「先用 Claude 的 API 來接」；下方標「預設」的細節由 team lead 依 Anthropic 官方建議選定，使用者可改）

> 背景：`backend/core/llm_client.py` 的 `AnthropicClient.call` 與 `OpenAIClient.call` 目前都是 `raise NotImplementedError`，整個平台只能跑 Mock 模式。`specs/live-action-hud/` 的階段 2（token 串流）與階段 3（LLM 評估 agent 編制）要有實際意義，必須先有一個真的 provider。本文件只做 **非串流** 的 Anthropic provider；串流留給 live-action-hud 階段 2。

## 目標

1. agent 的 YAML 設成 `llm_provider: anthropic` 且環境有 `ANTHROPIC_API_KEY` 時，pipeline 用真實的 Claude 跑完 PM → SWE → QA。
2. 用量與花費照實記錄（`usage:update`、預算守門照常運作）。
3. 沒有金鑰時的行為完全不變（自動退回 Mock），所有測試仍然離線。

## 非目標

- 串流（`agent:token`）、tool use、prompt caching、OpenAI／Gemini provider。
- 修改 `projects/example-project/agents/*.yaml`（使用者有未 commit 的修改，**不要碰**）。
- 修改既有測試檔。

## 設計決定

| # | 決定 | 理由 |
|---|---|---|
| D1 | 用官方 `anthropic` Python SDK，不用 `httpx` 手刻 HTTP。版本在 `requirements.txt` 釘死（`anthropic==<安裝當下的最新穩定版>`，把實際版本寫進回報）。 | 官方 SDK 有型別化的例外與重試；手刻會漏掉 API 的變動。 |
| D2 | **預設** 模型 `claude-opus-5-5`。只影響「新建立的 agent 設定的預設值」與文件範例；每個 agent 仍由自己 YAML 的 `llm_model` 決定。模型 ID 一律用不帶日期後綴的完整字串。 | Anthropic 目前建議的預設模型。成本是使用者的決定：想省錢可在個別 agent 的 YAML 改成 `claude-sonnet-5-5` 或 `claude-haiku-5-5`。 |
| D3 | 請求形狀：`client.beta.messages.create(model=..., max_tokens=16000, system=<system_prompt 或省略>, messages=[{"role":"user","content":prompt}], output_config={"effort":"medium"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")`。**不送** `thinking`、`temperature`、`top_p`、`top_k`、`budget_tokens`（新模型會回 400）。 | Opus 5.5 的 thinking 永遠開啟、不能關；effort 預設就是 medium，明寫避免換模型時行為漂移。`fallbacks="default"` 是 **預設開啟** 的拒絕後援：安全分類器拒絕時，API 在同一次呼叫內改由後援模型回答。 |
| D4 | `fallbacks` 與 `effort` 只在模型支援時送：實作一個小的對照（至少 `claude-opus-5-5`、`claude-sonnet-5-5`、`claude-fable-5-2` 送 fallbacks；`claude-haiku-5-5` 與未知模型不送 fallbacks、不走 beta 端點，改用 `client.messages.create`）。對照表寫成模組常數並有測試。 | Haiku 5.5 沒有 server-side fallback；未知模型送未知參數會 400。 |
| D5 | 回傳文字＝所有 `type == "text"` 的 content block 依序串接（先檢查 `.type`，不可直接取 `content[0].text`——第一個 block 可能是 thinking）。 | 新模型的回應會帶 thinking block。 |
| D6 | `stop_reason` 處理：`end_turn`／`stop_sequence` → 正常回傳；`refusal` → 拋 `LLMRefusalError`（訊息含 `stop_details.category`，不重試）；`max_tokens` → 拋 `LLMTruncatedError`（不重試，訊息提示輸出被截斷）；其他值 → 拋 `LLMUnexpectedStopError`。三個例外都繼承同一個 `LLMCallError`，定義在 `llm_client.py`。 | 被截斷或被拒絕的輸出不能當成 agent 的成果往下傳。 |
| D7 | 用量：`last_usage = LLMUsage(input_tokens = usage.input_tokens + (cache_creation_input_tokens or 0) + (cache_read_input_tokens or 0), output_tokens = usage.output_tokens)`。 | 目前不開快取，後兩者為 0；先加總避免日後開快取時少算。 |
| D8 | 重試：先讀 `agents/base_agent.py` 現有的 tenacity 重試（重試哪些例外、幾次）。SDK client 建構時設 `max_retries=0`，重試只由 BaseAgent 負責，避免 3×3 疊加。`anthropic.RateLimitError`、`APIConnectionError`、`APIStatusError(status>=500)` 視為可重試；`BadRequestError`／`AuthenticationError`／`PermissionDeniedError`／`NotFoundError` 與 D6 的三個例外不可重試。**若現有 BaseAgent 無法區分可重試與不可重試（例如一律重試所有 Exception），停下來回報方案，不要自行改 `base_agent.py` 的既有行為。** | 401 重試三次只是浪費時間；但改 BaseAgent 的重試語意會動到既有測試鎖住的行為。 |
| D9 | 逾時：SDK client 設 `timeout=600.0`（秒）。agent.run 在 thread 內同步呼叫，不會卡住 event loop。 | SDK 預設值，明寫。 |
| D10 | 可測性：`AnthropicClient.__init__` 增加只限關鍵字的選用參數 `sdk_client=None`；不給就 `anthropic.Anthropic(api_key=..., max_retries=0, timeout=600.0)`。`import anthropic` 放在建構子內（延遲匯入），沒裝 SDK 或走 Mock 時不載入。 | 測試注入假物件，完全不連網。 |
| D11 | 計價表（`core/usage_tracker.py`，單位是每 1k token）新增：`claude-opus-5-5` (0.004, 0.020)、`claude-sonnet-5-5` (0.002, 0.010)、`claude-haiku-5-5` (0.0001, 0.0005)、`claude-fable-5-2` (0.008, 0.040)。既有的列不刪。`anthropic` 的 provider 預設價改成 opus-5-5 的價格。 | 2026-11 的官方定價；沒有這幾列預算守門會低估花費。**發生 fallback 時實際由別的模型計費，花費是估計值**——在 README 註明。 |
| D12 | 金鑰只從環境變數讀（沿用 `api_key_env`），不寫進任何檔案、log、事件、例外訊息。 | 既有規則。 |

## 驗收標準

- AC1：沒有任何金鑰時，全套測試數與結果和改動前相同（另加本計畫的新測試），且測試不連網（新測試不得建立真的 `anthropic.Anthropic`）。
- AC2：注入假 SDK client 時，`call()` 送出的參數完全符合 D3／D4（逐 key 斷言；斷言 **沒有** `thinking`、`temperature`）。
- AC3：D5、D6、D7 每一條都有測試（含「第一個 block 是 thinking」「refusal」「max_tokens」「usage 加總」）。
- AC4：D8 的每一類例外都有測試證明重試或不重試。
- AC5：`PIPELINE_ENGINE` 兩種值各跑一次「假 SDK client 的端對端」：三個 agent 都用 `AnthropicClient`（注入假物件），任務跑到 done，`usage:update` 的 provider／model／tokens 正確，花費用 D11 的價格算出。
- AC6：金鑰不出現在任何 log 或例外訊息（測試：用假金鑰字串建構、觸發各種錯誤，斷言字串不在 caplog 與 `str(exc)` 中）。
- AC7：真實呼叫的煙霧腳本（不被 pytest 收集）可由使用者手動執行，印出回應前 200 字、stop_reason、model、usage、估計花費。

## Tasks

給執行者的須知同 `specs/langgraph-pipeline/tasks.md`（不 push、不 merge、commit 不加 Co-Authored-By／Generated-with、不改既有測試檔、不碰 frontend、不碰 `projects/example-project`）。本計畫 **解除** `backend/core/llm_client.py` 的禁改；`backend/agents/` 仍然不改（見 D8 的停下來條件）。分支：`feat/anthropic-provider`，從最新的 `dev` 開。

**必須停下來問**：需要改既有測試檔；需要改 `base_agent.py`；安裝 `anthropic` 會升降 fastapi／starlette／pydantic／httpx／websockets 的主版本（SDK 1.x 依賴 `httpx2`，與專案的 `httpx` 是不同套件，預期不衝突——以 `pip check` 與安裝前後的 `pip freeze` 為準）；SDK 的實際介面與 D3 不符（例如 `fallbacks`／`output_config` 參數名不同）——以安裝的 SDK 原始碼為準並回報，不要猜；**任何會發出真實 API 請求的動作（你不得執行煙霧腳本，也不得讀取或要求金鑰）**。

- [ ] A1. 〔Dev〕特徵化：新增 `tests/test_llm_client_characterization.py`，鎖住改動前的行為——`get_llm_client` 的四條選擇規則、`MockLLMClient.call` 對三個角色的輸出與 `last_usage`、`AnthropicClient` 沒金鑰時拋 `MissingAPIKeyError` 且訊息內容。先在未改動的程式上通過。— done when: 新檔通過；全套數字＝基準＋新測試數。
- [ ] A2. 〔Dev〕安裝與釘版：`requirements.txt` 加 `anthropic==<版本>`；記錄安裝前後 `pip freeze` 的差異。讀已安裝 SDK 的原始碼確認 `client.beta.messages.create` 的參數名（`betas`、`fallbacks`、`output_config`）、例外類別名、`Message.stop_reason`／`stop_details`／`usage` 欄位。— done when: `pip check` 無錯；回報列出確認過的介面與檔案位置；與 D3 不符之處明列。
- [ ] A3. 〔Dev〕實作 `AnthropicClient.call`（D3–D7、D9、D10、D12）與 `LLMCallError` 家族；新增 `tests/test_anthropic_client.py`（假 SDK client）。— done when: AC2、AC3、AC6 的測試通過；`grep -n "import anthropic" backend/core/llm_client.py` 只出現在建構子內。
- [ ] A4. 〔Dev〕重試分類（D8）：先回報 `base_agent.py` 現況；若不需改 BaseAgent 即可達成則實作並測試，否則停下來。— done when: AC4 通過，或已回報方案並停下。
- [ ] A5. 〔Dev〕計價表（D11）與測試；`.env.example` 的 Anthropic 註解改成現行模型名並加一行「建議同時設定 PROJECT_BUDGET_USD」。— done when: 新價格有測試；既有 usage_tracker 測試未改且通過。
- [ ] A6. 〔Dev〕端對端（AC5）：新增 `tests/test_pipeline_anthropic_e2e.py`，兩種引擎參數化。— done when: 通過；全套三種設定（未設定、legacy、langgraph）各 3 次數字一致。
- [ ] A7. 〔Dev〕煙霧腳本 `backend/scripts/anthropic_smoke.py`（AC7）與 README「接上 Claude」一節：如何設金鑰、如何把 agent YAML 改成 `llm_provider: anthropic`／`llm_model: claude-opus-5-5`／`api_key_env: ANTHROPIC_API_KEY`、如何換便宜的模型、fallback 與花費估計的說明、預算上限。腳本沒有金鑰時印清楚的訊息並 exit 2；**你不要執行它**。— done when: `python scripts/anthropic_smoke.py` 在無金鑰環境 exit 2；README 段落完成。
- [ ] A8. 〔QA〕覆核：範圍、AC1–AC6 逐項、自己設計假 SDK 回應的攻擊（空 content、只有 thinking block、usage 欄位為 None、stop_details 為 None 的 refusal）、確認測試沒有任何真實連線（例如測試期間把 `socket.socket.connect` 換成會拋錯的版本跑全套）。
- [ ] A9. 〔使用者〕在 `.env` 設 `ANTHROPIC_API_KEY`（與建議的 `PROJECT_BUDGET_USD`），執行煙霧腳本，再把一個測試專案的 agent 改成 anthropic 跑一張任務。
