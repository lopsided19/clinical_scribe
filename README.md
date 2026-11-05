# 临床智录：住院首次病程记录智能生成

面向第二届北京市大学生“人工智能+”创新大赛相应赛题。程序从给定入院资料批量生成病例特点、拟诊讨论、诊疗计划、来源依据和待核事项。仅供教学比赛与人工复核。

## 当前交付状态

- 已实现比赛输入输出协议、指定模型远程调用、完整日志、逐例异常隔离、调用预算与超时管理、原文事实台账、来源与数值规则校验。
- 已下载并固定 CMB-Clin、MIMIC-IV Demo 2.2 的开发资料、许可证、来源与 SHA-256。详见 `docs/DATA_SOURCES.md`。
- 已提供真实模型实验工具、模拟接口、自动测试、离线结果复核页面生成器和提交打包工具。
- 校验同时核对常见指标的数值与单位组合，提示相同数值被改换单位和事实引用遗漏单位；不擅自换算单位。
- 事实台账按来源位置记录时间、肯否/缺失状态和病程；开发评估支持独立的关键事实标注与遗漏覆盖检查，并提供 7 类操作、19 例受控扰动。见 `docs/FACT_COVERAGE.md`。
- **尚无主办方实际接口及最终调用限制。真实 Qwen 医学效果、改进收益和正式环境适配尚未完成验证。MOCK 输出只证明工程链路，不能作为正式结果。**
- 2026-11-04 已核查公开官网、临床智录资料分类及公告手册，未找到实际模型配置。核查证据、报名日期和接入渠道见 `docs/OFFICIAL_INTERFACE_CHECK.md`。

## 正式运行

使用主办方的 Python 3.11。源码解压后的根目录直接包含 `main.py`，无需启动本地服务器或交互输入。

```powershell
python -m pip install -r requires.txt
python main.py
```

运行依赖全部来自 Python 标准库，`requires.txt` 为可由 pip 读取的空依赖清单。开发测试工具也只使用标准库；项目说明 PDF 的重新生成单独需要 reportlab，不属于正式运行依赖。

主办方在根目录提供 `.env` 和 `input.json`。`.env` 仅含：

```dotenv
MODEL_NAME=主办方实际模型标识
ENDPOINT=主办方完整推理请求地址
```

直接使用完整 `ENDPOINT`，不追加路径。采用 Chat Completions 兼容 POST；不发送 `Authorization`、不读取密钥、不继承系统代理、不自动跟随重定向。输入输出路径固定在 `main.py` 所在目录。示例配置不代表已开通模型服务。

输入为 UTF-8 JSON 数组，每条包含 `case_id`、`record_cutoff`、`sources`，资料片段含 `source_id` 与 `content`。可复制 `examples/input.demo.json` 理解结构。

程序原子写入 `output.json`，条数、顺序与病例编号同输入。每条包含 `case_id/status/medical_record/error`，失败病例保留原位。成功病历恰含 `case_features/diagnostic_discussion/plan/evidence/review_items`。完整执行日志写入 `run_log.json`，不嵌入输出 JSON。

接口到位后可先运行 `python tools/check_model_connection.py --check-only` 做离线配置检查。去掉 `--check-only` 将只向配置地址发出一次非医疗 JSON 探测，结果写入 `artifacts/model_access_check.json`；请在赛方允许的开发预算中使用。

退出码：`0` 表示全部输入已处理并输出完整结果，允许单条失败；`1` 为任务级运行错误；`2` 为配置或基本输入协议错误。失败运行遗留的旧 `output.json` 不代表本次结果，应结合本次日志状态及文件哈希判断。

## 固定算法及调用预算

`settings.json` 是随源码提交的固定算法配置，不是 `.env` 的扩展。当前为保守的一次调用版本：

1. 校验所有病例编号，逐例校验资料格式，不静默截断资料。
2. 为原文建立带位置索引的台账，绑定指标、数值、单位、时间、肯否/缺失状态；原始 sources 保留完整且不再在台账中复制全文。
3. 一次调用指定模型生成五字段 JSON。
4. 校验正文、依据字段及引用来源，运行数值、指标绑定、缺失误写与计划措辞规则。
5. 保存结果、逐例日志、实际调用次数与版本哈希。

默认 `max_calls_per_case=1`、`max_retries=0`、串行处理、请求超时 120 秒、总时限 1800 秒、`temperature=0`、`top_p=1`、`max_tokens=4096`。**这些是本项目的开发默认值，不是主办方已确认的限制，必须按正式附件统一调整。** 所有 HTTP 尝试（含失败、重试、提取和修正）共用每例预算。只调用 `.env` 中的单一服务。不会缓存答案、为单例挑选最好候选或人工修改正式正文。

