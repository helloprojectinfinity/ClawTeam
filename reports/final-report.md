# ClawTeam 架構設計綜合分析報告

> **團隊**: poc-research (Swarm-Thinking)
> **日期**: 2026-03-26
> **整合者**: integrator
> **來源報告**: R1-arch.md (架構分析) | R2-workflow.md (工作流程) | V1-risk.md (風險評估)

---

## Executive Summary

ClawTeam v0.2.0 係一個 **架構設計正確但尚未成熟** 嘅多 Agent 協調框架。佢嘅核心理念——「Leader Agent 自主協調 Worker Agents，人類只需提供目標」——喺 PoC 同開發階段表現出色，但距離生產級部署仍有顯著差距。

**三份報告嘅共識：**
1. **File-based simplicity** 係正確嘅起步選擇（冇外部依賴，部署簡單）
2. **blocked_by 只解鎖、唔自動 spawn** 係最明顯嘅功能缺口
3. **冇認證/授權機制** 係最嚴重嘅安全問題

**關鍵分歧：**
- R1 認為架構「簡單可靠」，V1 則指出 file-based transport 嘅 race condition 同 lock leak 風險
- R2 強調 template 系統嘅靈活性，V1 則警告 template override 缺乏審計

**整體評級：MEDIUM-HIGH 風險**（適合 PoC/開發，唔適合生產環境）

---

## 1. 多 Agent 協調機制

### 1.1 架構分層

```
┌─────────────────────────────────────────────────────┐
│                   CLI Layer (Typer)                  │
│  clawteam spawn | task | inbox | board | launch ... │
├─────────────────────────────────────────────────────┤
│              Coordination Layer                      │
│  TeamManager │ MailboxManager │ TaskStore │ PlanMgr │
├─────────────────────────────────────────────────────┤
│              Execution Layer                         │
│  SpawnBackend (tmux │ subprocess) │ Adapters        │
├─────────────────────────────────────────────────────┤
│              Transport Layer                         │
│  FileTransport │ P2PTransport (ZeroMQ)              │
├─────────────────────────────────────────────────────┤
│              Storage Layer                           │
│  Filesystem (JSON files) │ Git Worktrees            │
└─────────────────────────────────────────────────────┘
```

### 1.2 Spawn 流程

Leader 透過 `clawteam spawn` 創建 Worker，每個 Agent 獲得：
- 獨立 **tmux window**（可視化監控）
- 獨立 **git worktree**（代碼隔離）
- 獨立 **inbox 目錄**（通訊隔離）
- 透過 **環境變數** 傳遞身份（`CLAWTEAM_AGENT_NAME` 等）

### 1.3 多 CLI 支持

`adapters.py` 支持 8 種 CLI 工具（Claude、Codex、OpenClaw、Gemini、Kimi、Qwen、OpenCode、Nanobot），每種 CLI 嘅 prompt 注入方式都唔同，Adapter 統一咗呢啲差異。

### 1.4 風險評估

| 風險 | 嚴重度 | 來源 |
|------|--------|------|
| Tmux session 碰撞（多 agent 互相覆蓋） | HIGH | V1 |
| Agent 進程死咗但 shell 殘留（假 alive） | HIGH | V1 |
| Shell injection via agent name/prompt | HIGH | V1 |
| `--dangerously-skip-permissions` 預設 True | HIGH | V1 |

---

## 2. Task 依賴管理系統

### 2.1 Task 數據模型

每個 Task 係一個獨立 JSON 檔案，包含：
- `status`: pending | in_progress | completed | blocked
- `blocked_by`: 依賴嘅 task ID 列表
- `locked_by`: 當前鎖定者
- `metadata`: 額外數據（如 duration_seconds）

### 2.2 依賴解析機制（DAG）

**核心發現（三份報告一致）：**
- `blocked_by` 係 **單向依賴**（DAG，有向無環圖）
- 完成一個 task 會觸發 **連鎖解鎖**（cascade unlock）
- **但唔會自動 spawn agent** —— 解鎖後需要 Leader 或 Worker 自己 poll

**Cycle Detection：** 建立 blocked_by 關係時用 DFS 檢測循環依賴。

### 2.3 Lock 機制

- `in_progress` 時自動 acquire lock
- Lock holder 死咗時可用 `--force` 強制覆蓋
- `release_stale_locks()` 清理過期鎖

### 2.4 風險評估

| 風險 | 嚴重度 | 來源 |
|------|--------|------|
| O(n) 依賴解析掃描（100+ task 時性能下降） | MEDIUM | V1 |
| Task lock 喺 agent crash 後可能 leak | MEDIUM | V1 |
| 冇 task `failed` 狀態（只有 pending/in_progress/completed/blocked） | MEDIUM | V1 |
| 冇自動重試機制 | MEDIUM | R2 |

---

## 3. Inbox 通訊系統

### 3.1 架構設計

```
MailboxManager（邏輯層）
    ↓
Transport（抽象層）← 可替換
    ↓
FileTransport / P2PTransport（實現層）
```

### 3.2 FileTransport

每個 message 係一個 JSON 檔案：
```
~/.clawteam/teams/{team}/inboxes/{agent}/msg-{timestamp}-{uuid}.json
```

**關鍵機制：**
- Atomic write（tmp + rename）
- Claim-based consumption（`.json` → `.consumed`）
- Dead letter queue（解析失敗嘅訊息）
- Event log（append-only 歷史記錄）

### 3.3 P2PTransport（ZeroMQ）

- PUSH/PULL socket 做即時通訊
- Peer discovery 透過 `peers/{agent}.json`
- Heartbeat thread 每秒更新 lease
- Offline agent 自動 fallback 到 FileTransport

