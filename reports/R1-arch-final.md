# R1: OpenClaw 架構深度分析（Final）

> Researcher: researcher-arch
> Team: poc-v3-phase1
> Focus: Gateway 架構、Binding 路由系統、memory-lancedb-pro（hybrid retrieval + decay engine）
> Date: 2026-03-26

---

## 核心問題

1. OpenClaw Gateway daemon 嘅詳細架構係點？
2. Binding 路由系統嘅優先級邏輯係點運作？
3. memory-lancedb-pro 嘅 hybrid retrieval 同 decay engine 點樣實現？

---

## 1. Gateway Daemon 詳細架構

### 1.1 進程模型

OpenClaw Gateway 係一個 **單一長壽進程**，喺 host 上管理所有 AI agent 活動：

```
Gateway Daemon (Node.js)
├── Channel Layer（通訊渠道管理）
│   ├── WhatsApp (Baileys)
│   ├── Telegram (grammY)
│   ├── Discord
│   ├── Signal
│   ├── Slack
│   └── 20+ 其他渠道...
│
├── Agent Loop Engine（Agent 執行引擎）
│   ├── Session Manager（Session 生命週期）
│   ├── Context Engine（上下文組裝）
│   ├── Model Router（模型選擇 + failover）
│   ├── Tool Executor（工具執行 + policy）
│   └── Streaming Pipeline（流式回傳）
│
├── Plugin Registry（插件註冊中心）
│   ├── Provider Plugins（模型提供商）
│   ├── Channel Plugins（通訊渠道）
│   ├── Memory Plugins（記憶系統）
│   └── Context Engine Plugins（上下文引擎）
│
├── Cron Scheduler（定時任務調度）
│
├── WebSocket Server (:18789)
│   ├── Clients（macOS app, CLI, WebChat）
│   └── Nodes（iOS, Android, Headless）
│
└── HTTP Server（Canvas, A2UI, Webhooks）
```

### 1.2 連接生命週期

```
Client → Gateway: req:connect（含 device identity + challenge signature）
Gateway → Client: res (ok) + snapshot（presence + health）
Gateway → Client: event:presence, event:tick（持續推送）
Client → Gateway: req:agent
Gateway → Client: res:agent {runId, status:"accepted"}
Gateway → Client: event:agent（streaming）
Gateway → Client: res:agent final {runId, status, summary}
```

### 1.3 配置結構

```json5
// ~/.openclaw/openclaw.json
{
  agents: {
    list: [
      { id: "main", workspace: "~/.openclaw/workspace" },
      { id: "coding", workspace: "~/.openclaw/workspace-coding" }
    ],
    defaults: {
      compaction: {
        reserveTokensFloor: 20000,
        memoryFlush: { enabled: true, softThresholdTokens: 4000 }
      }
    }
  },
  bindings: [...],
  channels: { whatsapp: {...}, telegram: {...} },
  plugins: { slots: { memory: "memory-lancedb-pro" } },
  session: { dmScope: "per-channel-peer", reset: { mode: "daily", atHour: 4 } }
}
```

**證據：** `~/.openclaw/openclaw.json` 同官方文檔 `/docs/concepts/architecture.md`。

---

## 2. Binding 路由系統優先級邏輯

### 2.1 路由規則

Binding 系統係 **deterministic + most-specific wins**：

```
優先級（由高到低）：

Tier 1: peer match
  → 精確匹配 DM sender ID 或 group ID
  → 例：{ channel: "whatsapp", peer: { kind: "direct", id: "+85212345678" } }

Tier 2: parentPeer match
  → Thread 繼承（Slack thread, Discord thread, Telegram topic）
  → 子訊息繼承父訊息嘅路由

Tier 3: guildId + roles（Discord）
  → 匹配 guild 同特定角色組合

Tier 4: guildId / teamId
  → 匹配整個 guild（Discord）或 team（Slack）

Tier 5: accountId match
  → 匹配特定 channel account
  → 例：whatsapp account "biz" → agent "work"

Tier 6: channel-level match
  → 匹配整個 channel（所有 accounts）
  → 例：{ channel: "telegram" }

Tier 7: fallback to default agent
  → agents.list[].default，或第一個 agent，或 "main"
```

### 2.2 AND Semantics

如果一個 binding 設置多個 match field，**所有 field 必須同時滿足**：

```json5
// 呢個 binding 要求 channel=whatsapp AND accountId=personal AND peer=group
{
  agentId: "work",
  match: {
    channel: "whatsapp",
    accountId: "personal",
    peer: { kind: "group", id: "1203630...@g.us" }
  }
}
```

### 2.3 Account Scope

- Omitting `accountId` → 只匹配 default account
- `accountId: "*"` → 匹配該 channel 嘅所有 accounts
- 同一個 binding 升級：如果後續加咗 explicit accountId，OpenClaw 會 upgrade 而唔係 duplicate

**證據：** 官方文檔 `/docs/concepts/multi-agent.md` 明確定義咗 8 層路由優先級同 AND semantics。

---

## 3. memory-lancedb-pro 詳細架構

### 3.1 Hybrid Retrieval

