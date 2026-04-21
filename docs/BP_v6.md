# Ephemeral OS – 商业计划书

**商业报告书 · 2026 年 4 月 · CONFIDENTIAL**

## 1. 项目定位：Multi-Agent 原生的 Coding Agent

Ephemeral OS 是全球首个 **Multi-Agent 原生的 AI Coding Agent 系统**。

我们从最硬核、压力最大、可验证性最强的场景——软件工程——切入，用 **瞬态智能体集群 + Overlay 共享沙盒 + 天眼系统 + 生产级 Harness 调度层**，彻底重构 AI 编码范式：

- 用户输入高层自然语言目标，系统自动拆解任务；
- 前端、后端、数据库、测试等专精智能体在同一个实时共享沙盒中**并行**工作；
- 任务完成后，所有智能体瞬态释放，不留冗余状态；
- 长任务、跨仓库、跨天级的复杂工程交付，首次成为可能。

一句话：**Cursor / Claude Code / Devin 是"AI 程序员"，我们是"能随时召唤任意专家的 AI 工程指挥部"。**

## 2. 行业核心痛点

### 2.1 长任务能力断崖，而且 SWE-Evo 最难档长到离谱

今天所有 AI 编程工具——包括 Claude Code、Codex、OpenCode、Cursor、Devin——都卡在同一个瓶颈上：

> **一条指令无法完成一个真正的长任务。**

- SWE-bench / SWE-Evo 等长任务基准上，SOTA 单智能体得分仅 ~25%。
- **SWE-Evo 最难档任务涉及 5000 万行代码、2700 个 P2P 测试**——这已经是中大型企业系统的整体改造，今天没有任何 single-agent 架构能跑通。
- 单 Agent 越往后执行，上下文越脏、幻觉越多、错误累积越快，失败后回滚成本极高。

这不是模型不够强，而是 **single-agent 架构的结构性天花板**。

### 2.2 反直觉的事实：行业现有的"多智能体"反而让分更低

Claude Code / Codex / OpenCode 都已推出 "agent team / subagent" 功能。但 **CooperBench 给出了一个刺眼的数据**：

> **同样一组任务，single-agent 拿 50 分；切到基于 A2A / P2P 通信方式的"multi-agent"，分数掉到 25 分。**

这不是偶然，是架构本身的必然结果——下一章解释为什么。

### 2.3 其它结构性痛点

| 痛点 | 现有工具局限 | Ephemeral OS 方案 |
|------|-------------|------------------|
| 复杂项目周期长 | 单体串行，需反复人工纠偏 | 多智能体并行，自动拆解调度 |
| 高并发上下文混乱 | Agent 各自为战 | **天眼系统**：共享沙盒，环境即上下文 |
| 多 Agent 并发写冲突 | 锁/串行化拖慢吞吐 | **Overlay + OCC**：独立 overlay 层 + 乐观并发控制 |
| 安全合规门槛高 | 隔离弱 | 独立沙盒实例 + 私有化部署 |
| 非技术用户上手难 | 需编程基础 | 自然语言"一键组队" |
| 资源消耗大 | Agent 常驻 | **瞬态生命周期**：用后即弃 |

## 3. 核心理论：为什么"multi-agent 反而更差"？因为它们都是 single-agent 衍生出来的

### 3.1 现状扫描：所有主流产品的"多智能体"都是伪原生

| 产品 | 是否有 agent team / subagent | 底层内核 |
|------|------------------------------|---------|
| Claude Code | 有 | Single-agent 派生 |
| Codex | 有 | Single-agent 派生 |
| OpenCode | 有 | Single-agent 派生 |
| Cursor | 有 background agent | Single-agent 派生 |
| Devin | 支持并行多实例 | Single-agent 派生 |
| 龙虾 / OpenClaw | subagent 能力 | Single-agent (pi-mono) |

它们的共同点：**先有一个 single-agent 内核，再叠一层 orchestration / subagent 包装**。派生出来的每一个 subagent，本质都是一个独立的 single-agent 副本。

### 3.2 CooperBench 为什么掉分？A2A / P2P 通信是致命缺陷

CooperBench 数据（50 → 25）揭示三个结构性问题：

1. **A2A / P2P 通信是"串行转述"**：A 做完一件事必须"说给" B 听。信息损耗严重，每次转述多一次幻觉机会。
2. **没有共享执行环境**：每个 subagent 各跑自己的沙盒，产出无法实时互见。B 对 A 状态的理解永远落后于真实状态。
3. **冲突靠主 agent 仲裁**：产出靠父 agent 串行合并，合并点成为新的瓶颈与幻觉爆发点。

**这就是 single-agent 50 分、派生 multi-agent 25 分的根本原因。**

### 3.3 Ephemeral OS：Multi-Agent 原生 + Overlay + 天眼 + Harness

