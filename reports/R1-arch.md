# R1: OpenClaw 架構設計深度分析

> Researcher: researcher-arch
> Focus: OpenClaw 技術架構、多 Agent 協調機制、記憶系統（memory-lancedb-pro）
> Date: 2026-03-26

---

## 1. 整體架構總覽

OpenClaw 係一個 **AI Agent Gateway**，核心設計理念係「一個長壽 Gateway 進程管理所有通訊表面」。佢唔係一個簡單嘅 chatbot wrapper，而係一個完整嘅 agent 運行時平台。

### 1.1 核心架構

```
┌─────────────────────────────────────────────────────────────┐
│                    Gateway Daemon (長壽進程)                  │
│  ┌──────────┬──────────┬──────────┬──────────┬───────────┐  │
│  │ WhatsApp │ Telegram │ Discord  │  Signal  │  Slack    │  │
│  │ (Baileys)│(grammY)  │          │          │           │  │
│  └────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬──────┘  │
│       │          │          │          │          │           │
│  ┌────▼──────────▼──────────▼──────────▼──────────▼──────┐  │
│  │              Channel Plugin Registry                   │  │
│  └───────────────────────┬───────────────────────────────┘  │
│                          │                                   │
│  ┌───────────────────────▼───────────────────────────────┐  │
│  │              Agent Loop Engine                         │  │
│  │  Session Mgmt → Context Assembly → Model Inference     │  │
│  │  → Tool Execution → Streaming Replies → Persistence    │  │
│  └───────────────────────┬───────────────────────────────┘  │
│                          │                                   │
│  ┌──────────┬────────────┼────────────┬──────────────────┐  │
│  │ Memory   │  Plugins   │  Cron      │  Sub-agents      │  │
│  │ System   │  Registry  │  Scheduler │  (sessions_spawn) │  │
│  └──────────┴────────────┴────────────┴──────────────────┘  │
│                                                              │
│  WebSocket API (127.0.0.1:18789)                             │
│  ├── Clients (macOS app, CLI, WebChat)                       │
│  └── Nodes (macOS, iOS, Android, headless)                   │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 關鍵設計原則

| 原則 | 實現 |
|------|------|
| **單一 Gateway** | 一個進程管理所有 channel 連接、session、tool 執行 |
| **Session 為核心** | 每個對話係一個 session，有獨立嘅 context window 同 transcript |
| **Plugin 架構** | 所有外部整合（provider、channel、memory）都係 plugin |
| **Workspace 隔離** | 每個 agent 有獨立嘅 workspace（AGENTS.md、SOUL.md、skills） |
| **Tool Policy** | 細粒度嘅工具權限控制（allow/deny/elevated） |

---

## 2. 多 Agent 協調機制

### 2.1 Agent 定義

OpenClaw 中嘅「一個 Agent」係一個完全隔離嘅大腦：

```
Agent = Workspace（文件、AGENTS.md/SOUL.md/USER.md）
      + State Directory（auth profiles、model registry）
      + Session Store（聊天歷史 + 路由狀態）
      + 獨立嘅 credentials（唔自動共享）
