# R2: OpenClaw Plugin 生態系統分析 — 工作流程視角

> **Researcher**: researcher-workflow
> **Team**: poc-v3-plugins-phase1
> **Date**: 2026-03-26
> **Core Question**: OpenClaw 嘅 Plugin 生態系統發展現狀、分類、架構模式

---

## Executive Summary

OpenClaw 嘅 Plugin 生態系統正喺度 **爆發式成長**。從 GitHub 搜索結果嚟睇，已經有 **20+ 個獨立 plugin project**，涵蓋通訊渠道、安全中間件、記憶系統、MCP 適配器等多個類別。生態系統嘅核心驅動力係 OpenClaw 嘅 **pluggable architecture**——每個 plugin 可以註冊 capabilities（channel、provider、tool 等），令第三方開發者可以擴展平台功能。

**三大核心發現：**

1. **通訊渠道 plugin 係最大類別**：WeCom、DingTalk、WeChat、Zalo 等中國/東南亞 IM 平台佔大多數
2. **安全同穩定性 plugin 開始出現**：Shellward（8 層防禦）、Stability（anti-drift）代表咗生態嘅成熟度提升
3. **Skills 生態遠大於 Plugin 生態**：awesome-openclaw-skills 有 42K stars 同 5,400+ skills，遠超 plugin 嘅規模

---

## 1. Plugin 分類與分析

### 1.1 通訊渠道 Plugin（Channel Plugins）

| Plugin | 功能 | Stars | Forks | 最近更新 |
|--------|------|-------|-------|---------|
| **sunnoy/openclaw-plugin-wecom** | 企業微信 AI 機器人，支援流式輸出、動態 Agent 管理、群聊集成 | 647 | 76 | 2026-03-26 |
| **DingTalk-Real-AI/dingtalk-openclaw-connector** | 钉钉機器人/DEAP Agent 連接，支援 AI Card 流式響應 | 1,916 | 159 | 2026-03-26 |
| **WecomTeam/wecom-openclaw-plugin** | 企業微信 channel plugin | - | - | 2026-03-26 |
| **11haonb/wecom-openclaw-plugin** | WeCom channel plugin | - | - | 2026-03-16 |
| **CzsGit/wechat-openclaw-plugin** | 微信通路插件（掃碼登錄 + AGP WebSocket + HTTP webhook） | - | - | 2026-03-16 |
| **darkamenosa/openzalo** | Zalo Personal messaging（越南） | 31 | 6 | 2026-03-26 |

**工作流分析：**
- 呢啲 plugin 通常實現咗 OpenClaw 嘅 **Channel capability**（`api.registerChannel(...)`）
- 支援 **流式輸出**（streaming）係共同特色，令用戶體驗更自然
- **群聊集成** 係複雜場景，需要處理 mention、reply、白名單等

### 1.2 協議與互操作 Plugin

| Plugin | 功能 | Stars | Forks | 最近更新 |
|--------|------|-------|-------|---------|
| **win4r/openclaw-a2a-gateway** | A2A（Agent-to-Agent）協議 v0.3.0 實現，雙向 agent 通訊 | 337 | 59 | 2026-03-26 |
| **androidStern-personal/openclaw-mcp-adapter** | 將 MCP server tools 暴露為 native agent tools | 33 | 12 | 2026-03-25 |

**工作流分析：**
- **A2A Gateway** 係最關鍵嘅互操作 plugin——令 OpenClaw 可以同其他 A2A-compliant agent 通訊
- **MCP Adapter** 令 OpenClaw 可以使用任何 MCP server 提供嘅工具，擴展咗 tool 生態
- 兩者都係 **跨系統協作** 嘅基礎設施

### 1.3 安全與穩定性 Plugin

