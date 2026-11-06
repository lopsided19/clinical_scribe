# Qwen 四组受控对照实验

2026-11-06。工具 `tools/qwen_ablation.py` 只准备、检查和汇总本地文件，不发模型请求。目前没有真实 Qwen 实验结果。

## 要回答的问题

在同一份开发病例、同一份提示词和代码、相同采样参数下，分别考察事实台账流程和思考模式。之前的模式候选配置采用各自配套采样建议，同时改变多个参数；这套实验从一份基础配置派生四组，避免将采样变化误算成思考的收益。

| 组别 | 原文台账 | 思考 | 每例请求上限 |
|---|---|---|---|
| baseline_nonthinking | 无 | 关闭 | 1 |
| grounded_nonthinking | 有 | 关闭 | 1 |
| baseline_thinking | 无 | 开启、预先固定强度 | 1 |
| grounded_thinking | 有 | 开启、预先固定强度 | 1 |

所有组完整保留原文，不重试、不修正。温度、top_p、top_k、惩罚项、输出预算和超时均取同一基础配置。台账组同时启用生成后规则审计，因而比较的是“台账与审计流程”，不能把规则告警数量直接解释为生成质量差异。思考组相对非思考组改变开关及预设强度，是一个模式因素；无法单独识别强度的影响。

统一采样和输出上限用于控制变量，并不保证每个模式都达到各自最佳效果。还需核对服务是否接受所有参数、思考用量是否计入 max_tokens。思考模式因正文预算不足而截断也应作为结果保留。

## 离线准备

先生成一份明确参数格式的基础配置，再生成计划。例如 CMB 开发集有 16 例，四组共有最多 64 次请求；每次 max_tokens=4096 时，请求输出上限之和为 262144。

```powershell
python tools/prepare_qwen_profile.py --out-dir artifacts/qwen_ablation_base --mode nonthinking --parameter-style chat_template_kwargs --max-tokens 4096
python tools/qwen_ablation.py prepare --input data/prepared/cmb/dev.json --settings-dir artifacts/qwen_ablation_base --out-dir artifacts/qwen_ablation_dev --thinking-effort medium --max-total-calls 64 --max-requested-output-tokens 262144
python tools/qwen_ablation.py check --plan-dir artifacts/qwen_ablation_dev
```

示例数字只是本地计划额度，不能视为赛方已授权的额度。工具计算超过任一额度时，在创建计划目录前拒绝；不按额度截断病例或挑选部分输出。空病例列表、无效输入、配置文件缺失也会被拒绝。

计划目录保存原输入副本、基础配置、四组配置、预定对照和文件哈希。已有目录禁止覆盖；数据、参数或相关代码改变后必须保留旧计划，另建新计划。这里验证的是文件一致性，哈希不是签名，不能防止联合改写。

**预算边界：**`sum_request_max_tokens` 是请求字段之和，不是实际 token 用量、总计费 token 或费用上限。输入 token 未计算，服务是否包含思考 token 尚未确认；接口探测也另计。工具没有全账户额度控制能力。实际服务用量按返回值记录，缺失不补零。

## 接口到手后执行

先核对赛方限制与基础配置的兼容性，并完成小请求检查。`check --env` 只生成结构化参数数组，不读取接口配置、不执行命令：

```powershell
python tools/qwen_ablation.py check --plan-dir artifacts/qwen_ablation_dev --env .env
```

四组仍由现有 `run_experiment.py` 分别运行。例如第一组：

```powershell
python tools/run_experiment.py --input artifacts/qwen_ablation_dev/input.json --env .env --variant baseline --settings-dir artifacts/qwen_ablation_dev/profiles/baseline_nonthinking --max-calls 1 --run-dir artifacts/qwen_ablation_dev/runs/baseline_nonthinking
```

其余三组使用 `check` 给出的对应参数，保持固定目录、病例与配置。运行目录非空时程序拒绝重跑，避免误覆盖和重复请求；人工另建目录或修改命令仍可能多花额度，应按预定计划管理。失败不自动重跑，更不能挑最好的一次输出替换。

四组完成后：

```powershell
python tools/qwen_ablation.py report --plan-dir artifacts/qwen_ablation_dev
```

报告写入 `comparison.json`。可加 `--annotations` 传入与输入匹配的独立事实标注，仅用于事后评价。报告要求四组运行都完成且通过冻结文件校验，并严格匹配预定输入、配置和运行代码；拒绝 MOCK 数据。它给出四个预定配对对照、失败情况和用量，不自动选“最好模型”，不合成未经验证的医学分数。

## 解释结果的限制

这是一轮开发对照，不是临床有效性证明。每组每例只有一次生成，随机波动、服务换版和执行时段可能影响结果。需要重复时，应在看结果前确定轮数、执行顺序和预算，完整报告所有轮次；不能重复到满意为止。四个对照也不是四份独立证据，不能从中只挑最有利的一项。

同名模型和同地址不能证明后端版本一致。留出集应在方案冻结后使用；本工具不通过文件名猜测数据归属，也不阻止手动传入留出集，所以准备阶段应确认数据划分。医学质量仍需独立人工评价，规则减少、结构合格或 token 更省都不能单独替代它。
