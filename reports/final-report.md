# OpenClaw 架構設計綜合分析報告（v3 Final）

> **團隊**: poc-v3-phase1 (Swarm-Thinking)
> **日期**: 2026-03-27
> **整合者**: integrator
> **來源報告**: R1-arch-final.md | R2-workflow-final.md | V1-risk-v3.md

---

## Executive Summary

OpenClaw 係一個 **AI Agent Gateway**，核心設計理念係「一個長壽 Gateway 進程管理所有通訊表面」。佢唔係一個簡單嘅 chatbot wrapper，而係一個完整嘅 agent 運行時平台。

**三份報告嘅核心共識：**

| 維度 | 發現 |
|------|------|
| **架構** | Gateway-centric 設計，單一進程管理所有 channel、session、tool 執行 |
| **多 Agent** | 係「路由隔離」唔係「協作編排」——冇原生嘅 agent 間任務分配 |
| **記憶** | memory-lancedb-pro 係最成熟嘅組件——hybrid retrieval + decay engine + tier manager |
| **安全** | Credential 明文存儲 + Gateway token 單點信任 = MEDIUM 風險 |

**整體評級：MEDIUM 風險**（適合個人使用，企業部署需要加固）

---

## 1. Gateway Daemon 架構

### 1.1 進程模型

```
Gateway Daemon (Node.js)
├── Channel Layer（通訊渠道管理）
│   ├── WhatsApp (Baileys) / Telegram (grammY) / Discord / Signal / Slack
│   └── 20+ 其他渠道...
├── Agent Loop Engine（Agent 執行引擎）
│   ├── Session Manager → Context Engine → Model Router
│   → Tool Executor → Streaming Pipeline
├── Plugin Registry（插件註冊中心）
│   ├── Provider / Channel / Memory / Context Engine Plugins
├── Cron Scheduler（定時任務調度）
├── WebSocket Server (:18789) — Clients + Nodes
└── HTTP Server（Canvas, A2UI, Webhooks）
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

### 2.1 Binding 路由系統

**路由優先級（most-specific wins）：**
1. peer match（精確 DM/group/channel ID）
2. parentPeer match（thread 繼承）
3. guildId + roles（Discord 角色路由）
4. guildId → teamId → accountId → channel → fallback

**設計亮點：** 同一個 Gateway 可以運行多個完全隔離嘅 agent，每個 agent 有自己嘅 Workspace、Model、Sandbox、Tool Policy。

### 2.2 Sub-agent 系統

```python
sessions_spawn(runtime="subagent")  # OpenClaw 內部子代理
sessions_spawn(runtime="acp")       # ACP 協議（Codex、Claude Code 等外部 agent）
```

**子代理特點：**
- 繼承父 session 嘅 workspace directory
- 可以 streaming 回傳結果（`streamTo: "parent"`）
- 支持 one-shot（`mode="run"`）同 persistent（`mode="session"`）模式

### 2.3 與 ClawTeam 嘅對比

| 特性 | OpenClaw | ClawTeam |
|------|----------|----------|
| Agent 啟動 | `sessions_spawn`（in-process） | `tmux new-window`（獨立進程） |
| 通訊 | Session-based（共享狀態） | File-based inbox（JSON files） |
| 隔離級別 | Workspace + Session + Auth | Git worktree + Inbox directory |
| 協調者 | Gateway（自動路由） | Leader Agent（手動協調） |
| Task 管理 | 冇內建 task 系統 | 完整嘅 DAG task 系統 |

---

## 3. Session 管理系統

### 3.1 Session 生命週期

```
Daily Reset（預設 4:00 AM）+ Idle Reset（可選，sliding window）
    ↓ 兩者取先到期者 → 觸發新 session
