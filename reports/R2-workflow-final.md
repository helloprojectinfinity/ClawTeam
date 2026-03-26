# R2: OpenClaw 架構設計分析 — 工作流程與協作視角（最終版）

> **Researcher**: researcher-workflow
> **Team**: poc-v3-phase1
> **Date**: 2026-03-26
> **Core Question**: OpenClaw 嘅架構設計、多 Agent 協調機制、記憶系統（memory-lancedb-pro）

---

## Executive Summary

OpenClaw 係一個 **Gateway-centric 嘅 AI 助理平台**，核心設計理念係「一個長壽 daemon 管理所有通訊表面，每個 Agent 係一個完全隔離嘅大腦」。

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

---

## 2. Session 生命週期管理

### 2.1 Session Key 格式

| 類型 | Key 格式 | 範例 |
|------|---------|------|
| **Direct（main scope）** | `agent:<agentId>:<mainKey>` | `agent:main:main` |
| **Direct（per-peer）** | `agent:<agentId>:direct:<peerId>` | `agent:main:direct:telegram:123` |
| **Direct（per-channel-peer）** | `agent:<agentId>:<channel>:direct:<peerId>` | `agent:main:telegram:direct:123` |
| **Group** | `agent:<agentId>:<channel>:group:<id>` | `agent:main:whatsapp:group:120363@g.us` |
| **Cron（isolated）** | `cron:<jobId>` | `cron:daily-backup` |
| **Cron（persistent）** | `session:<customId>` | `session:morning-briefing` |

### 2.2 Daily Reset（每日重設）

**預設行為：** 每日 4:00 AM（Gateway host 本地時間）

```
Session last update < 今日 4:00 AM
    ↓
Session 被視為 stale
    ↓
下一條訊息觸發新 session ID
```

**配置方式：**
```json5
{
  session: {
    reset: {
      mode: "daily",     // 預設
      atHour: 4,         // 預設 4 AM
    }
  }
}
```

### 2.3 Idle Reset（閒置重設）

**可選行為：** 當 session 閒置超過指定分鐘數時重設

```json5
{
  session: {
    reset: {
      mode: "daily",
      atHour: 4,
      idleMinutes: 120,  // 閒置 2 小時觸發重設
    }
  }
}
```

**規則：** Daily reset 同 idle reset，**邊個先到期就用邊個**。

### 2.4 Per-Type 同 Per-Channel Override

```json5
{
  session: {
    resetByType: {
      direct: { mode: "idle", idleMinutes: 240 },  // DM 閒置 4 小時
      group: { mode: "idle", idleMinutes: 120 },    // 群組閒置 2 小時
      thread: { mode: "daily", atHour: 4 },         // Thread 每日重設
    },
    resetByChannel: {
      discord: { mode: "idle", idleMinutes: 10080 }, // Discord 一週
    }
  }
}
```

### 2.5 Manual Reset

用戶可以透過 `/new` 或 `/reset` 指令手動觸發 session 重設。

### 2.6 Session Maintenance（維護）

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `mode` | `warn` | `warn`（只報告）或 `enforce`（執行清理） |
| `pruneAfter` | `30d` | 超過此時間嘅 stale entries 會被清理 |
| `maxEntries` | `500` | 最大 session 數量 |
| `rotateBytes` | `10mb` | sessions.json 超過此大小時旋轉 |
| `maxDiskBytes` | unset | 硬性磁碟上限 |
| `highWaterBytes` | 80% of max | 高水位線 |

---

## 3. Context 組裝流程

### 3.1 Context Engine Lifecycle

每次 model run，Context Engine 參與四個階段：

```
1. Ingest（攝取）
   新訊息加入 session
   → Engine 可以儲存或索引訊息

2. Assemble（組裝）
   每次 model run 前
   → Engine 返回符合 token budget 嘅訊息集合
   → 可選：systemPromptAddition（注入動態 recall 指導）

3. Compact（壓縮）
   context window 滿咗，或用戶執行 /compact
   → Engine 摘要舊歷史，釋放空間

4. After Turn（回合後）
   run 完成後
   → Engine 可以持久化狀態、觸發背景 compaction、更新索引
```