| Plugin | 功能 | Stars | Forks | 最近更新 |
|--------|------|-------|-------|---------|
| **jnMetaCode/shellward** | AI Agent 安全中間件：8 層防禦、DLP 數據流、prompt injection 偵測 | 48 | 5 | 2026-03-24 |
| **CoderofTheWest/openclaw-plugin-stability** | Agent 穩定性框架：Shannon entropy 監控、confabulation 偵測、loop guards | 8 | 4 | 2026-03-23 |

**工作流分析：**
- **Shellward** 代表咗 **security middleware** 嘅模式——喺 agent 同外部世界之間加一層防禦
- **Stability** 代表咗 **observability + guardrails** 嘅模式——監控 agent 行為，偵測 drift 同 confabulation
- 兩者都係 **治理工作流** 嘅組件

### 1.4 記憶與持續性 Plugin

| Plugin | 功能 | Stars | Forks | 最近更新 |
|--------|------|-------|-------|---------|
| **CoderofTheWest/openclaw-plugin-continuity** | Infinite Thread：持續、智能嘅 agent 記憶 | 13 | 4 | 2026-03-21 |

**工作流分析：**
- 同 memory-lancedb-pro 競爭，但定位唔同——continuity focus 喺 **thread continuity** 而唔係 **vector search**
- 代表咗記憶系統嘅另一種設計哲學

### 1.5 工具與搜索 Plugin

| Plugin | 功能 | Stars | Forks | 最近更新 |
|--------|------|-------|-------|---------|
| **5p00kyy/openclaw-plugin-searxng** | SearXNG 網頁搜索：隱私保護嘅自託管搜索 | 9 | 3 | 2026-03-22 |

### 1.6 其他 Plugin

| Plugin | 功能 | Stars | Forks | 最近更新 |
|--------|------|-------|-------|---------|
| **13rac1/openclaw-plugin-claude-code** | 喺 Podman/Docker 容器中運行 Claude Code | 14 | 6 | 2026-03-23 |
| **pepicrft/openclaw-plugin-vault** | HashiCorp Vault 整合 | - | - | 2026-02-10 |
| **luckybugqqq/claw-sama** | VRM Avatar 桌面寵物 | - | - | 2026-03-26 |
| **Skyzi000/openclaw-open-webui-channels** | Open WebUI Channels 連接 | - | - | 2026-03-26 |
| **FLock-io/openclaw-plugin-flock** | FLock 去中心化 AI 訓練 | - | - | 2026-03-01 |
| **redf0x1/camofox-browser** | 反偵測瀏覽器（AI agent 用） | - | - | 2026-03-26 |

---

## 2. 生態系統聚合項目

### 2.1 Awesome Lists

| 項目 | Stars | Forks | 內容 |
|------|-------|-------|------|
| **VoltAgent/awesome-openclaw-skills** | **42,158** | 4,011 | 5,400+ skills，從官方 Skills Registry 篩選同分類 |
| **ThisIsJeron/awesome-openclaw-plugins** | 8 | 9 | Plugin 精選列表 |
| **composio-community/awesome-openclaw-plugins** | - | - | 另一個 plugin 精選列表 |
| **hesamsheikh/awesome-openclaw-usecases** | - | - | 使用案例集合 |

**關鍵洞察：** Skills 生態（42K stars）遠大於 Plugin 生態（<2K stars），代表 OpenClaw 嘅 **skill-first** 策略成功。

### 2.2 官方生態

| 項目 | 功能 |
|------|------|
| **openclaw/clawhub** | 官方 Skills Registry（clawhub.com） |

---

## 3. Plugin 架構模式

### 3.1 Capability Model（能力模型）

OpenClaw plugin 可以註冊以下 capabilities：

| Capability | Registration Method | 範例 |
|------------|-------------------|------|
| **Text Inference** | `api.registerProvider(...)` | openai, anthropic |
| **Speech** | `api.registerSpeechProvider(...)` | elevenlabs |
| **Media Understanding** | `api.registerMediaUnderstandingProvider(...)` | openai, google |
| **Image Generation** | `api.registerImageGenerationProvider(...)` | openai, google |
| **Web Search** | `api.registerWebSearchProvider(...)` | google |
| **Channel / Messaging** | `api.registerChannel(...)` | wecom, dingtalk |

