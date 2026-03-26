# V1 Risk Assessment: ClawTeam Architecture

> **Reviewer**: Risk Reviewer (poc-research)
> **Date**: 2026-03-26
> **Codebase**: ClawTeam v0.2.0 (OpenClaw fork)
> **Scope**: Multi-agent coordination, task dependency management, inbox communication, template system

---

## Executive Summary

ClawTeam v0.2.0 is a **functional but immature** multi-agent orchestration framework. The architecture is sound in principle — file-based transport, tmux process isolation, DAG task dependencies — but carries **significant operational risks** at scale and in production scenarios. The most critical issues are: single-machine coupling, lack of authentication/authorization, file-system race conditions under concurrent load, and no graceful degradation when agents crash mid-task.

**Overall Risk Rating: MEDIUM-HIGH** (acceptable for PoC/development, risky for production)

---

## 1. Multi-Agent Coordination Mechanism

### 1.1 Tmux Backend — Process Isolation Risks

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| Tmux session collision (multiple agents overwrite each other's pane) | HIGH | MEDIUM | Agent output mixed, task corruption |
| Agent process dies silently, shell remains (bash/zsh) | HIGH | HIGH | False "alive" status, task hangs indefinitely |
| No resource limits (CPU/memory per agent) | MEDIUM | HIGH | One agent can starve others |
| Tmux not installed / version incompatibility | LOW | LOW | Complete spawn failure |

**Key Finding — Zombie Detection Flaw** (`spawn/registry.py:120-140`):

The `_tmux_pane_alive()` function checks `pane_dead` flag AND whether the running command is a shell (`bash`, `zsh`, `sh`, `fish`). If the agent process exits but leaves a shell running (which is the **default tmux behavior**), the agent is marked as dead. This is correct for detection but creates a **false negative** scenario: the `is_agent_alive()` call in `_acquire_lock()` (`store/file.py:97-104`) will release locks for agents that are technically "dead" but whose tmux window still exists — another agent could then take over the same task, causing **duplicate execution**.

**Key Finding — No Agent Heartbeat**:

There is no periodic heartbeat from agents back to the coordinator. The only liveness check is polling tmux pane state. If an agent enters an infinite loop or hangs (not crashed), it will appear "alive" forever. The `list_zombie_agents()` function (`registry.py:147-167`) has a `max_hours` threshold (default 2h), but this is only checked on-demand, not automatically.

### 1.2 Spawn Adapter — Command Injection Surface

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| Shell injection via agent name / prompt text | HIGH | MEDIUM | Arbitrary command execution |
| `--dangerously-skip-permissions` default = True | HIGH | HIGH | Agents run with full filesystem access |
| Environment variable leakage between agents | MEDIUM | MEDIUM | Cross-agent credential exposure |

**Key Finding — Shell Escaping Gaps** (`tmux_backend.py:100-110`):

The `full_cmd` string is built by joining shell-quoted components, but the `export_str` uses `shlex.quote()` on values only. The environment variable *names* are filtered by regex (`_SHELL_ENV_KEY_RE`), but the `cmd_str` and `exit_hook` are concatenated into a single shell string. If `final_command` contains elements with shell metachacters that aren't properly quoted, this is an injection vector.

More critically, the `_inject_prompt_via_buffer()` function writes prompts to temp files and loads them via tmux buffer — this bypasses shell escaping but the prompt content is **not sanitized**. A malicious prompt could contain tmux control sequences.

### 1.3 Coordination Protocol — No Consensus Mechanism

The current model is **leader-driven**: the leader assigns tasks, workers execute and report back. There is:
- No voting or consensus for task completion validation
- No mechanism for agents to negotiate task ownership
- No conflict resolution when two agents claim the same task (relies solely on file locking)

This is acceptable for single-leader topologies but breaks down if the leader crashes or if you need peer-to-peer agent coordination.

---

## 2. Task Dependency Management

### 2.1 DAG Validation — Correct but Incomplete

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| Cycle detection only on create/update, not on bulk operations | LOW | LOW | Theoretical — unlikely in practice |
| `blocked_by` resolution is O(n) scan of ALL tasks | MEDIUM | HIGH | Performance degradation with 100+ tasks |
| No partial completion / checkpoint support | MEDIUM | MEDIUM | Agent crash = restart from scratch |
| Task status transitions not enforced (any → any) | MEDIUM | LOW | Invalid state machine transitions |

**Key Finding — Dependency Resolution is Scan-Based** (`store/file.py:176-190`):

When a task completes, `_resolve_dependents_unlocked()` scans **every task file** on disk to find dependents. For a team with 200 tasks, this means reading and parsing 200 JSON files on every task completion. This is O(n) per completion, O(n²) for full pipeline.

**Recommendation**: Maintain a reverse-dependency index (task_id → list of dependents) updated at creation time.

### 2.2 Task Locking — Advisory, Not Mandatory

The task lock system (`_acquire_lock`) is **advisory**:
- Locks are only checked when status = `in_progress`
- A worker can `update` a task's metadata, description, or priority without acquiring a lock
- The `--force` flag bypasses lock checking entirely
- No lock timeout / auto-release mechanism (relies on `release_stale_locks()` being called manually)

**Risk**: Two agents can simultaneously modify the same task's non-status fields without detection.

### 2.3 No Task Retry / Rollback

If a task fails (agent crashes, produces wrong output), there is:
- No automatic retry mechanism
- No rollback of partially completed work
- No way to mark a task as "failed" (only `pending`, `in_progress`, `completed`, `blocked`)
- The `TaskStatus` enum lacks a `failed` state

**Impact**: The leader must manually detect failures and reassign tasks. The `TaskWaiter` detects dead agents and resets their tasks to `pending`, but this is reactive, not proactive.

---

## 3. Inbox Communication System

### 3.1 File Transport — Race Conditions

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| Message loss on concurrent writes to same inbox | MEDIUM | MEDIUM | Missed coordination messages |
| `.consumed` file lock is advisory (fcntl flock) | MEDIUM | LOW | Double-read of same message |
| No message ordering guarantee across agents | LOW | MEDIUM | Out-of-order task execution |
| Dead letter queue grows unbounded | LOW | HIGH | Disk space exhaustion |

**Key Finding — Atomic Write is Partial** (`transport/file.py:47-54`):

The `deliver()` method uses tmp + rename for atomicity, which is correct for single-writer scenarios. However, when **multiple agents send to the same recipient simultaneously**, the rename operations are not serialized. On most filesystems, `rename()` is atomic for the directory entry, but the ordering of multiple concurrent renames is undefined. This means message ordering is **not guaranteed**.

**Key Finding — Claimed Message Lock Leak** (`transport/file.py:76-100`):

The `claim_messages()` method opens a file handle and applies `fcntl.flock()`. If the agent process crashes after claiming but before calling `ack()` or `quarantine()`, the file handle is never closed and the lock is never released. The OS will eventually clean up when the process dies, but if the ClawTeam process itself is long-running, these leaked locks accumulate.

The `_is_locked()` function (`transport/file.py:40-52`) is a "best-effort" probe that tries to acquire and immediately release the lock. This has a **TOCTOU race**: between the release and the caller's subsequent use, another process could claim the message.

### 3.2 No Message Persistence Guarantee

Messages are files on disk. There is:
- No fsync after write (data could be in OS buffer cache)
- No replication or backup
- No message acknowledgment from the recipient (only from the transport layer)
- If the disk fails, all in-flight messages are lost

### 3.3 Broadcast is Member-List Dependent

The `broadcast()` method (`mailbox.py:70-90`) iterates over `self._transport.list_recipients()`, which reads the inbox directory listing. If a member was added but its inbox directory wasn't created yet (race condition), that member won't receive the broadcast.

---

## 4. Template System

### 4.1 TOML Parsing — No Validation Beyond Schema

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| User templates can override built-in templates (name collision) | MEDIUM | MEDIUM | Unexpected behavior |
| No template versioning or migration | LOW | MEDIUM | Breaking changes on upgrade |
| `{goal}` variable substitution is unescaped | MEDIUM | LOW | Template injection |
| Command field defaults to `["openclaw"]` — hardcoded | LOW | LOW | Won't work without openclaw installed |

**Key Finding — Template Override Without Audit** (`templates/__init__.py:120-140`):

User templates in `~/.clawteam/templates/` take priority over built-in templates. A malicious or accidental user template with the same name as a built-in will silently override it. There is no:
- Checksum verification
- Warning when overriding
- Template signing or provenance tracking

**Key Finding — Variable Substitution is Format-Based** (`templates/__init__.py:65-72`):

The `render_task()` function uses `str.format_map(_SafeDict(...))`. The `_SafeDict` class keeps unknown placeholders intact, which is safe. However, if a user passes a `goal` variable containing `{agent_name}` or other template variables, those will be **recursively substituted** in the next render pass. This is a minor injection risk.

### 4.2 No Template Composition

Templates are flat TOML files. There is:
- No `extends` or `include` mechanism
- No conditional agent inclusion (e.g., "only add reviewer if task count > 5")
- No dynamic task generation from templates

This limits template reusability for complex workflows.

---

## 5. Cross-Cutting Concerns

### 5.1 Authentication & Authorization — Absent

| Risk | Severity | Likelihood | Impact |
|------|----------|------------|--------|
| Any process on the machine can read/write team data | CRITICAL | HIGH | Full system compromise |
| No agent identity verification | HIGH | HIGH | Agent impersonation |
| Config file is world-readable (default umask) | MEDIUM | HIGH | Credential leakage via profiles |

There is **zero authentication** in the current architecture. Any process that can read `~/.clawteam/` can:
- Read all team configurations
- Read/write all task files
- Read/write all inbox messages
- Impersonate any agent by setting `CLAWTEAM_AGENT_NAME` env var

The `identity.py` module provides agent identity via environment variables, but this is **self-reported** — there is no cryptographic verification.

### 5.2 Single-Machine Coupling

The entire system is designed for single-machine operation:
- File-based transport (local filesystem only)
- Tmux backend (local process management only)
- No network communication layer (P2P transport exists but is experimental)

The ROADMAP.md acknowledges this and plans Redis transport for v0.4, but until then, **all agents must run on the same machine**.

### 5.3 Observability — Minimal

- No structured logging (only print statements)
- No metrics collection (task duration, message latency, agent utilization)
- No distributed tracing
- The event log (`events/` directory) is append-only but has no rotation or size limits

### 5.4 Data Durability

All data is stored as JSON files in `~/.clawteam/`:
- No database (no ACID transactions)
- No backup mechanism
- No data migration tooling
- File-level atomic writes (tmp + rename) but no directory-level atomicity

---

## 6. Risk Matrix Summary

| Category | Risk | Severity | Likelihood | Mitigation Priority |
|----------|------|----------|------------|---------------------|
| Security | No authentication/authorization | CRITICAL | HIGH | P0 |
| Security | `--dangerously-skip-permissions` default | HIGH | HIGH | P0 |
| Reliability | Agent zombie (hangs but appears alive) | HIGH | HIGH | P1 |
| Reliability | Task lock leak on agent crash | MEDIUM | HIGH | P1 |
| Performance | O(n) dependency resolution scan | MEDIUM | HIGH | P2 |
| Reliability | Message ordering not guaranteed | LOW | MEDIUM | P2 |
| Security | Template override without audit | MEDIUM | MEDIUM | P2 |
| Scalability | Single-machine coupling | HIGH | N/A (architectural) | P3 |
| Observability | No structured logging/metrics | MEDIUM | HIGH | P3 |
| Durability | No backup/replication | MEDIUM | MEDIUM | P3 |

---

## 7. Recommendations

### Immediate (P0 — Before Any Production Use)

1. **Add authentication**: At minimum, use HMAC signatures on messages. Ideally, use mTLS or SSH keys for agent identity.
2. **Change `skip_permissions` default to `False`**: The current default (`True`) means every spawned agent has unrestricted filesystem access.
3. **Add task `failed` status**: Extend `TaskStatus` enum with `failed` and implement failure detection in `TaskWaiter`.

### Short-term (P1 — Next Sprint)

4. **Implement agent heartbeat**: Agents should periodically write a timestamp file. The registry should check this instead of relying solely on tmux pane state.
5. **Add lock timeout**: Task locks should auto-expire after a configurable duration (e.g., 30 minutes).
6. **Fix lock leak**: Use context managers or `atexit` handlers to ensure claimed message locks are always released.

### Medium-term (P2 — Next Quarter)

7. **Build reverse-dependency index**: Maintain a `task-{id}.json → [dependent-task-ids]` mapping to make resolution O(1) instead of O(n).
8. **Add template checksums**: Sign built-in templates and verify user templates haven't tampered with them.
9. **Implement message ordering**: Use logical clocks (Lamport timestamps) or sequence numbers for deterministic ordering.

### Long-term (P3 — Roadmap Alignment)

10. **Redis transport**: As planned in ROADMAP.md Phase 2, for cross-machine communication.
11. **Structured logging**: Replace print statements with `structlog` or similar.
12. **Data migration tooling**: For schema evolution as the data model changes.

---

## 8. Feasibility Assessment

| Aspect | Assessment | Notes |
|--------|------------|-------|
| **Technical feasibility** | ✅ HIGH | Architecture is sound, code is clean, tests exist |
| **Operational feasibility** | ⚠️ MEDIUM | Requires manual monitoring, no self-healing |
| **Security feasibility** | ❌ LOW | No auth = unsuitable for multi-tenant or networked use |
| **Scalability feasibility** | ⚠️ MEDIUM | Single-machine limit, O(n) scans, file-based I/O |
| **Maintainability** | ✅ HIGH | Clean codebase, good separation of concerns, Pydantic models |

**Bottom line**: ClawTeam v0.2.0 is an excellent **development and PoC tool**. It should NOT be used in production without addressing the P0 security issues. The architecture is well-designed for its current scope (single-machine, single-user, trusted environment) and the roadmap addresses the key limitations.

---

_Report generated by Risk Reviewer — poc-research team_
_Output: reports/V1-risk.md_
