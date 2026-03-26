# Security Review: AI Agent 未來發展趨勢

> **Reviewer**: Security Reviewer
> **Date**: 2026-03-26
> **Scope**: 記憶體投毒、級聯故障、權限提升、隱私合規
> **Base Reports**: R1-arch.md | R2-workflow.md | V1-risk.md | R2-workflow (Phase 3)

---

## Executive Summary

AI Agent 生態正面臨 **三個 CRITICAL 級別嘅安全威別嘅安全威脅**，佢哋嘅共同特徵係：**影響係持久且難以偵測嘅**。傳統嘅 perimeter security（防火牆、入侵偵測）無法應對呢啲新型威脅，因為攻擊面已經從「網絡邊界」擴展到「Agent 嘅認知過程」。

**Overall Security Rating: LOW**（技術防禦機制尚未成熟）

---

## 1. Critical Issue #1：記憶體投毒（Memory Poisoning）

### 1.1 威脅模型

```
攻擊者 → 惡意資料注入 Agent 可存取嘅資料源
    ↓
Agent 檢索並將惡意內容存入長期記憶
    ↓
六個月後，Agent 檢索到該記憶
    ↓
將惡意內容當作可信事實使用
    ↓
惡意影響持續存在，即使原始資料已被移除
```

### 1.2 攻擊向量

| 向量 | 描述 | 嚴重度 |
|------|------|--------|
| **直接注入** | 攻擊者直接向 Agent 嘅記憶系統寫入惡意內容 | CRITICAL |
| **間接注入** | 攻擊者污染 Agent 可存取嘅外部資料源（文件、API） | HIGH |
| **幻覺固化** | Agent 嘅幻覺被存入記憶並重複引用 | HIGH |
| **跨 Agent 傳播** | 一個被污染嘅 Agent 將惡意記憶共享俾其他 Agent | CRITICAL |

### 1.3 防禦方案

#### 方案 A：記憶審批工作流（Memory Approval Workflow）

```
新資訊進入 Agent
    ↓
可信度評分（Source Trust Score）
    ├─ High Trust（已驗證來源）→ 自動寫入
    ├─ Medium Trust（部分驗證）→ 標記寫入 + 定期審計
    └─ Low Trust（未驗證來源）→ 進入審批隊列
                                      ↓
                              Guardian Agent 審查
                                      ├─ Approved → 寫入 + 附加審計標籤
                                      └─ Rejected → 記錄拒絕原因
```

#### 方案 B：記憶體來源追溯（Memory Provenance Tracking）

每個記憶條目必須包含：
```json
{
  "content": "事實內容",
  "source": "資料來源 URL/文件",
  "source_trust_score": 0.85,
  "ingested_at": "2026-03-26T14:00:00Z",
  "ingested_by": "agent-name",
  "approved_by": "guardian-agent",
  "expires_at": "2026-09-26T14:00:00Z",
  "retraction_url": null
}
```

#### 方案 C：記憶體驗證（Memory Verification）

- **交叉驗證**：多個獨立來源驗證同一事實
- **時間衰減**：舊記憶嘅可信度隨時間降低
- **撤銷機制**：當原始來源被標記為不可信時，自動撤銷相關記憶

### 1.4 實施優先級：P0

---

## 2. Critical Issue #2：級聯故障（Cascading Failures）

### 2.1 威脅模型

```
共享 LLM（如 GPT-4、Claude）
    ↓
系統性偏差喺所有 Agent 中同時出現
    ↓
一個 Agent 嘅幻覺被其他 Agent 當作「事實」傳播
    ↓
錯誤放大循環
    ↓
整體任務失敗
```

### 2.2 風險數據

- **Deloitte 預測**：不良嘅 Agent 編排可能使市場價值縮減 15-30%
- **IBM 建議**：需要「沙盒環境壓力測試」同「回滾機制」
- **現狀**：大多數框架缺乏「錯誤隔離」（fault isolation）機制

### 2.3 防禦方案

#### 方案 A：斷路器模式（Circuit Breaker Pattern）

```
Agent A → Circuit Breaker → Agent B
              │
              ├─ CLOSED（正常）：訊息通過
              ├─ OPEN（故障）：訊息阻斷，通知 Supervisor
              └─ HALF-OPEN（恢復中）：允許少量測試訊息
```

**觸發條件：**
- Agent B 連續 3 次回傳錯誤
- Agent B 延遲超過閾值
- Agent B 嘅輸出同預期格式不符

#### 方案 B：錯誤隔離邊界（Fault Isolation Boundary）

- 每個 Agent 嘅錯誤唔可以直接傳播到其他 Agent
- 錯誤必須經過 Supervisor Agent 審查先可以傳遞
- Supervisor 可以決定：忽略、修正、或阻斷錯誤

#### 方案 C：多模型冗餘（Multi-Model Redundancy）

- 唔同嘅 Agent 使用唔同嘅 LLM（避免共享偏差）
- 關鍵決策由多個 Agent 獨立判斷，取共識
- 當一個模型出現系統性錯誤時，其他模型可以糾正

### 2.4 實施優先級：P0

---

## 3. Critical Issue #3：權限提升（Privilege Escalation）

### 3.1 威脅模型

