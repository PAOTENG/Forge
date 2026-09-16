# 工业化 Coding Agent 测试标准调研

调研日期：2026-09-04  
范围：当前主流、可公开核验的 Coding Agent 评测做法，不是个人经验清单。  
结论用途：给 Forge 设计可执行测试，而不是给简历编造指标。

---

## 1. 工业界实际在测什么

成熟 Coding Agent 产品不会只跑一次 Demo。公开资料里，测试至少分成四层：

| 层级 | 测什么 | 典型做法 | 主要来源 |
|------|--------|----------|----------|
| 工程回归 | 工具、路径、权限、状态机是否稳定 | 普通单元/集成测试、CI | 任何软件工程团队的基线 |
| 任务级评测 | Agent 是否修对真实仓库问题 | Docker 隔离 + 项目自带测试打分 | SWE-bench 官方 harness |
| 轨迹级评测 | 过程对不对，而不只看最终补丁 | JSONL / Trajectory，检查工具调用、命令、产物 | OpenAI Codex evals、SWE-agent |
| 产品/技能回归 | Prompt、Skill、Harness 改动有没有变差 | 小样本 prompt 集 + 确定性 grader + 可选 LLM rubric | OpenAI Developers、Cursor SDK、Coder Eval |

Coding Agent 比普通后端更需要后两层，原因很明确：同一任务多次运行结果不稳定，测试通过也可能是改了测试本身，Prompt/工具描述一改效果就会漂。

---

## 2. SWE-bench：当前最标准的“修对了没有”

