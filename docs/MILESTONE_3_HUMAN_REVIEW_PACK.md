# Milestone 3 v0.3：Human Review Pack

本轮只消费 v0.2 的 60 个 selected candidate 和独立 expert reference。
不重新生成 pool，不修改 selected、generator 参数、H1–H6、P1–P3、D1–D3、G1、Graph0/1/2 或 Sheet1–Sheet5。
输出是人工评审准备材料；不生成 preference label，不自动判断哪个 Plan 更好。

## 新增文件

新增 15 个源文件，既有文件修改数为 0：

```text
configs/m3_human_review.yaml
src/review/__init__.py
src/review/config.py
src/review/representative_selector.py
src/review/plan_diff.py
src/review/pair_selector.py
src/review/blinding.py
src/review/report.py
scripts/build_human_review_pack.py
tests/review_fixtures.py
tests/test_m3_review_representative_selector.py
tests/test_m3_review_plan_diff.py
tests/test_m3_review_pair_selector.py
tests/test_m3_review_blinding.py
docs/MILESTONE_3_HUMAN_REVIEW_PACK.md
```

没有新增 dependency。使用既有 `.venv-m3` 运行：

```powershell
.venv-m3/Scripts/python.exe -B scripts/build_human_review_pack.py
.venv-m3/Scripts/python.exe -B -m unittest discover -s tests -v
```

CLI 可指定 `--input`、`--candidate-dir`、`--config`、`--output`。
配置沿用 YAML 合法的 JSON 子集，修改时保留 JSON 语法。

## 输入与冻结保护

入口读取并校对：

- selected_candidates.jsonl：60 个，NEAR/MEDIUM/FAR 各 20，使用冻结 serializer 校验 hash。
- selected_candidate_summary.csv：ID、hash、bucket、distance 和 SoftPenalty 一致。
- selected_plan_assignments.csv：224 个 Operation 全部覆盖，与 JSONL 和 reference 完全一致。
- expert_reference.json：与 M1 `ExpertReferencePlan` 逐 Operation 相同。
- Sheet5：通过冻结 `load_constraint_context` 取得 59 个 BEND influence metadata。

使用冻结 HardConstraintEngine/G1 再检查已有 Plan；使用冻结 `PlanDistance`，同一 M=113，绝不复制 distance 公式。
只为 reference 补算现有 SoftPrior 值用于内部 pair 设计；候选 score 直接读取既有结果。
没有调用 solver 或 CandidatePlanGenerator 来产生本轮材料。

开始时记录 87 个文件的 SHA-256，覆盖 M1/M2/M3 v0.1/v0.2 源码、配置、测试、规范、候选/报告和原始 Excel。
构建前后均检查；不会自动接受既有 frozen manifest 的变化。
本轮结果为 **87/87 全部一致**。

## 代表选择

每个 bucket 按以下顺序从剩余候选中选择，保证四个不同 Plan：

1. LOW_SOFT：最低 Total SoftPenalty，hash 打破并列。
2. TYPICAL：在移除 LOW_SOFT 后的剩余 19 个中计算 distance/soft 的 median 和 range。
3. HIGH_OP60_LOAD：剩余候选中 OP60 动作数最大；并列按 penalty、hash。
4. DIVERSITY：剩余候选到前三个的最小 d_norm 最大；并列按 penalty、hash。

**R2 使用剩余 19 个的统计范围，已由用户明确确认。**

```text
z_distance = (distance - median_distance) / max(1, distance_range)
z_soft = (soft - median_soft) / max(epsilon, soft_range)
typical_score = hypot(z_distance, z_soft)
```

epsilon 当前为 1e-12。TYPICAL score 并列时按 penalty、hash 保持确定性。
该 score 只用于选择代表点，不是新 soft prior、工艺质量或 preference label。
全部 19 个 TYPICAL score、median/range、DIVERSITY min-distance 和角色统计保存在内部 JSON。

| Internal ID | Bucket | Role | Expert raw distance | SoftPenalty | OP60 load |
| --- | --- | --- | --- | --- | --- |
| NEAR_001 | NEAR | LOW_SOFT | 9 | 0.038105 | 4 |
| NEAR_005 | NEAR | TYPICAL | 13 | 0.067842 | 4 |
| NEAR_015 | NEAR | HIGH_OP60_LOAD | 13 | 0.053187 | 7 |
| NEAR_002 | NEAR | DIVERSITY | 17 | 0.073306 | 5 |
| MEDIUM_001 | MEDIUM | LOW_SOFT | 30 | 0.057342 | 7 |
| MEDIUM_011 | MEDIUM | TYPICAL | 33 | 0.087154 | 7 |
| MEDIUM_006 | MEDIUM | HIGH_OP60_LOAD | 39 | 0.071296 | 7 |
| MEDIUM_015 | MEDIUM | DIVERSITY | 33 | 0.094180 | 7 |
| FAR_001 | FAR | LOW_SOFT | 49 | 0.065685 | 7 |
| FAR_004 | FAR | TYPICAL | 52 | 0.104850 | 7 |
| FAR_010 | FAR | HIGH_OP60_LOAD | 55 | 0.072551 | 7 |
| FAR_005 | FAR | DIVERSITY | 46 | 0.099196 | 7 |

