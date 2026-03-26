# R2: OpenClaw 架構設計分析 — 工作流程與協作視角

> **Researcher**: researcher-workflow
> **Team**: poc-v2-phase1
> **Date**: 2026-03-26
> **Core Question**: OpenClaw 嘅架構設計、多 Agent 協調機制、記憶系統（memory-lancedb-pro）

---

## Executive Summary

OpenClaw 係一個 **Gateway-centric 嘅 AI 助理平台**，核心設計理念係「一個長壽 daemon 管理所有通訊表面，每個 Agent 係一個完全隔離嘅大腦」。佢嘅架構同 ClawTeam（多 CLI 編排層）有根本性分別：OpenClaw 係一個自成一體嘅 agent runtime，而唔係一個協調層。

**三大核心發現：**

1. **Gateway 係唯一嘅入口**：所有通訊表面（WhatsApp、Telegram、Discord 等）都經 Gateway 統一管理
2. **多 Agent 係「路由隔離」唔係「協作編排」**：binding system 靈活但冇原生嘅 agent 間任務分配
3. **memory-lancedb-pro 係 production-grade 記憶系統**：hybrid retrieval + smart extraction + Weibull decay lifecycle

---

## 1. 架構設計分析

### 1.1 整體架構（5 層）

```
┌─────────────────────────────────────────────────────────────┐
│                    Gateway Daemon                            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Channel Layer                                       │   │
│  │  WhatsApp │ Telegram │ Discord │ Signal │ iMessage  │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │  Routing Layer (bindings)                             │   │
│  │  peer → agentId │ guild → agentId │ channel → agentId│   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │  Agent Layer                                          │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐              │   │
│  │  │ Agent A │  │ Agent B │  │ Agent C │              │   │
│  │  │workspace│  │workspace│  │workspace│              │   │
│  │  │sessions │  │sessions │  │sessions │              │   │
│  │  │memory   │  │memory   │  │memory   │              │   │
│  │  └─────────┘  └─────────┘  └─────────┘              │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Plugin Layer                                         │   │
│  │  memory │ contextEngine │ channels │ providers       │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  WebSocket API ←── Clients (macOS/CLI/WebChat/Nodes)        │
└──────────────────────────────────────────────────────────────┘
```

**證據**：`docs/concepts/architecture.md` 明確定義 Gateway 係唯一管理所有 messaging surfaces 嘅 daemon。

### 1.2 Agent 定義

一個 OpenClaw Agent 係一個 **fully scoped brain**，擁有：

| 組件 | 路徑 | 職責 |
|------|------|------|
| **Workspace** | `~/.openclaw/workspace` 或 `~/.openclaw/workspace-<agentId>` | AGENTS.md、SOUL.md、USER.md、記憶檔案 |
| **Agent Dir** | `~/.openclaw/agents/<agentId>/agent` | auth profiles、model registry、per-agent config |
| **Session Store** | `~/.openclaw/agents/<agentId>/sessions` | 對話歷史、routing state |

**關鍵設計：** 每個 Agent 嘅 auth profiles 係 **per-agent**，唔會自動共享。呢個係安全隔離嘅基礎。

### 1.3 Agent Loop（代理迴圈）

OpenClaw 嘅 agent loop 係一個 **serialized run per session**：

```
Message 進入
    ↓
Session 解析（sessionKey）
    ↓
Queue 排隊（per-session lane + optional global lane）
    ↓
Context Assembly（context engine）
    ├── System Prompt
    ├── Relevant Memory（memory_search）
    ├── Session History（compacted if needed）
    └── Tool Definitions
    ↓
Model Inference
    ↓
Tool Execution（如有 tool call）
    ↓
Streaming Reply
    ↓
Persistence（session JSONL + memory write）
```

**證據**：`docs/concepts/agent-loop.md` 定義咗完整嘅 lifecycle：intake → context assembly → model inference → tool execution → streaming replies → persistence。

---

## 2. 多 Agent 協調機制

### 2.1 Binding System（路由系統）

OpenClaw 嘅多 Agent 係 **路由隔離**，唔係 **協作編排**。

**路由規則（deterministic，most-specific wins）：**

| 優先級 | Match 類型 | 範例 |
|--------|-----------|------|
| 1 | `peer` | 特定 DM 或 group ID |
| 2 | `parentPeer` | Thread 繼承 |
| 3 | `guildId + roles` | Discord 角色路由 |
| 4 | `guildId` | Discord guild |
| 5 | `teamId` | Slack team |
| 6 | `accountId` | Channel account |
| 7 | channel-level | 整個 channel |
| 8 | fallback | default agent |

