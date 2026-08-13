# SCALE_PLAN:日产 10,000 道有效任务的规模化方案

## 1. 目标定义与产量换算

**目标按"有效任务"计**:可构建(build=0 退出)、Oracle 满分、Nop 零分、verifier 确定性复现 3 次一致的任务,才算"有效"。

按本次实测的漏斗估算(种子任务族 → 签名 → 生成 → 有效):

| 阶段 | 通过率(估) | 日产 10,000 有效所需 |
|---|---|---|
| 候选任务生成 | — | ~40,000 候选 |
| 环境可构建 | 80% | ~32,000 |
| Oracle=1.0 | 60% | ~19,200 |
| Nop=0.0 且 verifier 确定性 | 90% | ~17,280 |
| 人工/LLM 质检(harbor check 通过) | 58% | **10,000 有效** |

即 **每天需生成 ~40,000 道候选、执行 ~17 万次容器级作业**。

## 2. 每题执行次数与日作业量

每题:1 次 build(env+tests 两镜像)→ 1 次 Oracle → 1 次 Nop → 1 次确定性复跑(3× oracle)→ 8 条 rollout。

| 作业 | 每日量 | 单次耗时 | 并发 | 机器数 |
|---|---|---|---|---|
| build | 40,000×2 镜像 | ~60–300s | 20 台×10 并发 | 20 |
| oracle+nop+确定性 | 40,000×5 | ~30–120s | 同上 | 与 build 复用 |
| rollout(8 轨迹/题) | 80,000×8=… **80,000 条/天** | ~10–30min | 8×500 并发 | API 侧吞吐为主 |

**模型侧成本(按 DeepSeek 类定价估算)**:每条 rollout ~50–200k tokens(输入+输出),80,000 条/天 ≈ 8–16B tokens/天;按 $0.5/M 输出、$0.1/M 输入混合价 ~$0.2/M,约 **$1.6–3.2k/天**。若用 Fable 5 级模型按 $3–15/M 计,则为 **$24–240k/天**——成本在模型单价上差两个数量级,需按 budget 动态切换 rollout 模型(廉价模型做质量粗筛,旗舰模型做最终标定)。

**结论:瓶颈不在生成,在验证与 rollout 的吞吐;成本瓶颈在模型单价。**

## 3. 组件解耦

```
sampler(taxonomy 加权采样)
   └─> generator(LLM 提示工程 + 任务脚手架模板 instantiate)
        └─> queue[task_signature] (内容寻址, sha256(signature+模板) 为 task_id)
             ├─> build farm(Docker buildkit 集群; 镜像缓存按 base 层共享)
             ├─> oracle/nop farm(临时容器池, 复用镜像, 只跑 tests 层)
             ├─> rollout farm(长时任务队列, 每条轨迹独立 sandbox, 断点续传)
             ├─> quality gate(hard filters + soft scoring + LLM rubric 评审)
             ├─> dedup / decontamination(语义+结构去重, 评测集重叠检测)
             └─> export(SFT-ready JSONL + parquet, 内容寻址归档)
```

- **解耦方式**:全部经任务队列(如 SQS/Kafka/Redis Streams)通信;每阶段产出不可变 artifact(镜像 tag、结果 JSON、轨迹文件),以 task_id + stage 为键。
- **幂等 task_id**:`sha256(sampled_signature + generator_version)`——同一签名重跑得同一 ID,生成器升级只影响新任务。
- **内容寻址**:fixture/镜像/轨迹全部按内容 hash 存储(类 git/OCI),天然去重与缓存。
- **重试/超时/背压**:build 重试 3 次指数退避;rollout 超时按 agent_timeout×1.2;每阶段独立令牌桶限流;失败隔离为按 task_id 的 DLQ。

## 4. 质量漏斗:硬过滤 vs 软打分

硬过滤(任一不满足即丢弃):
1. build 失败;2. Oracle≠1.0;3. Nop≠0.0;4. verifier 3 次复跑不一致;5. 轨迹/提交物含密钥;6. canary GUID 缺失;7. 依赖偶然网络状态(探测:断网重跑 oracle)。