### 3.4 通訊模式

| 模式 | CLI 指令 |
|------|---------|
| Point-to-point | `inbox send {team} {agent} "content"` |
| Broadcast | `inbox broadcast {team} "content"` |
| Receive (consume) | `inbox receive {team} --agent {name}` |
| Peek (non-destructive) | `inbox peek {team} --agent {name}` |
| Event log | `inbox log {team}` |

### 3.5 風險評估

| 風險 | 嚴重度 | 來源 |
|------|--------|------|
| 並發寫入時訊息順序唔保證 | LOW-MEDIUM | V1 |
| Claimed message lock leak（agent crash 後） | MEDIUM | V1 |
| TOCTOU race condition（`_is_locked()` probe） | MEDIUM | V1 |
| 冇 fsync（數據可能喺 OS buffer cache） | LOW | V1 |

---

## 4. Template 系統

### 4.1 TOML 模板格式

```toml
[template]
name = "team-name"
command = ["openclaw"]
backend = "tmux"

[template.leader]
name = "integrator"
type = "leader"
task = "..."

[[template.agents]]
name = "researcher-1"
type = "researcher"
task = "..."

[[template.tasks]]
subject = "Task 1"
owner = "researcher-1"
blocked_by = ["Task 2"]
```

### 4.2 Launch 流程

`clawteam launch` 命令自動執行：
1. 加載模板
2. 創建團隊 + 註冊所有成員
3. 創建所有 tasks（兩遍：先創建，再設 blocked_by 依賴）
4. 按順序 spawn 所有 agents（leader first, then workers）

### 4.3 現有模板

| 模板 | 用途 |
|------|------|
| `swarm-thinking` | 多維度深度研究 |
| `research-paper` | AI 論文自動化研究 |
| `code-review` | 代碼審查團隊 |
| `software-dev` | 全棧開發團隊 |
| `hedge-fund` | 投資分析團隊 |
| `strategy-room` | 策略討論室 |

### 4.4 風險評估

| 風險 | 嚴重度 | 來源 |
|------|--------|------|
| User template 可以 override built-in（冇審計） | MEDIUM | V1 |
| Variable substitution 可能有 injection 風險 | MEDIUM | V1 |
| 冇 template 組合機制（extends/include） | LOW | V1 |

---

## 5. 風險矩陣總結

| 類別 | 風險 | 嚴重度 | 優先級 |
|------|------|--------|--------|
| 安全 | 冇認證/授權 | CRITICAL | P0 |
| 安全 | `--dangerously-skip-permissions` 預設 True | HIGH | P0 |
| 可靠性 | Agent zombie（hang 但 appear alive） | HIGH | P1 |
| 可靠性 | Task lock leak on agent crash | MEDIUM | P1 |
| 性能 | O(n) 依賴解析掃描 | MEDIUM | P2 |
| 可靠性 | 訊息順序唔保證 | LOW-MEDIUM | P2 |
| 安全 | Template override 冇審計 | MEDIUM | P2 |
| 擴展性 | 單機限制 | HIGH | P3 |
| 可觀測性 | 冇 structured logging/metrics | MEDIUM | P3 |
| 持久性 | 冇 backup/replication | MEDIUM | P3 |

---

## 6. 架構評估

### 6.1 優勢

- **簡單可靠**：file-based storage 冇外部依賴
- **可視化**：tmux backend 令所有 agent 嘅工作可見
- **靈活**：transport 抽象層允許未來替換實現
- **模板化**：TOML 模板令團隊配置可復用
- **Framework-agnostic**：唔綁定任何特定 AI CLI

### 6.2 改進空間

| 改進 | 優先級 | 說明 |
|------|--------|------|
| Auto-Spawn Handoff | P0 | blocked_by 解鎖後自動 spawn 下一個 agent |
| 認證機制 | P0 | 至少用 HMAC 簽名，理想用 mTLS |
| Task `failed` 狀態 | P1 | 擴展 TaskStatus enum |
| Agent Heartbeat | P1 | 定期寫 timestamp file，registry 檢查 |
| Lock Timeout | P1 | Task lock 自動過期 |
| Reverse-Dependency Index | P2 | O(1) 依賴解析 |
| Template Checksums | P2 | 簽名 built-in templates |
| Redis Transport | P3 | 跨機通訊 |

---

## 7. 與 OpenClaw 嘅整合點

- OpenClaw agent 作為 CLI 被 spawn（`openclaw agent --local --session-id ...`）
- ClawTeam 負責協調，OpenClaw 負責執行
- 兩者通過 tmux + 環境變數 + 文件系統溝通
- Fork 新增咗 OpenClaw adapter 支持

---

## 8. 結論

ClawTeam v0.2.0 係一個 **設計良好嘅 PoC/開發工具**。佢嘅架構選擇（file-based storage、transport abstraction、template 系統）喺當前 scope（單機、單用戶、受信任環境）下係正確嘅。

**唔適合生產環境**，除非解決 P0 安全問題（認證、權限控制）。

**Roadmap 對齊**：ROADMAP.md 已規劃 Redis transport（Phase 2）同其他改進，方向正確。

---

## 來源報告

| 報告 | 研究員 | 重點 |
|------|--------|------|
| R1-arch.md | researcher-arch | 架構設計、多 Agent 協調、Task 依賴、Template 系統 |
| R2-workflow.md | researcher-workflow | Inbox 通訊、Task 生命週期、Spawn 系統、Lifecycle 管理 |
| V1-risk.md | reviewer | 風險評估、安全分析、性能瓶頸、可靠性問題 |

---

_整合報告由 integrator 完成 — poc-research team_
