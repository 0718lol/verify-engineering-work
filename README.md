# Verify Engineering Work

[![CI](https://github.com/0718lol/verify-engineering-work/actions/workflows/ci.yml/badge.svg)](https://github.com/0718lol/verify-engineering-work/actions/workflows/ci.yml)

面向真实代码库的 AI 编程可靠性护栏。它不负责让模型“更聪明”，而是通过证据核验、精确编辑、风险分级验证和失败破局，降低复杂工程任务中的可避免错误。

当前版本：`0.3.0`。当前状态：边界触发基准、可恢复触发评测、实验快照和盲化配对运行链路已验证；真实工程质量提升仍需完成足量对照实验后才能声明。

## 面向谁

直接执行者是支持 Skills 的编码代理，真正的使用者是把代理接入真实工程环境的开发者和团队：

- 使用 AI 代理维护真实仓库的独立开发者和全栈工程师；
- 需要兼顾迭代速度与回归风险的研发团队；
- 将模型接入 IDE、CI、代码审查或自动修复流程的 Agent 平台开发者；
- 处理陌生 API、共享契约、生产配置、迁移、安全隐私和并发问题的工程维护者。

目前的元数据和隐式触发行为针对 Codex 设计。其他支持 `SKILL.md` 的代理可以复用工作流，但需要适配触发元数据并重新执行触发评测。

不建议用于普通问答、文案排版、完全隔离的小实验，也不能替代人工审查、权限隔离、发布审批、监控和回滚能力。

## 解决什么问题

- 在使用陌生或快速变化的 API 前建立证据，减少接口与参数幻觉；
- 使用精确补丁和结构化编辑，避免占位符覆盖原代码；
- 按风险选择验证深度，避免“语法通过即宣称完成”；
- 区分本次回归、历史失败、偶发测试和环境故障；
- 连续失败时更换假设或观察层级，避免重复无效修复；
- 保护已有修改、任务边界、秘密信息和测试完整性。

## 使用方式

该方案采用窄范围隐式触发。系统在生产、安全、迁移、并发、跨模块、重复失败等复杂场景加载主流程；加载后由启发式风险矩阵选择低风险精简路径或中高风险完整路径。

风险矩阵见 [references/risk-matrix.md](references/risk-matrix.md)：`0-1` 为低风险，`2-4` 为中风险，`5+` 为高风险。分数只用于决定验证深度，不代表客观安全概率，也不会扩大代理权限。生产、破坏性、安全边界或持久数据写入还要求用户对准确目标进行明确授权，并具备可行的回滚或恢复路径。

用户通常不需要记住命令，也可以主动调用：

```text
$verify-engineering-work 修复这个跨模块鉴权问题，并验证兼容性和失败路径。
```

## 可验证假设

在完成真实实验前，本项目只提出以下方向性假设，不承诺具体提升比例：

| 假设 | 主要指标 | 可能代价 |
|---|---|---|
| 减少未经核实的 API 与参数 | 虚构接口严重错误率 | 更多文档和源码查询 |
| 减少占位符和无关覆盖 | 变更完整性得分 | 更长的编辑与 diff 检查时间 |
| 提高风险匹配的验证深度 | 生产合规通过率 | Token、耗时和测试调用增加 |
| 减少重复无效调试 | 相似失败重复率、人工干预次数 | 第三次尝试前增加诊断成本 |
| 提高报告可信度 | 未验证即宣称完成的发生率 | 最终报告略长 |

只有评测结果通过样本量、配对和元数据门槛后，才能使用“经过测试提升了多少”的表述。

## 工程质量评测

使用同一模型、工具权限、任务输入和相近 Token 预算，为每个任务分别运行 `baseline` 与 `skill`。建议至少准备 40 个任务对，随机运行顺序，并由不知道分组的评审者按 [references/eval-rubric.md](references/eval-rubric.md) 打分。

`scripts/run_paired_eval.py` 会检查两套 Codex Home 和每个变体的独立 Git 工作区，固定种子随机运行，以盲编号保存代理回答和 diff，并把分组映射写到单独文件。基线 Codex Home 不得安装本 Skill，Skill Codex Home 必须包含 `skills/verify-engineering-work/SKILL.md`。任务集格式见 [references/paired-task-set.example.json](references/paired-task-set.example.json)。

```bash
python3 scripts/run_paired_eval.py \
  paired-tasks.json paired-runs.json \
  --baseline-codex-home /path/to/baseline-codex-home \
  --skill-codex-home /path/to/skill-codex-home \
  --artifacts-dir paired-artifacts \
  --review-output blind-review.json \
  --mapping-output blind-mapping.json \
  --skill-commit "$(git rev-parse HEAD)" \
  --seed 20260718 \
  --max-total-tokens 2000000 \
  --allow-workspace-write

# 盲评者参考 references/blind-scores.example.json 填写评分后：
python3 scripts/build_scored_eval.py \
  paired-runs.json blind-mapping.json blind-scores.json scored-results.json
python3 scripts/score_eval.py scored-results.json
python3 scripts/score_eval.py --self-test
```

运行器要求显式传入 `--allow-workspace-write`，因为任务会修改工作区。默认拒绝脏工作区和重复使用同一目录；中断后用完全相同参数追加 `--resume`。原始事件与 diff 默认脱敏，但用于正式评审前仍应检查任务夹具中是否包含秘密信息。`--token-budget` 是每个任务的实验设计元数据，`--max-total-tokens` 是在下一次运行前检查的全局软上限。

评分器会：

- 强制每个任务 ID 恰好包含一条 baseline 和一条 skill 结果；
- 拒绝空样本、重复样本、缺失配对、无穷值和越界分数；
- 输出通过率、严重错误率、成本均值及 95% 区间；
- 使用成对 Bootstrap 计算 Skill 相对基线的差异区间；
- 只有达到 40 对样本并提供版本、模型、运行器和 commit 元数据时，标记为 `publication_ready`。

## 隐式触发评测

[references/trigger-benchmark-v1.json](references/trigger-benchmark-v1.json) 提供 40 条基础中英文平衡样本。[references/trigger-benchmark-v2.json](references/trigger-benchmark-v2.json) 进一步提供 40 条正负交替的边界样本：负样本会故意包含“生产、安全、迁移”等高风险词，但只要求解释、整理或只读审阅；正样本则减少直白关键词，测试隐含工程风险能否触发。正式触发结论应优先使用 v2。

```bash
python3 scripts/run_trigger_eval.py \
  references/trigger-benchmark-v2.json trigger-observations.json \
  --codex-home "$CODEX_HOME" \
  --workspace /path/to/read-only-fixture \
  --order stratified \
  --seed 20260718 \
  --snapshot-dir trigger-snapshot \
  --limit 8 \
  --max-total-tokens 500000 \
  --retries 1 \
  --logs-dir trigger-logs

python3 scripts/score_trigger_eval.py \
  references/trigger-benchmark-v2.json trigger-observations.json
python3 scripts/score_trigger_eval.py --self-test
```

批量运行器只使用只读沙箱，并采用多信号检测：优先识别结构化 Skill 事件，其次识别工具输入中对完整 `SKILL.md` 的读取。只有在 [references/compatibility.json](references/compatibility.json) 中具有正反事件样本的 Codex CLI 版本，才会把“完整运行但没有正向信号”判为未触发；未知版本会记录 `triggered: null` 和 `detection_status: indeterminate`，避免把不可观测误算成未触发。`--allow-unverified-negative` 可以显式覆盖该保护，但不建议用于正式结果。

运行器会把基准、兼容矩阵、选中 ID、关键 Skill 文件和评测脚本写入 `--snapshot-dir`，并在结果中记录 SHA-256。非空快照目录只有在内容与本轮完全一致、且内部文件哈希全部通过时才能复用，从而避免后续版本覆盖实验来源。`--order benchmark|shuffle|stratified` 与 `--seed` 控制可复现顺序；平衡数据建议使用 `stratified`。完整结果只有同时携带基准、兼容矩阵、实际 Skill 文件和快照 manifest 的哈希，才可能成为 `publication_ready`。

每个样本完成后会原子写入检查点。中断后使用相同的选择、顺序、快照和实验元数据，并追加 `--resume` 即可跳过已完成样本：

```bash
python3 scripts/run_trigger_eval.py \
  references/trigger-benchmark-v1.json trigger-observations.json \
  --codex-home "$CODEX_HOME" \
  --workspace /path/to/read-only-fixture \
  --order stratified \
  --seed 20260718 \
  --snapshot-dir trigger-snapshot \
  --limit 8 \
  --max-total-tokens 500000 \
  --retries 1 \
  --logs-dir trigger-logs \
  --resume
```

已有输出不会被静默覆盖；重新开始需要显式使用 `--overwrite`。`--max-input-tokens`、`--max-output-tokens` 和 `--max-total-tokens` 会在启动下一个样本前执行软上限，因此最后一个已完成样本可能使总量略微超过阈值。费用上限 `--max-cost-usd` 只有同时提供 `--input-usd-per-million` 与 `--output-usd-per-million` 才能使用，项目不会猜测模型价格。

通过 `--logs-dir` 保存的事件日志默认对常见 API Key、访问令牌、密码和 Bearer 凭据脱敏，并在观察值中记录原始内容的 SHA-256，便于完整性核对。仅在隔离环境确有需要时使用 `--unsafe-raw-logs`。正式测试前建议先用 `--ids` 或 `--limit` 做小样本 Pilot，并运行 `python3 scripts/run_trigger_eval.py --self-test` 验证事件检测器。

Pilot 结果可用 `score_trigger_eval.py --allow-subset` 评分，但会明确标记为覆盖不完整且不可发布。

如果保留的 JSONL 日志证明检测规则有缺陷，可在不重新调用模型的情况下写出一份独立的重分类结果，再重新评分。工具会核对未脱敏日志的 SHA-256、保留旧分类，并记录新旧检测 Schema：

```bash
python3 scripts/reclassify_trigger_eval.py \
  trigger-observations.json trigger-logs trigger-observations-reclassified.json \
  --skill-body-path /path/recorded/in/events/SKILL.md
```

Schema 2.3 会同时识别 Skill 安装入口及其符号链接解析后的精确路径。旧日志使用名称不含 Skill 名的冻结目录时，应通过 `--skill-body-path` 指定日志中实际读取的文件；工具会记录该文件的 SHA-256，不能用宽泛的任意 `SKILL.md` 匹配代替来源证明。

### 历史 Pilot（Skill 0.2.0）

2026-07-17 使用 `gpt-5.6-sol` 与 Codex CLI `0.144.0-alpha.4` 在只读沙箱运行了 8 条平衡样本：4 条应触发任务全部触发，4 条不应触发任务均未触发，未观察到误触发或漏触发。点估计准确率为 100%，但 95% Wilson 区间仍为 67.6% 至 100%，因此该结果只是运行链路和触发描述的初步证据，`publication_ready` 为 `false`。

原始观察值见 [evals/trigger-pilot-2026-07-17.json](evals/trigger-pilot-2026-07-17.json)。这组 Pilot 没有测试代码修改质量，也不能支持“生产合规率提高了多少”的结论。

### 扩展 Pilot（Skill 0.2.2，检测 Schema 2.2.0）

2026-07-18 使用同一模型和 CLI，以固定种子将正负样本交替排列，并在 `60` 万总 Token 软预算下完成 13 条：7 条应触发任务全部触发，6 条不应触发任务均未触发。重分类后的准确率为 100%，95% Wilson 区间为 77.2% 至 100%；累计记录 678,478 个 Token，结果仍因未覆盖完整 40 条而不可发布。

本轮首先被检测 Schema 2.1.0 错记为 7 个漏触发，因为运行时从仓库直链读取 `verify-engineering-work/SKILL.md`，而检测器只接受包含 `skills/` 的路径。保留日志证明 Skill 实际已加载；Schema 2.2.0 放宽了安装目录假设，并通过日志 SHA-256 校验重分类，没有重新调用模型。原始分类历史保留在 [evals/trigger-pilot-2026-07-18-reclassified.json](evals/trigger-pilot-2026-07-18-reclassified.json) 中。

观察文件格式：

```json
{
  "experiment": {
    "skill_version": "0.2.3",
    "model": "model-name-and-version",
    "harness": "agent-runtime-and-version",
    "skill_commit": "git-commit",
    "detection_schema_version": "2.3.0",
    "negative_detection_verified": false,
    "compatibility_schema_version": "1.0.0"
  },
  "observations": [
    {"id": "trigger-01", "triggered": true, "detection_method": "skill_body_read"},
    {"id": "no-trigger-01", "triggered": null, "detection_method": "unobservable_harness"}
  ]
}
```

评分器要求观察文件完整覆盖基准集，并报告召回率、准确率、特异度、误触发 ID、漏触发 ID、无法判定 ID 及其 95% 区间。v0.3 还会按 `category` 输出混淆矩阵，并分别汇总应触发、不应触发和全体样本的输入、缓存输入、输出及推理输出 Token。无法判定样本不进入准确率分母，并会阻止结果成为 `publication_ready`；运行器未验证负判定能力时，即使使用 `--allow-unverified-negative` 得到布尔结果，也不能通过发布门槛。不得用基准标签自动生成观察结果，那只能测试评分器，不能测试真实触发行为。

## 结果表述

合规的结论应同时写明样本数、模型、运行器、任务集版本、Skill commit、点估计和 95% 区间。例如：

> 在 N 个成对工程任务上，生产合规率差异为 X 个百分点（95% CI：L 至 U）；实验环境与原始盲评分记录见随附结果。

当区间跨过 0、样本不足或元数据缺失时，应表述为“本轮未获得稳定提升证据”，不能只引用点估计。

## 安装

将 `verify-engineering-work` 文件夹放入个人 Skills 目录并重新加载 Codex。Skill 会根据触发描述自动参与复杂任务，用户仍可通过 `$verify-engineering-work` 主动要求完整验证。仓库根目录的 [VERSION](VERSION) 是项目版本的唯一来源，运行器和仓库校验会自动读取它。

## 持续集成

每次 push、Pull Request 或手动运行都会触发 GitHub Actions。CI 使用 Python 3.12 和标准库检查脚本语法、评分器自测、Skill 元数据、40 条触发基准的完整性、Pilot 状态及文档本地链接：

```bash
python3 scripts/score_eval.py --self-test
python3 scripts/score_trigger_eval.py --self-test
python3 scripts/run_trigger_eval.py --self-test
python3 -m unittest discover -s tests -p "test_*.py" -v
python3 scripts/validate_repository.py
```

## 许可证

MIT，见 [LICENSE](LICENSE)。