**證據**：`docs/concepts/multi-agent.md` 明確定義「Bindings are deterministic and most-specific wins」。

### 2.2 DM Scope（直接訊息範圍控制）

| Scope | 行為 | 適用場景 |
|-------|------|---------|
| `main`（default） | 所有 DM 共享一個 session | 單用戶 |
| `per-peer` | 按 sender ID 隔離 | 多用戶 |
| `per-channel-peer` | 按 channel + sender 隔離 | 推薦用於多用戶 inbox |
| `per-account-channel-peer` | 按 account + channel + sender 隔離 | 多 account 場景 |

**安全警告：** 如果 agent 可以接收多個人嘅 DM，預設嘅 `main` scope 會導致 Alice 嘅私人資訊洩漏俾 Bob。

### 2.3 Agent 間通訊

OpenClaw 支援 agent 之間嘅 direct messaging，但 **預設關閉**：

```json5
{
  tools: {
    agentToAgent: {
      enabled: false,       // 預設關閉
      allow: ["home", "work"]  // 白名單
    }
  }
}
```

### 2.4 同 ClawTeam 嘅根本分別

| 維度 | OpenClaw | ClawTeam |
|------|----------|---------|
| **Agent 關係** | 路由隔離（唔同人用唔同 agent） | 協作編排（agent 之間合作） |
| **通訊方式** | Channel → Gateway → Agent | Agent → Inbox File → Agent |
| **任務分配** | 冇原生支援 | Task + blocked_by + auto-unlock |
| **狀態管理** | Session JSONL | Task JSON + Inbox JSON |
| **執行模式** | 內建 agent loop | 外部 CLI process（tmux） |

---

## 3. 記憶系統（memory-lancedb-pro）

### 3.1 兩層記憶架構

```
Layer 1: Markdown Files（人類可讀，source of truth）
  ├── MEMORY.md              ← 長期記憶（curated）
  └── memory/YYYY-MM-DD.md   ← 每日日誌（append-only）

Layer 2: Vector Database（機器可搜索）
  ├── LanceDB Table          ← 向量索引（ANN，cosine distance）
  ├── BM25 FTS Index         ← 全文搜索
  └── Hybrid Fusion          ← Vector + BM25 融合
```

**證據**：`docs/concepts/memory.md` 明確定義「OpenClaw memory is plain Markdown in the agent workspace. The files are the source of truth」。

### 3.2 memory-lancedb-pro 核心功能

#### Hybrid Retrieval（混合檢索）

```
Query → embedQuery() ─┐
                       ├─→ RRF Fusion → Rerank → Lifecycle Decay → Length Norm → Filter → MMR
Query → BM25 FTS ─────┘
```

**多階段評分管線：**

| 階段 | 效果 |
|------|------|
| **RRF Fusion** | 結合語意同精確匹配嘅 recall |
| **Cross-Encoder Rerank** | 提升語意精確度（60% cross-encoder + 40% original） |
| **Lifecycle Decay Boost** | Weibull freshness + access frequency + importance × confidence |
| **Length Normalization** | 防止長條目主導（anchor: 500 chars） |
| **Hard Min Score** | 移除不相關結果（default: 0.35） |
| **MMR Diversity** | cosine similarity > 0.85 → demoted |

#### Smart Extraction（智能提取，v1.1.0）

**6 類記憶類別：**

| 類別 | 描述 | 合併策略 |
|------|------|---------|
| **profile** | 用戶檔案 | always merge |
| **preferences** | 偏好設定 | merge |
| **entities** | 實體資訊 | merge |
| **events** | 事件記錄 | append-only |
| **cases** | 案例經驗 | append-only |
| **patterns** | 行為模式 | merge |

**L0/L1/L2 分層儲存：**
- **L0**：一句話索引（one-sentence index）
- **L1**：結構化摘要（structured summary）
- **L2**：完整敘述（full narrative）

**兩階段去重：**
1. Vector similarity pre-filter（≥0.7）
2. LLM semantic decision（CREATE/MERGE/SKIP）

#### Memory Lifecycle（記憶生命週期，v1.1.0）

**Weibull Decay Engine：**
```
composite_score = recency + frequency + intrinsic_value
```

**三層晉降級：**
```
Peripheral ⟷ Working ⟷ Core
```

- 重要嘅記憶 decay 得慢（importance-modulated half-life）
- 常用嘅記憶會晉升到 Core tier
- 唔常用嘅記憶會降級到 Peripheral tier