官方流程见 [SWE-bench Evaluation Guide](https://www.swebench.com/SWE-bench/guides/evaluation/)：

1. Agent 产出补丁（`model_patch`）。
2. Harness 在 Docker 里把仓库固定到指定 commit。
3. 打上补丁。
4. 跑仓库自己的测试。
5. 同时检查两类测试：
   - **FAIL_TO_PASS**：修之前失败，修之后必须通过。证明真修了这个问题。
   - **PASS_TO_PASS**：修之前就通过，修之后仍须通过。证明没有引入回归。
6. 两项都过，才算 **resolved**。

官方统计字段也值得照抄：submitted / completed / resolved / unresolved / empty patch / errors。不要只报一个成功率。

SWE-agent 还会保存完整 `.traj`，用来区分“没找到文件”和“找到了但改错了”。见 [SWE-agent trajectories](https://github.com/SWE-agent/SWE-agent/blob/main/docs/usage/trajectories.md)。

Terminal-Bench / Harbor 走另一条工业路径：任务目录里放 `instruction.md`、环境 Dockerfile、`solution/solve.sh`（oracle）和 `tests/test.sh`（verifier）。先用 oracle 确认环境可解，再让真实 Agent 跑。见 [Harbor](https://github.com/harbor-framework/harbor) 与 [Terminal-Bench](https://www.tbench.ai/)。

这些基准测的是 **模型 + harness**。Forge 这种产品项目不应直接报 SWE-bench 分数，除非真的接过官方 harness。

---

## 3. OpenAI Codex：测过程，不只测结果

OpenAI 官方文章 [Testing Agent Skills Systematically with Evals](https://developers.openai.com/blog/eval-skills) 把一次 eval 定义成：

> prompt → 捕获运行（trace + artifacts）→ 一组检查 → 可跨版本比较的分数

它把检查分成四类：

- **Outcome**：任务完成了吗，应用能跑吗。
- **Process**：有没有走预期技能/命令/步骤。
- **Style**：是否遵守约定。
- **Efficiency**：有没有空转、重复命令、Token 膨胀。

落地方法：

1. 先写可检查的 Definition of Done。
2. 用 10–20 条小 prompt 集，包含明确触发、隐式触发和负例。
3. `codex exec --json` 拿 JSONL 事件流。
4. 用确定性规则打分：是否执行了某命令、是否生成了某文件、顺序对不对。
5. 规则不好打的，再用 `--output-schema` 做结构化 rubric。
6. 技能成熟后再加构建检查、Token 预算、仓库清洁度、权限是否被扩大。

招聘侧也把这套当成工作内容，而不是加分装饰。例如 [OpenAI Codex Core Agent](https://openai.com/careers/applied-ai-engineer-codex-core-agent-san-francisco/) 要求跑 eval、分析失败模式、把生产失败转回评测集。

---

## 4. Cursor：同一条 Agent Loop 必须能进 harness

[Cursor Evals 文档](https://cursor.com/docs/evals) 的核心要求是：评测必须调用产品同一条 Agent 循环，而不是另写一套 demo 脚本。

Harness 需要：

- 稳定的编程接口：给任务，收回 transcript 和最终状态。
- 本地工作树或隔离云 VM。
- 流式工具事件。
- 超时、取消、限制工具集。
- 跑完后用测试、judge 或精确匹配打分。
- 换模型时 harness 保持不变，避免把 harness 漂移当成模型差异。

[Cursor Agent Evaluation and Quality](https://jobs.ashbyhq.com/cursor/2bbe9f02-83a5-4173-98be-9085d1cb5693) 岗位也写明：离线回放、scorer、回归告警、失败聚类、把真实使用信号喂回评测。

---

## 5. 产品评测框架的共同结构

UiPath [Coder Eval](https://github.com/UiPath/coder_eval)、Agoda [agent-catalog-eval](https://github.com/agoda-com/agent-catalog-eval) 等框架虽然产品不同，结构几乎一样：

1. 每个 case 有 prompt、初始工作区、期望终态或 verifier。
2. 真实 Agent 在沙箱里跑。
3. 确定性检查优先，LLM judge 只补定性项。
4. 记录工具调用、Token、耗时、成本。
5. 同一 case 跑 k 次，报 pass@1 / pass@k。
6. CI 用阈值卡住回归，而不是只看手工 Demo。

pass@k 来自代码生成评测习惯：pass@1 看单次可靠性，pass@3 或 pass@4 看小预算内能不能碰到正确解。不要把 pass@4 包装成“成功率 100%”。

---

## 6. 工业标准测试清单

一个要对外宣称“Coding Agent 项目”的系统，通常需要下面这些测试。不是每一项都要第一周做完，但缺哪一层，简历就不能写哪一层。

### 6.1 工程测试（必须）

- 工具参数校验：路径逃逸、危险 git、密钥文件
- 审批/权限：未批准不能执行变更
- 状态持久化：重启、未完成 tool call、取消
- 沙箱限制：网络、内存、PID、超时
- API/前端构建与基础回归

### 6.2 环境/Oracle 测试（必须，且要先于 Agent 评测）

Terminal-Bench 要求先用 oracle 确认任务可解。SWE-bench 也要求 FAIL_TO_PASS 测试在修复前确实失败。

如果验收器本身是绿的，后面所有 Agent 分数都不可信。

### 6.3 Agent 任务评测（产品要迭代时必须）

- 固定仓库、固定故障、固定模型、固定审批策略
- 隔离工作副本，禁止污染主工作区
- 自动记录：是否修对、改了哪些文件、工具次数、耗时、Token、是否改测试、是否绕过安全
- 每题重复 3–5 次
- 对比基线：有无项目规则、有无固定热修顺序、不同模型

### 6.4 过程/安全评测（Coding Agent 特别需要）

工业评测明确会抓“看起来修好、实际作弊”的行为，例如删测试、放宽断言、关校验、泄露密钥。OpenTrain 的 [Coding-Agent Benchmark Engineer](https://www.opentrain.ai/jobs/senior-coding-agent-benchmark-engineer--cmtjzt5ry000209jhbuflg9pm/) 把这类不安全捷径写进了岗位职责。

### 6.5 CI 门禁（上线或持续迭代时必须）

- 工程测试每提交都跑
- 小规模 eval 集在 Prompt/工具变更时跑
- 大基准按发布节奏跑，因为贵、慢

---

## 7. 对 Forge 的直接含义

Forge 已经有工程回归和故障靶场，这对应工业清单的前半段。

Forge 还没有：

- 可脚本化的无头 Agent 入口（类似 `codex exec` / Cursor SDK）
- 自动批准或评测专用审批旁路
- 任务级 pass@k
- Trajectory 打分
- Prompt/工具变更回归集
- CI

因此当前只能诚实执行：工程回归 + 故障验收器质量检查 + 运行时探针。  
不能执行、也不能在简历里填写的：Agent 修复成功率、平均耗时、Token 成本。

详见同目录《02-Forge项目分层测试方案.md》。
