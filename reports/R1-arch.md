# R1: ClawTeam 架構設計深度分析

> Researcher: researcher-arch
> Focus: 技術架構、多 Agent 協調機制、Task 依賴管理、Inbox 通訊系統、Template 系統

---

## 1. 整體架構總覽

ClawTeam 係一個 **framework-agnostic** 嘅多 Agent 協調框架，核心設計理念係「Leader Agent 自主協調 Worker Agents」。人類只需要提供目標，系統自動處理 spawn、task 分配、通訊、進度監控。

### 1.1 分層架構

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

### 1.2 核心組件對應

| 組件 | 模組 | 職責 |
|------|------|------|
| Team Manager | `team/manager.py` | 團隊生命週期（create/add_member/cleanup） |
| Mailbox Manager | `team/mailbox.py` | Agent 間通訊（send/broadcast/receive） |
| Task Store | `store/file.py` | 任務 CRUD + 依賴管理 + 鎖機制 |
| Spawn Backend | `spawn/tmux_backend.py` | Agent 進程啟動（tmux window） |
| Adapters | `spawn/adapters.py` | 各 CLI 工具嘅命令適配 |
| Transport | `transport/` | 訊息傳遞（檔案 / ZeroMQ P2P） |
| Templates | `templates/` | TOML 團隊模板系統 |

---

## 2. 多 Agent 協調機制

### 2.1 Spawn 流程

Leader Agent 透過 `clawteam spawn` 命令創建 Worker。流程如下：

```
Leader 決定需要一個 worker
    ↓
clawteam spawn --team my-team --agent-name worker1 --task "Implement auth"
    ↓
1. 創建 team（如果不存在）→ TeamManager.create_team()
2. 註冊 member → TeamManager.add_member()
3. 創建 git worktree（隔離工作區）→ WorkspaceManager.create_workspace()
4. 構建 prompt（identity + task + coordination guide）→ build_agent_prompt()
5. 啟動 tmux window → TmuxBackend.spawn()
6. 註冊到 spawn registry → register_agent()
7. 注入 prompt（tmux paste-buffer / send-keys）
```

**關鍵設計：**
- 每個 Agent 有獨立嘅 **tmux window**（可視化監控）
- 每個 Agent 有獨立嘅 **git worktree**（代碼隔離）
- 每個 Agent 有獨立嘅 **inbox 目錄**（通訊隔離）
- Agent 透過 **環境變數** 獲取身份（`CLAWTEAM_AGENT_NAME`, `CLAWTEAM_TEAM_NAME` 等）

### 2.2 Agent 身份系統

```python
# identity.py
class AgentIdentity:
    agent_id: str      # 唯一標識（12 位 hex）
    agent_name: str    # 邏輯名稱（如 "researcher-arch"）
    agent_type: str    # 類型（leader, researcher, writer, reviewer...）
    team_name: str     # 所屬團隊
    user: str          # 用戶標識
    is_leader: bool    # 是否為 Leader
```

身份透過環境變數傳遞，Agent 啟動時自動讀取。

### 2.3 多 CLI 支持

`adapters.py` 實現咗一個 **NativeCliAdapter**，支持 8 種 CLI 工具：

| CLI | 命令偵測 | 特殊處理 |
|-----|----------|----------|
| Claude Code | `claude`, `claude-code` | `--dangerously-skip-permissions`, prompt injection via buffer |
| Codex | `codex`, `codex-cli` | `--dangerously-bypass-approvals-and-sandbox` |
| OpenClaw | `openclaw` | `--local`, `--session-id`, `--message` |
| Gemini | `gemini` | `--yolo` |
| Kimi | `kimi` | `-w workspace`, `--print -p prompt` |
| Qwen | `qwen`, `qwen-code` | `--yolo` |
| OpenCode | `opencode` | `--yolo` |
| Nanobot | `nanobot` | `-w workspace`, `-m prompt` |

**設計亮點：** 每種 CLI 嘅 prompt 注入方式都唔同（Claude 用 buffer paste，Codex 用 post-launch injection，OpenClaw 用 `--message` 參數），Adapter 統一咗呢啲差異。

---

## 3. Task 依賴管理

### 3.1 Task 數據模型

```python
class TaskItem:
    id: str                    # 8 位 hex UUID
    subject: str               # 任務標題
    description: str           # 任務描述
    status: TaskStatus         # pending | in_progress | completed | blocked
    priority: TaskPriority     # low | medium | high | urgent
    owner: str                 # 負責 agent
    locked_by: str             # 當前鎖定者
    locked_at: str             # 鎖定時間
    blocks: list[str]          # 此任務阻塞嘅任務 ID 列表
    blocked_by: list[str]      # 阻塞此任務嘅任務 ID 列表
    started_at: str            # 開始時間
    created_at: str            # 創建時間
    updated_at: str            # 更新時間
    metadata: dict             # 額外數據（如 duration_seconds）
```

