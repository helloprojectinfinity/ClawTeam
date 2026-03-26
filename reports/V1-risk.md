# V1 Risk Assessment: OpenClaw GitHub PR Analysis

> **Reviewer**: Risk Reviewer (poc-v3-github-phase1)
> **Date**: 2026-03-26
> **Source**: openclaw/openclaw — 30 most recent PRs (gh pr list --state all --limit 30)
> **Scope**: PR 內容風險分析、安全影響評估、代碼品質觀察

---

## Executive Summary

分析 openclaw/openclaw 最近 30 個 PR，發現 **3 個高安全影響 PR**、**5 個中等風險 PR**、以及若干代碼品質觀察。整體而言，上游社群活躍，安全修復 PR 反應迅速（如 #55241 systemd secret 洩漏修復），但也存在大規模重構 PR（#55278 XL 級）帶來的合併風險。

**Overall Risk Rating: MEDIUM**（社群活躍，安全意識良好，但大 PR 合併風險需關注）

---

## 1. High-Security-Impact PRs

### PR #55241 — fix(daemon): use EnvironmentFile= instead of inline secrets in systemd units

| Attribute | Value |
|-----------|-------|
| **Author** | natedemoss (Nathan DeMoss) |
| **Status** | OPEN |
| **Labels** | gateway, size: S |
| **Security Impact** | HIGH |

**問題**：`gateway install` 命令將 `~/.openclaw/.env` 中的 secret 值直接寫入 systemd unit file 的 `Environment=` 指令中，導致：
- Secrets 洩漏到 `.bak` 備份檔案
- `openclaw doctor` 誤判配置為非標準，觸發循環重裝

**修復**：改用 `EnvironmentFile=<path>` 引用外部 `.env` 檔案，避免 inline 寫入 secrets。

**風險評估**：
- ✅ **正面**：直接修復了 credential 洩漏問題
- ⚠️ **注意**：`.env` 檔案的權限管理未在 PR 中明確規定（應設為 0600）
- ⚠️ **注意**：修復僅覆蓋 Linux systemd，macOS launchd 狀況未提及

**緩解建議**：確認 `.env` 檔案權限為 0600，且僅限 gateway 用戶可讀。

---

### PR #55281 — Gateway: require caller scope for subagent session deletion

| Attribute | Value |
|-----------|-------|
| **Author** | jacobtomlinson (Jacob Tomlinson) |
| **Status** | OPEN |
| **Labels** | gateway, maintainer, size: S |
| **Security Impact** | HIGH |

**問題**：Plugin subagent 的 `deleteSession` 調用路徑存在 `syntheticScopes` 覆蓋，允許未經認證的 fallback 調用者刪除 session。

**修復**：移除 `syntheticScopes` 覆蓋，要求所有 session 刪除操作必須經過正常的 gateway 授權。

**風險評估**：
- ✅ **正面**：修復了權限提升漏洞——第三方 plugin 不能再透過 fallback 路徑刪除 session
- ⚠️ **注意**：破壞性變更——依賴 fallback deletion 的第三方 plugin 將失效
- ⚠️ **注意**：需要社群溝通，告知 plugin 開發者遷移

**緩解建議**：在 release notes 中明確標註此 breaking change，並提供遷移指南。

---

### PR #55278 — Gateway: reconcile ingress and embedded runner auth seam

| Attribute | Value |
|-----------|-------|
| **Author** | THESPRYGUY |
| **Status** | OPEN |
| **Labels** | docs, gateway, cli, scripts, commands, agents, **size: XL** |
| **Security Impact** | HIGH (behavioral) |

**問題**：Gateway ingress 與 embedded runner 之間的信任邊界模糊，`previous_response_id` session 重用缺乏 scope 限制。

**修復**：
- Ingress 執行現在要求明確的 `senderIsOwner` 和 `allowModelOverride` 標誌
- `previous_response_id` session 重用限制在相同 auth/agent 邊界內
- `x-openclaw-model` override 經過 allowlist 驗證

**風險評估**：
- ⚠️ **高風險**：XL 級 PR（90+ 檔案變更），包含大量非核心檔案（ops/、scripts/、M10 文檔）
- ⚠️ **高風險**：PR 描述承認是「partial Lane 2 submission」，完整 reconciliation 延後到 Lane 2b
- ⚠️ **高風險**：包含 `reviewer_feedback.txt` 和 `ops/config/openclaw.json.snapshot`（可能洩漏配置）
- ✅ **正面**：Security Impact 自我評估完整，明確列出變更的權限/執行面影響
- ✅ **正面**：76/76 focused tests passed

**緩解建議**：
1. 拆分為多個較小 PR（gateway ingress、embedded runner auth、docs/scripts 分離）
2. 移除 `ops/config/openclaw.json.snapshot`（可能含敏感配置）
3. 在合併前完成 full upstream `run/attempt.ts` reconciliation

---

## 2. Medium-Risk PRs

### PR #55211 — fix: prevent re-entrant loop in internal hook trigger