```
Agent 獲得工具使用能力（檔案讀寫、API 呼叫、程式執行）
    ↓
缺乏適當嘅權限控制
    ↓
一個被 prompt injection 攻擊嘅 Agent 可能：
  - 刪除檔案
  - 發送郵件
  - 執行惡意程式
    ↓
多 Agent 系統中，一個被入侵嘅 Agent 可以：
  - 利用其他 Agent 嘅信任關係橫向移動
```

### 3.2 攻擊向量

| 向量 | 描述 | 嚴重度 |
|------|------|--------|
| **Prompt Injection** | 透過使用者輸入或外部資料注入惡意指令 | CRITICAL |
| **工具投毒** | 惡意工具回傳精心構造嘅輸入，操控 Agent 行為 | HIGH |
| **記憶體投毒** | 喺 Agent 嘅長期記憶中植入惡意指令（見 Issue #1） | CRITICAL |
| **橫向移動** | 一個被入侵嘅 Agent 利用其他 Agent 嘅信任關係 | HIGH |

### 3.3 防禦方案

#### 方案 A：信任梯度工作流（Trust Gradient Workflow）

```
Level 0: Full Human Control（完全人類控制）
  適用：首次執行、高風險操作、異常情況
  流程：Agent 提案 → 人類審批 → Agent 執行 → 人類確認

Level 1: Human-on-the-Loop（人類監控）
  適用：已驗證嘅常規操作
  流程：Agent 自動執行 → 即時通知人類 → 異常時人類介入

Level 2: Autonomous with Audit（自主執行 + 審計）
  適用：低風險、高頻率操作
  流程：Agent 自動執行 → 定期審計報告 → 問題時回滾

Level 3: Full Autonomous（完全自主）
  適用：已通過大量測試嘅穩定場景
  流程：Agent 完全自主 → 異常偵測 → 自動回滾
```

#### 方案 B：沙盒隔離（Sandbox Isolation）

- 所有 Agent 嘅工具調用必須喺沙盒環境執行
- 沙盒限制：檔案系統存取範圍、網絡連接、系統調用
- 每個 Agent 有獨立嘅沙盒實例

#### 方案 C：最小權限原則（Principle of Least Privilege）

- Agent 只獲得完成任務所需嘅最小權限
- 權限喺任務完成後自動撤銷
- 高風險操作需要額外嘅授權

### 3.4 實施優先級：P0

---

## 4. CRITICAL Issue #4：隱私合規（Privacy Compliance）

### 4.1 威脅模型

- PII（個人識別資訊）洩漏至記憶系統
- 跨 Agent 記憶共享導致資料外洩
- 記憶體無法真正「刪除」（被遺忘權問題）

### 4.2 合規挑戰

| 挑戰 | 描述 |
|------|------|
| **向量資料庫中嘅資料難以精確刪除** | 嵌入向量不可逆 |
| **多 Agent 共享記憶時，資料流向難以追蹤** | 合規審計困難 |
| **缺乏記憶體存取控制** | 哪個 Agent 可以讀寫哪些記憶？ |

### 4.3 防禦方案

#### 方案 A：PII 偵測與過濾

- 喺記憶寫入前自動偵測 PII
- PII 資料自動加密或匿名化
- 記錄所有 PII 存取日誌

#### 方案 B：記憶體存取控制（Memory Access Control）

```
Agent A 寫入記憶
    ↓
記憶分類標籤
    ├─ Private（僅 Agent A 可讀）→ 直接寫入
    ├─ Team（團隊內共享）→ 附加存取控制列表
    └─ Public（所有 Agent 可讀）→ 附加審計標籤

Agent B 讀取記憶
    ↓
存取控制檢查
    ├─ 有權限 → 返回記憶 + 使用記錄
    └─ 無權限 → 拒絕 + 通知 Guardian Agent
```

#### 方案 C：記憶體 TTL（Time-to-Live）

- 所有記憶條目必須有過期時間
- 過期記憶自動歸檔或刪除
- 用戶可以隨時請求刪除特定記憶

### 4.4 實施優先級：P1

---

## 5. Security Recommendations Summary

| 優先級 | 威脅 | 建議方案 | 實施方式 |
|--------|------|---------|---------|
| **P0** | 記憶體投毒 | 記憶審批工作流 + 來源追溯 | Guardian Agent 審查 |
| **P0** | 級聯故障 | 斷路器模式 + 錯誤隔離 | Circuit Breaker + Supervisor |
| **P0** | 權限提升 | 信任梯度 + 沙盒隔離 | 分層授權 + Sandbox |
| **P1** | 隱私合規 | PII 偵測 + 存取控制 | 自動偵測 + ACL |

---

## 6. Conclusion

AI Agent 嘅安全威脅已經從「傳統網絡安全」擴展到「認知安全」。記憶體投毒、級聯故障、權限提升係 2026 年最被低估嘅威脅——佢哋嘅影響係持久且難以偵測嘅。

**產業需要從「快速部署」轉向「安全部署」，喺追求自主性嘅同時，建立堅實嘅信任、安全同合規基礎。**

> **Bottom line**: The technology works. The governance doesn't — yet.

---

_Security Review Report — poc-phase3-write team_
