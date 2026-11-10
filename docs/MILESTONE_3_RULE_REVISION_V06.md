# M3 v0.6：确认规则修订与历史候选复核

日期：2026-10-15。规则版本：m3.v0.6。

## 授权与边界

用户确认 CAD 先后必须为 child→parent；两处笔误已由用户在原表修正：O024.parent=O023、O047.parent=O046。重新检查 153 条关系全部满足参考方案，允许按全量有效关系实现。

本版本使用独立的 `src.constraints.v06` 入口。旧 `src.constraints`、候选生成器、M1/M2、图定义、旧规范及历史结果保持原样。新入口不会静默替换旧版 API。历史候选仅复核，不生成或覆盖；不重建盲化评审包，不训练模型。

## 硬约束

H1–H6 复用原定义。H7 / FEATURE_TREE_PRECEDENCE：每个 child.cad_parent_operation_id=parent 要求 order(P(child)) < order(P(parent))，全类型生效，严格排除同 Station。只用 Station.order，不比较名称或几何。

Context 校验 CAD parent 存在、非自引用，且 H3/H4/H6/H7 合并图无环。保留每个来源；H7 来源定位为 Sheet1 的 cad_parent_operation_id 单元格。局部 capability 不可行、或无环但无法放入有限 Station 的组合，由可行性求解明确报告，不修复输入。

先验证 expert；expert 不可行时停止 soft 和候选后续结论。没有专家的未来正式推理不应调用这个离线评估脚本；本版纯硬可行模型和方向边界求解不读取专家分配、G1 或专家距离。

CAD_PARENT 图边方向依然 parent→child；制造约束的反向顺序不翻转图边。数据纠错改变新构建实例中的两条 CAD 边，图的定义不变。

## P1–P3 的版本修订

P1 保持 1.15 面积显著差异、同序不计 inversion。先排除原 region ancestor/descendant，再排除其它已被 H3/H4/H6/H7 直接或传递确定顺序的 BEND pair，避免硬规则对应的恒定项稀释分量。输出两个互斥排除集合和路径依据。

P2 按唯一有向 (before,after) pair 求原局部 g_min/g_max 与实际 gap，g 使用连续 Station rank。gap 上下界仍仅依赖该 pair capability、fixed assignment 和严格前后，不改成全局边界。每个 pair 只计一次，所有原始来源均保留。source 统计允许重叠，不能把 source count 或 contribution 再相加作 total；另给互斥 source-set 统计以便核账。g_min=g_max 时 penalty=0。

P3 原 FLANGING fragmentation 定义不变，只启用 FLANGING。

权重保持 P1=0.45、P2=0.20、P3=0.35，总和为 1。新版 total_soft_penalty 仅包含这三项；新版 P1/P2 口径已变，不能与旧版本数字直接作排名对比。

## BEND_DIRECTION_FRAGMENTATION（单独报告）

复用 SMOOTH/ROUGH 材料侧标签，按本项目用户给定的 BEND 方向解释使用，不推导最终三维姿态。BEND 缺失合法类别时明确报错，不猜测。

令 F 为全部 H1–H7 硬可行方案，不含 reference、G1 或 soft 目标。K_d(P) 为方向 d 占用的不同 Station 数。K_min,d=min(F)K_d，K_max,d=max(F)K_d，采用 CP-SAT 证明准确极值。缓存属于当前不可变 context 和 solver 参数；不从 expert 或候选样本学习边界。

p_d=(K_d−K_min,d)/(K_max,d−K_min,d)。边界相等时 p_d=0；仅对 present 且 K_max>K_min 的方向求平均，集合为空时分量为 0。每项范围 [0,1]。边界未知或硬可行域为空时报告失败，不使用近似值或截断掩盖问题。不同方向各自最低值不保证能同时实现。

当前该分量无权重，不进入 total_soft_penalty。SMOOTH 若 [3,3]，不会被处罚，也不能衡量三个 Station 内负载是否更集中；本版本不追加负载偏好。

## 邻近折弯异形孔（独立诊断）

用户明确此处指翻孔附近的折弯异形孔，自身预孔仍是 PRE_PUNCH。所有 46 个翻孔的 process_group 都是 PRE_PUNCH/FLANGING；不把 IRREGULAR_PUNCH 自动认作其配套孔。

复用冻结 Graph2 默认 k=8 的 KNNDiagnostics.selected_neighbors，截取每节点前 k=5 项；distance_norm×length_scale 恢复边界距离。保持距离舍入与 ID 决胜逻辑。k 可配置但不能超过输入已保存的原始近邻长度；不从对称边列表截 k 条，不另写距离逻辑。

任一方向入选即纳入无向 union，按排序 ID 对去重。报告双侧 rank、全类型同序/异序分布、无共同 capability 或硬路径造成的强制分序证据、FLANGING 逐个最近邻列表。IRREGULAR_PUNCH 的 CAD parent 若为 BEND 则加事实标记；这不等于声明它与某个翻孔一一配套，也不是几何推导的新关系。该强制分序统计是充分条件下界，未标记者不代表已证明可同序。

用户给定案例 O131（翻孔）/O203（折弯异形孔11）互为近邻：rank=2/3，边界距离约 2.1，expert 为 OP30/OP20。O203 的 CAD parent 为空，因此 BEND parent 不能作为纳入折弯异形孔的必要条件；仍按 IRREGULAR_PUNCH 和真实几何近邻纳入，保留来源特征名，不新增类型或配套关系。

尚未配置冲突类型集合，不实现或启用 PROXIMITY_CONFLICT 评分，不给新权重。候选公式与待确认问题保留在先前分析报告：无向 configured pair 上同 Station 指示函数的平均，空集合为 0。确认前不加入总分、predecessor 或图边。

## 参数与运行

全部新数值入口：[configs/m3_constraints_v06.yaml](../configs/m3_constraints_v06.yaml)。包括权重、面积阈值、方向类别、诊断 k、solver 时间/种子/worker。方向 weight 必须为 null；不允许静默给权。

运行：`.venv-m3/Scripts/python.exe -B scripts/evaluate_rule_revision_v06.py`。

输出单独写入 outputs/m3_rule_revision_v06：专家完整报告、候选池/selected 复核、逐条 violation 与新分量、输入配置 SHA-256 和本轮冻结完整性。旧报告不覆盖。旧评审标签继续保留其规则版本；新训练样本按新可行性另行筛选。

## 后续里程碑

完成本版后，先确认 proximity 冲突集合及新 soft 权重，再决定新版候选生成和评审。正式无参考生成、五零件模型训练仍按 [训练实施流程](REFERENCE_FREE_INFERENCE_AND_TRAINING_PLAN.md) 单独实施。本版只提供可复用的 reference-free 硬可行模型，不实现正式 CandidatePlanGenerator。P001 已参与规则设计，不能再称为完全未参与开发的测试零件。
