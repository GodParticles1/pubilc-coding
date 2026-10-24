# Green Migration — Leader 项目状态与迭代入口

> 维护日期：2026-10-24。**项目代码与发布权威是本仓库；Issue/PR 的最新状态优先于本页的时间点摘要。** 本页是当前项目级管理入口，不新增跨项目治理系统，不自动授权生产写入。

## 产品目标与非谈判约束

**可靠性为底线，以可复现实测持续缩短端到端迁移时间和实际业务不可用窗口。** 不允许用弱化冷态、一致性、镜像校验、回滚或业务验收来交换速度。下一代架构保持 `DESIGN_UNRESOLVED`，不预先指定 Shell、Go、Rust、Python 或并发/存储算法。

当前最小交付是：源机身份和原状态记录 → 停写者确认与冷态镜像 → relay/本地或已挂载归档的完整性封存 → 源机原状态恢复 → 兼容目标预检和可信回滚点 → 受约束的恢复 → 服务/依赖/业务分层验收。**镜像完成、服务 Running、业务可用为不同结论。**

## 单一权威与责任

| 事项 | 权威和执行边界 |
| --- | --- |
| 代码、版本、需求、决策、测试与发布候选 | `GodParticles1/pubilc-coding/projects/green-migration/`；本页导航，GitHub Issue 记录任务和证据，PR 记录可审查的实现。`main` 不是未合并 RC 的源码镜像。 |
| 真实现场 Jenkins INLINE/原始脚本/历史镜像 | 由操作者控制的**私有原件**；未通过字节、配置映射与敏感信息检查前，不宣称已经存入 Git。含凭据的原始 `config.xml`、客户数据、镜像及未经许可的 ARM 二进制不得进入 public Git。私有原件也必须有访问控制，禁止用普通 Git 代替密钥管理。 |
| 领导与验收 | Leader 持有范围、优先级、冲突处置、风险门禁、独立审查和发布决策；一个范围内只有一个 Writer；Reviewer 独立报告已验证证据；现场执行者仅在独立的授权边界内操作。Writer 自测不能自授 ACCEPT。 |
| 会话、历史、长期恢复 | 私有 `GodParticles1/chat-memory/projects/green-migration/ENTRY.md` 及相应 checkpoint。这里的当前状态与源码提交来自本仓库；历史摘要不替代 live HEAD。 |
| xRocket 产品、DB/MQ、监督/HA、一致性知识 | `GodParticles1/xrocket-product-knowledge` 的适用版本和证据。不能将静态模型或其他现场样本直接提升为当前业务验收。 |

## 源码基线与保全状态