```

### 3.2 Context 組裝流程

```
1. System Prompt（base + skills + bootstrap + per-run overrides）
2. Session History（JSONL transcript）
3. Memory Files（MEMORY.md + memory/YYYY-MM-DD.md）
4. Tool Results
5. Compaction（當 context window 接近上限時自動壓縮）
```

### 3.3 Compaction 機制

當 session 接近 auto-compaction 閾值時：
1. 觸發 **silent memory flush**（提醒 model 寫入持久記憶）
2. 壓縮舊嘅 context（summarize older messages）
3. 釋放 token 空間

---

## 4. 記憶系統：memory-lancedb-pro

### 4.1 核心架構

```
┌─────────────────────────────────────────────┐
│           memory-lancedb-pro Plugin          │
├─────────────────────────────────────────────┤
│  Smart Extractor    │  Noise Filter         │
├─────────────────────────────────────────────┤
│  Hybrid Retriever                          │
│  ├── Vector Search（語義相似度）             │
│  ├── BM25 Search（關鍵詞匹配）               │
│  └── Cross-encoder Rerank（重排序）          │
├─────────────────────────────────────────────┤
│  Decay Engine      │  Tier Manager          │
│  (Weibull 時間衰減) │  (核心/工作/邊緣分層)   │
├─────────────────────────────────────────────┤
│  Scope Manager（多範圍隔離）                  │
├─────────────────────────────────────────────┤
│  LanceDB Storage（向量數據庫 + 元數據）       │
└─────────────────────────────────────────────┘
```

### 4.2 Hybrid Retrieval（混合檢索）

```
Query → Vector Search（語義）+ BM25 Search（關鍵詞）
    → Merge + Weighted Score → Cross-encoder Rerank → Final Results
```

### 4.3 Decay Engine（時間衰減）

```python
score = base_score * exp(-λ * age_days)
# 參數：recencyHalfLifeDays、frequencyWeight、intrinsicWeight、importanceModulation
```

### 4.4 Tier Manager（分層管理）

| 層級 | 特徵 | 衰減速度 |
|------|------|---------|
| **Core Memory** | 高訪問頻率 + 高重要度 | 最慢 |
| **Working Memory** | 中等訪問頻率 | 中等 |
| **Peripheral Memory** | 低訪問頻率 + 較舊 | 最快 |

### 4.5 Scope Manager（多範圍隔離）

```python
scopes = { "default", "user", "agent", "session", "project" }
```

---

## 5. 風險評估摘要

### 5.1 Critical Issues

| Issue | 級別 | 描述 | 建議 |
|-------|------|------|------|
| **API Key 明文存儲** | CRITICAL | openclaw.json 包含所有敏感資訊 | 整合 OS keychain |
| **Gateway Token 單點信任** | CRITICAL | 所有請求共用同一 token | 加入 token 過期 + 輪換 |
| **Memory Poisoning** | HIGH | autoCapture 可能存入錯誤/惡意內容 | 加審查機制 |
| **Sub-agent 隔離不足** | HIGH | 子代理繼承父 workspace | 加權限控制 |
| **Context Pruning** | MEDIUM | Compaction 可能丟失關鍵資訊 | 加重要度標記 |

### 5.2 風險矩陣

| 類別 | 風險 | 嚴重度 | 優先級 |
|------|------|--------|--------|
| 安全 | API Key 明文存儲 | CRITICAL | P0 |
| 安全 | Gateway Token 單點信任 | CRITICAL | P0 |
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

### 6.2 建議發展方向

| 優先級 | 建議 |
|--------|------|
| **P0** | API Key 加密（整合 OS keychain） |
| **P0** | Gateway Token 加入過期 + 輪換機制 |
| **P1** | Memory autoCapture 加審查機制 |
| **P1** | Sub-agent workspace 隔離加強 |
| **P2** | 整合 ClawTeam 編排能力 |
| **P2** | 跨 Agent 記憶共享機制 |

---

## 7. 結論

OpenClaw 係一個 **設計精良嘅 Agent Gateway**，喺 runtime、session 管理、記憶系統方面表現出色。但喺安全（credential 管理）同多 Agent 協作（冇原生編排）方面仍有改善空間。

**與 ClawTeam 係互補關係，唔係競爭關係。** OpenClaw 做 runtime + 記憶，ClawTeam 做編排 + task。兩者結合可以構建一個完整嘅多 Agent 協作平台。

> **Bottom line**: OpenClaw 係一個好嘅 Agent Runtime，但需要 ClawTeam 做編排先可以發揮多 Agent 嘅真正潛力。

---

## 來源報告

| 報告 | 研究員 | 重點 |
|------|--------|------|
| R1-arch-final.md | researcher-arch | Gateway 架構、Binding 路由、memory-lancedb-pro |
| R2-workflow-final.md | researcher-workflow | Session 管理、Context 組裝、Cron/Heartbeat |
| V1-risk-v3.md | reviewer | 安全模型、可靠性、性能瓶頸 |

---

_整合報告由 integrator 完成 — poc-v3-phase1 team_
