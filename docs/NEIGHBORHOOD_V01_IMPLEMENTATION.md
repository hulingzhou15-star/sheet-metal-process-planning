# 几何邻域与候选局部探索 v0.1

日期：2026-10-16。独立试验版本，执行依据为本轮确认的邻域上下文、候选局部调整和可视化核查计划。`PROJECT_SPEC.md`及冻结定义保持原样。

## 信息流与边界

新零件事实（可无专家列）→ InferenceContext → NeighborhoodContext → 独立基础候选和邻域局部变体 → 完整H1–H7复核、去重及候选间多样性选择。

本轮未训练评分模型、未建立生产推荐服务。候选的邻域状态用独立Overlay记录，未来可由新版本模型输入适配器消费。没有修改Graph0/1/2或增加其中的边；新邻域并不是旧Graph2的kNN定义。

正式入口不读取专家方案、负载、盲号、比较答案或历史候选。P001人工指出的四组案例只在独立离线回归脚本读取，不进入生成配置、种子或采样优先级。不能用旧D标签推导pair冲突标签。

## 邻域定义

主动类型为FLANGING、BEND、IRREGULAR_PUNCH。PRE_PUNCH和CIRCLE_PUNCH仅作为被检查对象，不主动凑邻居。DIMPLE/BRIDGE保持类型政策待定；无geometry保持缺少几何。没有匹配只表示当前试验规则未找到，不能推断无需工艺邻居。

同process_group的PRE_PUNCH—FLANGING为内部工艺关系，两个Operation分别保留，内部关系不参与外部相对排序。CAD parent不是自动合并特征的依据；CAD、hard precedence及region先后证据单独保留，不随邻域过滤变化。

| 组合 | 距离 | 试验范围 |
|---|---|---|
| 含FLANGING的圆形—圆形 | 圆心距离 | 3×该FLANGING最大记录同心圆直径 |
| BEND—圆形动作 | 圆心到有限线段 | 3×圆形动作记录直径 |
| IRREGULAR_PUNCH—圆形动作 | 实际记录曲线最短距离 | 3×圆形动作记录直径 |
| BEND—IRREGULAR_PUNCH | 实际曲线最短距离 | 暂定10源坐标单位 |
| IRREGULAR_PUNCH—IRREGULAR_PUNCH | 同上 | 暂定10源坐标单位 |
| BEND—BEND | 有限线段最短距离 | 暂定10源坐标单位 |

双FLANGING分别用自己的尺度判断；任一侧通过即保留无向关系，同时保留触发方向。其它组合在满足范围后仅从主动类型发出查询方向。简单孔可从BEND/IRREGULAR侧被发现。

3倍直径不是工具/模具直径或安全间距；异形孔不制造等效直径。10源单位不是经验验证后的工艺界限。P001沿既有毫米坐标观察，新零件应遵守相同源坐标单位契约或先明确其单位；本模块不猜测、不自动换算。5/10/15绝对值与2/3/4倍径提供单因素对照，不自动选最优阈值。

每个anchor下，按family、metric分别排序已通过范围的外部邻居。使用M2的9位数值桶比较，ID只用于平局稳定排序。相邻正距离首个≥3倍跳变之前为PRIORITY、之后为EXTENDED；两层全部保留。零到正数没有有限ratio，等距离桶不拆开。无断层则全部PRIORITY。这里的优先是局部关注层级，不是已证明的冲突强度或方案质量。5只控制页面初始行数，绝不控制计算或采样邻域个数。

最短位置、圆心投影、端点/段内、夹角、双侧投影重叠、采样分位数及已有先后关系保留作证据。曲线相交可以入邻域，但不自动禁止同序；长折弯不能按全长度平均距离剔除局部接近。24点采样不是精确接近长度、不能取代解析距离。

## 候选局部探索

新入口复用v0.7无reference基础生成器，创建全新输出目录。配置的基础池默认16个，选4个多样性基础方案；每个基础方案按family、tier、当前同/分序分层循环抽取最多24组关系。桶内按归一化距离分层覆盖，未抽中及硬先后强制分序关系均有审计清单。

同序pair尝试分序，分序pair尝试同序。两个分支分别固定B移动A、固定A移动B；其它Operation允许因硬约束连带移动。若两分支都没找到解，再尝试两端均可动。目标仅是该分支改动Operation数最小，所有变化完整披露，允许FEASIBLE但仅OPTIMAL报告最小性。INFEASIBLE仅证明当前分支不可行，UNKNOWN表示预算内没找到解。

新变体与基础池按完整分配hash去重，多重来源均保留，最终用候选间farthest-first选16个。预算、单worker、派生种子、求解状态及耗时保存；墙钟上限仍可能影响边界情况下结果，不承诺跨机器绝对确定性。尚无质量模型，选中的方案仍可能包含被专家驳回的同序安排。

比较按两方案hash去重，每次局部尝试及其所有连带移动可追踪。answer/confidence/reason_note留空，不自动宣称变体胜出。Overlay给出每条关系是否同序、rank差和gap；Station rank来自Station.order排序，不解析OP字符串。局部改变还记录受影响的其它邻域关系状态，不把同序总数作为分数。

## 页面

`neighborhood_review.html`是内部核查页面：五种参数场景、224个Operation、内部/外部列表、优先/扩展层、真实几何与恢复外轮廓、270°统一显示映射、命中图元、同步缩放平移及候选基础/变体Station过滤。

页面包含构造方向、变化记录，不符合盲评包装要求，不直接发送专家。旧R02包不更新，继续暂停。`known_cases/neighborhood_review.html`仅供已知问题回归核查。

## 参数与入口

配置唯一入口：`configs/neighborhood_v01.json`。规则、相对倍率、敏感性值、采样预算、求解预算及显示参数集中在该文件。范围数值不在业务逻辑里散落。

```powershell
.venv-m3/Scripts/python.exe -B scripts/run_neighborhood_v01.py
# 可选：使用事实指纹匹配的已有全量几何证据缓存；新零件无需缓存
.venv-m3/Scripts/python.exe -B scripts/run_neighborhood_v01.py --evidence outputs/proximity_geometry_review/geometry_evidence.json
.venv-m3/Scripts/python.exe -B scripts/audit_neighborhood_p001_cases.py
.venv-m3/Scripts/python.exe -B -m unittest tests.test_neighborhood_v01
```

主产物在`outputs/neighborhood_v01/`，含REPORT.md、neighborhood_context.json、sensitivity.json、candidate_exploration.json、candidate_pool.jsonl、selected_candidates.jsonl、candidate_neighborhood_overlays.json、known_case_audit.json及HTML/PNG预览。

验证脚本会从原始事实重新计算几何（不依赖缓存）、重建完整邻域指纹、独立复核候选与Overlay、检查原有文件SHA、测试日志及真实Edge检查结果。历史全套测试中因已确认原始数据修正导致的快照失败单独记录，不回写旧快照。

## 下一阶段条件

先核查几何入域是否合理、敏感性是否过强、局部变体是否可供工艺比较。规则确认后再设计新盲评材料和训练适配；当前既没有统一“近邻必须分序”规则，也没有学到这种偏好。不能把本版本视为已解决旧R02候选质量问题或已具备生产推荐质量保证。
