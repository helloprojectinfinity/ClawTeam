# R2: ClawTeam 架構設計分析 — 工作流程與通訊系統

> Researcher: researcher-workflow
> Date: 2026-03-26
> Fork: `/home/tinpo/ClawTeam-OpenClaw-fork-new`

---

## Executive Summary

ClawTeam 係一個 **framework-agnostic 嘅多 agent 協調框架**，核心設計理念係「每個 agent 係獨立嘅 CLI process，透過檔案系統 + tmux 進行通訊同協調」。佢唔係一個 agent runtime，而係一個 **orchestration layer**（編排層），坐喺多個 AI CLI（Claude、Codex、OpenClaw、Gemini 等）之上。

---

## 1. Inbox 通訊系統

### 1.1 架構總覽

```
Agent A (tmux pane)                    Agent B (tmux pane)
    │                                      │
    ▼                                      ▼
clawteam inbox send ──→ MailboxManager ──→ clawteam inbox receive
                            │
                    ┌───────┴───────┐
                    │   Transport   │
                    │  (pluggable)  │
                    └───────┬───────┘
                    ┌───────┴───────┐
            FileTransport    P2PTransport
            (檔案系統)       (ZeroMQ + 檔案fallback)
```

**關鍵設計：Transport 抽象層**

`MailboxManager` 唔直接操作檔案，而係委託俾一個 `Transport` 介面。呢個設計令通訊機制可以喺唔同場景下切換：

- **FileTransport**（預設）：每個 message 係一個 JSON 檔案，路徑為 `~/.clawteam/teams/{team}/inboxes/{agent}/msg-{timestamp}-{uuid}.json`
- **P2PTransport**：用 ZeroMQ PUSH/PULL socket 做即時通訊，offline 嘅 agent 自動 fallback 到 FileTransport

### 1.2 Message 結構（`TeamMessage` model）

```python
class TeamMessage(BaseModel):
    type: MessageType          # message, join_request, shutdown_request, idle, broadcast...
    from_agent: str            # 發送者
    to: str | None             # 接收者（broadcast 時為 None）
    content: str | None        # 訊息內容
    request_id: str | None     # 關聯 ID（用於 join/plan/shutdown 握手）
    timestamp: str             # ISO-8601
    # 以下係特定 type 嘅擴展欄位
    proposed_name: str | None  # join_request 用
    assigned_name: str | None  # join_approved 用
    reason: str | None         # rejection 用
    last_task: str | None      # idle notification 用
```

**12 種 MessageType：**
`message`, `join_request`, `join_approved`, `join_rejected`, `plan_approval_request`, `plan_approved`, `plan_rejected`, `shutdown_request`, `shutdown_approved`, `shutdown_rejected`, `idle`, `broadcast`

### 1.3 FileTransport 詳細機制

**Atomic Write：** 先寫 `.tmp-{uid}.json`，再 `rename` 為正式檔名。避免 partial read。

**Claimed Message Pattern：** `claim_messages()` 方法：
1. 將 `.json` 改名為 `.consumed`（原子 rename）
2. 對 `.consumed` 檔案加 `fcntl.flock`（Unix advisory lock）
3. 讀取內容
4. 回傳 `ClaimedMessage` 物件，包含 `ack()` 和 `quarantine()` 回調

**Dead Letter Queue：** 無法解析嘅 message 會被移到 `~/.clawteam/teams/{team}/dead_letters/{agent}/`，附帶 `.meta.json` 記錄錯誤原因。

**Event Log：** 每個 send 嘅 message 同時寫入 `~/.clawteam/teams/{team}/events/evt-{ts}-{uid}.json`，呢個係 append-only 嘅歷史記錄，唔會被 consume。

### 1.4 P2PTransport 詳細機制

**Peer Discovery：** 每個 agent 啟動時喺 `~/.clawteam/teams/{team}/peers/{agent}.json` 寫入自己嘅 host/port/pid + lease metadata。

**Heartbeat：** 背景 thread 每秒更新 lease，lease 過期時間為 5 秒。