| Attribute | Value |
|-----------|-------|
| **Author** | ggzeng (Gavin Zeng) |
| **Status** | OPEN |
| **Labels** | size: S |
| **Risk** | MEDIUM — 無限循環可能導致資源耗盡 |

**分析**：Internal hook trigger 的 re-entrant loop 是一個穩定性問題。如果 hook 觸發自身，可能導致無限遞迴和 OOM。PR body 過於簡略（僅 "See PR body above"），缺乏問題描述。

---

### PR #55267 — fix(plugins): apply bundled allowlist compat in plugin status report

| Attribute | Value |
|-----------|-------|
| **Author** | pingren (Ping) |
| **Status** | OPEN |
| **Labels** | size: XS |
| **Risk** | MEDIUM — CLI 診斷資訊不準確 |

**分析**：當 `plugins.allow` 配置時，CLI 報告 bundled plugins 為 disabled，但 gateway runtime 正常載入。這導致診斷誤導，用戶可能以為插件未啟用。

---

### PR #55221 — perf(agents): add contextInjection option to skip workspace re-injection

| Attribute | Value |
|-----------|-------|
| **Author** | cgdusek |
| **Status** | OPEN |
| **Risk** | MEDIUM — 跳過 workspace re-injection 可能導致 context 不完整 |

**分析**：Performance optimization 但可能犧牲 context 完整性。需要確認跳過 re-injection 的條件是否足夠嚴格。

---

### PR #55214 — fix: trigger model fallback on HTTP 503 Service Unavailable

| Attribute | Value |
|-----------|-------|
| **Author** | bugkill3r |
| **Status** | OPEN |
| **Risk** | MEDIUM — Ollama 503 錯誤處理 |

**分析**：直接影響本地 Ollama 部署的可靠性。如果 Ollama 返回 503，應該 failover 到備用模型而不是報錯。

---

### PR #55226 — Update Minimax API base URL

| Attribute | Value |
|-----------|-------|
| **Author** | wcc0077 |
| **Status** | OPEN |
| **Risk** | MEDIUM — API endpoint 變更可能影響服務可用性 |

**分析**：變更 API base URL 需要驗證新 endpoint 的穩定性和安全性。

---

## 3. Merged PRs (Positive Signals)

### PR #55227 — test: improve test runner help text

| Attribute | Value |
|-----------|-------|
| **Author** | codex (AI-generated) |
| **Status** | MERGED |
| **Signal** | ✅ CI/CD 改進持續進行 |

---

## 4. Pattern Analysis

### 4.1 PR 類型分佈

| Type | Count | Percentage |
|------|-------|------------|
| fix | 18 | 60% |
| feat | 5 | 17% |
| perf | 2 | 7% |
| ci/test | 3 | 10% |
| docs | 2 | 7% |

**觀察**：60% 為 fix PR，顯示代碼庫處於穩定化階段。

### 4.2 安全相關 PR 比例

| Category | Count | PR Numbers |
|----------|-------|------------|
| Auth/Credential | 3 | #55241, #55278, #55281 |
| Plugin Security | 2 | #55267, #55212 |
| Input Validation | 2 | #55210, #55209 |
| Total Security | 7 | 23% |

**觀察**：23% 的 PR 涉及安全問題，顯示社群對安全的重視程度較高。

### 4.3 AI-Generated PRs

至少 2 個 PR 明確標註 AI assistance（#55241 Claude Code, #55227 codex）。這是一個值得關注的趨勢：
- ✅ AI 輔助加速修復
- ⚠️ 需要人工審核確保品質

---

## 5. Risk Matrix Summary

| PR | Risk | Severity | Priority |
|----|------|----------|----------|
| #55278 | XL PR 合併風險 + 配置洩漏 | HIGH | P0 |
| #55241 | .env 權限未明確規定 | MEDIUM | P1 |
| #55281 | Breaking change for plugins | MEDIUM | P1 |
| #55211 | Re-entrant loop（PR body 不完整） | MEDIUM | P1 |
| #55221 | Context injection skip 風險 | LOW | P2 |
| #55214 | Ollama 503 failover | LOW | P2 |

---

## 6. Recommendations

### For Upstream (openclaw/openclaw)

1. **PR #55278 拆分**：XL PR 應拆分為 gateway ingress、embedded runner auth、docs/scripts 三個獨立 PR
2. **配置洩漏檢查**：移除 PR #55278 中的 `ops/config/openclaw.json.snapshot`
3. **Breaking change 溝通**：PR #55281 需要在 release notes 中明確標註
4. **PR body 範本執行**：PR #55211 的 body 過於簡略，應強制執行 PR 範本

### For Our Fork

5. **Cherry-pick 安全修復**：優先合併 #55241（systemd secret 洩漏）和 #55281（session 刪除權限）
6. **延後 #55278**：等待上游拆分後再考慮合併
7. **監控 #55214**：Ollama 503 failover 對我們的本地部署很重要

---

_Report generated by Risk Reviewer — poc-v3-github-phase1 team_
_Output: reports/V1-risk.md_
