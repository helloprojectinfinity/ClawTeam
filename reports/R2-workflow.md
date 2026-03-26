# R2: OpenClaw 架構設計分析 — 工作流程與協作視角

> **Researcher**: researcher-workflow
> **Team**: poc-v2-phase1
> **Date**: 2026-03-26
> **Focus**: OpenClaw 架構、多 Agent 協調機制、記憶系統（memory-lancedb-pro）

---

## Executive Summary

OpenClaw 係一個 **個人 AI 助理平台**，核心設計理念係「一個 Gateway daemon 管理所有通訊表面，每個 Agent 係一個完全隔離嘅大腦」。同 ClawTeam（多 CLI 編排層）唔同，OpenClaw 係一個 **自成一體嘅 agent runtime**，內建記憶、session、cron、heartbeat 等完整生命週期管理。

**三大核心發現：**

1. **架構分層清晰**：Gateway → Agent → Session → Context Engine，每層有明確嘅責任邊界
2. **多 Agent 係「路由隔離」而非「協作編排」**：OpenClaw 嘅多 Agent 係為咗隔唔同人/場景嘅通訊，唔係為咗 agent 之間協作
3. **記憶系統係最成熟嘅組件**：memory-lancedb-pro 提供 hybrid retrieval、smart extraction、memory lifecycle 等 production-grade 功能

---

## 1. OpenClaw 架構設計

### 1.1 整體架構

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

### 1.2 核心組件

| 組件 | 職責 | 資料位置 |
|------|------|---------|
| **Gateway** | 管理所有 channel 連接、路由訊息、維護 WS API | daemon process |
| **Agent** | 一個「大腦」：workspace + sessions + auth | `~/.openclaw/agents/<agentId>/` |
| **Session** | 對話歷史 + routing state | `~/.openclaw/agents/<agentId>/sessions/` |
| **Memory** | 長期記憶（Markdown + Vector DB） | workspace `MEMORY.md` + `memory/*.md` |
| **Context Engine** | 組裝 model context、管理 compaction | plugin slot |
| **Heartbeat** | 定期 agent turn，主動發現需要關注嘅事 | config `agents.defaults.heartbeat` |
| **Cron** | 定時任務排程 | `~/.openclaw/cron/` |

### 1.3 同 ClawTeam 嘅架構對比

| 維度 | OpenClaw | ClawTeam |
|------|---------|---------|
| **定位** | 個人 AI 助理平台 | 多 CLI 編排層 |
| **Agent 關係** | 路由隔離（唔同人用唔同 agent） | 協作編排（agent 之間合作） |
| **通訊** | Channel → Gateway → Agent | Agent → Inbox → Agent |
| **狀態** | Session JSONL + Memory MD | Task JSON + Inbox JSON |
| **執行** | 內建 agent loop | 外部 CLI process（tmux） |
| **記憶** | 內建（Markdown + Vector DB） | 依賴各 CLI 自身 |

---

## 2. 多 Agent 協調機制

### 2.1 路由模型（Binding System）

OpenClaw 嘅多 Agent 係 **路由隔離**，唔係 **協作編排**。每個 Agent 係一個完全隔離嘅大腦，有自己嘅 workspace、sessions、auth profiles。

**路由規則（deterministic，most-specific wins）：**

1. `peer` match（exact DM/group/channel id）
2. `parentPeer` match（thread inheritance）
3. `guildId + roles`（Discord role routing）
4. `guildId`（Discord）
5. `teamId`（Slack）
6. `accountId` match for a channel
7. channel-level match
8. fallback to default agent

**典型場景：**

| 場景 | 配置 |
|------|------|
| 一個 WhatsApp 號碼，多個人用 | `dmScope: "per-channel-peer"` 隔離 DM |
| 多個 WhatsApp 號碼 | 每個 `accountId` 綁定唔同 agent |
| WhatsApp 日常 + Telegram 深度工作 | channel-level binding |
| 家庭群組專用 agent | peer-level binding |

### 2.2 Agent 間通訊（Agent-to-Agent）

OpenClaw 支援 agent 之間嘅 direct messaging，但 **預設關閉**：

```json5
{
  tools: {
    agentToAgent: {
      enabled: false,  // 預設關閉
      allow: ["home", "work"],  // 白名單
    },
  },
}
```

**同 ClawTeam 嘅分別：**
- OpenClaw：agent 之間直接 send message（透過 Gateway 內部路由）
- ClawTeam：agent 之間透過 inbox file（檔案系統）

### 2.3 Sub-agent 系統

