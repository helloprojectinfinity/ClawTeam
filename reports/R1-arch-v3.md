# R1: OpenClaw 架構深度分析（v3）

> Researcher: researcher-arch
> Team: poc-v3-phase1
> Focus: OpenClaw 整體架構、多 Agent 協調機制、memory-lancedb-pro、ClawTeam 整合點
> Date: 2026-03-26

---

## 核心問題

OpenClaw 作為一個 AI Agent Gateway，佢嘅架構設計點樣支持多 Agent 協調？記憶系統（memory-lancedb-pro）點樣實現長期記憶？同 ClawTeam 嘅整合點喺邊度？

---

## 1. OpenClaw 整體架構

### 1.1 Gateway 模型

OpenClaw 嘅核心係一個 **長壽 Gateway Daemon**，管理所有通訊表面：

```
┌─────────────────────────────────────────────────────┐
│                 Gateway Daemon                       │
│                                                      │
│  Channel Plugins（WhatsApp/Telegram/Discord/Signal） │
│         ↓                                            │
│  Binding Router（消息 → Agent 路由）                  │
│         ↓                                            │
│  Agent Loop（Session → Context → Model → Tools）     │
│         ↓                                            │
│  Plugin Registry（Memory/Provider/Channel）          │
│                                                      │
│  WebSocket API (:18789)                              │
│  ├── Clients（macOS/CLI/WebChat）                    │
│  └── Nodes（iOS/Android/Headless）                   │
└─────────────────────────────────────────────────────┘
```

**關鍵設計：**
- 一個 Gateway per host，係唯一嘅 source of truth
- 所有 session 狀態喺 Gateway 管理（`~/.openclaw/agents/<agentId>/sessions/`）
- Channel plugins 註冊到 central registry，唔 hardcode 到 core

### 1.2 Session 模型

Session 係 OpenClaw 嘅核心抽象：

```
Session Key 結構：
  Direct (main):     agent:<agentId>:<mainKey>
  Direct (per-peer): agent:<agentId>:direct:<peerId>
  Group:             agent:<agentId>:<channel>:group:<id>
  Cron (isolated):   cron:<jobId>
  Sub-agent:         agent:<agentId>:subagent:<label>
```

**Session 生命週期：**
- Daily Reset（預設 4:00 AM）
- Idle Reset（可選 sliding window）
- 兩者取先到期者 → 觸發新 session
- Compaction：context window 接近上限時自動壓縮

### 1.3 Agent 模型

一個 Agent 係完全隔離嘅大腦：

```
Agent = Workspace（AGENTS.md/SOUL.md/USER.md/skills/）
      + State Directory（auth profiles/model registry）
      + Session Store（chat history/routing state）
      + 獨立 credentials（唔自動共享）
```

**證據：** `~/.openclaw/agents/` 目錄結構，每個 agent 有獨立嘅 `agent/` 和 `sessions/` 子目錄。

---

## 2. 多 Agent 協調機制

### 2.1 Binding 路由系統

Binding 決定邊個 message 去邊個 agent，**most-specific wins**：

```
優先級（由高到低）：
1. peer match（精確 DM/group/channel ID）
2. parentPeer match（thread 繼承）
3. guildId + roles（Discord 角色路由）
4. guildId / teamId
5. accountId match
6. channel-level match
7. fallback to default agent
```

**證據：** 多 Agent Routing 文檔明確定義咗 8 層路由優先級，同埋 AND semantics（多個 match field 必須同時滿足）。

### 2.2 Sub-agent 系統

```python
# 兩種 runtime：
sessions_spawn(runtime="subagent")  # OpenClaw 內部子代理
sessions_spawn(runtime="acp")       # ACP 協議（Codex/Claude Code 等外部 agent）
```

**Sub-agent 特點：**
- 繼承父 session 嘅 workspace directory
- 支持 streaming 回傳（`streamTo: "parent"`）
- one-shot（`mode="run"`）同 persistent（`mode="session"`）模式
- Thread-bound 模式支持 Discord thread 場景

### 2.3 Agent-to-Agent 通訊

```python
# 跨 agent 通訊
sessions_send(sessionKey="agent:other-agent:main", message="...")
sessions_list(kinds=["subagent"])  # 列出所有子代理
```

---

## 3. 記憶系統：memory-lancedb-pro

### 3.1 架構總覽