```

### 2.2 路由機制

**Binding 系統**決定邊個 message 去邊個 agent：

```
路由優先級（most-specific wins）：
1. peer match（精確 DM/group/channel ID）
2. parentPeer match（thread 繼承）
3. guildId + roles（Discord 角色路由）
4. guildId（Discord）
5. teamId（Slack）
6. accountId match
7. channel-level match
8. fallback to default agent
```

**設計亮點：** 同一個 Gateway 可以運行多個完全隔離嘅 agent，每個 agent 有自己嘅：
- Workspace（唔同嘅 SOUL.md = 唔同嘅人格）
- Model（一個用 Sonnet，一個用 Opus）
- Sandbox（一個冇沙盒，一個嚴格沙盒）
- Tool Policy（一個全權限，一個只讀）

### 2.3 Sub-agent 系統

OpenClaw 透過 `sessions_spawn` 實現子代理：

```python
# 兩種 runtime：
sessions_spawn(runtime="subagent")  # OpenClaw 內部子代理
sessions_spawn(runtime="acp")       # ACP 協議（Codex、Claude Code 等外部 agent）
```

**子代理特點：**
- 繼承父 session 嘅 workspace directory
- 可以 streaming 回傳結果（`streamTo: "parent"`）
- 支持 one-shot（`mode="run"`）同 persistent（`mode="session"`）模式
- Thread-bound 模式支持 Discord thread 等場景

### 2.4 與 ClawTeam 嘅對比

| 特性 | OpenClaw | ClawTeam |
|------|----------|----------|
| Agent 啟動 | `sessions_spawn`（in-process） | `tmux new-window`（獨立進程） |
| 通訊 | Session-based（共享狀態） | File-based inbox（JSON files） |
| 隔離級別 | Workspace + Session + Auth | Git worktree + Inbox directory |
| 協調者 | Gateway（自動路由） | Leader Agent（手動協調） |
| Task 管理 | 冇內建 task 系統 | 完整嘅 DAG task 系統 |

---

## 3. Session 管理系統

### 3.1 Session Key 結構

```
Direct (main):    agent:<agentId>:<mainKey>
Direct (per-peer): agent:<agentId>:direct:<peerId>
Group:            agent:<agentId>:<channel>:group:<id>
Channel:          agent:<agentId>:<channel>:channel:<id>
Cron (isolated):  cron:<jobId>
Cron (persistent): session:<custom-id>
Sub-agent:        agent:<agentId>:subagent:<label>
```

### 3.2 Session 生命週期

```
Daily Reset（預設 4:00 AM）
    +
Idle Reset（可選，sliding window）
    ↓
兩者取先到期者 → 觸發新 session
```

### 3.3 Context 組裝流程

```
1. System Prompt（base + skills + bootstrap + per-run overrides）
2. Session History（JSONL transcript）
3. Memory Files（MEMORY.md + memory/YYYY-MM-DD.md）
4. Tool Results
5. Compaction（當 context window 接近上限時自動壓縮）
```

### 3.4 Compaction 機制

當 session 接近 auto-compaction 閾值時：
1. 觸發 **silent memory flush**（提醒 model 寫入持久記憶）
2. 壓縮舊嘅 context（summarize older messages）
3. 釋放 token 空間

---

## 4. 記憶系統：memory-lancedb-pro

### 4.1 架構總覽

memory-lancedb-pro 係一個 **LanceDB-backed 增強記憶插件**，提供：

```
┌─────────────────────────────────────────────┐
│           memory-lancedb-pro Plugin          │
├─────────────────────────────────────────────┤
│  Smart Extractor    │  Noise Filter         │
│  (LLM-based 提取)   │  (過濾噪音記憶)       │
├─────────────────────────────────────────────┤
│  Hybrid Retriever                          │
│  ├── Vector Search（語義相似度）             │
│  ├── BM25 Search（關鍵詞匹配）               │
│  └── Cross-encoder Rerank（重排序）          │
├─────────────────────────────────────────────┤
│  Decay Engine      │  Tier Manager          │
│  (時間衰減)         │  (核心/工作/邊緣分層)   │
├─────────────────────────────────────────────┤
│  Scope Manager                             │
│  (多範圍隔離：user/agent/session/project)    │
├─────────────────────────────────────────────┤
│  LanceDB Storage                           │
│  (向量數據庫 + 元數據)                       │
└─────────────────────────────────────────────┘
```

### 4.2 核心組件

#### Embedding 層
- 支持 **OpenAI-compatible** 嘅 embedding provider
- 支持多 API key round-robin rotation
- 自動 chunking（長文檔自動分段）
- 可配置 dimensions、task query/passage

#### 混合檢索（Hybrid Retrieval）
```python
# 檢索流程
Query
    ├── Vector Search → 候選集 A（語義相似）
    ├── BM25 Search  → 候選集 B（關鍵詞匹配）
    └── Merge + Weighted Score
            ↓
    Cross-encoder Rerank（可選）
            ↓
    Final Results