12 个代表之间 66 个 unordered pair：raw distance 最小/中位/最大为 18/44/57；
d_norm 最小/中位/均值/最大为 0.159292/0.389381/0.377849/0.504425。
SoftPenalty 范围 0.038105–0.104850；OP60 load 范围 4–7。

## 24 对 comparison

全局去重 unordered pair；同一 Plan 可参与多次比较。

| Group | Count | 设计 |
| --- | --- | --- |
| A | 6 | 每 bucket 的 LOW_SOFT、DIVERSITY 分别与 reference 比较 |
| B | 6 | 每 bucket LOW_SOFT/HIGH_OP60_LOAD、TYPICAL/DIVERSITY |
| C | 6 | NEAR/MEDIUM 和 MEDIUM/FAR 各 3，对所有可用跨档 pair 按最小绝对 soft 差贪心选取 |
| D | 6 | 3 对相近 expert-distance/大 soft gap；3 对相近 soft/大 assignment distance |

其它 Group 已采用的 pair 不允许 Group D 再次使用。
C 的匹配不消耗 Plan 顶点，允许同一个代表多次参与，禁止重复完整 pair。
同一主排序指标并列时使用确定性 hash；D 的并列先比较更接近的次指标，再 hash。

D 第一类优先 raw expert-distance gap<=5，按绝对 soft gap 降序；第二类优先 soft gap<=0.01，按 d_norm 降序。
不足时保留已选 pair，按配置逐步扩大阈值填余缺，并保存每轮 eligible/chosen count 和阈值。
当前两类均在初始阈值找到 3 对，**没有放宽**。

| Comparison | Internal pair | Assignment raw distance | Expert-distance gap | Soft gap |
| --- | --- | --- | --- | --- |
| CMP_011 | FAR_001 / FAR_005 | 45 | 3 | 0.033511 |
| CMP_021 | FAR_001 / FAR_004 | 47 | 3 | 0.039165 |
| CMP_024 | MEDIUM_001 / MEDIUM_015 | 49 | 3 | 0.036838 |
| CMP_001 | NEAR_005 / FAR_001 | 50 | 36 | 0.002158 |
| CMP_005 | NEAR_002 / FAR_010 | 52 | 38 | 0.000755 |
| CMP_008 | NEAR_005 / FAR_010 | 49 | 42 | 0.004708 |

前 3 行是相近 expert-distance，后 3 行是相近 soft。
24 个 comparison 的 raw assignment distance 最小/中位/最大为 9/45.5/52，d_norm 范围 0.079646–0.460177。
没有重复 unordered pair。

## Internal 与 expert-facing 的分离

Internal 文件可以包含真实 ID、bucket、角色、distance、soft components、reference identity 和随机 seed。
Expert-facing 只从白名单数据构建：Station load、OperationType×Station、完整 assignment、两 Plan 不同 Operation 和 BEND metadata。
不会把完整 candidate JSON 或内部 audit 嵌入 HTML。

比较涉及 12 个候选和 1 个 reference，所以统一为 **13 个 Plan 分配 PLAN_A01–PLAN_A13**。
reference 使用普通盲号，没有 REF 标识或专属编号格式。
mapping 内部保存双向可逆关系，不出现在 expert-facing。
Plan blind seed=20261012，comparison seed=20261013；comparison 的展示顺序和 A/B 朝向都打散，不把 Group A 放在固定的前六项。

Internal diff card 保留：

- 候选身份、盲号、角色、raw/normalized distance 和 P1/P2/P3 raw/weight/contribution。
- 全部 OP10–OP70 load、delta。
- 全部类型 total/changed/unchanged/transitions。
- 全部 changed Operation 的 rank delta、process group、required predecessor。
- BEND 的 axis/depth/length/area/region/parent/level。
- 参与 H3/H4/H6 来源的布尔标记；只陈述参与事实，不推断质量。