```
┌─────────────────────────────────────────────┐
│         memory-lancedb-pro Plugin            │
├─────────────────────────────────────────────┤
│  Smart Extractor（LLM-based 自動提取）       │
│  Noise Filter（噪音過濾）                    │
├─────────────────────────────────────────────┤
│  Hybrid Retriever                           │
│  ├── Vector Search（語義相似度）              │
│  ├── BM25 Search（關鍵詞匹配）               │
│  └── Cross-encoder Rerank（重排序）          │
├─────────────────────────────────────────────┤
│  Decay Engine（時間衰減）                    │
│  Tier Manager（核心/工作/邊緣分層）           │
├─────────────────────────────────────────────┤
│  Scope Manager（多範圍隔離）                 │
├─────────────────────────────────────────────┤
│  LanceDB Storage（向量數據庫 + 元數據）       │
└─────────────────────────────────────────────┘
```

### 3.2 Hybrid Retrieval

**證據：** `openclaw.plugin.json` 定義咗 retrieval 配置：
- `mode`: "hybrid" | "vector"
- `vectorWeight` / `bm25Weight`: 混合權重
- `rerank`: "cross-encoder" | "lightweight" | "none"
- `candidatePoolSize`: 候選池大小
- `recencyHalfLifeDays`: 時間衰減半衰期

### 3.3 Decay Engine + Tier Manager

**記憶分層：**
- **Core Memory**：高訪問頻率 + 高重要度，較慢衰減（`betaCore`）
- **Working Memory**：中等訪問頻率，中等衰減（`betaWorking`）
- **Peripheral Memory**：低訪問頻率 + 較舊，較快衰減（`betaPeripheral`）

**證據：** `openclaw.plugin.json` 定義咗 `decay` 同 `tier` 配置區塊，包含 `coreAccessThreshold`、`coreCompositeThreshold`、`peripheralAgeDays` 等參數。

### 3.4 Smart Extraction

自動從對話中提取持久記憶：
1. 監控對話 messages（`extractMinMessages` 閾值）
2. 用 LLM 分析對話內容
3. 提取事實、偏好、決定
4. 過濾噪音（NoisePrototypeBank）
5. 存入 LanceDB（含 smart metadata）

### 3.5 Session Reflection

```python
sessionStrategy = "memoryReflection"   # Plugin 自己做 reflection
sessionStrategy = "systemSessionMemory" # 用 OpenClaw 內建 session memory
sessionStrategy = "none"                # 唔做 session summary（預設）
```

---

## 4. 與 ClawTeam 嘅整合點

### 4.1 架構對比

| 特性 | OpenClaw | ClawTeam |
|------|----------|----------|
| Agent 啟動 | `sessions_spawn`（in-process） | `tmux new-window`（獨立進程） |
| 通訊 | Session-based（共享狀態） | File-based inbox（JSON files） |
| 隔離級別 | Workspace + Session + Auth | Git worktree + Inbox directory |
| 協調者 | Gateway（自動路由） | Leader Agent（手動協調） |
| Task 管理 | 冇內建 task 系統 | 完整 DAG task 系統 |

### 4.2 整合模式

```
ClawTeam Leader Agent
    ↓ clawteam spawn
OpenClaw Agent（作為 CLI 被 spawn）
    ├── --local（本地模式）
    ├── --session-id agent-name
    └── --message "task prompt"
    ↓
OpenClaw Agent Loop
    ├── 自己嘅 Session
    ├── 自己嘅 Workspace
    └── 透過 clawteam CLI 跟 Leader 通訊
```

**證據：** `adapters.py` 明確處理 OpenClaw 命令：
```python
def is_openclaw_command(command):
    return command_basename(command) == "openclaw"
```

### 4.3 互補關係

- **OpenClaw 提供**：Agent 運行時、Session 管理、記憶系統、Channel 整合
- **ClawTeam 提供**：多 Agent 協調、Task 依賴管理、Git worktree 隔離、Inbox 通訊

兩者結合：ClawTeam 負責「點樣組織團隊」，OpenClaw 負責「每個 Agent 點樣思考同執行」。

---

## 來源可信度

| 來源 | 可信度 | 理由 |
|------|--------|------|
| OpenClaw 官方文檔 | ★★★★★ | 直接來自項目文檔，最權威 |
| memory-lancedb-pro 源碼 | ★★★★★ | 直接讀取 plugin 代碼同配置 |
| ClawTeam 源碼 | ★★★★★ | 直接讀取框架代碼 |

## 研究限制

1. **冇深入測試**：只分析咗架構設計，冇實際 benchmark 性能
2. **版本差異**：分析基於當前安裝版本，可能同最新版有差異
3. **整合點分析有限**：OpenClaw 同 ClawTeam 嘅實際整合測試未做

---

_Sources: OpenClaw docs (architecture.md, multi-agent.md, memory.md, session.md, plugin architecture), memory-lancedb-pro source code, ClawTeam source code_