```
Query
  ↓
┌──────────────────────────────────────────┐
│ Step 1: Parallel Search                   │
│  ├── Vector Search → 候選集 A             │
│  │   （LanceDB cosine similarity）        │
│  │                                        │
│  └── BM25 Search → 候選集 B              │
│      （Apache Arrow full-text search）    │
├──────────────────────────────────────────┤
│ Step 2: Weighted Merge                    │
│  score = vectorWeight × vector_score      │
│        + bm25Weight × bm25_score          │
│        + recencyWeight × time_decay       │
├──────────────────────────────────────────┤
│ Step 3: Rerank（可選）                    │
│  ├── cross-encoder（Jina/SiliconFlow/    │
│  │   Voyage/Pinecone/Dashscope/TEI）     │
│  └── lightweight（本地 rerank）           │
├──────────────────────────────────────────┤
│ Step 4: Filter + Return                   │
│  ├── minScore 閾值過濾                   │
│  ├── hardMinScore 硬閾值                 │
│  └── Noise Filter（噪音原型匹配）         │
└──────────────────────────────────────────┘
```

**配置參數（`openclaw.plugin.json`）：**
- `mode`: "hybrid" | "vector"
- `vectorWeight`: 向量搜索權重
- `bm25Weight`: BM25 搜索權重
- `candidatePoolSize`: 候選池大小
- `rerank`: "cross-encoder" | "lightweight" | "none"
- `rerankProvider`: jina / siliconflow / voyage / pinecone / dashscope / tei
- `filterNoise`: 噪音過濾開關

### 3.2 Decay Engine

**記憶衰減模型：**

```
importance(t) = base_importance × decay_factor(t)

decay_factor(t) = exp(-λ × age_days)

其中：
  λ = ln(2) / half_life_days

可配置參數：
  - recencyHalfLifeDays：最近性半衰期
  - frequencyWeight：訪問頻率權重
  - intrinsicWeight：內在重要度權重
  - importanceModulation：重要度調節
  - reinforcementFactor：重複訪問增強因子
```

### 3.3 Tier Manager

**三層記憶分層：**

```
┌─────────────────────────────────────────┐
│ Core Memory（核心記憶）                  │
│  - 條件：access ≥ coreAccessThreshold   │
│          AND composite ≥ coreComposite  │
│          AND importance ≥ coreImportance │
│  - 衰減：betaCore（最慢）               │
│  - 下限：coreDecayFloor                 │
├─────────────────────────────────────────┤
│ Working Memory（工作記憶）               │
│  - 條件：access ≥ workingAccess         │
│          AND composite ≥ workingComp    │
│  - 衰減：betaWorking（中等）            │
│  - 下限：workingDecayFloor              │
├─────────────────────────────────────────┤
│ Peripheral Memory（邊緣記憶）            │
│  - 條件：composite ≥ peripheralComp     │
│          OR age ≥ peripheralAgeDays     │
│  - 衰減：betaPeripheral（最快）         │
│  - 下限：peripheralDecayFloor           │
└─────────────────────────────────────────┘
```

### 3.4 Smart Extraction + Session Reflection

**Smart Extraction 流程：**
1. 監控對話 messages（`extractMinMessages` 閾值）
2. LLM 分析內容 → 提取事實/偏好/決定
3. NoisePrototypeBank 過濾噪音
4. buildSmartMetadata → 存入 LanceDB

**Session Reflection：**
```python
sessionStrategy = "memoryReflection"   # Plugin reflection → LanceDB
sessionStrategy = "systemSessionMemory" # OpenClaw 內建 session memory
sessionStrategy = "none"                # 唔做 summary（預設）
```

---

## 4. 與 ClawTeam 嘅整合點

### 4.1 互補關係

| 層次 | OpenClaw 提供 | ClawTeam 提供 |
|------|--------------|--------------|
| Agent 運行時 | Session、Context、Model、Tools | tmux window、git worktree |
| 協調 | Binding 路由（自動） | Leader 手動分配 |
| 通訊 | Session-based（共享狀態） | File-based inbox（JSON） |
| Task | 冇內建系統 | DAG + blocked_by |
| 記憶 | memory-lancedb-pro | 共享文件系統 |

### 4.2 整合模式

```
clawteam spawn --command openclaw --task "..."
    ↓
OpenClaw Agent（tmux window 內）
    ├── --local --session-id agent-name
    ├── 自己嘅 Session + Workspace
    └── 透過 clawteam CLI 跟 Leader 通訊
        ├── clawteam inbox send
        ├── clawteam task update
        └── clawteam lifecycle idle
```

---

## 來源可信度

| 來源 | 可信度 | 理由 |
|------|--------|------|
| OpenClaw 官方文檔 | ★★★★★ | 項目官方文檔，最權威 |
| memory-lancedb-pro 源碼 | ★★★★★ | 直接讀取 plugin 代碼 |
| ClawTeam 源碼 | ★★★★★ | 直接讀取框架代碼 |
| 實際運行數據 | ★★★★☆ | 來自實際使用經驗 |

## 研究限制

1. **冇性能 benchmark**：只分析架構設計，冇實際性能測試
2. **版本差異**：分析基於當前安裝版本
3. **整合測試未做**：OpenClaw + ClawTeam 實際聯合測試未執行

---

_Sources: OpenClaw docs (architecture.md, multi-agent.md, memory.md, session.md, plugin architecture.md, agent-loop.md), memory-lancedb-pro source code (~/.openclaw/extensions/memory-lancedb-pro/), ClawTeam source code_