```

**權重配置：**
- `vectorWeight`：向量搜索權重
- `bm25Weight`：BM25 搜索權重
- `minScore`：最低分數閾值
- `candidatePoolSize`：候選池大小

#### 時間衰減（Decay Engine）
```python
# 記憶重要度 = f(時間, 訪問頻率, 內在重要度)
score = base_score * decay_factor

# 衰減因子
decay_factor = exp(-λ * age_days)

# 可配置參數：
- recencyHalfLifeDays：最近性半衰期
- frequencyWeight：訪問頻率權重
- intrinsicWeight：內在重要度權重
- importanceModulation：重要度調節
```

#### 分層管理（Tier Manager）
```
Core Memory（核心記憶）
    → 高訪問頻率 + 高重要度
    → 較慢衰減（betaCore）

Working Memory（工作記憶）
    → 中等訪問頻率
    → 中等衰減（betaWorking）

Peripheral Memory（邊緣記憶）
    → 低訪問頻率 + 較舊
    → 較快衰減（betaPeripheral）
```

#### 多範圍隔離（Scope Manager）
```python
scopes = {
    "default": "通用記憶",
    "user": "用戶專屬記憶",
    "agent": "Agent 專屬記憶",
    "session": "Session 專屬記憶",
    "project": "項目專屬記憶"
}
```

### 4.3 Smart Extraction

**自動從對話中提取持久記憶：**
1. 監控對話 messages（`extractMinMessages` 閾值）
2. 用 LLM 分析對話內容
3. 提取事實、偏好、決定等
4. 過濾噪音（NoisePrototypeBank）
5. 存入 LanceDB（含 smart metadata）

### 4.4 Session Reflection

```python
# 兩種 session 策略
sessionStrategy = "memoryReflection"  # Plugin 自己做 reflection
sessionStrategy = "systemSessionMemory"  # 用 OpenClaw 內建嘅 session memory
sessionStrategy = "none"  # 唔做 session summary（預設）
```

**Memory Reflection 流程：**
1. Session 結束時觸發
2. LLM 分析 session 內容
3. 生成 reflection slices
4. 存入 LanceDB（含 derived metadata）
5. 可選注入到下一個 session（inheritance + derived）

### 4.5 與 OpenClaw 預設 Memory 嘅對比

| 特性 | OpenClaw Default | memory-lancedb-pro |
|------|------------------|-------------------|
| 存儲 | Markdown files | LanceDB（向量數據庫） |
| 檢索 | file read + vector index | Hybrid（vector + BM25 + rerank） |
| 範圍 | workspace-level | multi-scope（user/agent/session/project） |
| 衰減 | 無（手動清理） | 自動時間衰減 + 分層管理 |
| 提取 | 手動（model 自己決定） | Smart Extraction（LLM-based 自動提取） |
| 噪音過濾 | 無 | NoisePrototypeBank |
| Session Reflection | 內建 compaction | Plugin-level reflection |

---

## 5. Plugin 系統

### 5.1 四層架構

```
Layer 1: Manifest + Discovery
    → 從配置路徑、workspace、global extension root 發現 plugin

Layer 2: Enablement + Validation
    → 決定 plugin 係 enabled/disabled/blocked/selected

Layer 3: Runtime Loading
    → Native plugin 透過 jiti in-process 加載
    → Compatible bundle 作為 metadata/content pack

Layer 4: Surface Consumption
    → Registry 暴露 tools、channels、providers、hooks、routes
```

### 5.2 Capability Model

| Capability | Registration Method | Example |
|------------|---------------------|---------|
| Text Inference | `api.registerProvider(...)` | openai, anthropic |
| Speech | `api.registerSpeechProvider(...)` | elevenlabs, microsoft |
| Media Understanding | `api.registerMediaUnderstandingProvider(...)` | openai, google |
| Image Generation | `api.registerImageGenerationProvider(...)` | openai, google |
| Web Search | `api.registerWebSearchProvider(...)` | google |
| Channel | `api.registerChannel(...)` | msteams, matrix |
| Memory | `plugins.slots.memory` | memory-lancedb-pro |
| Context Engine | `api.registerContextEngine(...)` | 自定義 context 管線 |

### 5.3 Plugin 執行模型

**Native OpenClaw plugin 跟 Gateway 同一個 process。** 唔係 sandboxed。

Implications：
- 可以 register tools、network handlers、hooks、services
- Bug 可以 crash Gateway
- 惡意 plugin = 任意代碼執行

---

## 6. Agent Loop

### 6.1 執行流程

```
Message In
    ↓