**Liveness Check：**
- 同 host：用 `os.kill(pid, 0)` 檢查 PID
- 跨 host：依賴 lease freshness

**Offline Fallback：** 如果 peer 唔 reachable，message 自動 fallback 到 FileTransport。

### 1.5 通訊模式

| 模式 | CLI 指令 | 實現 |
|------|---------|------|
| Point-to-point | `inbox send {team} {agent} "content"` | `MailboxManager.send()` → `Transport.deliver()` |
| Broadcast | `inbox broadcast {team} "content"` | 遍歷 `Transport.list_recipients()`，逐一 deliver |
| Receive (consume) | `inbox receive {team} --agent {name}` | `MailboxManager.receive()` → `claim_messages()` → `ack()` |
| Peek (non-destructive) | `inbox peek {team} --agent {name}` | `MailboxManager.peek()` → `fetch(consume=False)` |
| Event log | `inbox log {team}` | 讀取 `events/` 目錄，唔 consume |
| Watch (blocking) | `inbox watch {team} --agent {name}` | `InboxWatcher` 輪詢 + exec hook |

---

## 2. Task 生命週期管理

### 2.1 Task 狀態機

```
                    ┌──────────┐
                    │ blocked  │ ←── blocked_by 未完成
                    └────┬─────┘
                         │ blocked_by 全部完成（自動解鎖）
                         ▼
┌─────────┐  spawn   ┌──────────┐  task update  ┌─────────────┐
│ (create) │ ──────→ │ pending  │ ────────────→ │ in_progress │
└─────────┘         └──────────┘   --status      └──────┬──────┘
                         │           in_progress        │
                         │                              │ task update
                         │                              │ --status completed
                         │                              ▼
                         │                        ┌───────────┐
                         │                        │ completed │
                         │                        └───────────┘
                         │                              │
                         └──────────────────────────────┘
                              _resolve_dependents_unlocked()
                              自動將 downstream task 從 blocked → pending
```

### 2.2 Lock 機制

**Task Lock** 係防止多個 agent 同時操作同一個 task 嘅機制：

```python
# in_progress 時自動 acquire lock
task.locked_by = caller    # agent name
task.locked_at = _now_iso()

# completed/pending 時自動 release
task.locked_by = ""
task.locked_at = ""
```

**Force Override：** 如果 lock holder 已經死咗（透過 `is_agent_alive()` 檢查 tmux pane 或 PID），可以用 `--force` 強制覆蓋。

**Stale Lock Cleanup：** `release_stale_locks()` 方法遍歷所有 locked task，用 `is_agent_alive()` 檢查 lock holder 是否存活。

### 2.3 依賴管理（blocked_by）

**Cycle Detection：** 建立 blocked_by 關係時，用 DFS 檢測有冇 cycle。如果有，reject 呢個操作。

**Auto-Unlock：** 當一個 task 完成時，`_resolve_dependents_unlocked()` 會遍歷所有 task，將 completed task 從其他 task 嘅 `blocked_by` list 中移除。如果某個 task 嘅 `blocked_by` 變空，佢嘅 status 會自動從 `blocked` 變為 `pending`。

**重要發現：blocked_by 只解鎖，唔自動 spawn agent。** Task B 完成後，Task A 會從 blocked 變 pending，但冇人會自動去執行 Task A。呢個係目前嘅 gap。

### 2.4 Duration Tracking

當 task 變為 completed 時，自動計算 `started_at` 到現在嘅時間差，存入 `task.metadata["duration_seconds"]`。

### 2.5 File-based Storage

每個 task 係一個獨立 JSON 檔案：`~/.clawteam/tasks/{team}/task-{id}.json`

用 `fcntl.flock`（Unix）或 `msvcrt.locking`（Windows）做 concurrent access serialization。

---

## 3. Template 系統

### 3.1 Template 結構

Template 係 TOML 格式，定義一個完整嘅 team 配置：