### 3.2 依賴解析機制

**創建時：**
- 如果 `blocked_by` 非空，task 自動設為 `blocked` 狀態
- 驗證唔可以有循環依賴（DFS cycle detection）

**完成時自動解鎖：**
```python
def _resolve_dependents_unlocked(self, completed_task_id: str):
    # 遍歷所有 task，如果某 task 嘅 blocked_by 包含已完成嘅 task_id
    # → 移除該依賴
    # → 如果 blocked_by 清空且狀態為 blocked → 改為 pending
```

**呢個設計嘅核心洞察：**
- `blocked_by` 係 **單向依賴**（DAG，有向無環圖）
- 完成一個 task 會觸發 **連鎖解鎖**（cascade unlock）
- 但 **唔會自動 spawn agent** —— 解鎖後需要 Leader 或 Worker 自己 poll

### 3.3 Task 鎖機制

```python
def _acquire_lock(self, task, caller, force):
    # 如果 task 已被鎖定且鎖定者仲 alive
    # → 拋出 TaskLockError（除非 --force）
    # 否則 → 設定 locked_by + locked_at
```

- 鎖定者 alive 判斷：透過 `spawn.registry.is_agent_alive()` 檢查 tmux pane / PID
- Agent 退出時自動清理：`lifecycle on-exit` hook 重置 in_progress task 為 pending

### 3.4 並發控制

FileTaskStore 使用 **OS 級別嘅 advisory lock**：
- Linux: `fcntl.flock(LOCK_EX)`
- Windows: `msvcrt.locking(LK_LOCK)`

每個 task 係獨立嘅 JSON file，寫入時用 **atomic rename**（write tmp → rename）防止 partial read。

---

## 4. Inbox 通訊系統

### 4.1 架構設計

```
MailboxManager（邏輯層）
    ↓
Transport（抽象層）
    ↓
FileTransport / P2PTransport（實現層）
```

**MailboxManager** 負責：
- 訊息構建（`TeamMessage` Pydantic model）
- 事件日誌記錄（`events/` 目錄，永不消費）
- 廣播（broadcast to all members）

**Transport** 負責：
- 訊息傳遞（deliver / fetch / count）
- 並發控制（file lock / ZMQ）

### 4.2 訊息類型

```python
class MessageType(str, Enum):
    message = "message"                    # 普通訊息
    join_request = "join_request"          # 加入請求
    join_approved = "join_approved"        # 批准加入
    join_rejected = "join_rejected"        # 拒絕加入
    plan_approval_request = "..."          # 計劃審批請求
    plan_approved = "plan_approved"        # 計劃批准
    plan_rejected = "plan_rejected"        # 計劃拒絕
    shutdown_request = "..."               # 關機請求
    shutdown_approved = "..."              # 批准關機
    shutdown_rejected = "..."              # 拒絕關機
    idle = "idle"                          # 空閒通知
    broadcast = "broadcast"                # 廣播
```

### 4.3 FileTransport 實現

每個訊息係一個 JSON file：
```
~/.clawteam/teams/{team}/inboxes/{agent}/msg-{timestamp}-{uuid}.json
```

**關鍵機制：**
1. **Atomic writes**: write tmp → rename（防止 partial read）
2. **Claim-based consumption**: `.json` → `.consumed`（rename + file lock）
3. **Dead letter queue**: 解析失敗嘅訊息移到 `dead_letters/` 目錄
4. **Lock probe**: `_is_locked()` 檢查 file 係咪被其他進程鎖定

### 4.4 P2PTransport（ZeroMQ）

```python
class P2PTransport(Transport):
    # PULL socket: 監聽 incoming messages
    # PUSH socket: 發送 messages 到其他 agents
    # Peer discovery: 透過 peers/{agent}.json
    # Offline fallback: 如果 peer 唔 reachable → FileTransport
```

**Peer 發現機制：**
- 每個 agent 啟動時寫 `peers/{agent}.json`（host, port, pid, heartbeat）
- Heartbeat thread 每秒更新 lease
- 5 秒 lease 過期後自動清理

**設計亮點：** P2P 係 optional 優化，file transport 作為 fallback，確保離線 agent 都可以收到訊息。

---

## 5. Template 系統

### 5.1 TOML 模板格式

```toml
[template]
name = "team-name"
description = "..."
command = ["openclaw"]      # 預設 CLI 命令
backend = "tmux"            # 預設後端

[template.leader]
name = "integrator"
type = "leader"
task = "You are the leader..."

[[template.agents]]
name = "researcher-arch"
type = "researcher"
task = "You are the researcher..."

[[template.tasks]]
subject = "Architecture research"
owner = "researcher-arch"
blocked_by = ["Other task"]   # 可選依賴
```

### 5.2 模板加載優先級