### 3.2 Context 組裝內容

```
System Prompt
    + Relevant Memory（memory_search 結果）
    + Session History（compacted if needed）
    + Tool Definitions
    + Current Message
    ↓
Final Context → LLM
```

### 3.3 Compaction 機制

**Auto-Compaction（自動壓縮）：**
```
Session token 接近 context window 上限
    ↓
Memory Flush（提醒 agent 寫入長期記憶）
    ↓
Compaction（摘要舊訊息，保留最近對話）
    ↓
摘要存入 session JSONL 歷史
```

**Manual Compaction（手動壓縮）：**
用戶發送 `/compact` 或 `/compact <instructions>` 觸發。

**Compaction 配置：**
```json5
{
  agents: {
    defaults: {
      compaction: {
        model: "openrouter/anthropic/claude-sonnet-4-6",  // 可選：用唔同模型做摘要
        identifierPolicy: "strict",  // 保留 opaque identifiers
      }
    }
  }
}
```

### 3.4 ownsCompaction 模式

| 模式 | 行為 | 適用場景 |
|------|------|---------|
| `ownsCompaction: true` | Engine 完全控制 compaction | 自定義 compaction 算法（DAG summaries、vector retrieval） |
| `ownsCompaction: false` | Engine 可以 delegate 畀 runtime | 使用 OpenClaw 內建 compaction |

---

## 4. 多 Agent 協調機制

### 4.1 Binding System（路由系統）

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

### 4.2 同 ClawTeam 嘅根本分別

| 維度 | OpenClaw | ClawTeam |
|------|----------|---------|
| **Agent 關係** | 路由隔離（唔同人用唔同 agent） | 協作編排（agent 之間合作） |
| **通訊方式** | Channel → Gateway → Agent | Agent → Inbox File → Agent |
| **任務分配** | 冇原生支援 | Task + blocked_by + auto-unlock |
| **狀態管理** | Session JSONL | Task JSON + Inbox JSON |
| **執行模式** | 內建 agent loop | 外部 CLI process（tmux） |

---

## 5. 記憶系統（memory-lancedb-pro）

### 5.1 兩層記憶架構

```
Layer 1: Markdown Files（人類可讀，source of truth）
  ├── MEMORY.md              ← 長期記憶（curated）
  └── memory/YYYY-MM-DD.md   ← 每日日誌（append-only）

Layer 2: Vector Database（機器可搜索）
  ├── LanceDB Table          ← 向量索引（ANN，cosine distance）
  ├── BM25 FTS Index         ← 全文搜索
  └── Hybrid Fusion          ← Vector + BM25 融合
```

### 5.2 Hybrid Retrieval（混合檢索）

```
Query → embedQuery() ─┐
                       ├─→ RRF Fusion → Rerank → Lifecycle Decay → Length Norm → Filter → MMR
Query → BM25 FTS ─────┘
```

### 5.3 Smart Extraction（智能提取，v1.1.0）

**6 類記憶類別：** profile、preferences、entities、events、cases、patterns

**L0/L1/L2 分層儲存：**
- **L0**：一句話索引
- **L1**：結構化摘要
- **L2**：完整敘述

### 5.4 Memory Lifecycle（記憶生命週期，v1.1.0）

**Weibull Decay Engine：** composite_score = recency + frequency + intrinsic_value

**三層晉降級：** Peripheral ⟷ Working ⟷ Core

---

## 6. 自動化系統

### 6.1 Heartbeat（心跳）