软打分(进入排名,影响保留顺序与采样权重回传):
- **难度**:rollout 成功率(0 分=不可解,1 分=过易,0.3–0.7 为甜区);
- **区分度**:pass@1 与 pass@8 的差距、弱/强模型成功率差;
- **多样性**:domain/persona/language 熵,与已产集合的语义距离;
- **训练价值**:成功轨迹的完整性(无截断/无缺工具返回)、失败轨迹的可分析性。

## 5. 难度标定与采样权重的动态闭环

- 每个 seed 任务族维护 pass-rate 滚动窗口(按模型×推理预算分组)。
- **过易**(pass@1>0.9):提高该族的 complexity 采样权重(short→moderate→complex),或上采样 adversarial fixture 密度。
- **不可解**(连续 3 批 reward=0 且失败模式是 instruction/verifier 缺陷):回炉 generator,硬过滤拦截,族权重降权。
- verifier 阈值(如 metric_threshold 类)随 pass-rate 做 UCB/Thompson 采样调整,目标把族 pass-rate 锁在 0.2–0.7。

## 6. 多样性与去污染

- **结构去重**:fixture 生成参数、Dockerfile AST、verifier 类型三元组聚类,只保留簇代表。
- **语义去重**:instruction 嵌入(小模型)余弦 ≥0.92 判重;跨 seed 族的任务要求距 seed instruction 语义距离 ≥ 阈值(防改名换数)。
- **评测集去污染**:产出任务与 TerminalBench/Harbor Hub 现有任务、以及模型训练数据窗口(滚动更新)做 n-gram + 嵌入重叠检测;canary GUID 作为切片级标记。
- **泄漏/脆弱 verifier/捷径检测**:批量跑"cheat 探测"(给 agent 注入 solution 片段,reward 应显著升高才说明 verifier 真在测核心行为,反而过高的提升=verifier 泄漏);随机扰动 fixture(改 seed)后 oracle 复跑,reward 抖动>0 即脆弱;reward hacking 检测用轨迹审计(agent 是否改写了 /logs 或利用了 verifier 侧信道)。

## 7. 基础设施与 SLO

- **sandbox**:K8s 容器池 + buildkit 远程 builder;单机 16C/32G 可承载 build+验证 ~8–10 并发,rollout 并发受 API 限流约束。
- **模型服务**:API 代理层(统一鉴权、重试、限流、成本记账);80,000 条 rollout/天需 ~50–100 并发 agent 进程持续运转。
- **日志/数据**:轨迹 JSONL 入对象存储,元数据入列式仓库(parquet);SFT-ready 管线:完整性校验(工具调用/返回成对、无截断、无密钥)→ 规范化 → 版本化导出。
- **SLO**:
  - build 成功率 ≥95%(重试后);oracle 确定性 100%;rollout 基础设施错误率 <2%;
  - 队列积压 p99 延迟:build<5min,oracle<3min,rollout 完成率 ≥98%/天;
  - 成本:单有效任务全成本(生成+验证+8 rollout)< $X,按模型档位动态预算。
- **自动化 vs 专家审核**:签名采样、模板 instantiate、build/验证、去重、完整性检查全自动;专家只审:新族上线、verifier 语义变更、质量 gate 的边缘案例仲裁(日审 ~50 例,1–2 人天)。

## 8. 两小时内的当前方法与它的差距

本地单机 + harbor CLI + Docker 的串行管线验证了质量闭环(build→oracle→nop→rollout),但:
- 无队列/无内容寻址 → 无法并行扩展;
- fixture/verifier 靠手写 → 生成通量受人工限制;
- 模型调用走单账号 → 无并发与成本治理。

**迁移路径**:把"手写任务"里的可复用部分(脚手架模板、fixture 生成器模式、verifier 骨架)固化为 generator 资产,接入 3 节的流水线,即可从"2 小时 1 题"走到"40,000 候选/天"。