兼容性考虑默认不发送 `seed` 和 `response_format`；支持时可在固定配置启用。种子记录在配置中，但关闭 `send_seed` 时服务器不接收种子；即使温度为零、设置种子也不保证远程服务逐字确定，需记录模型版本并核查服务随机性。

可选流水线：`baseline` 不增加台账；`grounded` 使用原文台账；`extract_generate` 先提取再生成，提取片段必须在来源中逐字出现，且生成阶段仍可读取完整原文。`enable_repair=true` 可按统一规则执行至多一次修正，仍受预算约束。正式限制发布前不默认启用多次调用。

结构或来源引用错误一律记录失败；内容规则默认 `quality_action=warn`，只写日志、不自动重写医学正文，防止启发式误报丢弃病例。可固定为 `fail` 拒绝高风险规则命中，需在同批开发资料比较覆盖率和误报。校验规则不是医学事实蕴含证明，不能保证消除幻觉。

## 本地工程演示

无需任何外部模型。命令只启动绑定 `127.0.0.1` 的临时模拟接口，并在结束后关闭：

```powershell
python tools/run_local_demo.py
```

打开 `artifacts/local_demo_review.html` 查看原始资料与模拟输出。该输出有醒目标识，只回显资料，不代表真实病历生成效果。根目录不自动创建 `.env`，模拟配置局限在演示目录中。

## 真实模型对照实验

仅使用赛方允许的模型与已获准开发数据。每次实验复制运行代码、提示词和配置至新的独立目录，保留该次完整版本，禁止覆盖已有实验。

```powershell
python tools/run_experiment.py --input data/prepared/cmb/dev.json --env .env --variant baseline
python tools/run_experiment.py --input data/prepared/cmb/dev.json --env .env --variant grounded
```

赛方允许足够预算后再测试 `--variant extract_generate` 或 `--variant repair`。数据的 `holdout.json` 应在方法冻结后用于最终开发评估；本地 MOCK 为工程测试已遍历所有输入，不等于真实模型已在留出集上调参。公开基准可能已被模型预训练看到，不能宣称无污染。

每次输出含 `metrics.json` 与 `review.html`。自动指标仅为结构成功率、来源检查和数字一致性等工程指标，不是比赛总分。医学维度需按 `docs/CLINICAL_REVIEW.md` 人工评价。详见 `docs/EXPERIMENT_PLAN.md`。

## 测试与打包

```powershell
python -m unittest discover -s tests -v
python tools/build_submission.py --team-id YOUR_TEAM_ID
```

仓库的 GitHub Actions 会在 Windows、Linux 的 Python 3.11 上执行工程测试、19 例受控扰动回归、142 例本机 MOCK 批处理与独立算法 ZIP 验证。CI 不使用真实模型、密钥或正式病例。实际测试数量及结果见 `artifacts/verification.json`；远程 CI 状态以 Actions 页面为准。

算法 ZIP 根目录直接包含入口、提示词、源码、配置和说明；排除实际 `.env`、病例数据、答案、模拟服务和任何预生成输出。打包器不上传任何内容。

正式运行后，用本次运行目录打包结果：

```powershell
python tools/build_submission.py --team-id YOUR_TEAM_ID --run-dir .
```

打包器核对输入、输出、代码、提示词与配置哈希，拒绝把带 `MOCK` 模型标识的测试输出当作正式结果。拒绝 MOCK 不能认证任意自定义服务是真实指定模型，实际模型及版本仍须赛方核实。

原规则正文要求 `results.json`，技术附件要求运行产物 `output.json`。当前打包器保留二者的相同字节副本，并附原始日志；最终应向赛方确认打包口径。算法运行始终生成技术附件要求的 `output.json`。

## 数据、检索和适用范围

正式运行不联网搜索病例、不读取本地公开病例集、不加载标题或参考答案、不使用外部知识库。公开数据仅用于离线开发；若未来加入检索，须按赛方要求登记资料并固定版本。MIMIC 派生数据单独遵循原 ODbL 许可，CMB 遵循发布方 Apache-2.0 声明；工程源码与第三方数据许可分开处理。

完整方法说明见 `docs/PROJECT_DESCRIPTION.md`，简明启动说明见 `开始使用.md`。声明文本是待队伍核对并签署的材料草稿，未代替任何人签名或向赛方提交。