我们从第一行代码起就在解决这三个问题：

- **共享执行环境（Overlay 沙盒）**：所有 Agent 工作在同一份共享基底上，各自一层 overlay；提交时通过 **OCC 乐观并发控制**校验并合并，冲突区域由语义合并层和裁决 Agent 解决。产出不用"转述"，直接"看见"。
- **天眼系统**：所有 Agent 的动作、产出、依赖关系实时暴露给调度层和其它 Agent，彻底消除 A2A / P2P 通信链路。
- **Harness 调度层**：任务拆解 / 失败重试 / 断点续跑 / 预算控制一体化，让长任务真正跑得下来。
- **Multi-Agent 原生内核**：调度、上下文、记忆、产出收割面向"N 个 Agent 同时存在"建模。

> **别人是"给 single agent 装四条手臂"，所以越加越乱；我们是"组织四个专业的人在同一间共享办公室一起工作，墙上有实时大屏（天眼），桌面有自动版本控制（OCC overlay）"，所以越加越强。**

## 4. 产品与技术优势

### 4.1 瞬态智能体引擎
按需诞生、专注执行、使命完成即消亡；集体智慧在系统层永续沉淀。

### 4.2 Overlay 沙盒：下一代 Git Worktree + OCC 合并

> 这是 Ephemeral OS 最深的护城河之一。它不是一个特性，而是一整层基础设施。

#### 4.2.1 为什么不能用 Git Worktree？

业界最朴素的"多 agent 并行写代码"方案，是给每个 agent 开一个 git worktree。但 worktree 在 multi-agent 场景下有四个致命缺陷：

1. **粒度太粗**：worktree 是整个工作目录的物理拷贝，开销大、启动慢，无法支撑高并发瞬态 Agent。
2. **状态不可见**：worktree 之间互相不可见，必须等 commit + merge 才能感知——退化成串行。
3. **冲突检测只在文本层**：git merge 只看行级 diff，不理解代码语义。
4. **没有运行时观测能力**：worktree 是静态文件视图，天眼系统无从介入。

**Git worktree 是"给人类用的"，不是为高并发 AI Agent 设计的。**

#### 4.2.2 我们的 Overlay 是什么

Overlay 是我们自研的一层 **"瞬态智能体专用的可叠加文件系统 + 并发控制运行时"**——**下一代 Git Worktree**：共享只读基底 + 每个 Agent 一层轻量可写 overlay + OCC 智能合并 + 天眼实时观测。

由四层咬合构成：

**① 文件系统层：分层、瞬态、Copy-on-Write**
- 共享只读 base layer（仓库快照），所有 Agent 共用，零拷贝、零启动开销。
- 每个 Agent 派生独立可写 overlay 层，只记录差异（CoW），秒级创建、秒级回收。
- 文件读取按 overlay → base 顺序穿透，Agent 看到"私有 + 共享"叠加后的完整视图。

**② 并发控制层：OCC 乐观并发控制**
- 多个 Agent 在自己的 overlay 上自由读写、不加锁，最大化并行度。
- 提交时进行 **OCC 校验**：读取过的文件版本号与当前 base 是否一致。
- 一致 → fast-forward 合并到 base；
- 不一致 → 触发语义级冲突解决。
- 彻底摆脱锁带来的串行化损耗，"先做后校验"才是 multi-agent 真正的并发哲学。

**③ 语义合并层：超越行级 diff**
- 普通 git merge 看行；我们看 AST、符号表、语义引用。
- 改同一函数不同语句 → 自动合并；
- 改同一行不同字符 → 轻量裁决 Agent 自动决议；
- 语义冲突（如同一函数签名双向修改） → 触发上层 Agent 重新规划，把冲突打包成最小可裁决 patch。
- 建立在实时代码索引之上，是天眼系统的副产品。

**④ 可观测层：与天眼系统咬合**
- Overlay 的每次读写、版本号变化、OCC 冲突、合并决策，被天眼实时捕获。
- 调度层据此做下一波 Agent 派发；其它 Agent 据此动态感知共享世界最新状态。
- **Overlay 不仅是文件系统，它本身就是 multi-agent 系统的"事件总线"。**

#### 4.2.3 为什么这是护城河

Overlay 同时涉及 **分布式文件系统 + OCC 并发控制 + 语义级代码理解 + 实时可观测性** 四个领域的深度工程能力。任何一个单独做都不难，但**把四件事咬合在同一个运行时里、为 multi-agent 场景做端到端优化**，需要几个季度甚至几年的系统级工程积累。

竞品处境：
- Claude Code / Codex / OpenCode：底层 single agent，根本不需要 overlay，没有动力也没有积累。
- Cursor / Devin：subagent 各跑 worktree 或 docker container，升级到 overlay 等价推倒重来。
- 龙虾（OpenClaw）：pi-mono 单体架构，overlay 对它而言是"为别人造的桥"。

