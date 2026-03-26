# OpenClaw 架構設計綜合分析報告

> **團隊**: poc-v2-phase1 (Swarm-Thinking)
> **日期**: 2026-03-26
> **整合者**: integrator
> **來源報告**: R1-arch.md (架構分析) | R2-workflow.md (工作流程) | V1-risk.md (風險評估)

---

## Executive Summary

OpenClaw 係一個 **AI Agent Gateway**，核心設計理念係「一個長壽 Gateway 進程管理所有通訊表面」。佢唔係一個簡單嘅 chatbot wrapper，而係一個完整嘅 agent 運行時平台。

**三份報告嘅核心共識：**
1. **架構分層清晰**：Gateway → Agent → Session → Context Engine
2. **多 Agent 係「路由隔離」唔係「協作編排」**：冇原生嘅 agent 間任務分配
3. **memory-lancedb-pro 係最成熟嘅組件**：hybrid retrieval + smart extraction + Weibull decay lifecycle

**與 ClawTeam 嘅互補關係：**
- **OpenClaw** 做 runtime + 記憶
- **ClawTeam** 做編排 + task

**整體評級：MEDIUM 風險**（適合個人使用，企業部署需要加強）

---

## 1. 整體架構分析

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

### 2.2 路由機制（Binding 系統）

**路由優先級（most-specific wins）：**
1. peer match（精確 DM/group/channel ID）
2. parentPeer match（thread 繼承）
3. guildId + roles（Discord 角色路由）
4. guildId（Discord）
5. teamId（Slack）
6. accountId match
7. channel-level match
8. fallback to default agent

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

#### Hybrid Retrieval（混合檢索）
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

#### Decay Engine（時間衰減）
```python
# 記憶重要度 = f(時間, 訪問頻率, 內在重要度)
score = base_score * decay_factor

# 衰減因子
decay_factor = exp(-λ * age_days)
```

#### Tier Manager（分層管理）
```
Core Memory（核心記憶）→ 高訪問頻率 + 高重要度 → 較慢衰減
Working Memory（工作記憶）→ 中等訪問頻率 → 中等衰減
Peripheral Memory（邊緣記憶）→ 低訪問頻率 + 較舊 → 較快衰減
```

#### Scope Manager（多範圍隔離）
```python
scopes = {
    "default": "通用記憶",
    "user": "用戶專屬記憶",
    "agent": "Agent 專屬記憶",
    "session": "Session 專屬記憶",
    "project": "項目專屬記憶"
}
```

### 4.3 Smart Extraction（智能提取）

**自動從對話中提取持久記憶：**
1. 監控對話 messages（`extractMinMessages` 閾值）
2. 用 LLM 分析對話內容
3. 提取事實、偏好、決定等
4. 過濾噪音（NoisePrototypeBank）
5. 存入 LanceDB（含 smart metadata）

---

## 5. 風險評估摘要

### 5.1 Critical Issues

| Issue | 級別 | 描述 |
|-------|------|------|
| **Gateway Token 單點信任** | CRITICAL | Gateway token 係唯一嘅認證機制，洩漏 = 完全控制 |
| **API Key 明文存儲** | CRITICAL | Config 入面嘅 API key 係明文，冇加密 |
| **Memory Poisoning via autoCapture** | HIGH | Smart extraction 可能將錯誤/惡意內容存入記憶 |
| **Sub-agent Workspace 隔離不足** | HIGH | 子代理繼承父 workspace，可能存取敏感文件 |
| **Context Pruning 可能丟失關鍵上下文** | MEDIUM | Compaction 可能壓縮掉重要資訊 |

### 5.2 風險矩陣

| 類別 | 風險 | 嚴重度 | 優先級 |
|------|------|--------|--------|
| 安全 | Gateway Token 單點信任 | CRITICAL | P0 |
| 安全 | API Key 明文存儲 | CRITICAL | P0 |
| 可靠性 | Memory Poisoning | HIGH | P1 |
| 可靠性 | Sub-agent 隔離不足 | HIGH | P1 |
| 性能 | Context Pruning | MEDIUM | P2 |

---

## 6. 與 ClawTeam 嘅整合建議

### 6.1 互補關係

| 層次 | OpenClaw | ClawTeam |
|------|----------|----------|
| **Runtime** | ✅ Gateway + Session + Tool | ⚠️ 依賴外部 CLI |
| **記憶** | ✅ memory-lancedb-pro | ❌ 冇內建記憶 |
| **編排** | ⚠️ 基礎路由 | ✅ DAG Task + Template |
| **通訊** | ✅ Session-based | ✅ File-based inbox |
| **可視化** | ⚠️ WebSocket API | ✅ Tmux + Board |

### 6.2 整合方案

```
方案 A：OpenClaw 作為 ClawTeam 嘅 Runtime
  ClawTeam（編排層）
      ↓ spawn
  OpenClaw Agent（執行層）
      ↓ sessions_spawn
  Sub-agents（子代理）

方案 B：ClawTeam 作為 OpenClaw 嘅編排插件
  OpenClaw Gateway
      ↓ plugin
  ClawTeam Plugin（編排層）
      ↓ task create/inbox send
  OpenClaw Agents（執行層）
```

### 6.3 建議發展方向

| 優先級 | 建議 |
|--------|------|
| **P0** | Gateway Token 加密存儲 |
| **P0** | API Key 加密（至少用 OS keychain） |
| **P1** | Memory autoCapture 加審查機制 |
| **P1** | Sub-agent workspace 隔離加強 |
| **P2** | 整合 ClawTeam 編排能力 |
| **P2** | 跨 Agent 記憶共享機制 |

---

## 7. 結論

OpenClaw 係一個 **設計精良嘅 Agent Gateway**，喺 runtime、session 管理、記憶系統方面表現出色。但喺安全（token/key 管理）同多 Agent 協作（冇原生編排）方面仍有改善空間。

**與 ClawTeam 係互補關係，唔係競爭關係。** OpenClaw 做 runtime + 記憶，ClawTeam 做編排 + task。兩者結合可以構建一個完整嘅多 Agent 協作平台。

> **Bottom line**: OpenClaw 係一個好嘅 Agent Runtime，但需要 ClawTeam 做編排先可以發揮多 Agent 嘅真正潛力。

---

## 來源報告

| 報告 | 研究員 | 重點 |
|------|--------|------|
| R1-arch.md | researcher-arch | Gateway 架構、多 Agent 路由、Session 管理、memory-lancedb-pro |
| R2-workflow.md | researcher-workflow | 工作流程、Heartbeat、Cron Job、Agent 間通訊 |
| V1-risk.md | reviewer | 安全模型、可靠性風險、性能瓶頸 |

---

_整合報告由 integrator 完成 — poc-v2-phase1 team_