### 3.3 Auto-Capture 同 Auto-Recall

| Hook | 觸發點 | 功能 |
|------|--------|------|
| **Auto-Capture** | `agent_end` | 從對話提取 preference/fact/decision/entity，deduplicate，每 turn 最多 3 條 |
| **Auto-Recall** | `before_agent_start` | 注入 `<relevant-memories>` context（最多 3 條） |

### 3.4 Noise Filtering（噪音過濾）

**過濾內容：** agent refusals、meta-questions、greetings、low-quality content

**Adaptive Retrieval（自適應檢索）：**
- 跳過檢索：greetings、slash commands、simple confirmations、emoji
- 強制檢索：memory keywords（"remember"、"previously"、"last time"）
- CJK-aware：中文 6 字 vs 英文 15 字 threshold

---

## 4. Context Engine（上下文引擎）

### 4.1 Pluggable Architecture

OpenClaw 嘅 context engine 係 **pluggable** 架構：

| Engine | 類型 | 特色 |
|--------|------|------|
| **legacy** | Built-in | 預設嘅 context 組裝邏輯 |
| **Plugin engine** | Third-party | 可替換 context 組裝策略 |

### 4.2 Lifecycle Hooks

| Hook | 觸發點 | 功能 |
|------|--------|------|
| **Ingest** | 新訊息加入 session | 儲存或索引訊息 |
| **Assemble** | 每次 model run 前 | 組裝 context（決定包含邊啲訊息） |
| **Compact** | context window 滿咗 | 摘要舊歷史，釋放空間 |
| **After turn** | run 完成後 | 持久化狀態、觸發背景 compaction |

---

## 5. 自動化系統

### 5.1 Heartbeat vs Cron

| 維度 | Heartbeat | Cron |
|------|-----------|------|
| **觸發** | 固定間隔（default 30m） | cron 表達式 |
| **執行環境** | Main session（完整 context） | Isolated session（冇 context） |
| **用途** | 主動發現需要關注嘅事 | 定時執行特定任務 |
| **適合** | 監控、狀態檢查 | 定時報告、備份 |

### 5.2 Session Compaction

```
Session token 接近 context window 上限
    ↓
Memory Flush（提醒 agent 寫入長期記憶）
    ↓
Compaction（摘要舊訊息，保留最近對話）
```

---

## 6. 來源可信度評估

| 來源 | 類型 | 可信度 | 備註 |
|------|------|--------|------|
| OpenClaw 官方文檔 | Primary source | **High** | 直接來自項目文檔 |
| memory-lancedb-pro README | Primary source | **High** | 插件作者嘅官方文檔 |
| memory-lancedb-pro source code | Primary source | **High** | 實際實現 |
| ClawTeam source code | Primary source | **High** | 用於對比分析 |

---

## 7. 研究限制

1. **冇深入測試 memory-lancedb-pro 嘅實際性能**：報告基於文檔同 source code，冇跑 benchmark
2. **冇分析 multi-agent 嘅實際協作場景**：OpenClaw 嘅多 Agent 主要係隔離唔係協作，實際協作案例有限
3. **冇比較其他記憶系統嘅實際效果**：Mem0、MemGPT 等系統嘅比較基於公開數據，冇喺同一環境下測試
4. **Context Engine plugin 生態未成熟**：目前主要用 legacy engine，第三方 plugin 資訊有限

---

## 8. 關鍵發現摘要

| # | 發現 | 證據來源 |
|---|------|---------|
| 1 | Gateway 係唯一入口，管理所有 channel | architecture.md |
| 2 | Agent 係 fully scoped brain（workspace + sessions + auth） | multi-agent.md |
| 3 | 多 Agent 係路由隔離唔係協作編排 | multi-agent.md binding rules |
| 4 | DM scope 預設 `main`，多用戶有安全風險 | session.md security warning |
| 5 | 記憶 source of truth 係 Markdown files | memory.md |
| 6 | memory-lancedb-pro 提供 hybrid retrieval（Vector + BM25） | README.md |
| 7 | Smart extraction 分 6 類，L0/L1/L2 分層儲存 | README.md v1.1.0 |
| 8 | Weibull decay + 三層晉降級管理記憶生命週期 | README.md v1.1.0 |
| 9 | Agent loop 係 serialized run per session | agent-loop.md |
| 10 | Context engine 係 pluggable architecture | context-engine.md |

---

_Report saved: 2026-03-26 by researcher-workflow (poc-v2-phase1)_
