# V1 Risk Assessment: OpenClaw Architecture (v3)

> **Reviewer**: Risk Reviewer (poc-v3-phase1)
> **Date**: 2026-03-26
> **Version**: OpenClaw 2026.3.23-2
> **Scope**: Security model, reliability, performance, industry gaps

---

## Executive Summary

OpenClaw 作為一個持續運行的個人 AI 助手平台，在多 channel 整合和 plugin 生態方面表現出色。然而，其安全模型存在結構性弱點——特別是 credential 管理和存取控制層面。本報告聚焦四大風險維度，提出分級緩解建議。

**Overall Risk Rating: MEDIUM**（個人使用可接受，生產環境需加固）

---

## 1. Security Model — 安全模型

### 1.1 Credential 管理

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| API keys 明文存儲於 openclaw.json | CRITICAL | LOW | 供應商帳戶被盜用 |
| Gateway token 單一認證層 | HIGH | MEDIUM | Token 洩漏 = 完全控制 |
| 缺乏 token 輪換機制 | HIGH | MEDIUM | 無法快速撤銷洩漏 token |
| Telegram bot token 明文 | HIGH | LOW | Bot 被劫持 |

**核心發現 — Config 檔案是最大的 attack surface**：

`~/.openclaw/openclaw.json` 包含所有敏感資訊（API keys、tokens），且以明文存儲。雖然 `config.get` 回傳時遮罩（`__OPENCLAW_REDACTED__`），但：
- 任何能讀取該檔案的本地進程都能取得所有 credentials
- 沒有與 OS keychain（macOS Keychain、Linux secret-service）整合
- 沒有檔案權限強制（依賴用戶 umask）

**Gateway Token 的單點信任問題**：

```
gateway.auth:
  mode: "token"
  rateLimit: { maxAttempts: 5, windowMs: 60000 }
```

- 所有請求共用同一 token，無角色區分
- Rate limiting 僅保護登入端點，不保護已認證請求
- 沒有 token 過期機制
- 攻擊者取得 token 後可執行：config 修改、session 存取、任意命令執行

### 1.2 ACP 權限模型

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| `permissionMode: "approve-all"` 互動模式 | HIGH | MEDIUM | 子 agent 獲得過大權限 |
| `nonInteractivePermissions: "deny"` 是唯一防線 | MEDIUM | HIGH | 非互動模式下功能受限 |
| 缺乏細粒度權限（檔案/網路/命令分離） | MEDIUM | MEDIUM | 最小權限原則無法實施 |

### 1.3 Channel Security

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| Telegram `groupPolicy: "open"` | MEDIUM | HIGH | 任何群組成員可觸發 bot |
| Webhook hooks token 暴露面 | MEDIUM | LOW | 未授權喚醒 |
| 缺乏訊息簽名驗證 | LOW | MEDIUM | 訊息偽造（理論上） |

---

## 2. Reliability Risks — 可靠性風險

### 2.1 Memory System (memory-lancedb-pro)

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| AutoCapture 固化幻覺為「記憶」 | CRITICAL | MEDIUM | 長期行為偏差 |
| 向量資料庫投毒（惡意內容注入） | CRITICAL | LOW | 持續性操控 |
| Embedding 服務（Ollama）單點故障 | HIGH | MEDIUM | 記憶功能完全失效 |
| 記憶體無限膨脹（無自動清理） | MEDIUM | HIGH | 檢索效能退化 |

**核心發現 — AutoCapture 的信任邊界模糊**：

```yaml
autoCapture: true
smartExtraction: true
extractMinMessages: 2
extractMaxChars: 8000
```

系統自動從對話中擷取記憶，但：
- 用戶的錯誤陳述、bot 的幻覺都可能被固化
- 擷取用的 LLM（xiaomi/mimo-v2-pro）本身可能產生不準確的擷取
- **沒有用戶確認機制**——記憶直接寫入向量資料庫
- 一旦寫入，影響是持久的（類似「記憶體投毒」）

**Hybrid Retrieval 的低閾值風險**：

```yaml
retrieval:
  mode: "hybrid"
  vectorWeight: 0.6
  bm25Weight: 0.4
  minScore: 0.3    # 相當低
  rerank: "none"   # 無重排序
```

`minScore: 0.3` 確保高召回率，但可能檢索不相關記憶干擾推理。無 reranking 意味著初步結果即最終結果。

### 2.2 Session Management

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| Context pruning 可能丟失關鍵上下文 | HIGH | MEDIUM | Agent 行為不一致 |
| Compaction memory flush 競態 | MEDIUM | MEDIUM | 記憶遺失 |
| 872K context tokens 高記憶體佔用 | MEDIUM | HIGH | OOM 風險 |
| Heartbeat 10 分鐘間隔過長 | MEDIUM | HIGH | 異常偵測延遲 |

**Context Pruning 的雙刃劍**：

```yaml
contextPruning:
  mode: "cache-ttl"
  ttl: "6h"
  softTrimRatio: 0.3
  hardClearRatio: 0.5
```

- 6 小時 TTL：超過時間的上下文被裁剪
- `hardClearRatio: 0.5`：極端情況下丟棄 50% 上下文
- **沒有「不可裁剪」標記機制**——關鍵決策上下文可能被丟棄

**Compaction Flush 競態**：

```yaml
compaction:
  memoryFlush:
    enabled: true
    softThresholdTokens: 10000
```

- Flush 是非同步的
- 如果 compaction 在 flush 完成前執行，記憶可能遺失
- 沒有確認機制確保 flush 成功後才執行 compaction