每个 blind Plan 有独立 Markdown/HTML/CSV，可查阅全部 224 个 Operation、59 个 BEND 及类型×Station 矩阵。
Comparison 卡默认先显示 Station/type/count 摘要；Operation 和 BEND 明细分别折叠。
只显示 A/B assignment 不同的 Operation，按 operation_type、operation_id 排序。
展示 delta=B load−A load，既不显示相对 reference 的修改数量，也不显示 scores 或距离。

## 空白表单与离线 HTML

统一问题：仅从当前给出的工艺方案看，你认为哪一种安排更合理？
允许 A 更合理、B 更合理、基本等价、两者都存在明显问题、信息不足五个选项。
confidence 为 1–5，可选 reason_note。

24 个 CSV form 的 answer/confidence/reason_note 均为空。
HTML radio 没有 checked，confidence 首项为空，textarea 为空。
页面没有后端、外部 CDN 或 JavaScript，不自动保存/提交；可以使用 CSV 单独记录人工判断。
重建时若 CSV 已有人工内容，拒绝覆盖；不擦除或替换人工填写结果。
CSV 正确处理 Windows newline，每条记录一行，不产生额外空白行。

## 盲化检查与验证范围

自动扫描 expert-facing 的全部 .md/.csv/.html，共 **42 个文件，0 findings**。
禁止真实 candidate ID、NEAR_/MEDIUM_/FAR_、EXPERT_REFERENCE、SoftPenalty、独立 P1/P2/P3、expert/reference、role/bucket/seed/solver 等标识，HTML 注释/属性也扫描。
P1/P2/P3 使用独立 token 边界，避免 OP10/OP20/OP30 的合法 Station 名被误判。
除了 forbidden pattern，还按真实 internal IDs 单独扫描。

HTML source 检查：14 个 HTML，24 个可折叠 comparison，120 个未选 radio，24 个空 confidence、24 个空备注，48 个完整 Plan 链接全部有效，网络资源为 0。

**浏览器视觉预览未完成。** Browser Use 的 URL 安全策略拒绝 file:// 本地文件导航，仅允许 HTTP/HTTPS；没有改换浏览器、代理或启动服务器绕过该限制。
因此本轮验证包括结构、内容、链接、表单和泄露检查，不包含实际浏览器截图与布局检查。
这项限制保存在 `internal/html_validation.json` 和总报告。

## 输出

```text
outputs/m3_human_review/
  plan_comparison_pack.zip       # 只含 42 个盲化对外文件
  human_review_pack_report.md
  human_review_pack_report.json
  internal/
    representative_selection_report.md
    representative_selection.json
    blind_plan_mapping.json
    comparison_pair_design.md
    comparison_pairs_internal.csv
    comparison_pairs_internal.json
    station_load_summary.csv
    operation_type_movement_summary.csv
    changed_operations.csv
    bend_changed_operations.csv
    diff_cards/                  # 12 份 Markdown + 12 份 JSON
    frozen_input_manifest.json
    frozen_integrity_check.json
    blinding_leakage_check.json
    html_validation.json
    pack_validation.json
    test_results.txt
    test_results.json
  expert_facing/
    review_pack.html
    review_pack.md
    comparison_pairs.csv
    plan_cards/                  # 13 份 Markdown + 13 份 HTML + 13 份 CSV
```

总报告及 internal 供研究者审计；expert-facing 是独立的盲化材料目录。
没有在对外材料中链接 internal mapping 或总审计报告。
`plan_comparison_pack.zip` 只打包 expert-facing 的 42 个文件，压缩包逐项泄露扫描为 0 findings，不含内部 JSON/mapping/score；完整解压后保留本地相对链接。

## 测试与人工可读性

既有 220 tests 加新增 36 tests，**256 tests 全部通过，0 failures/errors/skipped**。
新模块计数：representative_selector=9，plan_diff=8，pair_selector=8，blinding=11。
覆盖用户要求的 27 项，另验证 zero-range epsilon、输入不变、阈值放宽审计、隐藏 HTML 泄露、已有人工内容不被覆盖、Windows CSV 行数。
原候选及其全部既有材料保持原 SHA-256。

当前比较的主要人工工作量：每 pair 有 9–52 个不同 Operation（中位 45.5），其中 BEND 差异 3–24（中位 15.5）。
通过摘要与折叠区域减少一次呈现的明细数量；完整 Plan 分开查阅。
没有发现重复 pair、缺失 metadata、编号映射、数据不一致或明示盲化泄露。
本轮不自动填写答案，不把任何选择角色或 soft 指标解释为专家结论。
完成到此停止，等待下一步指令。
