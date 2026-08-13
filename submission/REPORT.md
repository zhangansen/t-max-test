# REPORT:TerminalBench 3 高难任务合成与 Rollout 实操

## 汇总表

| task_id | seed case | sampled signature | build | oracle | nop | Fable trials | success | SFT-ready | time/cost | evidence path |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| tb3-claims-duplicate-audit | telecom-entity-resolution × medical-claims-processing | entity_resolution / data_engineering / claims_analyst / python / moderate / bash+code / csv_text / exact | ✅ | ✅ 1.0 | ✅ 0.0 | 1 (deepseek-v4-pro 替代) | ✅ reward=1.0 | ✅ 35/35 工具调用配对、无截断、零密钥 | 18m01s / $2.60 (1.91M in + 58k out) | logs/task1-rollout-2/ + rollouts/ |

> 现场使用 DeepSeek v4 Pro(deepseek-v4-pro)替代 Claude Fable 5 执行 rollout:
> 面试环境未提供 Anthropic 凭证,本机 `ANTHROPIC_BASE_URL=https://api.deepseek.com`,
> 仅暴露 `deepseek-v4-flash` / `deepseek-v4-pro`。rollout 命令与题面给出的
> `harbor run -p ... -a claude-code -m anthropic/claude-fable-5 --ak reasoning_effort=max`
> 的差异仅在于模型标识;拿到 Fable 5 凭证后可直接切换。见 logs/ 内各 result.json。

## 1. 任务产出统计

- 任务目录:1 个(完整 Harbor 格式,含 canary GUID)
- 可构建任务:1 / 1(environment + tests 双镜像构建通过)
- 有效任务:1 / 1(Oracle=1.0、Nop=0.0、verifier 隔离且确定性)
- 真实 rollout:1 条(deepseek-v4-pro,reward=1.0,轨迹已归档)

## 2. Rollout 统计

| 指标 | 值 |
|---|---|
| 总 rollout | 2 次尝试 = 1 成功 + 1 基础设施错误(agent setup 网络超时,已修复) |
| 成功 / 失败 / 超时 / 基础设施错误 | 1 / 0 / 0 / 1(失败为 AgentSetupTimeoutError,非模型能力信号) |
| 模型与 harness | deepseek-v4-pro + harbor claude-code agent v2.1.229 + docker sandbox |
| wall-clock / turns / tokens | 18m01s(agent_execution 17m19s)/ 35 次工具调用 / 1,905,825 输入(1,862,656 cached)+ 58,078 输出,$2.60 |
| verifier | 4/4 通过,含 exact_cluster_match(100k 留出集 precision=recall=1.0) |

轨迹落盘:`rollouts/raw/`(原始 session JSONL + stream 日志)、`rollouts/normalized/`(harbor ATIF 轨迹),`rollouts/manifest.json` 含 task 内容哈希、模型、harness、运行参数与用量。全部经密钥扫描:0 处凭证。

## 3. 最满意的一道题:claims-duplicate-audit

**任务**:在 ~55k 行、6 文件的理赔 CSV 中,按业务规则(姓名归一化:小写+标点转空格+空白折叠;多日期格式;日期 ±7 天;金额 ±1%)找重复理赔,并做传递闭包聚类,输出 JSON。

**难度 crux(三层,真实工作负载)**:
1. **算法必须**:100k 行的留出验证集上朴素 O(n²) 需 ~560s(实测外推),verifier 硬超时 300s——必须设计 blocking(正确解 = 按 (service_code, 归一化姓名) 精确分桶,oracle 实测 2.8s/100k 行,约 200× 加速);
2. **规则编码**:多日期格式解析、标点→空格(而非删除)的归一化语义、相对金额容差;
3. **传递闭包**:链式构造(相邻匹配、端点不匹配)强制 union-find,只做"成对去重"会漏并。

**对抗 fixture**:verifier 留出集注入五类 near-miss decoy(金额差 5%、日期差 8 天、单字符拼写差异、生日差 1 天、服务码不同),任何一类被误并 → precision<1 → 0 分。

**区分度**:预期弱模型卡在 O(n²)(超时)或规则语义(删除标点 vs 转空格);强模型需同时做对三件事。pass@k 与推理预算差异将在 rollout 结果中体现。

**训练价值**:成功轨迹含完整的"读规则→设计 blocking→调试对抗样本"过程;失败轨迹可用于定位模型在性能意识/规则忠实度上的短板。

### 3.1 基于真实 rollout 的判断(难度/区分度/质量/训练价值)

**实际 rollout 证据**(deepseek-v4-pro,effort=max):

- 成功:reward=1.0,4/4 verifier 测试全过,100k 留出集精确匹配。
- 过程:35 次工具调用,17m19s。轨迹显示模型**并非一步到位**:中间 3 次工具调用报错(含对数据格式的误判——最初按规则文本假设 service_date 只有 ISO 格式,扫描数据后发现 3 种混合格式,主动修正了解析器),说明任务在"数据探查 → 规则修正"层面产生了真实认知摩擦。
- 发现 7,107 个重复簇,与 GT 完全一致(union-find 传递闭包做对)。