### 2.3 Multi-Agent Coordination

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| Sub-agent 共享主 agent workspace | HIGH | MEDIUM | 檔案衝突、狀態污染 |
| 缺乏 agent 間通訊標準 | MEDIUM | MEDIUM | 整合複雜度高 |
| Session 並發上限（maxConcurrent: 4） | LOW | MEDIUM | 資源競爭 |

---

## 3. Performance Bottlenecks — 性能瓶頸

### 3.1 Memory Retrieval

| Bottleneck | Impact | Current Mitigation |
|------------|--------|-------------------|
| Embedding 生成（Ollama localhost） | 200-1000ms | 本地部署，無 fallback |
| 向量搜尋（LanceDB） | 100-500ms | candidatePoolSize: 12 |
| BM25 全文搜尋 | 50-200ms | 索引優化 |
| 記憶體膨脹 | 隨時間惡化 | 無自動清理 |

**Ollama 單點故障**：

Embedding 依賴 `http://localhost:11434/v1`。如果 Ollama 崩潰：
- autoCapture、autoRecall 全部失效
- 沒有 fallback（降級到雲端或跳過）
- 沒有健康檢查或自動重啟

### 3.2 Context Management

| Bottleneck | Impact | Current Mitigation |
|------------|--------|-------------------|
| 872K context tokens | 高記憶體佔用 | Context pruning |
| 4 個並發 session | 線性增長 | maxConcurrent 限制 |
| Compaction 開銷 | 暫時性延遲 | Safeguard mode |

---

## 4. Industry Gap Analysis — 業界差距

### 4.1 vs. Enterprise Security Best Practices

| Practice | Industry Standard | OpenClaw Status | Gap |
|----------|------------------|-----------------|-----|
| Secret Management | Vault / Keychain / HSM | Plaintext config | **CRITICAL** |
| Zero Trust | mTLS + RBAC + Audit | Token auth only | **CRITICAL** |
| Data Encryption at Rest | AES-256 | None | **HIGH** |
| Observability | OpenTelemetry | File-based logs | **MEDIUM** |
| Incident Response | Auto-rollback | Manual restart | **MEDIUM** |

### 4.2 vs. AI Agent Frameworks

| Feature | Industry Trend | OpenClaw | Gap |
|---------|---------------|----------|-----|
| Agent isolation | Sandbox / Container | Shared workspace | **落后** |
| Memory provenance | Source tracking | None | **落后** |
| Tool permission scoping | Fine-grained RBAC | Binary approve/deny | **落后** |
| Human-in-the-loop | Configurable gates | ACP approve-all | **可改進** |
| Plugin ecosystem | Growing | 60+ skills | **領先** |
| Multi-channel | Fragmented | Unified整合 | **領先** |
| Personalization depth | Shallow | Deep (memory + personality) | **領先** |

---

## 5. Risk Matrix Summary

| # | Risk | Severity | Likelihood | Priority |
|---|------|----------|------------|----------|
| 1 | API key 明文存儲 | CRITICAL | LOW | P0 |
| 2 | Gateway token 單點信任 | HIGH | MEDIUM | P0 |
| 3 | Memory autoCapture 幻覺固化 | CRITICAL | MEDIUM | P0 |
| 4 | Telegram groupPolicy 開放 | MEDIUM | HIGH | P1 |
| 5 | Context pruning 資料遺失 | HIGH | MEDIUM | P1 |
| 6 | Sub-agent workspace 隔離不足 | HIGH | MEDIUM | P1 |
| 7 | Compaction flush 競態 | MEDIUM | MEDIUM | P1 |
| 8 | Embedding 服務單點故障 | MEDIUM | MEDIUM | P2 |
| 9 | 記憶體膨脹無自動清理 | MEDIUM | HIGH | P2 |
| 10 | Heartbeat 間隔過長 | MEDIUM | HIGH | P2 |

---

## 6. Recommendations

### P0 — 立即行動

1. **API Key 外部化**：整合 OS keychain 或環境變數，config 不再明文存儲
2. **Gateway Token 輪換**：支援多 token、過期時間、快速撤銷
3. **Memory 來源標記**：每條記憶標註來源（用戶/bot/外部）+ 用戶確認機制

### P1 — 下一迭代

4. **Telegram 群組白名單**：groupPolicy 改為需明確授權
5. **Context 保護標記**：允許標記關鍵上下文為不可裁剪
6. **Sub-agent workspace 隔離**：使用獨立 worktree 或沙盒
7. **Compaction flush 確認**：確保 flush 完成後才執行 compaction

### P2 — 季度規劃

8. **Embedding fallback**：Ollama 不可用時降級到雲端或跳過
9. **記憶體自動清理**：基於時間和使用頻率的淘汰機制
10. **Heartbeat 間隔縮短**：5 分鐘或可配置

### P3 — 年度願景

11. **Zero Trust 架構**：mTLS + RBAC + 完整審計日誌
12. **記憶體加密**：LanceDB 靜態加密
13. **Multi-model fallback**：自動 failover

---

## 7. Conclusion

OpenClaw 在個人化深度和多 channel 整合方面領先業界，但安全模型存在結構性弱點。**最大的風險不是技術缺陷，而是「便利性與安全性的取捨」**——autoCapture 帶來便利但也帶來記憶污染風險，plaintext config 簡化部署但也暴露 credentials。

> **Bottom line**: Excellent personal AI platform. Needs credential hardening and memory provenance before any multi-user or production deployment.

---

_Report generated by Risk Reviewer — poc-v3-phase1 team_
_Output: reports/V1-risk-v3.md_