Gateway RPC: agent
    ↓
1. Validate params + resolve session
2. Persist session metadata
3. Return { runId, acceptedAt } immediately
    ↓
AgentCommand (async):
    ├── Resolve model + thinking/verbose defaults
    ├── Load skills snapshot
    ├── Call runEmbeddedPiAgent
    │   ├── Serialize runs（per-session + global queue）
    │   ├── Resolve model + auth profile
    │   ├── Build pi session
    │   ├── Subscribe to pi events
    │   ├── Stream assistant/tool deltas
    │   └── Enforce timeout → abort if exceeded
    └── Emit lifecycle end/error
    ↓
Stream to Client:
    ├── tool events → stream: "tool"
    ├── assistant deltas → stream: "assistant"
    └── lifecycle events → stream: "lifecycle"
```

### 6.2 並發控制

- **Per-session serialization**：同一個 session 嘅 runs 係 sequential
- **Global lane**（可選）：全局序列化
- **Queue modes**：collect/steer/followup（控制 message 點排隊）

---

## 7. 設計模式總結

### 7.1 OpenClaw 嘅核心設計哲學

1. **Gateway-centric**：所有嘢都經過 Gateway，佢係唯一嘅 source of truth
2. **Session-first**：Session 係核心抽象，唔係 message
3. **Plugin-extensible**：所有外部整合都係 plugin，core 只定義 capability contract
4. **Workspace-as-brain**：AGENTS.md/SOUL.md 就係 agent 嘅人格同規則
5. **Markdown memory**：記憶係 plain Markdown，model 只記住寫落 disk 嘅嘢

### 7.2 與其他系統嘅架構對比

| 系統 | 核心抽象 | Agent 協調 | 記憶 | 部署 |
|------|----------|-----------|------|------|
| **OpenClaw** | Session + Workspace | Multi-agent routing | Markdown + Plugin | Single Gateway |
| **ClawTeam** | Task + Inbox | Leader orchestration | File-based | tmux processes |
| **AutoGen** | Conversation | Chat-driven | Stateless | Python runtime |
| **CrewAI** | Role + Task | Crew orchestration | RAG | Python runtime |
| **LangGraph** | State Machine | Graph-based | Checkpoint | Python runtime |

### 7.3 OpenClaw 嘅獨特優勢

1. **多 Channel 統一管理**：一個 Gateway 管 WhatsApp、Telegram、Discord 等
2. **Session 持久化**：自動 compaction、daily reset、idle reset
3. **Plugin 生態**：memory-lancedb-pro 就係一個好例子
4. **Node 系統**：手機/平板都可以作為 agent node
5. **Canvas**：agent 可以生成 HTML/CSS/JS UI

### 7.4 已知限制

1. **冇內建嘅 multi-agent task 系統**：OpenClaw 冇 DAG task 依賴管理（呢個係 ClawTeam 補充嘅部分）
2. **冇自動 handoff**：Sub-agent 完成後唔會自動觸發下一個（需要外部 cron/heartbeat）
3. **Plugin 唔 sandboxed**：native plugin 有同 core 一樣嘅信任邊界
4. **Single Gateway per host**：冇 built-in 嘅分布式支持

---

## Sources

- OpenClaw 官方文檔：`/docs/concepts/architecture.md`
- OpenClaw 官方文檔：`/docs/concepts/multi-agent.md`
- OpenClaw 官方文檔：`/docs/concepts/memory.md`
- OpenClaw 官方文檔：`/docs/concepts/session.md`
- OpenClaw 官方文檔：`/docs/concepts/delegate-architecture.md`
- OpenClaw 官方文檔：`/docs/plugins/architecture.md`
- OpenClaw 官方文檔：`/docs/concepts/agent-loop.md`
- memory-lancedb-pro source code：`~/.openclaw/extensions/memory-lancedb-pro/`