1. User templates: `~/.clawteam/templates/{name}.toml`
2. Built-in templates: `clawteam/templates/{name}.toml`

### 5.3 變量替換

```python
def render_task(task: str, **variables: str) -> str:
    return task.format_map(_SafeDict(**variables))
# 支持: {goal}, {team_name}, {agent_name}
# 未知變量保留原樣（唔會 KeyError）
```

### 5.4 Launch 流程

`clawteam launch` 命令自動執行：
1. 加載模板
2. 創建團隊 + 註冊所有成員
3. 創建所有 tasks（兩遍：先創建，再設 blocked_by 依賴）
4. 按順序 spawn 所有 agents（leader first, then workers）
5. 每個 agent 獲得包含身份 + 任務嘅完整 prompt

### 5.5 現有模板

| 模板 | 用途 |
|------|------|
| `swarm-thinking` | 多維度深度研究（arch + workflow + risk → writer） |
| `research-paper` | AI 論文自動化研究 |
| `code-review` | 代碼審查團隊 |
| `software-dev` | 全棧開發團隊 |
| `hedge-fund` | 投資分析團隊 |
| `strategy-room` | 策略討論室 |

---

## 6. Workspace 隔離系統

### 6.1 Git Worktree 隔離

每個 Agent 獲得一個獨立嘅 git worktree：
```
原 repo: /home/user/project/
    ↓
Agent A: /home/user/project/.git/worktrees/clawteam-team-agent-a
Agent B: /home/user/project/.git/worktrees/clawteam-team-agent-b
```

**隔離效果：**
- 每個 agent 有獨立嘅 working directory
- 分支自動創建（`clawteam/{team}/{agent}`）
- 代碼修改互不干擾
- 可以 checkpoint（auto-commit）和 merge 回主分支

### 6.2 衝突檢測

```python
def detect_overlaps(team_name, repo):
    # 檢測多個 agent 修改同一文件嘅情況
    # 返回: [{file, agents, severity}]
```

---

## 7. 生命週期管理

### 7.1 Agent 狀態流轉

```
Spawned → Active → Idle → Shutdown
    ↓         ↓       ↓
  (crash)  (crash)  (timeout)
    ↓         ↓       ↓
  on-exit  on-exit  zombie detection
```

### 7.2 on-exit Hook

Agent 進程退出時自動執行：
1. 清理 session 文件
2. 重置 in_progress tasks 為 pending
3. 通知 Leader（透過 inbox）

### 7.3 Zombie Detection

```python
def list_zombie_agents(team_name, max_hours=2.0):
    # 檢測運行超過 max_hours 嘅 agents
    # 透過 tmux pane liveness + PID check
```

---

## 8. 設計模式總結

### 8.1 核心設計原則

1. **Filesystem as Database**: 所有狀態存儲為 JSON files，用 file lock 做並發控制
2. **Transport Abstraction**: 通訊層可替換（file / P2P / 未來嘅 Redis）
3. **Identity via Environment**: Agent 身份透過環境變數傳遞，唔需要額外嘅認證
4. **Atomic Operations**: 所有寫操作用 tmp + rename，防止 partial state
5. **Graceful Degradation**: P2P 失敗時 fallback 到 file transport

### 8.2 與 OpenClaw 嘅整合點

- OpenClaw agent 作為 CLI 被 spawn（`openclaw agent --local --session-id ...`）
- ClawTeam 負責協調，OpenClaw 負責執行
- 兩者通過 tmux + 環境變數 + 文件系統溝通

### 8.3 已知限制

1. **blocked_by 只解鎖，唔自動 spawn**：Task 解鎖後需要 agent 自己 poll 或 Leader 手動觸發
2. **File-based transport 嘅延遲**：依賴 poll interval（預設 1-5 秒）
3. **單機限制**：P2P transport 雖然支持跨機，但 peer discovery 依賴共享文件系統
4. **冇 built-in 嘅結果傳遞機制**：Agent 之間嘅數據傳遞依賴共享文件系統（reports/ 目錄）

---

## 9. 架構評估

### 9.1 優勢

- **簡單可靠**：file-based storage 冇外部依賴（唔需要 Redis/PostgreSQL）
- **可視化**：tmux backend 令所有 agent 嘅工作可見
- **靈活**：transport 抽象層允許未來替換實現
- **模板化**：TOML 模板令團隊配置可復用

### 9.2 改進空間

- **自動 Handoff**：blocked_by 解鎖後應自動觸發下一個 agent（目前需要手動）
- **結果傳遞**：需要標準化嘅 artifact 傳遞機制（唔係依賴共享目錄）
- **錯誤恢復**：agent crash 後嘅 task 重試機制可以更 robust
- **跨機支持**：P2P transport 依賴共享文件系統做 peer discovery，限制咗真正嘅分布式部署

---

_Sources: ClawTeam v0.2.0 source code analysis (HKUDS/ClawTeam)_
