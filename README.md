# T-Max 高难任务合成与 Rollout 实操

按 [instruction(1).md](instruction(1).md) 完成的 2 小时实操测试交付物：基于 TerminalBench 3 /
Harbor 框架生成高难度终端任务、跑真实模型 rollout 采集轨迹，并给出日产 10,000 道有效任务的规模化方案。

## 目录结构

```
submission/
├── tasks/tb3-claims-duplicate-audit/   # 完整 Harbor 任务（理赔重复审计）
├── generator/                          # 维度组合采样器 + taxonomy + 任务脚手架模板
├── rollouts/
│   ├── raw/                            # 原始轨迹（Claude Code 原生 session + stream 日志）
│   ├── normalized/                     # 规范化轨迹（harbor ATIF 格式）
│   └── manifest.json                   # 可追溯索引（哈希/模型/参数/reward/复现命令）
├── logs/                               # build / oracle / nop / rollout / harbor check 日志
├── tools/                              # 轨迹规范化、SFT-ready 检查、性能基线、check 环境配方
├── REPORT.md                           # 结果报告（难度/区分度/训练价值判断）
└── SCALE_PLAN.md                       # 日产 10,000 道有效任务的规模化方案
jobs/                                   # harbor check 的 rubric 评审报告
```

## 核心结果

- 任务 `tb3-claims-duplicate-audit`：build ✓ / Oracle=1.0 ✓ / Nop=0.0 ✓ / verifier 确定性 ✓ / harbor check 11/11 PASS
- 真实 rollout（deepseek-v4-pro，经 claude-code agent v2.1.229）：**reward=1.0**，18m01s，1.91M 输入 + 58k 输出 tokens，$2.60；轨迹 35/35 工具调用配对、无截断、零密钥，已归档

## 复现

前置：Harbor CLI（v0.21.0）、Docker、可访问 DeepSeek API 的凭证（以环境变量注入，不写入仓库）。

```bash
# 环境变量（Anthropic 兼容端点）
export ANTHROPIC_BASE_URL=https://api.deepseek.com
export ANTHROPIC_AUTH_TOKEN=<your-token>   # 绝不写进任何文件
export ANTHROPIC_MODEL=deepseek-v4-pro
export CLAUDE_EFFORT=max

# Oracle / Nop 验证
harbor run -p submission/tasks/tb3-claims-duplicate-audit -a oracle -e docker -k 1 -n 1 -y
harbor run -p submission/tasks/tb3-claims-duplicate-audit -a nop    -e docker -k 1 -n 1 -y

# 真实 rollout
harbor run -p submission/tasks/tb3-claims-duplicate-audit \
  -a claude-code -m deepseek-v4-pro -e docker -k 1 -n 1 \
  -o submission/logs/task1-rollout-3 -y

# 质量评审
harbor check submission/tasks/tb3-claims-duplicate-audit -m deepseek-v4-flash
```

拿到 Anthropic 凭证后，将模型标识换成题面要求的 `anthropic/claude-fable-5` 即可，其余不变。

## 两处 Docker 环境说明（非标准网络下必需）

任务文件本身不依赖网络；以下两条只影响 **harbor 工具链自身的运行**，与任务难度无关：

1. **任务环境镜像预装 claude CLI**：`tasks/*/environment/Dockerfile` 内已预装 node 18 +
   claude-code CLI（npm 走 npmmirror）。harbor 检测到 `command -v claude` 后跳过其安装阶段，
   避免运行容器内 `apt-get install` 直连 archive.ubuntu.com 超时（实测 AgentSetupTimeout 360s）。
2. **harbor check 评审环境**：内置模板使用 `python:3.13-slim` 原镜像，同样会触发运行时 apt。
   可用 `tools/check_env.Dockerfile` 本地烘焙后再打同名 tag：
   ```bash
   docker build -f submission/tools/check_env.Dockerfile -t python:3.13-slim .
   # 原镜像先备份：docker tag python:3.13-slim python:3.13-slim-orig
   ```
   国内网络另需配置 Docker 镜像加速器（本机实测可用：docker.1ms.run / hub.rat.dev）与
   构建代理，见 REPORT.md「环境问题记录」。

## 安全

- 仓库内任何文件不得含 API key / token（提交前已全量扫描，0 命中）；凭证只经环境变量注入。
- 任务 verifier 与 solution 隔离：agent 看不到 GT 与测试逻辑，artifact 以 nobody 身份运行。