```toml
[template]
name = "team-name"
description = "..."
command = ["openclaw"]       # 預設 agent CLI
backend = "tmux"             # spawn backend

[template.leader]
name = "integrator"
type = "leader"
task = "You are the leader..."

[[template.agents]]
name = "researcher-1"
type = "researcher"
task = "You are a researcher..."

[[template.tasks]]
subject = "Task 1"
owner = "researcher-1"
blocked_by = ["Task 2"]      # 依賴關係
```

### 3.2 Variable Substitution

Template 中嘅 `{goal}`, `{team_name}`, `{agent_name}` 等變數會喺 `launch` 時被替換。用 `_SafeDict` 實現——未知嘅 placeholder 會保留原樣（`{unknown}` → `{unknown}`）。

### 3.3 Launch 流程

`clawteam launch {template} --goal "..."` 嘅執行流程：

1. Load template TOML
2. Create team（`TeamManager.create_team`）
3. Add all agents as members
4. Create all tasks（先建立 task，再設定 blocked_by 依賴）
5. Get spawn backend（tmux/subprocess）
6. Spawn all agents（leader first，然後 workers）
   - 每個 agent 獲得一個 build 嘅 prompt（identity + task + coordination protocol）
   - 如果有 workspace flag，建立 git worktree

### 3.4 Builtin Templates

| Template | 用途 |
|----------|------|
| `swarm-thinking` | 多維度深度研究（R1/R2/V1 + Writer） |
| `research-paper` | 論文寫作 |
| `code-review` | 代碼審查 |
| `hedge-fund` | 投資分析 |
| `software-dev` | 軟件開發 |
| `strategy-room` | 策略討論 |

User templates 放喺 `~/.clawteam/templates/`，優先於 builtin。

---

## 4. Spawn 系統（Agent 啟動）

### 4.1 TmuxBackend

每個 agent 係一個 tmux window，喺 session `clawteam-{team}` 入面。

**Spawn 流程：**
1. 溺備環境變數（`CLAWTEAM_AGENT_ID`, `CLAWTEAM_AGENT_NAME`, `CLAWTEAM_TEAM_NAME` 等）
2. 用 `NativeCliAdapter.prepare_command()` 溺備 CLI 命令（處理 skip_permissions、prompt injection 等）
3. 建立 tmux window，執行命令
4. 等待 pane ready（poll `tmux list-panes`）
5. 處理各種 startup prompt（workspace trust、permissions confirmation、Codex update）
6. 如果係 post-launch prompt injection（Claude/Codex interactive mode），用 `tmux load-buffer` + `paste-buffer` 注入
7. 註冊到 spawn registry（`register_agent`）

**Exit Hook：** 命令執行完後自動執行 `clawteam lifecycle on-exit --team {team} --agent {agent}`，清理 session 並將 in_progress task 重設為 pending。

### 4.2 Multi-CLI Support

`adapters.py` 定義咗 8 種 CLI 嘅 detection 同 command preparation：
- Claude Code
- Codex
- Gemini CLI
- Kimi CLI
- Qwen Code
- OpenCode
- Nanobot
- **OpenClaw** ← fork 新增

每種 CLI 有唔同嘅 prompt injection 方式（有些用 `-p` flag，有些用 post-launch buffer injection）。

### 4.3 Spawn Registry

`~/.clawteam/teams/{team}/spawn_registry.json` 記錄每個 agent 嘅：
- backend（tmux/subprocess）
- tmux_target（`clawteam-{team}:{agent_name}`）
- pid
- command
- spawned_at（unix timestamp）

用嚟做 liveness check 同 zombie detection。

---

## 5. Lifecycle 管理

### 5.1 Shutdown Protocol

```
Leader                          Agent
  │                               │
  │ shutdown_request              │
  │ ──────────────────────────→   │
  │                               │ (agent 做嘢，準備收尾)
  │ shutdown_approved/rejected    │
  │ ←──────────────────────────   │
```

### 5.2 Idle Notification

Worker 完成所有 task 後，send idle message 畀 leader。Leader 收到後可以 assign 新 task 或 approve shutdown。

### 5.3 On-Exit Hook

Agent process 終止時自動觸發：
1. Clear session store
2. Reset in_progress tasks → pending
3. Notify leader（透過 inbox send）