| 基线 | 精确锚点 / 现状 | 状态和后续动作 |
| --- | --- | --- |
| 历史 V1 / V27 原件 | `README.md` 记录原 `green_migration_tool.sh` SHA256 `362ce75ae07d9ccb4e9737c31cbae40c30ca835ef09bcdc78cb6ababc7c86b2f`；`v1-reference/` 是脱敏、截断的历史文本。 | **REFERENCE_ONLY**；不能运行，也不可补猜缺失尾部。完整可信脱敏版本的恢复由 [Issue #2](https://github.com/GodParticles1/pubilc-coding/issues/2) 追踪。 |
| 真实 Jenkins V1 基线 | 已确认 `green-migration-test` 的定义是 INLINE Pipeline；工作区 `old.groovy` / `new.groovy` 不自动等于实际定义。操作者报告生命周期改动前曾成功；没有完整的对应源字节和当次构建映射。 | **PRIVATE_SOURCE_INTAKE_PENDING**；仅采集该 Job、匹配原始脚本和必要构建元数据，按 SHA 和时间确认“历史可用 A / 当前现场 B”，保留机密原件于操作者私域。见 [Issue #4 的现场基线回执](https://github.com/GodParticles1/pubilc-coding/issues/4#issuecomment-6358430379)。 |
| V1.1 RC2 候选 C | [Draft PR #5](https://github.com/GodParticles1/pubilc-coding/pull/5) `d558ef9e5d171075f678f5c7f8e63ce9225c6d4a`；已验证测试源码 `39df25173ba6011268b376a855fbd7f39646b495`。 | **OFFLINE_REVIEW=PASS / FIELD=HOLD**；CI Linux 174/174，独立审查关闭原 3 个 P1 的离线范围；没有实机同架构迁移、离线恢复、DB/MQ 和业务验收。不得合并/部署为正式发布。 |
| 历史 Go v2 rebuild.2 | `v2-status.md` 记录外部工程 ZIP `green-migration-v2-rebuild2-engineering.zip` SHA256 `d58142e8960e4e1f4510dc818aa34f5e14a47379ad0fd1197fd0fc3ab3145ea5`。 | **SOURCE_NOT_PUBLISHED**；不是已丢失的 dev.3，也不能作为本轮未来架构选型结论。接收原包后先验证、脱敏、按单独 PR 保全源码。 |

基线保全采用最少的可追溯记录：原始来源和保管者、准确 SHA256、源码版本与实际 Jenkins definition 关联、时间/拓扑作用域、脱敏副本 SHA256、可运行性和验收层级。**先拿到并校验真实字节，再发布脱敏源码**；不从 SHA、文件名或聊天描述重建源码。对客户数据和秘密，只记录经过脱敏的 provenance，不上传正文。

## 正式迭代节奏

默认按 **一周一个管理迭代** 规划和复盘，但不因日历到期自动运行 Jenkins、合并 PR 或做生产变更。每一轮只设一个可验证的主交付目标；保留研究线独立推进，不让 #6 阻塞 #4。

1. **选择目标**：Leader 以未完成 Issue、上轮 evidence/风险和实际人力/环境为输入，确定目标、拒绝条件、验收 oracle 与唯一 Writer；需要产品取舍才交用户决策。
2. **开发**：基于可追溯基线在 topic branch 修改；PR 链接 Issue、准确 HEAD、必要安全反例、离线测试、兼容与回滚路径。禁止为同一失败连续堆积多个无人验收的现场热修包。
3. **独立门禁**：Reviewer 区分 `confirmed_static`、离线控制实验、CI、现场和业务级证据。源码字节改变后按影响重验，不能继承不适用的旧 PASS。
4. **资格与交付**：先隔离同架构环境、可证明离线恢复根和回滚；再由现场操作者按明确批准的 bounded plan 执行。缺冷态/身份/恢复源则 HOLD。独立验收后再决定 merge/release/tag。
5. **收口与记忆**：当决策、版本、证据或风险实质变化时，同轮更新 owning Issue/PR 和 `chat-memory` checkpoint/入口，记录 commit、结果、未决事实与下一最小动作；无新事实不为周报而造提交。

## 当前迭代队列（以 Issue 的实时状态为准）

| 优先顺序 | 可交付里程碑 | 退出条件 |
| --- | --- | --- |
| **M0 — 项目管理落地** | 建立本页、恢复入口和开发/审核/归档关系。 | 文档 PR 可审查，记忆入口反向关联当前 native authority；不以此宣称 RC 发布。 |
| **M1 — 唯一可信现场基线** | [Issue #4](https://github.com/GodParticles1/pubilc-coding/issues/4)：私有采集真实 INLINE 及匹配脚本，完成历史成功 A / 当前现场 B / RC2 C 三方差异，按需修订**同一** PR #5。 | A/B/C 的来源 SHA/运行版本和有决策价值的差异闭合；涉密不入公开仓；#215 恢复/会话/进程的未决事实不被猜测抹去。 |
| **M2 — V1.1 资格与现场验收** | 保持 PR #5 Draft/HOLD，逐项验证真实镜像、ACL/xattr/链接、独立离线根/回滚容量、ARMv7/DB/MQ/writer、实际 SSH/WebDAV、启动和业务 oracle。 | 同架构受控端到端成功、故障恢复/回滚闭合、独立签收与明确上线授权；此前 V1.1 不 CLOSED。 |
| **M3 — R1 工程测量** | [Issue #6](https://github.com/GodParticles1/pubilc-coding/issues/6)：先基于同等正确性做合成 E1 阶段 profiling，再按主瓶颈触发预复制 E2 / 有界优化。 | 报告实验条件、原始指标、反例、失败、效应和不确定性；没有比较数据不选最终语言和架构。 |

## 当前阻塞与已接受决策

- **D-20261024-01（代码归属）**：不立即新建 `green-migration-tool` 仓库。现有公开仓的项目目录、PR #3/#5 和 Issue #2/#4/#6 是连续权威；迁仓只有在独立发布/权限/维护分离的明确需求出现时单独评估，并迁移历史和引用，不产生第二套活跃代码权威。
- **D-20261024-02（源码保全）**：公开仓只保存来源可核、可合法公开的脱敏源码与测试；私有原件/镜像由操作者受控保存。`SOURCE_BYTES_PENDING` 不得写成已完成版本归档。
- **D-20261024-03（发布）**：沿用 RC2 和现有 Writer 分支，不新开平行修复线；未知会话/锁/写入者保持 fail-closed；不自动合并 PR #5，不触发真实停机或迁移。
- **D-20261024-04（研究）**：产品可靠性与迁移停机优化目标沿用 #6；R0 已交付，R1 设计可先在隔离合成环境执行，但不可将 Mock/CI 冒充生产结果。

## 下一个最小动作

操作者按既定单 Job 范围取得 Jenkins `green-migration-test` 的**私有源包和安全元数据**，在受控本地环境提供给现有 #4 Writer；核原始 SHA、真实 INLINE 定义与构建历史后做 A/B/C 三方 diff。无须重采所有 Jenkins Job、上传客户镜像或公开凭据。Leader 随证据决定是否对 RC2 做一处有界修订并安排资格验证。

记忆接入遵守 `chat-memory` 的 `ENTRY.md` 和持久化规则；Memory Control Layer 可提供经过授权的项目发现/导航，但其只读、派生、配置/客户端激活边界与 native ownership 分离，不能被本页文字当作已经上线的自动长期记忆。