**核心機制：** 定期喺 main session 執行 agent turn，令 model 可以主動發現需要關注嘅事。

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `every` | `30m` | 心跳間隔（`0m` 禁用） |
| `target` | `none` | 訊息投遞目標（`none`/`last`/channel id） |
| `lightContext` | `false` | 只注入 HEARTBEAT.md（減少 token） |
| `isolatedSession` | `false` | 每次心跳用 fresh session（冇對話歷史） |
| `activeHours` | unset | 限制心跳只喺指定時段執行 |

**Response Contract：**
- 如果冇嘢需要關注 → 回覆 `HEARTBEAT_OK`
- 如果有 alert → 回覆 alert 內容（唔包含 HEARTBEAT_OK）

**HEARTBEAT.md：** 可選嘅 checklist 檔案，每次心跳會讀入 context。

**成本優化：**
- `isolatedSession: true`：token 從 ~100K 降至 ~2-5K
- `lightContext: true`：只注入 HEARTBEAT.md
- 用 cheaper model 做心跳

### 6.2 Cron Jobs（定時任務）

| 維度 | Heartbeat | Cron |
|------|-----------|------|
| **觸發** | 固定間隔（default 30m） | cron 表達式 |
| **執行環境** | Main session（完整 context） | Isolated session（冇 context） |
| **用途** | 主動發現需要關注嘅事 | 定時執行特定任務 |
| **適合** | 監控、狀態檢查 | 定時報告、備份 |
| **Session** | `agent:<id>:<mainKey>` | `cron:<jobId>`（isolated）或 `session:<id>`（persistent） |

**Cron Session 特性：**
- Isolated cron jobs 每次執行都 mint fresh sessionId（冇 idle reuse）
- Persistent cron jobs 用固定 session key，保持上下文連續性

---

## 7. 來源可信度評估

| 來源 | 類型 | 可信度 | 備註 |
|------|------|--------|------|
| OpenClaw docs/concepts/session.md | 官方文檔 | **High** | Session 生命週期權威來源 |
| OpenClaw docs/concepts/context-engine.md | 官方文檔 | **High** | Context 組裝同 compaction 機制 |
| OpenClaw docs/gateway/heartbeat.md | 官方文檔 | **High** | Heartbeat 配置同行為 |
| OpenClaw docs/concepts/memory.md | 官方文檔 | **High** | 記憶系統設計 |
| memory-lancedb-pro README.md | Plugin 文檔 | **High** | 插件功能同架構 |

---

## 8. 研究限制

1. **冇深入測試 memory-lancedb-pro 嘅實際性能**：報告基於文檔同 source code，冇跑 benchmark
2. **冇分析 multi-agent 嘅實際協作場景**：OpenClaw 嘅多 Agent 主要係隔離唔係協作
3. **Context Engine plugin 生態未成熟**：目前主要用 legacy engine
4. **Cron job 實際執行穩定性未驗證**：基於文檔分析，冇長期觀察

---

## 9. 關鍵發現摘要

| # | 發現 | 證據來源 |
|---|------|---------|
| 1 | Gateway 係唯一入口，管理所有 channel | architecture.md |
| 2 | Agent 係 fully scoped brain（workspace + sessions + auth） | multi-agent.md |
| 3 | 多 Agent 係路由隔離唔係協作編排 | multi-agent.md binding rules |
| 4 | Session 預設每日 4AM 重設，可配置 idle reset | session.md |
| 5 | Daily reset 同 idle reset，邊個先到期就用邊個 | session.md |
| 6 | Context engine 有 4 個 lifecycle hooks：ingest/assemble/compact/afterTurn | context-engine.md |
| 7 | Compaction 摘要存入 session JSONL 歷史 | compaction.md |
| 8 | Heartbeat 預設 30m，可用 isolatedSession 省 95% token | heartbeat.md |
| 9 | Cron jobs 用 isolated session，每次 fresh sessionId | session.md |
| 10 | memory-lancedb-pro 提供 hybrid retrieval + smart extraction + Weibull decay | README.md |

---

_Report saved: 2026-03-26 by researcher-workflow (poc-v3-phase1)_
