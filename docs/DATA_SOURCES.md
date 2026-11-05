# 公开病例与数据来源核查

核查日期：2026 年 11 月 4 日。下列资料按发布方原始页面核对。项目实际下载的每个文件均在 `data/download_manifest.json` 记录完整 URL、版本、字节数和 SHA-256；再次运行下载器默认重放固定清单，而非静默切换版本。

## 已下载并接入

### CMB-Clin

- 原作者仓库：https://github.com/FreedomIntelligence/CMB
- 官方数据卡：https://huggingface.co/datasets/FreedomIntelligence/CMB/blob/main/README.md
- 论文：https://arxiv.org/abs/2308.08833
- 发布方声明许可：Apache-2.0。仓库许可证及官方数据卡均已保存；许可判断依据发布方声明，不把仓库开源等同于重新确认所有底层教材权利。
- 原始规模：74 例中文复杂病例，带标题和问答参考。属于教学病例分析基准，不能称作本赛题的住院首次病程标准答案。
- 项目转换：只使用 `description`，不输入可能泄露诊断的 `title`、`QA_pairs`。参考答案单独放在 `data/references/`，正式运行及算法 ZIP 不读取、不包含。
- 时序初筛：对含术后、入院后、出院、病理、活检、治疗等风险词的整例描述进行隔离，不自动将其截成“无风险病例”。22 例通过启发式初筛（16 例开发、6 例留出），52 例进入 `quarantine.json`。词法规则非常保守，可能把“未做病理”或既往手术史也隔离；通过规则同样不保证真实入院时间明确，须人工逐例核对。
- 截止点：`T0_CASE_DESCRIPTION` 仅表示教学描述的资料边界，是改造假设，不是原始数据提供的真实入院时间。不能据此宣称已构建真实临床前瞻性评估。
- 原始描述按连续段落原文切分，不修正数值和术语，不凭参考答案补充缺失症状。
- 公开基准可能已包含在模型预训练资料中，留出集只能减少本项目开发中的调参偏差，不能证明不存在预训练污染。

### MIMIC-IV Clinical Database Demo 2.2

- 发布页：https://physionet.org/content/mimic-iv-demo/2.2/
- DOI：https://doi.org/10.13026/dp1f-ex47
- 发布方许可：Open Data Commons Open Database License v1.0（ODbL-1.0）。原许可已保存，派生数据目录另附许可，不以工程代码许可覆盖数据库许可。
- 开放程度：Demo 可直接访问，包含 100 位去标识化患者；**不包含自由文本临床记录**。完整 MIMIC 和 MIMIC-IV-Note 另有资格认证与数据使用协议，不能直接当作无门槛公开下载资源。
- 下载内容仅有 `patients`、`admissions`、`d_labitems`、`labevents` 四表及许可、说明、官方校验和。下载文件已与发布方 SHA256SUMS 比对。
- 构建 85 例练习输入：每位患者选最早满足条件的一次入院，保留 2,719 条检验，按患者分为 68 例开发和 17 例留出。
- 截止点设为入院后 6 小时，保留同一入院编号下、采样/记录时间位于入院前 6 小时至截止点之间且 `charttime <= storetime <= cutoff` 的结果；缺失结果可用时间或时间次序异常的一律排除。该窗口是项目开发设定，不是赛方规定。
- 官方字段解释：https://mimic.mit.edu/docs/iv/modules/hosp/labevents.html 。`charttime` 通常接近采样时间，`storetime` 是实验室系统中可用时间。仅筛采样时间会泄漏尚未得到的检查结果。
- 只向输入提供性别、时间和符合条件的检验，不提供出院诊断、死亡、结局、未来记录或医疗人员身份。英文检验名和单位保留；少数常见项目增加中文对照，未自行转换单位。
- 缺少主诉、病史与体格检查，因此只能用于数值、时序、来源和批处理练习，不能据此评分完整首程或诊断能力。
- 原始数据中的 `flag` 为空时只写“原实验室未提供异常标记”，不直接推定检验正常。
- 数据库引用：Johnson A, Bulgarelli L, Pollard T, Horng S, Celi LA, Mark R. MIMIC-IV Clinical Database Demo (version 2.2). PhysioNet, 2023. DOI 10.13026/dp1f-ex47。

## 已调查，暂未纳入项目运行数据

| 数据 | 发布方入口 | 适用性与处理决定 |
|---|---|---|
| CMExam | https://github.com/williamliujl/CMExam | 仓库声明 Apache-2.0，主要是医学考试选择题；可后续筛查临床题干，不能把选项、答案、解释混入入院资料。目前未下载，避免题型偏差。 |
| MedQA | https://github.com/jind11/MedQA | 仓库 MIT，含多语种考试题与教材资源；底层试题/教材权利需另核对。当前任务更需要首程生成资料，未导入教材或答案。 |
| Synthea | https://synthetichealth.github.io/downloads.html | 官方合成患者记录可用于开发，生成器为 Apache-2.0；是合成而非真实病例，适合结构和时间测试。已有 MIMIC Demo 后暂未重复下载。 |
| MIMIC-IV-Note | https://mimic.mit.edu/docs/iv/modules/note/ | 包含出院摘要和影像报告，完整数据需认证及单独协议；出院摘要还存在强烈未来信息风险。未下载、未绕过访问限制。 |
| MedDialog | https://github.com/sidney1994/Medical-Dialogue-System | 原仓库说明原始中文对话版权属于 haodf.com。公开可见不能直接视为无限制可用的开放病例，且问诊对话不是住院首程资料。未批量抓取。 |

## 正式比赛边界

开发病例和正式检索资料是两种用途。当前生成程序不读取任何公开病例或参考答案，仅接收主办方的 `input.json`。若后续新增本地 RAG，必须先获得赛方允许并固定资料版本、来源和许可；不得在正式评测期间临时联网检索病例答案。

公开资料、合成工程样例和主办方示例分别标识。人工核查表和全部转换来源可追溯，但当前未获得临床专家对开发数据的逐例审定。