OpenClaw 有兩種 sub-agent 機制：

| 機制 | Runtime | 用途 |
|------|---------|------|
| **Subagent** | OpenClaw 內建 | 輕量級任務委派，共享 workspace |
| **ACP** | 外部 CLI（Codex/Claude Code/OpenCode） | 獨立 coding agent，有自己的 sandbox |

**同 ClawTeam 嘅分別：**
- OpenClaw subagent：喺 OpenClaw 內部執行，有完整嘅 session + memory context
- ClawTeam agent：喺 tmux 中執行，完全獨立嘅 process，冇 OpenClaw context

---

## 3. 記憶系統（Memory-lancedb-pro）

### 3.1 記憶架構

OpenClaw 嘅記憶系統分為兩層：

```
Layer 1: Markdown Files（人類可讀）
  ├── MEMORY.md          ← 長期記憶（curated）
  └── memory/YYYY-MM-DD.md ← 每日日誌（append-only）

Layer 2: Vector Database（機器可搜索）
  ├── LanceDB table      ← 向量索引
  ├── BM25 index         ← 全文搜索
  └── Hybrid fusion      ← Vector + BM25 融合
```

### 3.2 memory-lancedb-pro 核心功能

| 功能 | 描述 | 對比 built-in memory-lancedb |
|------|------|------------------------------|
| **Hybrid Retrieval** | Vector + BM25 full-text search | built-in 只有 vector |
| **Cross-encoder Rerank** | 多 provider reranking | ❌ |
| **Smart Extraction** | LLM 自動提取 6 類記憶 | ❌ |
| **Weibull Decay** | 記憶衰減 + 3-tier promotion | ❌ |
| **Multi-Scope Isolation** | per-agent/per-user/per-project 隔離 | ❌ |
| **Noise Filtering** | 過濾無意義記憶 | ❌ |
| **Adaptive Retrieval** | 根據查詢類型調整策略 | ❌ |
| **Session Memory** | session 級別嘅記憶 | ❌ |
| **Management CLI** | 備份、遷移、匯入匯出 | ❌ |

### 3.3 Smart Extraction（智能提取）

memory-lancedb-pro v1.1.0 引入 LLM-powered 記憶自動提取，分為 6 個類別：

| 類別 | 描述 | 範例 |
|------|------|------|
| **Preference** | 用戶偏好 | 「我鍾意用繁體中文」 |
| **Fact** | 事實性知識 | 「天保係 Green Tomato 嘅員工」 |
| **Decision** | 決策記錄 | 「決定用 ClawTeam 做多 agent 編排」 |
| **Entity** | 實體資訊 | 「OpenClaw 係一個 AI 助理平台」 |
| **Reflection** | 反思總結 | 「呢個方案嘅優點係...」 |
| **Other** | 其他 | 不屬於以上類別嘅記憶 |

**運作流程：**
```
用戶對話
    ↓
LLM 分析對話內容
    ↓
提取記憶 + 分類 + 評分重要性
    ↓
寫入 MEMORY.md + 向量資料庫
```

### 3.4 Memory Lifecycle（記憶生命週期）

```
新記憶寫入
    ↓
Tier 1: Hot Memory（活躍記憶）
    ↓ （Weibull decay）
Tier 2: Warm Memory（溫記憶）
    ↓ （進一步衰減）
Tier 3: Cold Memory（冷記憶）
    ↓ （自動清理或歸檔）
Archive / Delete
```

**Weibull Decay：** 重要嘅記憶 decay 得慢，唔重要嘅 decay 得快。呢個模型比簡單嘅 TTL 更符合人類記憶嘅自然衰減模式。

### 3.5 同其他記憶系統嘅對比

| 系統 | 核心思路 | 優勢 | 限制 |
|------|---------|------|------|
| **OpenClaw memory-lancedb-pro** | Markdown + Vector DB + Smart Extraction | 人類可讀、hybrid search、lifecycle | 綁定 OpenClaw |
| **Mem0** | 結構化記憶 + 圖譜關聯 | 66.9% 準確率、0.20s 延遲 | 需要額外 infra |
| **MemGPT** | 操作系統模式、虛擬化 context | 無限 context window 錯覺 | 複雜度高 |
| **AWS AgentCore** | 高壓縮率記憶系統 | 89-95% 壓縮率 | AWS 綁定 |
| **ClawTeam** | 依賴各 CLI 自身記憶 | 簡單 | 冇統一記憶層 |

---

## 4. 自動化系統

### 4.1 Heartbeat vs Cron