### 5.4 Zombie Detection

`check-zombies` 命令檢查運行超過指定時間（預設 2 小時）嘅 agent，用嚟發現可能卡住嘅 process。

---

## 6. 與外部系統嘅整合

### 6.1 OpenClaw 整合

fork 新增咗 `is_openclaw_command()` detection 同 OpenClaw-specific 嘅 command preparation：

```python
elif is_openclaw_command(normalized_command):
    if "agent" in normalized_command:
        final_command.append("--local")
        final_command.extend(["--session-id", agent_name])
        if prompt:
            final_command.extend(["--message", prompt])
```

### 6.2 Git Workspace 隔離

每個 agent 可以有自己嘅 git worktree（`--workspace` flag），喺獨立嘅 branch 上工作，唔影響 main branch。

### 6.3 Board / Dashboard

提供 kanban board 視圖（`clawteam board show`）、live refresh mode、Web UI server、甚至 Gource visualization。

---

## 7. 架構優勢與限制

### 優勢

1. **Framework-agnostic**：唔綁定任何特定 AI CLI，支援 8+ 種 runtime
2. **File-based simplicity**：唔需要 database server，用檔案系統就做到 persistent state
3. **Transport abstraction**：可以喺 file（簡單）和 P2P（高效）之間切換
4. **Tmux 可觀測性**：每個 agent 嘅工作過程可以即時睇到
5. **Complete lifecycle**：join → task → idle → shutdown 全覆蓋
6. **Template 一鍵啟動**：`clawteam launch` 一條命令搞掂成個 team

### 限制

1. **blocked_by 只解鎖，唔自動 spawn**：Task 完成後 downstream task 變 pending，但冇自動觸發機制
2. **File-based 性能**：大量 task/message 時，glob + JSON parse 可能成為 bottleneck
3. **冇 retry 機制**：Task failed 後冇自動重試
4. **冇 task 優先級排程**：有 priority 欄位但冇 scheduler 自動按 priority 分配
5. **Cross-host 限制**：P2P transport 需要 network connectivity，file transport 需要 shared filesystem
6. **冇 authentication**：Inbox 訊息冇簽名或加密，依賴 filesystem permissions

---

## 8. 關鍵代碼證據

| 組件 | 檔案 | 關鍵行 |
|------|------|--------|
| Inbox 通訊 | `team/mailbox.py:55-70` | `send()` 方法：建立 TeamMessage → Transport.deliver() → _log_event() |
| File Transport | `transport/file.py:88-100` | `deliver()`：atomic write (tmp + rename) |
| P2P Transport | `transport/p2p.py:130-145` | `deliver()`：ZMQ send → fallback to file |
| Task Lock | `store/file.py:100-110` | `_acquire_lock()`：check alive → raise TaskLockError |
| Auto-unlock | `store/file.py:191-205` | `_resolve_dependents_unlocked()`：遍歷移除 completed task |
| Cycle Detection | `store/file.py:220-240` | `_validate_blocked_by_unlocked()`：DFS cycle detection |
| Template Loader | `templates/__init__.py:55-70` | `_parse_toml()`：TOML → TemplateDef |
| Spawn | `spawn/tmux_backend.py:50-90` | `spawn()`：env setup → tmux new-window → register |
| Prompt Builder | `spawn/prompt.py:35-80` | `build_agent_prompt()`：identity + task + context + coordination |
| Exit Hook | `cli/commands.py:1680-1720` | `lifecycle_on_exit()`：clear session → reset tasks → notify leader |

---

## 9. 建議改進方向

1. **Auto-Spawn Handoff**：blocked_by 解鎖後自動 spawn 下一個 agent（需要 integrator 觸發或 event-driven 機制）
2. **Task Retry**：failed task 自動重試 N 次
3. **Priority Scheduler**：按 priority 自動分配 task 畀 idle agent
4. **Message Signing**：對 inbox message 加簽名，防止 tampering
5. **Metrics Dashboard**：task duration、agent utilization、message throughput 等指標

---

_Report saved: 2026-03-26 by researcher-workflow_