**这条护城河越早挖、挖得越深，竞品越不可能用一两个 release 追上。**

### 4.3 Harness 调度层
任务拆解 / 依赖图 / 波次调度 / 失败重试 / 断点续跑 / 预算控制一体化——single-agent 路线最缺的一块。

### 4.4 模型中立 + 弹性部署
兼容 Claude / GPT / Qwen / DeepSeek / Kimi / Minimax 等主流模型；云端 SaaS + 本地私有化双模式。

**核心优势一句话**：**Multi-agent 原生架构与生产级 Harness 的完美结合，让我们能做 single-agent coding agent 做不了的长任务和高并发任务。**

---

## 5. 长期愿景：从 Coding Agent 演进为通用 General Agent

### 5.1 所有通用 Agent 的底座都是 Coding Agent

无论龙虾、Manus，还是任何冲击"通用 AI agent"的产品，底层做的都是同一件事：

> 把用户的自然语言目标，翻译成一系列可执行的代码/命令/工具调用，在一个沙盒环境里运行、观察、修正、迭代。

调用 API、操作浏览器、清洗数据、跨系统编排、自动化办公——本质都是写代码。**通用 agent 的底座 = coding agent + 工具适配层 + 沙盒**。谁的 coding agent 底座更强，谁就能长成更强的通用 agent。

### 5.2 龙虾（OpenClaw）：通用路径正确，但 pi-mono 底座受限

龙虾代表目前最高水平的通用 agent，已证明这条路线可行。但它有一个从第一天就埋下的瓶颈：

> **龙虾底层是 pi-mono——一个单体 coding agent。**

两个致命短板：

1. **单体上下文天花板**：所有规划、决策、执行、反思都挤在一个 agent 的上下文里。任务一长上下文就脏，幻觉就来。
2. **Multi-agent 协同羸弱**：派生出的 subagent 仍是独立 single-agent 副本，没有共享沙盒、没有实时可见性、没有统一调度层——**信息损耗严重，并行度极低**。

**龙虾的"多 agent"是"派生型多 agent"，不是"原生型多 agent"。**

### 5.3 我们的机会：Multi-Agent 原生底座天然适合长成"加强版龙虾"

| | 龙虾（pi-mono） | Ephemeral OS（multi-agent 原生） |
|---|---|---|
| 底层 coding 内核 | Single agent | **Multi-agent 原生** |
| 上下文 | 单体，易脏 | 每个瞬态 Agent 上下文永远干净 |
| 并行度 | 伪并行 | **真并行（Overlay + OCC + 天眼）** |
| 长任务稳定性 | 受限于单体边界 | **架构层面突破** |
| 通用任务覆盖 | 高 | **对等，且可横向扩展** |

**龙虾能做的通用任务我们都能做；龙虾做不了的长任务和高并发任务，只有我们能做。**

### 5.4 三步走：从 Coding Agent 到通用 Agent OS

**Step 1（2026）**：以编码场景为锚，打磨 multi-agent 原生底座；SWE-Evo 从 ~25% 突破到 50%+。

**Step 2（2026 Q4 - 2027）**：抽象 **Universal Workspace**——文件、浏览器、终端、API、第三方工具统一为可 overlay、可观测、可回滚的资源。任务协议从"写代码"泛化到"完成任意数字任务"。

**Step 3（2028+）**：插件/技能市场 + 垂直行业方案（金融/医疗/制造/政务），成为**通用智能操作系统**。

---

## 6. 商业模式与下一步：主攻复杂长任务市场

整个 agent 行业卡在"做不了长任务"上，这正是我们的商业化楔子：

- **企业级复杂长任务交付**：跨仓库重构、系统迁移、合规改造、存量现代化、长周期调研、运营自动化。
- **结果交付式计价**：按任务结果而非 token 计费，ARPU 显著高于订阅制。
- **私有化部署**：金融、政务、医疗等合规敏感场景。
- **SaaS + API**：覆盖个人开发者、中小团队、企业弹性调用。

| 年份 | 订阅 | API / 按量 | 企业级长任务 & 私有化 | 人均 ARPU |
|------|------|-----------|--------------------|-----------|
| 2026 | 70% | 20% | 10% | $150 |
| 2027 | 55% | 25% | 20% | $220 |
| 2028 | 40% | 25% | 35% | $300 |

## 7. 市场机会

- Omdia：企业级 Agentic AI $15B (2025) → $418B (2030)，CAGR ~95%。
- Mordor Intelligence：Agentic AI 开发平台 $107.5B → $512.6B，CAGR 36.67%。
- Grand View Research：AI Agents $54B → $503B，CAGR 45.8%。
- 自动代码开发是 Omdia 认定最大应用场景，2030 年 $82B。
- Cursor / Devin / Claude Code 年化收入已破 $31 亿——三年前这个市场还不存在。

