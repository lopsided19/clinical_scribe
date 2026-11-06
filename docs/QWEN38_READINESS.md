# Qwen 3.8-27B 接入与实验准备

更新日期：2026-11-06。模型名称来自用户转述的赛题组答复；实际 MODEL_NAME、ENDPOINT、服务框架、部署版本及调用限制仍未取得。本轮只做官方文档核对与模拟接口验证，没有真实 Qwen 运行或训练结果。

## 官方资料与赛方配置分开

核对来源：[Qwen 官方模型卡的 API Usage](https://huggingface.co/Qwen/Qwen3.8-27B#api-usage)。模型卡说明默认开启思考，提供 low/medium/xhigh 强度；本地框架示例将思考开关放入 chat_template_kwargs，而 Qwen Cloud 使用顶层字段。模型卡提供不同模式的采样建议，并提醒参数支持随服务框架变化。这些说明不等于赛方部署实际开放了全部参数。

项目仅支持无密钥的赛题调用协议。支持顶层思考字段不代表已接入需要鉴权的商业 Qwen Cloud。不得据显示名称硬编码正式模型标识、地址、最大上下文或请求额度。

## 已实现的兼容选项

正式根目录 settings.json 保持原有值；新增设置的默认值不会给请求增加 Qwen 专属字段。所有显式设置会进入冻结配置和运行日志。

| 配置 | 默认 | 显式启用后的作用 |
|---|---|---|
| qwen_parameter_style | disabled | chat_template_kwargs 或 top_level；由实际接口说明选择，不自动猜测 |
| enable_thinking | null | true/false；null 表示不发送，不代表服务已关闭思考 |
| preserve_thinking | null | 可选布尔值，按所选格式发送；不表示程序会保存模型思考正文 |
| reasoning_effort | null | low/medium/xhigh；要求显式 enable_thinking=true |
| top_k、min_p、presence_penalty、repetition_penalty | null | 可选采样参数；关闭 send_sampling_parameters 时与 temperature/top_p 一并省略 |

直接 HTTP 请求不发送 SDK 的 extra_body 包装；相应字段放到正确的 JSON 层级。未知设置和不合理组合在发请求前拒绝。不因参数不兼容自动切换格式、换模型或偷偷重试，否则会混淆实验并消耗预算。

独立 reasoning_content/reasoning 不是病历正文；程序只读取最终 content。只有思考而没有正文时返回 MODEL_NO_FINAL_CONTENT；finish_reason=length 时仍按截断失败处理并保留服务报告的用量。完整的前置 think 标签可被解析器剥离，未闭合的标签不作为合法 JSON 接受。日志和最终输出不保存这些思考正文。

## 不联网生成候选配置

```powershell
python tools/prepare_qwen_profile.py --out-dir artifacts/qwen38_nonthinking --mode nonthinking --parameter-style chat_template_kwargs --max-tokens 4096
python tools/prepare_qwen_profile.py --out-dir artifacts/qwen38_thinking_medium --mode thinking-medium --parameter-style chat_template_kwargs --max-tokens 4096
```

4096 在这里只是演示如何填写项目现有预算，不是赛方上限，也不保证足够完成思考和正文。实际运行前替换成获准值。模式也支持 thinking-low、thinking-xhigh；参数格式可显式选择 top_level。

工具在新目录生成完整 settings.json 和带来源、配置哈希的 profile_manifest.json，拒绝覆盖已有目录，不创建 .env，不发网络请求。候选采用官方建议的模式配套采样值，不能声称它们已在中文病历任务上更优。默认不开启跨轮思考保留参数，默认不发 seed 或 JSON 模式扩展。

如果要单独衡量思考开关的影响，必须在两组配置中保持其他采样参数相同。直接比较工具生成的两个配套配置，只能评价“模式与采样组合”，不能把差异完全归因于思考开关。

## 取得允许的接口后

先由赛方提供根目录 .env。离线检查配置：

```powershell
python tools/check_model_connection.py --check-only --settings-dir artifacts/qwen38_nonthinking
```

随后在获准开发预算内执行一次非医疗请求：

```powershell
python tools/check_model_connection.py --settings-dir artifacts/qwen38_nonthinking --max-tokens 128
```

该命令仍然最多一次请求、不重试；指定探测输出上限不得超过配置的 max_tokens。探测默认 128 token，可能不足以完成开启思考的响应。报告的 http_response_received 表示收到了 HTTP 状态，包含 HTTP 错误，不等于最终 JSON 有效或实际模型身份得到认证。遇到截断先核对预算与服务模式，不盲目反复调用。探测请求的单次超时仍最多 30 秒，总期限 35 秒，因此不用于测量长推理性能。

真实开发实验使用指定候选配置目录：

```powershell
python tools/run_experiment.py --input data/prepared/cmb/dev.json --env .env --variant baseline --settings-dir artifacts/qwen38_nonthinking --run-dir artifacts/runs/qwen38_nonthinking_baseline
python tools/run_experiment.py --input data/prepared/cmb/dev.json --env .env --variant grounded --settings-dir artifacts/qwen38_nonthinking --run-dir artifacts/runs/qwen38_nonthinking_grounded
```

run_experiment 的 settings-dir 作为基础配置；variant 决定流水线、审计/修正和默认调用预算，max-calls 可再显式设置预算。其余超时、重试、采样与思考选项保留并冻结。读取目录必须实际包含 settings.json，不能因路径拼错静默退回默认值。settings-dir 不会更改根目录正式配置。

## 针对该模型的实验顺序

已补充 [四组受控对照工具](QWEN_ABLATION.md)：从同一配置派生“有/无台账 × 开/关思考”，固定采样、提前计算请求预算并检查运行是否偏离计划。准备和检查均不联网。

1. 核实接口格式、实际版本、思考默认状态、max_tokens 是否包含思考用量以及允许的参数。
2. 在一例获准练习资料上验证五字段正文、来源和截断行为，再进行小批次。
3. 同一模式与采样下比较 baseline/grounded，观察台账收益。
4. 固定流程后比较模式或采样组合，记录失败、事实遗漏、医学评价、用量和耗时。不能用“思考越深越好”替代结果。
5. 在限额允许时才启用抽取或一次修正。每次 HTTP 尝试共享预算，模式改变不能绕过限制。

根目录现有温度为零只是工程初始配置，并非模型卡推荐设置或临床最优结果。没有实际分词器与服务模板时不把字符数换算成精确 token 数；也不把官方模型最大上下文当作比赛可用窗口。

## 本地硬件与验证边界

本机此前通过 nvidia-smi 读取为 RTX 3060 12GB。尚未下载 Qwen 权重或实测推理；不能直接承诺 27B 全模型在该卡上运行。若需本地部署，再核对内存、量化文件、后端与性能；量化/模板不同的本地结果不能替代赛方部署结果。

专项测试覆盖两种参数位置、配置拒绝、无鉴权头、思考与正文分离、截断报告、探测预算、离线配置生成和实验配置冻结。测试使用本机模拟服务，验证的是程序行为，不是远程服务参数支持或医学效果。
