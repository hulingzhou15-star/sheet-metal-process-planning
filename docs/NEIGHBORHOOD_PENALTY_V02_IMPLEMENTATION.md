# 邻域 penalty v0.2 实施结果

日期：2026-10-16。实施依据：`NEIGHBORHOOD_PENALTY_V02_DESIGN.md`。采用独立入口，历史定义及产物不覆盖。

## 已生效行为

事实入口 → 几何邻域 → 固定P5关系清单 → H1–H7下证明P5最小值 → 在该最优域探索次级目标 → 分数、代表性与多样性选择。

P5不是普通加权和的一项。新入口不添加旧N1的逐对不同序条件；P5=0最优时固定最优值自然实现全部有效pair异序，P5>0最优时允许输出硬合法的残余罚分方案。只有OPTIMAL或独立复核的非负零见证可以宣布最优。旧N1入口保留。

次级目标为0.30×面积排序 + 0.20×依赖额外间隔 + 0.50×同类动作额外拆散。P1/P2沿用v0.6数学口径；合并拆散按全部已配置OperationType分组，BEND再按已有方向分组，在H1–H7下证明K_min/K_max。方向先在BEND类型内平均，避免类型重复计权。没有调整空间不参与平均。所有评分和CP目标使用精确有理数，不将权重四舍五入成近似整数。

类型及阈值均为试验偏好，不是实际模具间隙、碰撞证明或模型标签。未知BEND方向、未证明的拆散边界、未知P5最优值均明确报告，不能换用capability数量猜测界限。

## P001结果

- 三类混合动作的优先邻域共133对；94对已有硬先后、17对合法工序域无交集；有效P5为22对翻孔—异形孔。
- P5最小值0，有完整硬合法见证。64个唯一候选、16个入选，全部硬合法且P5=0。
- 四通道各生成16个：次级总分、合并拆散、额外间隔、随机探索。本次64次尝试全部OPTIMAL；这不表示任意新零件都能在相同预算内证明最优。
- 8个因次级分数入选，1个为最少间隔代表，7个保留多样性。最少拆散代表已包含于前8个。
- 原N1的16个方案在新合并拆散口径下全部为1；新池已出现0、2/3、1。未要求探索预算一定覆盖所有可能分值。
- 当前有选择空间的类型是CIRCLE_PUNCH、IRREGULAR_PUNCH、BEND:ROUGH；FLANGING范围[1,1]、SMOOTH范围[3,3]，均不处罚其必要工序数量。
- 最低次级分数候选NP2_001：P5=0，次级约0.058866；圆孔16个集中OP20、异形孔38个集中OP20、ROUGH折弯14个集中OP40。上述工序由本件求解结果得到，不是跨零件固定偏好。
- O066/O200、O015/O196、O031/O196、O035/O196在新64个及入选16个中同序均为0。
- 36个对照：16个入选与最近落选、16个入选与原N1、4个已知案例。完整记录全部动作移动、各项贡献差以及其它邻域状态变化。48个落选方案有排名及多样性竞争记录。

高次级分数的多样性代表不冒充分数最优；P5=0不意味着其它邻域或未建模工艺均合理。原新方案的分布差异只作描述性比较，不能据此宣布已获专家验证。

## 入口与产物

```powershell
.venv-m3/Scripts/python.exe -B scripts/run_neighborhood_penalty_v02.py --baseline outputs/neighborhood_n1 --history outputs/neighborhood_v01 --evidence outputs/proximity_geometry_review/geometry_evidence.json
# 新零件不需要专家答案、历史候选或几何缓存：
.venv-m3/Scripts/python.exe -B scripts/run_neighborhood_penalty_v02.py --input path/to/new_part.xlsx --output outputs/new_part_penalty_v02
.venv-m3/Scripts/python.exe -B -m unittest tests.test_neighborhood_penalty_v02
.venv-m3/Scripts/python.exe -B scripts/verify_neighborhood_penalty_v02.py
```

使用不同事实或配置必须换一个输出目录。历史候选只在新池及选择固定后打开；不能作为warm start，亦不使用专家负载或reference距离。新零件无专家列、无缓存、无baseline的完整CLI路径已有合成工作簿测试。

输出目录：`outputs/neighborhood_penalty_v02/`。

- `penalty_internal_review.html`：可离线打开的内部对照，包含构造角色，不是专家盲评包。
- `REPORT.md`、`verification.json`：结果及审计。
- `p5_catalogue.json`、`primary_optimization.json`、`fragmentation_bounds.json`：规则清单及证明。
- `candidate_pool.jsonl`、`selected_candidates.jsonl`、`generation_report.json`、`selection_report.json`：分配、求解记录、入选与落选理由。
- `historical_rescoring.json`、`cohort_summary.json`、`plan_scores.csv`：新口径复评，不覆盖历史分数。
- `comparisons.json`、`all_comparison_changes.csv`、`known_case_coverage.json`：完整对照。
- 四张预览PNG：`preview_o066_penalty.png`、`preview_whole_penalty.png`、`preview_difference_penalty.png`、`preview_selection_explanation.png`。

配置集中在`configs/neighborhood_penalty_v02.json`，几何范围引用原`configs/neighborhood_v01.json`，不回写旧配置。

## 验证

本轮相关96项测试通过，其中新增36项。包括正残余最优、FEASIBLE零见证证明、FEASIBLE正值未决、UNKNOWN及未证明边界、精确目标与独立评分一致、跨类型归一化、必要拆分、无专家CLI及原有约束/几何回归。

Edge核查36个对照、22个关系、4个已知案例、270°几何命中、轮廓、工序颜色、差异过滤、同步平移缩放、分组边界与选择解释，页面错误和HTTP请求均为0。四张PNG均作可视检查。

独立验证脚本重新计算22155对源几何，重建清单，以另一套占用表达式重新求10组极值，逐方案重算155个新旧方案分数，检查完整移动和选择竞争日志，再核验既有文件SHA。最终结论见`verification.json`。

本轮未重跑全部历史套件；此前5项冻结快照失败没有通过改动旧断言掩盖。Office锁文件不读取、不删除、不关闭其工作簿。

## 新增文件

```text
configs/neighborhood_penalty_v02.json
docs/NEIGHBORHOOD_PENALTY_V02_IMPLEMENTATION.md
src/neighborhood_penalty/__init__.py
src/neighborhood_penalty/config.py
src/neighborhood_penalty/catalogue.py
src/neighborhood_penalty/scoring.py
src/neighborhood_penalty/model.py
src/neighborhood_penalty/generation.py
src/neighborhood_penalty/review.py
src/visualization/neighborhood_penalty_review.html
scripts/run_neighborhood_penalty_v02.py
scripts/verify_neighborhood_penalty_v02.py
tests/test_neighborhood_penalty_v02.py
tests/neighborhood_penalty_v02_browser_qa.cjs
```

未实现模型训练、专家结果自动标注、新专家盲评打包或其它邻域组合的新评分。P001已参与开发，后续泛化评价遵守训练/验证隔离。