**难度判断**:对 deepseek-v4-pro(max effort)可解但非零摩擦——pass@1=1.0 意味着此题对该模型偏**易**;但难度主要压在弱模型上(O(n²) 超时 + 规则语义),Nop=0.0 证明下界成立。按 SCALE_PLAN 的甜区标准(pass-rate 0.2–0.7),此题对旗舰模型需要上采样 fixture 规模或规则歧义。

**区分度**:对旗舰模型区分度不足(一次过),但"性能门槛 + 规则忠实度"双 crux 对弱模型/低推理预算应能拉开差距——待补 deepseek-v4-flash 对照(见 §5)。

**质量**:verifier 只测核心行为(聚类集合精确相等),无表面文件/魔法字符串;以 nobody 运行 artifact,reward 通道 700;留出集 5 类对抗 decoy 全防住。唯一扣分项:合成数据真实感(见 §4)。

**训练价值**:轨迹含完整推理链(4 个 reasoning 块)、数据探查决策、错误修正过程,35/35 工具配对无截断,零密钥——**满足 SFT-ready 完整性要求**,成功轨迹可作为"性能敏感的实体解析"正例。

## 3.2 harbor check 质检(补跑)

`harbor check -m deepseek-v4-flash` 评审结果:**11/11 项 PASS**(评审费 $0.84,报告:`jobs/2026-08-13__15-24-30/check_report.json`)。

评审官实际读取了全部任务文件、用 5 个 seed 复现了 solution 与 GT 的一致性,并独立发现了两个真实问题(均已修复):

1. **rules.yaml 注释不实**:注释称 service_date"一律 YYYY-MM-DD",实际数据有 3 种混合格式——正是 rollout 中模型被误导的同一个点。已修正两份 rules.yaml 的注释,oracle/nop 复验通过(final2-oracle=1.0、final2-nop=0.0)。
2. **遗留开发产物**:任务目录残留 `.tmp_test/gt_1337.json`(留出集 GT),未被任何 Dockerfile 拷入镜像(无运行时捷径),但已删除。

环境备注:harbor check 的 reviewer 容器(内置 `python:3.13-slim` 模板)同样触发 runtime apt 安装卡死,复用"预装 claude CLI"思路解决——本地将烘焙镜像 tag 为 `python:3.13-slim`(原镜像备份为 `python:3.13-slim-orig`)。

## 4. 当前最大的质量风险

1. **verifier 性能门槛的可绕过性**:理论上 agent 可用 numpy 向量化 O(n²) 规避 blocking——已加"仅标准库"约束 + 第三方包检测 + 以 nobody 身份运行(无法 pip install)三层缓解,但未在容器网络层强隔离。
2. **fixture 真实感**:生成的姓名/金额分布与真实理赔数据仍有差距(合成数据通病),后续需 vendored 真实脱敏数据。
3. **单一 rollout 的难度结论方差大**:1 条轨迹不足以标定难度,需 ≥8 条 + pass@k。

## 5. 再给两小时优先做什么

1. 为同一任务补 7 条 rollout(含 deepseek-v4-flash 对照),算 pass@1/pass@8;
2. 跑 `harbor check` rubric 评审并按其意见迭代 instruction;
3. 用 generator 脚手架量产第 2、3 道任务(采样器已就绪,见 generator/);
4. 建 trajectory normalization 脚本并验证 SFT-ready 完整性检查。

## 环境问题记录(可复现日志)

| 问题 | 证据 | 解决 |
|---|---|---|
| harbor 在 GBK 控制台崩溃(UnicodeEncodeError '⠋') | logs/smoketest 首次运行 traceback | `PYTHONUTF8=1 TERM=dumb` |
| tb-check 仓库 CRLF 行尾致 test.sh shebang 失效 | logs/smoketest/…/verifier/test-stdout.txt | `git config core.autocrlf false` + re-checkout |
| Docker 镜像源失效(daocloud 镜像站 EOF,docker.io 直连被断) | logs/task1-oracle/2026-08-13__13-18-09 exception.txt | 换 1ms.run/rat.dev 镜像 + 缓存续传 |
| `pytest-ctrf` 包名错误 | 同目录 13-33-52 exception.txt | 改用 `pytest-json-ctrf==0.5.2`(与 seed 一致) |
| rollout agent setup 卡死:容器内 `apt-get install nodejs npm` 360s 超时(archive.ubuntu.com 直连间歇性抽风) | logs/task1-rollout/…/exception.txt AgentSetupTimeoutError | 镜像内预装 node18 + claude CLI 2.1.229(npmmirror 源),harbor 检测 `command -v claude` 后跳过整个安装阶段;agent_setup 从 >360s 降至 2.4s |
