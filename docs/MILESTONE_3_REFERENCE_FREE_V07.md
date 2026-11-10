# M3 v0.7：无 reference 候选与临近对照采样

日期：2026-10-15。实施范围为 LOCAL_PROCESS_PREFERENCE_LEARNING_PLAN.md 前四项，不包括新盲化评审页、训练 tensor adapter 或模型训练。

## 本版行为

新增独立 src.inference，不替换 M1、M2、v0.1/v0.6 约束入口和旧生成器。规则版本仍是 m3.v0.6 的 H1–H7；v0.7 标识输入、生成和采样流程。没有新增临近 hard/soft 规则。

InferenceFacts 只包含 Operation、Geometry、Station、ProcessGroup 与来源审计，不含 expert_reference_plan 字段。InferenceContext 继承已确认规则逻辑，并拒绝提供专家访问接口。Excel 读取只解析必需事实字段：expert_station 可有值、为空、缺少列、包含无效 Station、Excel 错误或无缓存公式，均不参与事实校验和决策。Sheet4/5 仍为必需 metadata；缺几何沿用 M1 定义，并记录覆盖缺口。

数据基础对象与静态验证方法复用冻结 M1 实现，显式跳过专家相关检查；没有伪造空 ExpertReferencePlan 通过旧入口。推理的硬可行性由 H1–H7 模型建立，不依赖专家能否通过。

事实指纹按事实和 metadata 规范化计算，排除来源位置、输入路径和文件整体 SHA。随机种子由事实指纹、配置 seed、阶段、序号派生。文件 SHA 仅留作来源审计，不驱动生成。

## 候选生成与筛选

复用纯硬 CP-SAT 模型，每次设置可配置的随机 Operation–Station 成本；对已找到的完整分配施加 no-good 排除。该方法探索可行域，不是均匀采样，也不宣称具有工艺质量最优性。

每个返回方案均由独立 HardConstraintEngine 再验 H1–H7。区分目标达到、可行域耗尽、硬不可行、部分结果预算耗尽、预算内无解和模型错误。没有到专家方案的距离、专家 warm start、NEAR/MEDIUM/FAR、专家负载 G1。允许独立生成恰好与专家相同的方案，不为避免相同而读取专家答案。

从池中以最小 plan_hash 为确定性起点，按既有候选间可移动 Operation 分配距离执行 farthest-first，hash 决胜。没有可移动 Operation 时允许返回唯一合法方案，不强行凑数。

当前没有软分预筛；P1–P3 和未加权方向分量单独报告，标记 used_in_selection=false。临近同序方案不会因此被过滤，可用于后续学习比较。生成前的原三项及新方向权重没有改动。

所有预算配置位于 configs/m3_reference_free_v07.json：池64、筛选16、最多96次尝试、随机成本绝对值上界100；每次求解5秒且确定性预算1.0、单worker、seed=20261015。这是首批验证配置，不是经过质量优化的参数。墙钟限制仍可能造成机器负载相关的超时，因此报告全部状态；当前 P001 批次所有求解均已得到 OPTIMAL。

## 训练局部对照

复用 Graph2 默认 k=8 的有序邻居，取每节点前5，再对称 union、去重并筛选 FLANGING–IRREGULAR_PUNCH。保持全类型近邻定义，不按某个类型重做 kNN。不要求 IRREGULAR_PUNCH 有 BEND parent。

在46个适用 pair 中按实际边界距离排名等距分层选最多16个。取新多样性筛选的首个方案为 base，为当前同序状态的相反分支求解，最小化相对 base 改变的 Operation 数。所有硬规则保留；无解或预算不足分别记录，联动变化完整披露，不预先判定异序更好。

去重以两个完整方案 hash 的无序对为单位，保留所有 focus pair 和 trial→contrast 定位。same_station_plan/different_station_plan 仅为内部构造角色。answer、reason_note 保持空白，后续需另行盲化，不能直接把内部构造角色当专家 A/B。

首批16个 trial 全部 OPTIMAL，均只改变1个 Operation；合并重复完整方案后13组比较。不同 focus pair 可能对应同一组分配，因此16不等于16组独立比较。当前只用一个 base，尚未覆盖多个 base、全部 pair 或完整工艺偏好。

## 图与历史监督审计

A02/A06/A08 的同序 FLANGING–IRREGULAR_PUNCH k5 pair 分别29/31/19对，缺少 Graph2 几何边均为0。O131/O203 在 Graph2 中，距离约2.1、排名2/3；A02 中为OP30/OP20。没有重建额外图边或推断工艺冲突。13个新局部比较的关注 pair 也由独立产物检查核验图边及两侧同序属性。

旧截图24对与原CSV的比较ID、左右方向一致；当前原始表、盲化映射、原表单及截图SHA均与转录来源吻合。18个D、6个A/B，其中6个A/B全部涉及reference，仅4个A/B双方都满足H1–H7。这是当前监督覆盖不足的具体依据，未改旧答案或制造标签。

## 运行和产物

运行：

```powershell
.venv-m3/Scripts/python.exe -B scripts/run_reference_free_v07.py --historical-p001-audit
.venv-m3/Scripts/python.exe -B scripts/verify_reference_free_v07.py
```

历史审计开关仅为离线P001复核，生成模块不读取历史评审、映射或专家分配。其它零件不使用该开关。专家列隔离验证在临时ZIP副本完成，不写原始Excel。

主结果位于 outputs/m3_reference_free_v07：SAMPLING_REPORT.md、candidate_pool.jsonl、selected_candidates.jsonl、local_contrasts.json、local_comparison_summary.csv、local_operation_changes.csv。其它JSON保存事实校验、图覆盖、监督审计、完整四种输入隔离结果、求解配置/过程、分量诊断、独立产物验证与完整测试结果。

专家列完整、空白、缺失、替换四种P001输入均按完整配置重新生成64个候选并筛选16个，事实指纹、派生种子、随机目标hash、候选hash及筛选hash逐项一致。额外一次原始输入完整批次复跑用于检查可复现性。不是仅凭小批次推断完整批次一致。

新增代码/配置/测试清单：

- src/inference/__init__.py
- src/inference/input.py
- src/inference/generation.py
- src/inference/contrasts.py
- src/inference/audit.py
- src/inference/report.py
- configs/m3_reference_free_v07.json
- scripts/run_reference_free_v07.py
- scripts/verify_reference_free_v07.py
- tests/test_m3_reference_free_v07.py
- docs/MILESTONE_3_REFERENCE_FREE_V07.md

原有文件不修改。冻结基线包含26270个既有文件（包括历史辅助依赖），验证输出见 frozen_integrity.json。历史5项快照失败继续如实报告，不修改旧manifest掩盖用户此前纠错及已批准规范修订。

后续第5项才生成新盲化评审批次。当前CSV是内部采样摘要，尚不是新的最终评审表；局部对照不含任何自动偏好标签。训练监督仍需专家评审，模型与Top-K推荐尚未实现。
