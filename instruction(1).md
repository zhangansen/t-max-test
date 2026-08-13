# TerminalBench 3 高难任务合成与 Rollout 实操测试

## 测试信息

- **时长：** 2 小时，严格计时
- **形式：** 独立实操，结束时提交代码、任务、轨迹和报告
- **参考资料：** [T-Max: A Simple Recipe for Terminal Agents](https://arxiv.org/pdf/2606.23321)、[TerminalBench 3](https://github.com/harbor-framework/terminal-bench)

## 背景

我们希望围绕 TerminalBench 3 的 70 多个 seed case，分别生成难度高、区分度强、质量可靠的新任务。生成后的任务将交给 Claude Fable 5 执行，采集 rollout 轨迹，并进一步加工为 SFT 数据。

T-Max 提出了一条终端任务合成路线：对领域、技能、persona、语言、任务复杂度、命令复杂度、fixture 和 verifier 等维度进行组合采样，生成完整环境，再通过环境构建、可执行性验证和 rollout 结果进行过滤与难度标定。

本测试不考察你能否复述论文，而是考察你能否在两小时内真正跑通这条管线，并说明如何将其扩展为日产 10,000 道有效任务的生产系统。

## 任务目标

请使用 T-Max 的思路，为 TerminalBench 3 生成一批新任务，并尽可能使用 Claude Fable 5 运行 rollout。

你需要完成两个层级的目标。

### Level 1：跑通最小闭环

这是本测试的基本要求。至少完成：

1. 一道完整、可执行的 TerminalBench 3 任务；
2. 一次环境构建；
3. 一次成功的 Oracle 验证；
4. 一次失败的 Nop 验证；
5. 一次真实的 Claude Fable 5 rollout，并保存完整轨迹；
6. 对该任务的难度、区分度、质量和训练价值作出判断。

Claude Fable 5 的 rollout 可以成功或失败，但必须来自真实模型运行，不能手写、补写或伪造。成功且满足数据完整性要求的轨迹更适合作为 SFT 候选数据。

### Level 2：扩大有效产出

跑通最小闭环后，继续生成尽可能多的有效任务，并为尽可能多的任务运行 rollout。

我们不按生成的目录数或题目文案数计算产量，只统计能够实际构建、验证和复现的有效任务。

同时，你需要说明：如果目标是每天稳定生产 10,000 道有效任务，你当前的方法是否支持？需要如何自动化、并行化和重构？

## T-Max 到 TerminalBench 3 的迁移

你可以自行设计技术路线。建议把每个 TerminalBench 3 seed case 当作任务族或能力锚点，再组合采样以下维度：

| 维度 | 示例 |
|---|---|
| Seed case / 能力族 | 系统运维、安全、数据处理、科学计算 |
| Domain / primitive skills | 依赖解析、协议调试、逆向、数据校验 |
| Persona | SRE、事件响应人员、量化研究员、生信工程师 |
| Language | Python、C、Bash、Rust、Go、多语言 |
| Task complexity | short、moderate、complex、intricate |
| Command complexity | Bash；Bash + 代码；Bash + 代码 + 系统服务 |
| Fixture | 文本、图片、音频、视频、二进制、vendored package、多服务环境 |
| Verifier | exact、metric threshold、adversarial corpus、fuzz equivalence、multi-protocol |

不要求机械使用所有维度，但需要体现对多样性、难度控制和 verifier 设计的理解。

难度不能只靠扩大输入、缩短时间、禁用网络、隐藏必要信息或堆叠无意义步骤获得。优秀任务应来自真实且有价值的工作，难点应落在规划、环境探索、跨工具协作、专业知识、迭代调试或长程执行上。

## 任务实现要求

每道任务应尽量符合当前 TerminalBench 3 / Harbor task format：

```text
tasks/<task-name>/
├── instruction.md
├── task.toml
├── environment/
│   ├── Dockerfile
│   └── ...
├── solution/
│   ├── solve.sh
│   └── ...
└── tests/
    ├── Dockerfile
    ├── test.sh
    └── ...
```

一道有效任务至少应满足：

- 目录和必需文件完整，能够被 Harbor 识别；
- 环境能够成功构建；
- Oracle solution 获得满分；
- Nop agent 不能通过验证；
- instruction 清楚描述预期产物、绝对路径、格式和约束；
- verifier 检查任务的核心行为，而不只是表面文件或固定字符串；
- verifier 在隔离环境中运行，不向 agent 泄漏 solution、隐藏答案或测试逻辑；
- 不依赖偶然网络状态、竞态或基础设施错误制造难度；
- 相比 seed case 存在能力迁移或组合，而不是简单改名、换数字或同义改写。

建议保存以下命令的输出、退出状态和日志位置：

```bash
harbor check "tasks/<task-name>" -m <review-model>
harbor run -p "tasks/<task-name>" -a oracle
harbor run -p "tasks/<task-name>" -a nop
harbor run -p "tasks/<task-name>" \
  -a claude-code \
  -m anthropic/claude-fable-5 \
  --ak reasoning_effort=max
```

如果现场环境使用不同的模型标识、agent 名称或参数，请使用面试方提供的实际配置，并在报告中记录差异。

## Rollout 轨迹要求

每条提交的 rollout 至少应能够追溯到：

- task ID、任务版本或内容哈希；
- 模型、agent harness 和关键运行参数；
- 完整输入、assistant 输出、工具调用、工具返回和终止原因；
- verifier reward 及子项结果；
- wall-clock time、turn 数和 token 数；
- 成功、失败、超时或基础设施错误标签；
- 原始轨迹文件路径和复现命令。

轨迹不得包含 API key、访问令牌或其他秘密。存在无法解析的工具调用、缺失工具返回、日志截断、人工补写或基础设施错误时，不应标记为 SFT-ready。

请明确区分：

- **运行完成：** 模型真实执行并产生结构完整的轨迹；
- **任务成功：** verifier 判定任务完成；
- **SFT-ready：** 轨迹通过完整性、安全性和格式检查；
- **高价值失败轨迹：** 运行有效但任务未通过，可用于错误分析或难度标定，不应伪装为成功样本。

## 难度与价值分析

不要只用“模型没有做出来”证明任务很难。对重点任务，请至少说明：

- 预期专家耗时、所需技能和真正的 difficulty crux；
- 模型失败是否来自能力不足，而不是 instruction、环境或 verifier 的缺陷；
- 任务如何区分不同模型、推理预算、harness 或采样次数；
- Oracle/Nop 和真实 rollout 提供了什么难度证据；
- 任务覆盖了什么新的领域、技能或工具链；
- 成功和失败 rollout 分别具有什么训练或分析价值。

如时间允许，可通过重复采样、不同推理预算或不同能力模型报告 pass@1、pass@k 或成功率。过易任务会迅速饱和，始终零奖励的任务也可能缺少当前阶段所需的学习信号。

## 建议时间安排

| 时间 | 期望状态 |
|---|---|
| 0～20 分钟 | 选择 seed case，确定首个任务签名和 verifier |
| 20～60 分钟 | 完成首道任务，跑通 build、Oracle 和 Nop |
| 60～90 分钟 | 跑出第一条 Claude Fable 5 轨迹并检查完整性 |
| 90～110 分钟 | 批量生成、验证更多任务或增加 rollout 覆盖 |
| 110～120 分钟 | 冻结结果，完成报告和规模化方案 |

如果 45 分钟时仍未跑通最小闭环，应主动缩小首题范围，优先获得端到端证据。

## 最终交付物

请按以下结构提交：

```text
submission/
├── tasks/                    # 所有生成任务
├── generator/                # 生成脚本、prompt、taxonomy、采样配置或模板
├── rollouts/
│   ├── raw/                  # 原始轨迹
│   └── normalized/           # 规范化轨迹；如已实现
├── logs/                     # build、Oracle、Nop、模型运行日志
├── REPORT.md                 # 结果报告
└── SCALE_PLAN.md             # 日产 10,000 题规模化方案
```

### REPORT.md

至少包含以下汇总表：

| task_id | seed case | sampled signature | build | oracle | nop | Fable trials | success | SFT-ready | time/cost | evidence path |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|

并回答：

1. 共生成多少个任务目录、多少道可构建任务、多少道有效任务？
2. 共运行多少条真实 rollout？成功、失败、超时和基础设施错误各多少？
3. 最满意的一道题是什么？它的难度 crux、区分度和训练价值是什么？
4. 当前最大的质量风险是什么？
5. 如果再给两小时，你会优先修复或扩展什么？

### SCALE_PLAN.md

篇幅建议 1～3 页，必须包含数字和系统设计，不能只写“增加并发”或“使用更多 GPU”。至少回答：

1. 目标是 10,000 道候选题/天还是 10,000 道有效题/天？按预计通过率需要生成多少候选？
2. 每题执行多少次 build、Oracle、Nop 和模型 rollout？日总作业量、并发、延迟和成本是多少？
3. sampler、generator、build farm、验证、rollout、过滤、去重、数据注册和导出如何解耦？
4. 如何实现队列、幂等 task ID、内容寻址、重试、超时、背压和失败隔离？
5. 哪些检查是硬过滤，哪些是软打分？质量漏斗如何设计？
6. 如何根据 pass-rate 动态调整采样权重和 verifier 阈值，避免过易或完全不可解？
7. 如何衡量多样性，并完成语义去重、结构去重和评测集去污染？
8. 如何批量检测泄漏、脆弱 verifier、shortcut 和 reward hacking？
9. 若每题运行 8 条轨迹，如何承载每天 80,000 条 rollout 的 sandbox、模型服务、日志和数据清洗？
10. 监控哪些 SLO？哪些环节可自动化，哪些必须由专家审核？

## 现场资源与边界

面试开始前，面试方将确认：

- TerminalBench 3 仓库及 seed case 的访问方式；
- Harbor、Docker 或远程 sandbox 环境；
- Claude Fable 5 的模型标识和凭证注入方式；
- API、计算和并发额度；
- 提交目录或代码仓库。

不得在代码、日志或提交物中写入凭证。外部服务不可用、额度耗尽或面试方环境故障时，请保存完整错误证据并立即告知面试官。仅口头声称环境有问题不能替代可复现日志。