| 維度 | Heartbeat | Cron |
|------|-----------|------|
| **觸發** | 固定間隔（default 30m） | cron 表達式 |
| **執行環境** | Main session（有完整 context） | Isolated session（冇 context） |
| **用途** | 主動發現需要關注嘅事 | 定時執行特定任務 |
| **適合** | 監控、狀態檢查 | 定時報告、備份 |

### 4.2 Context Engine

OpenClaw 嘅 context engine 係一個 **pluggable** 架構：

- **Built-in legacy engine**：預設嘅 context 組裝邏輯
- **Plugin engine**：第三方可以替換 context 組裝策略

**Context 組裝流程：**
```
System Prompt
    + Relevant Memory（memory_search 結果）
    + Session History（compacted if needed）
    + Tool Definitions
    + Current Message
    ↓
Final Context → LLM
```

### 4.3 Session Compaction

當 session token 接近 context window 上限時：
1. 觸發 **memory flush**（提醒 agent 寫入長期記憶）
2. 執行 **compaction**（摘要舊訊息，保留最近嘅對話）

---

## 5. 工作流效率分析

### 5.1 優勢

1. **單一 daemon 管理一切**：唔需要管理多個 process
2. **記憶系統成熟**：memory-lancedb-pro 係 production-grade
3. **路由靈活**：binding system 支援各種複雜場景
4. **Plugin 架構**：可擴展（memory、context engine、channels）
5. **完整生命週期**：heartbeat + cron + session compaction

### 5.2 限制

1. **多 Agent 係隔離唔係協作**：冇原生嘅 agent 間任務分配機制
2. **冇 task 依賴管理**：唔似 ClawTeam 有 blocked_by 自動解鎖
3. **冇 template 系統**：唔可以一條命令啟動一個 agent 團隊
4. **Agent 門檻高**：每個 agent 需要獨立嘅 workspace + auth + sessions
5. **冇可觀測性 board**：唔似 ClawTeam 有 kanban board 同 Gource visualization

### 5.3 同 ClawTeam 嘅互補性

| 功能 | OpenClaw | ClawTeam | 互補方案 |
|------|----------|----------|---------|
| Agent runtime | ✅ 內建 | ❌ 依賴外部 CLI | OpenClaw 做 runtime |
| 多 Agent 協作 | ⚠️ 只有路由 | ✅ 完整編排 | ClawTeam 做編排 |
| 記憶系統 | ✅ 成熟 | ❌ 依賴各 CLI | OpenClaw 做記憶 |
| Task 管理 | ❌ 冇 | ✅ 完整 | ClawTeam 做 task |
| 可觀測性 | ⚠️ 有限 | ✅ board + gource | ClawTeam 做可觀測 |

---

## 6. 建議

### 6.1 OpenClaw 層面

1. **引入 task 系統**：參考 ClawTeam 嘅 blocked_by 機制
2. **Agent 間協作協議**：唔淨係路由隔離，仲要支援任務分配
3. **Template 系統**：一條命令啟動預設嘅 agent 配置
4. **可觀測性增強**：board、metrics、distributed tracing

### 6.2 整合層面

1. **OpenClaw 做 runtime + 記憶**：agent 喺 OpenClaw 內執行，用 memory-lancedb-pro 做記憶
2. **ClawTeam 做編排 + task**：用 ClawTeam 嘅 template + task + inbox 做多 agent 協調
3. **橋接機制**：ClawTeam agent 透過 MCP 或 A2A 同 OpenClaw agent 通訊

---

## Sources

| 來源 | 類型 | 核心內容 |
|------|------|---------|
| OpenClaw docs/concepts/architecture.md | 官方文檔 | Gateway 架構、WS API |
| OpenClaw docs/concepts/multi-agent.md | 官方文檔 | 多 Agent 路由、binding 系統 |
| OpenClaw docs/concepts/memory.md | 官方文檔 | 記憶系統、Markdown + Vector DB |
| OpenClaw docs/concepts/session.md | 官方文檔 | Session 管理、compaction |
| OpenClaw docs/concepts/delegate-architecture.md | 官方文檔 | Delegate 模型、capability tiers |
| OpenClaw docs/concepts/context-engine.md | 官方文檔 | Context 組裝、pluggable engine |
| memory-lancedb-pro/README.md | Plugin 文檔 | Hybrid retrieval、smart extraction |
| memory-lancedb-pro/src/ | Source code | 實現細節 |

---

_Report saved: 2026-03-26 by researcher-workflow (poc-v2-phase1)_