## 8. 竞争分析

| 维度 | **Ephemeral OS** | Claude Code / Codex / OpenCode | Cursor / Devin | 龙虾 / OpenClaw |
|------|------------------|------|------|------|
| 当前定位 | **Multi-Agent 原生 Coding Agent** | 编码 Single-Agent | 编码 Single-Agent | 通用 Single-Agent (pi-mono) |
| agent team / subagent | **原生多智能体** | 有（派生） | 有（派生） | 有（派生） |
| 架构本质 | **多智能体原生** | Single + subagent | Single + 多实例 | Single + subagent |
| A2A / P2P 通信 | **无（共享沙盒 + 天眼取代）** | 有（信息损耗） | 有 | 有 |
| CooperBench 类行为预期 | **并行加分** | 掉分 | 掉分 | 掉分 |
| 多 Agent 共享文件系统 | **Overlay（CoW + 分层）** | 无 / 各自 worktree | docker / worktree | 无 |
| 并发控制机制 | **OCC + 语义合并** | git merge / 串行 | git merge | 无 |
| 中间产物可见性 | **实时（天眼 + Overlay）** | 不可见 | 不可见 | 不可见 |
| 工程深度 | **文件系统 + 并发 + 语义 + 观测 四层咬合** | 无 | 浅 | 无 |
| 长任务能力 | **为长任务而生** | 受限 | 受限 | 受限 |
| 未来可演进为通用 Agent | **是** | 否 | 否 | 已是，但受 pi-mono 限制 |

**核心壁垒**：竞品在 multi-agent 这条路上不是"领先了半步"，而是"走错了方向"。我们不是在错路上跑得更快，而是换了一条路。

## 9. 核心团队（待补充）

## 10. 发展规划

| 阶段 | 时间 | 里程碑 |
|------|------|--------|
| Phase 1 | 2026 Q2-Q3 | Multi-agent coding 内核封闭测试；SWE-Evo 从 ~25% 突破至 50%+ |
| Phase 2 | 2026 Q4 | 开发者预览版；抽象 Universal Workspace |
| Phase 3 | 2027 Q1-Q2 | 商用版发布；通用任务覆盖对标龙虾；企业长任务交付上线 |
| Phase 4 | 2027 Q3-Q4 | 插件/技能市场雏形；大客户拓展 |
| Phase 5 | 2028+ | 垂直行业方案；成为通用智能操作系统 |

## 11. 财务预测摘要（示意）

| 项目 | 2026E | 2027E | 2028E |
|------|-------|-------|-------|
| 总收入 | $0.1M | $1.8M | $15.0M |
| SaaS 订阅 | $0.08M | $1.0M | $6.0M |
| API 按量 | $0.02M | $0.4M | $3.8M |
| 企业长任务 & 私有化 | — | $0.4M | $5.2M |
| 毛利率 | 65% | 72% | 78% |

## 12. 风险与应对

| 风险 | 应对 |
|------|------|
| 通用 agent 竞赛白热化（龙虾、Manus 等） | multi-agent 原生底座形成架构代差；长任务差异化切入 |
| 多智能体稳定性与幻觉 | Overlay + 天眼 + Harness 从架构层降低状态不一致 |
| 付费转化率 | Freemium + 企业级长任务高 ARPU 双轮驱动 |
| 合规 | 原生沙盒 + 私有化部署 |

## 13. 结论

AI 编程的行业现状可以用三个数字概括：
- **SWE-Evo SOTA ~25%**——single-agent 的长任务天花板。
- **CooperBench 50 → 25**——派生型 multi-agent 不但没加分，反而严重扣分。
- **SWE-Evo 最难档：5000 万行代码、2700 P2P 测试**——没有一个现有产品能够承接。

Claude Code、Codex、OpenCode、Cursor、Devin、龙虾——所有打着"多智能体"旗号的产品，底层都是 single-agent 派生。它们的 multi-agent 越强调，分数反而越低——这是架构决定的，不是努力能改的。

Ephemeral OS 从第一行代码起就是 **multi-agent 原生 + Overlay 共享沙盒（下一代 Git Worktree + OCC + 语义合并 + 天眼可观测）+ 生产级 Harness**。这套底座：

- **短期**：在 SWE-Evo 等长任务基准上打破 25% 的行业天花板；
- **中期**：以更强的 coding 底座演进为通用 agent，覆盖龙虾能做的所有场景；
- **长期**：成为 Agentic AI 时代的通用智能操作系统。

在 Agentic AI 从 $15B 走向 $418B 的五年窗口里，**拥有真正 multi-agent 原生底座的那个玩家，会拿走最大一块蛋糕**。

我们相信那会是 Ephemeral OS。
