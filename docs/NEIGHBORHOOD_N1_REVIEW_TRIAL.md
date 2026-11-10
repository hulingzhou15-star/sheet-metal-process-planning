# N1 送审筛选策略试验

这是2026-10-16确认执行的六步计划：固定适用关系、审计旧池、联合可行性、新候选生成、效果/差异对照、内部验收材料。它独立于已冻结的M1/M2/M3定义，暂不作为正式工艺约束或训练监督。

## 精确范围

从本件主场景NeighborhoodContext取关系：

```
kind = EXTERNAL_NEIGHBOR
arc.anchor.operation_type = FLANGING
arc.neighbor.operation_type = IRREGULAR_PUNCH
arc.tier = PRIORITY
```

方向必须是FLANGING发出的PRIORITY arc。反向IRREGULAR的优先层不能替代FLANGING侧的扩展层。内部PRE_PUNCH关系、扩展邻域、折弯及其它组合不进入N1。

对每一适用pair `(f,i)`，试验候选要求：

\[
station_P(f)\ne station_P(i).
\]

不新增加工先后，不指定OP20/OP30，不使用专家分配推导范围。3倍记录直径和相对断层仍为原邻域试验口径；无孔径的10源单位不作为N1分序范围依据。工序字符串仅作本件ID，不作跨零件类别。

H1–H7继续是完整硬约束引擎的全部正式判定。一个方案可以通过H1–H7、同时未通过N1试验筛选，报告必须分别展示。N1不是H8，不加入P1–P3或SoftPenalty，不从“通过N1”制造质量/偏好标签。

## 联合检查与停止

先单独建立H1–H7可行性，再把所有N1条件同时加入CP-SAT。

- `joint_feasible`：有经独立复核的完整见证，可以生成。
- `joint_infeasible`：证明H1–H7＋全部N1无解，停止新候选生成，保留冲突分析。
- `joint_feasibility_unknown` / `hard_feasibility_unknown`：预算内未建立可行性，不当作无解，也不放宽规则。
- `hard_infeasible`：H1–H7本身无解，不归咎于N1。

无解时，每对N1用assumption开关获得充分冲突集，随后在配置预算内逐条删除重解。只有每条保留关系均有删除后可行的证据，才报告“删除最小”；不是最少基数冲突集。整个H1–H7模型始终保留，未声称其中的硬规则也已经最小化。

## 生成和独立性

正式生成函数只接收InferenceContext、N1 catalogue、配置及联合状态，不接收历史候选、专家、盲号或评价答案。

每次从事实重新建立H1–H7＋全部N1模型，用固定种子的随机分配目标探索，no-good排除已得分配。联合可行见证仅用于复核，不插入池、不作warm start。目标16个，预算32次；不足则返回实际数量及精确状态，不为凑数放宽条件。

每个候选由H1–H7引擎和独立N1相等检查复核，完整分配hash去重，再按候选间farthest-first选16个。权重、专家负载、reference distance不参与。单worker和确定性预算有助复现，但墙钟上限仍可能导致跨机器边界差异，不声称均匀采样。

历史75个池、旧16个selected只读审计和过滤。运行脚本先生成并固定全部新候选，再打开历史池与四组已知案例资料用于离线对照，避免把这些方案作为隐含初始化。案例ID不写进N1选择逻辑。

## 对照与解释

五组分别报告：旧pool、旧selected、旧pool通过N1后的子集、新pool、新selected。

每组包含逐方案H1–H7/N1结果及具体违规pair、逐pair同序次数、候选间Hamming差异、Station负载/类型分布、每Operation实际使用的Station、其它外部邻域同序统计。

旧16与新16提供等样本数的描述性比较；生成方法不同，不能把差异变化直接解释为N1的因果代价。旧75通过比例不是可行空间体积保留比例。空筛选池的min/mean/max应为null，不造0或虚假多样性。

新selected在生成结束后与最近旧selected配对展示；四个已知案例另外从原pool选择确有同序问题的方案作对照。记录所有Operation变化、其它邻域同序状态新增/消除。配对通常涉及多个动作，不冒充只改变一个因素的局部实验，不自动说新方案更优。

内部HTML展示完整/局部、工序颜色、差异模式、270°视角、恢复外轮廓、点击详情及同步缩放/平移。页面明确显示试验构造角色，不是专家盲评包。旧R02不修改，继续暂停。

## 参数和运行

唯一新配置：`configs/neighborhood_n1.json`。邻域本身的参数继续引用`configs/neighborhood_v01.json`。两者及所有旧产物均不回写。

```powershell
.venv-m3/Scripts/python.exe -B scripts/run_neighborhood_n1.py --baseline outputs/neighborhood_v01 --evidence outputs/proximity_geometry_review/geometry_evidence.json
# 新零件既可以没有专家列，也可以没有旧候选/几何缓存：
.venv-m3/Scripts/python.exe -B scripts/run_neighborhood_n1.py --input path/to/new_part.xlsx --output outputs/new_part_n1
.venv-m3/Scripts/python.exe -B -m unittest tests.test_neighborhood_n1
```

输出目录`outputs/neighborhood_n1/`：n1_policy.json、n1_pair_catalogue.csv、joint_feasibility.json、generation_report.json、candidate_pool.jsonl、selected_candidates.jsonl、cohort_audit.json、plan_audit.csv、station_load_statistics.csv、known_case_coverage.json、comparisons.json、comparison_changes.csv、other_neighbor_changes.csv、n1_internal_review.html、预览PNG及REPORT.md。

验收关注四组已知问题全部覆盖、新候选全部合法且N1违规为0、实际候选数量与多样性，以及尚存的其它邻域问题。这个结果只建立更有针对性的内部审阅基础，不等于已证明满足全部生产工艺。
