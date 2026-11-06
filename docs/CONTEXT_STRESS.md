# 信息位置与无关资料干扰实验

此工具是 [研究框架](RESEARCH_FRAMEWORK.md) 中 E5 的一部分，准备输入和评价材料，不调用模型、不训练权重。它检验可定位事实在输入排列变化后的词法保留情况，不替代临床评分或完整语义核查。

## 实验设计

从项目 3 个固定人工种子各生成 5 种条件，共 15 例。每个种子的截止点、原来源编号和原始正文逐字保留；病例编号增加条件后缀。人工写定 8 条工程期望，覆盖既往与入院检验值、单位、否定、缺失和症状病程。期望不从生成结果反推，也不是医学专家认定的完整关键事实集。

| 条件 | 输入变化 | 对照 |
|---|---|---|
| original | 原始资料顺序，不加填充 | 母条件 |
| reverse | 原资料片段倒序 | original，检查来源顺序影响 |
| front | 病例资料位于填充之前 | original，检查增加无关资料的影响 |
| middle | 病例资料位于填充中间 | front，检查位置影响 |
| end | 病例资料位于填充之后 | front，检查位置影响 |

填充只描述文档排版，不添加患者事实或控制模型的指令。front/middle/end 的来源集合和字符总量相同，避免将长度改变与位置改变混为一谈。默认填充为 8 个片段、每片段重复 16 次，最大正文总量为 5,428 个 Unicode 字符；可调整强度，但不是百万 token 长上下文测试。清单中的位置是 sources 正文字符偏移，不包含 JSON、提示词或台账，不是精确 token 位置。

原始/倒序与填充条件的长度不同，只有三个填充条件之间用于位置比较。重复排版文字比真实医疗噪声简单，不能从通过此测试推断复杂长病例已经稳健。

## 无接口时准备

在仓库目录运行：

```powershell
python tools/context_stress.py prepare --out-dir artifacts/context_stress
```

工具生成 `cases.json`、`manifest.json` 和 `coverage_annotations.json`，包含操作、母病例分组、输入与标注哈希。已有目录会拒绝覆盖。改变强度时使用新目录，例如 `--blocks 16 --repeats 32`；先核对输入长度与实际服务预算，不据模型宣传上下文长度推断比赛额度。

测试集不加入现有 142 例 MOCK 总批次，避免将新增的受控条件误记为新增独立患者。正式算法包不包含该工具、测试集或标注。

## 接口可用且允许开发调用后

```powershell
python tools/preflight.py --input artifacts/context_stress/cases.json
python tools/run_experiment.py --input artifacts/context_stress/cases.json --env .env --variant baseline --run-dir artifacts/runs/context_baseline --annotations artifacts/context_stress/coverage_annotations.json
python tools/run_experiment.py --input artifacts/context_stress/cases.json --env .env --variant grounded --run-dir artifacts/runs/context_grounded --annotations artifacts/context_stress/coverage_annotations.json
python tools/context_stress.py evaluate --run-dir artifacts/runs/context_baseline --suite-dir artifacts/context_stress --report artifacts/context_baseline_report.json
python tools/context_stress.py evaluate --run-dir artifacts/runs/context_grounded --suite-dir artifacts/context_stress --report artifacts/context_grounded_report.json
python tools/compare_runs.py --baseline artifacts/runs/context_baseline --candidate artifacts/runs/context_grounded --annotations artifacts/context_stress/coverage_annotations.json --report artifacts/context_method_comparison.json
```

上述两次默认运行共计划 30 次请求，实际失败路径可能少于此数；重试和修正按实际配置另算。preflight 检查的是项目根配置，实验脚本会构建各方法配置，应同时查看冻结目录配置。生成程序不读取标注；`--annotations` 只供运行后评价。

正式分析入口验证冻结运行、整套输入、清单及标注与生成器一致，拒绝 MOCK。不能用旧清单配新输入，不能剔除难例后保留原分母。报告路径不能覆盖已有冻结运行或测试集文件。

## 读报告时避免误判

- 每个条件分别报告可用输出数、应保留事实数、词法覆盖数；每种条件应评价 8 条期望。
- 母病例内配对列出新增遗漏、恢复事实、运行失败回退、双方都失败/无效。相同错误或共同失败不会变成一个“100% 稳定”分数。
- 病例缺失、失败或编号错配仍保留期望分母。记录完整规则提示，数字或否定错误不能仅凭文字相似被当作保留成功。
- 事实都出现也不代表没有额外编造、计划合理或语义相同。查看 `case_checks` 并人工对照原文，不能将词法命中直接作为医学正确性。
- 独立母病例只有 3 个，15 个变体不能当作 15 位独立患者，也不据此报告稳定的统计显著性。它们与原种子属于同一开发分组，不能把同源变体放进留出集。
- baseline 与 grounded 比较使用同一套输入、同一模型和配套冻结日志。前者不加入台账，可观察原文位置影响；后者有额外索引，可研究索引是否减轻影响。台账增加的上下文也是方法成本的一部分。

当前只完成生成、回显工程检查和受控故障测试。真实 Qwen 的位置敏感度、抗干扰能力及台账收益均未测量。