### 3.2 Plugin Shapes（形狀分類）

| Shape | 描述 | 範例 |
|-------|------|------|
| **plain-capability** | 註冊一種 capability | mistral（provider only） |
| **hybrid-capability** | 註冊多種 capabilities | openai（text + speech + media + image） |
| **hook-only** | 只註冊 hooks，冇 capabilities | 舊式 plugin |
| **non-capability** | 註冊 tools/commands/services/routes，冇 capabilities | 大部分社區 plugin |

### 3.3 Plugin Manifest

每個 plugin 必須有 `openclaw.plugin.json`：
- Plugin 身份（name、version、description）
- Config schema（JSON Schema，用於驗證）
- Auth metadata
- UI hints

---

## 4. 工作流數據流分析

### 4.1 Channel Plugin 數據流

```
外部 IM 平台（WeCom/DingTalk/WeChat）
    ↓
Channel Plugin（接收訊息）
    ↓
OpenClaw Gateway（路由）
    ↓
Agent（處理）
    ↓
Channel Plugin（發送回覆）
    ↓
外部 IM 平台
```

### 4.2 A2A Gateway 數據流

```
OpenClaw Agent A
    ↓
A2A Gateway Plugin（編碼為 A2A 協議）
    ↓
網絡傳輸
    ↓
外部 Agent（A2A-compliant）
    ↓
A2A Gateway Plugin（解碼回 OpenClaw 格式）
    ↓
OpenClaw Agent A
```

### 3.3 MCP Adapter 數據流

```
OpenClaw Agent
    ↓
MCP Adapter Plugin（攔截 tool call）
    ↓
MCP Server（執行工具）
    ↓
MCP Adapter Plugin（返回結果）
    ↓
OpenClaw Agent
```

---

## 5. 來源可信度評估

| 來源 | 類型 | 可信度 | 備註 |
|------|------|--------|------|
| GitHub repo metadata | Primary source | **High** | 直接來自項目 |
| OpenClaw docs/plugins/ | 官方文檔 | **High** | Plugin 架構權威來源 |
| gh search repos | GitHub API | **High** | 即時數據 |
| awesome-openclaw-skills | 社區聚合 | **Medium** | 可能有 bias |

---

## 6. 研究限制

1. **冇深入閱讀每個 plugin 嘅 source code**：分析基於 README 同 metadata
2. **冇測試 plugin 嘅實際功能**：基於文檔分析
3. **Stars 同 forks 可能有偏差**：新項目可能 stars 少但質量高
4. **冇覆蓋所有 plugin**：GitHub 搜索可能有遺漏

---

## 7. 關鍵發現摘要

| # | 發現 | 證據來源 |
|---|------|---------|
| 1 | 通訊渠道 plugin 係最大類別（WeCom、DingTalk、WeChat） | gh search repos |
| 2 | DingTalk connector 最受歡迎（1,916 stars） | GitHub metadata |
| 3 | A2A Gateway 係最關鍵嘅互操作 plugin（337 stars） | GitHub metadata |
| 4 | 安全 plugin 開始出現（Shellward、Stability） | gh search repos |
| 5 | Skills 生態遠大於 Plugin 生態（42K vs <2K stars） | GitHub metadata |
| 6 | Plugin 有 4 種 shape：plain/hybrid/hook-only/non-capability | architecture.md |
| 7 | Channel plugin 實現 `api.registerChannel(...)` | architecture.md |
| 8 | Plugin manifest 必須有 `openclaw.plugin.json` | manifest.md |
| 9 | 中國/東南亞 IM 平台係主要嘅 channel plugin 來源 | gh search repos |
| 10 | 社區正在構建 security middleware 模式（Shellward） | GitHub metadata |

---

_Report saved: 2026-03-26 by researcher-workflow (poc-v3-plugins-phase1)_
