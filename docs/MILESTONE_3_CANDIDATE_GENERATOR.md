# Milestone 3 v0.2：CandidatePlanGenerator

本轮依据 `PROJECT_SPEC.md`、冻结的 M1/M2、`MILESTONE_3_CONSTRAINT_SPEC.md` 和用户确认的 v0.2 请求实现。
所有既有 H1–H6、P1–P3、D1–D3、G1、Graph0/1/2 和 expert reference 保持不变。
输出是 candidate Plans，不是 training labels、preferred/optimal/expert-quality Plans。
不训练 GNN，不自动生成 preference label，不把 penalty 较低解释为工艺质量较好。

## 新增文件与依赖

本轮新增 17 个源文件，没有修改既有文件：

```text
requirements-m3.txt
configs/m3_candidate_generator.yaml
src/candidate/__init__.py
src/candidate/config.py
src/candidate/cp_sat_model.py
src/candidate/generator.py
src/candidate/distance.py
src/candidate/selector.py
src/candidate/serialization.py
src/candidate/report.py
scripts/generate_candidate_plans.py
tests/candidate_fixtures.py
tests/test_m3_candidate_model.py
tests/test_m3_candidate_distance.py
tests/test_m3_candidate_generator.py
tests/test_m3_candidate_selector.py
docs/MILESTONE_3_CANDIDATE_GENERATOR.md
```

实际测试使用 **OR-Tools 9.15.6755、Python 3.12.14**。OR-Tools 版本锁定在独立 `requirements-m3.txt`。
M1/M2 的 `requirements.txt` 未修改。本地创建 `.venv-m3`，继承已有只读依赖环境，仅在这个独立 venv 中安装新增 OR-Tools 及其缺失依赖。

API 依据 [OR-Tools 官方 CP-SAT 文档](https://developers.google.com/optimization/cp/cp_solver) 和 [v9.15 Python 接口源码](https://github.com/google/or-tools/blob/v9.15/ortools/sat/python/cp_model.py)。

## 使用

在项目根目录运行：

```powershell
.venv-m3/Scripts/python.exe -B scripts/generate_candidate_plans.py
.venv-m3/Scripts/python.exe -B -m unittest discover -s tests -v
```

其它环境先安装冻结 M1 依赖及 `requirements-m3.txt`，再使用对应 Python 执行。
CLI 支持 `--input`、`--config`、`--constraint-config`、`--output`。
输出必须位于项目 `outputs/`。已存在的冻结清单先校验后复用，不自动接受冻结文件变化。

```python
from src.constraints import load_constraint_context
from src.candidate import CandidatePlanGenerator

context = load_constraint_context("data/raw/钣金加工数据表_P001.xlsx")
generator = CandidatePlanGenerator(context)
pool, selected, run = generator.run()
```

先检查冻结 M1 Validator 和 expert H1–H6。无 movable Operation、best-known 最大距离为 0、expert 不可行、模型无可行 incumbent 或实现一致性错误，均明确停止。

## CP-SAT 模型

对每个 Operation 及 capability 允许的 Station 建立 `x[o,s] ∈ {0,1}`。
Station 按 `Station.order` 排序，映射为 `rank=0,1,...`。

```text
sum_s x[o,s] = 1                         H1
只建立 capability 允许的 x[o,s]          H2
rank[o] = sum_s rank(s)*x[o,s]
rank[pred] < rank[operation]             H3/H4/H6
rank[fixed_TRIM] = rank(fixed_station)   H5
```

H3/H4/H6 直接读取冻结 `ConstraintContext.precedence_relations`，不复制 process-group/parent relation 推导逻辑。
H5 读取原 Sheet4 fixed assignment。不根据 CAD parent、area、depth、距离或 D3 增加其它规则。

G1 通过冻结 `StationOverloadGuardrail.evaluate(expert)` 取得每 Station 上限，不复制公式。
只有 generator 的 `apply_station_overload_guardrail` 与 v0.1 G1 enabled 同时为 true，才加入 `load_j <= U_j`。
G1 是生成域 guardrail，不改变 `HardConstraintEngine` 的 hard feasibility；关闭可用于后续明确的 ablation。
关闭时仍运行 G1 observation，并保存实际 pass/loads。

每个 solver incumbent 必须再由冻结 HardConstraintEngine 和 G1 验证。
CP-SAT feasible 而 H1–H6 不可行是实现错误，立即停止；模型启用了 G1 却输出超限也立即停止，不忽略或修补。
独立 Hamming distance 必须等于模型距离，并满足请求的 exact/fallback 范围和 bucket。

## Movable set、距离与最大距离

局部合法 Station 直接来自 `context.allowed_stations(o)`，包含 H2 capability 与 H5 fixed assignment。
至少有两个局部选择的 Operation 属于固定集合 M。
M 只是距离归一化集合，不宣称每个成员在全局 H1–H6/G1 下都可以实际改变。

```text
d_raw(Pa,Pb) = count(o in M, Pa(o) != Pb(o))
d_norm(Pa,Pb) = d_raw(Pa,Pb)/|M|
```

所有候选、expert distance、pairwise selection 使用同一 M；完整 Plan 的 hash 使用全部 Operation assignment。
|M|=0 时停止，不能回退到全部 Operation 数归一化。

先最大化对 expert 的 raw Hamming distance，并保持 H1–H6 及有效 G1。
默认 timeout=30 秒。OPTIMAL 才保存已证明的 D_max；FEASIBLE 时 `D_max=null`，只记录 `best_known_D_max`、upper bound 和 status。
只有得到 incumbent 才能建立 bucket；UNKNOWN 不能被解释为证明无解或理论最大值。

## Bucket 的整数边界

设用于 bucket 的最大/已知距离为 D，当前 near ratio=0.25、medium ratio=0.55：

```text
NEAR:   1 ... floor(0.25*D)
MEDIUM: floor(0.25*D)+1 ... floor(0.55*D)
FAR:    floor(0.55*D)+1 ... D
```

比例按配置 decimal 字符串计算 floor，避免浮点乘法错误移动整数边界。
1...D 恰好覆盖一次，没有重叠和空档。lower>upper 的 bucket 明确为空。
expert 的 D=0 永不进入 pool。
空 bucket 或不足配额不伪造 Plan；pool 和 selected 的短缺在其它可用 bucket 中按稳定 bucket 顺序重新分配，全部过程留 audit。
pool 最多为 3×pool_target_per_bucket，selected 最多为 3×final_target_per_bucket，不突破总量。

## 候选生成、seed 与去重

每个 bucket 内每次 attempt：

1. 从整数范围选择 target distance。
2. 加 `D(P,expert)=target`。
3. 从 derived seed 生成每个 assignment Boolean 的有界整数 coefficient，当前 [-100,100]。
4. 随机决定 maximize/minimize 此线性表达式，求一个 feasible incumbent。
5. 二次验证、去重，随后才调用冻结 `SoftPriorEngine.evaluate`。

objective 只含 assignment variables 和探索 coefficient，没有 P1/P2/P3，没有工艺含义。
raw pool 是随机线性 objective 搜索的可行角点集合，不能当作均匀随机可行域样本。

只有 exact model 返回 **INFEASIBLE**，才允许配置的 target±delta fallback，当前 delta=1；fallback 范围与 bucket 取交集。
UNKNOWN 不触发“无解”fallback。
no-good 已排除其它 pool Plan 时，INFEASIBLE 仅表示该 residual search model 无解，不证明原可行域中这个距离从未存在。
fallback 使用单独 derived seed，保存 original target、实际 distance、fallback 范围与 status。

derived seed 来自 SHA-256(base_seed, bucket, attempt/phase)，不使用进程随机化的 Python hash。
seed 限于 CP-SAT 非负 signed-32-bit 范围。变量和操作遍历顺序稳定，固定单 worker。
相同模型、seed、版本的完成求解可复现；若以后仅达到 wall-time 截止的 FEASIBLE，跨机器速度差异仍可能影响 incumbent，status/seed 已保存用于审计。

Plan hash 是排序后的全部 `(operation_id, station_id)` assignment 的 UTF-8 canonical JSON SHA-256，与输入 mapping 顺序无关。
默认逐步添加 no-good：`sum_o x[o,P(o)] <= N-1`，排除完整已接受 assignment。
另有 hash 去重保护：重复丢弃并计数，不用复制填配额。

## Post-hoc soft 与两阶段选择

所有 soft raw/weight/contribution/total 均调用既有 evaluator 后提取，不复制公式。
保留 0.45/0.20/0.35；P001 P3 恒为 0 是允许结果，不更换 type，不重新归一化 active weights。
Diagnostics 不参与 objective、prefilter 或选择。

Stage A：各 bucket 按 `(SoftPenalty, plan_hash)` 升序，保留 `ceil(0.70*pool_count)`。
ceil 同样使用配置 decimal，避免浮点误保留额外一项。

Stage B：各 bucket 独立 farthest-first。
第一名选最低 penalty，hash 打破并列。随后选择到已选集合的最小 normalized distance 最大者。
distance 并列先比较更低 penalty，再比较 hash。
没有隐藏综合权重。
缺额重分配仅在已预筛集合中继续同一个 max-min 过程，不重新纳入已筛掉 Plan。

选中顺序决定稳定 ID：NEAR_001...、MEDIUM_001...、FAR_001...。
EXPERT_REFERENCE 单独保存，不计入候选数。

## 输出与审计

```text
outputs/m3_candidates/
  candidate_pool.jsonl
  selected_candidates.jsonl
  selected_candidate_summary.csv
  selected_plan_assignments.csv
  candidate_generation_report.json
  candidate_generation_report.md
  expert_reference.json
  operation_movement_frequency.csv
  frozen_input_manifest.json
  frozen_integrity_check.json
  test_results.txt
  test_results.json
  batch_validation.json
```

每个 JSONL candidate 保存完整 assignments/hash、raw/normalized distance/bucket、hard feasibility、G1 pass/enforced/load/upper bounds、P1/P2/P3 raw/weight/contribution/total、seed/target/fallback/status/objective metadata。
objective coefficients 可由 seed 和稳定变量次序重建，只保存 coefficient count/bound/hash 和 direction，不保存无意义 solver 内部状态。
CSV 使用 UTF-8 BOM，便于 Excel 打开。assignment matrix 每 Operation 一行，含 expert_station 和每个 selected candidate 列。
报告保留 max-distance witness、全部 movable ID、全部 attempt、pool/selection 配额、完整 pairwise distance、Station load、113 个 Operation movement、类型转移及 D1–D3 汇总。

`solve_attempts` 和 `feasible_solutions` 统计 pool 阶段；最大距离优化单列为一次求解，总 solver calls 另计。
分布均给 count/min/median/mean/max；空集合为 null/n/a，不能解释为真实零。

D1 复用冻结的 depth pair 计算。
D2 forest 从冻结实现取得一次，只汇总 selected 中的 region Station 与 parent-child rank gap。
D3 membership 从冻结几何实现取得一次；Plan 只改变 region 成员的 Station 分布。
它们在 selection 结束后才运行，不影响 score/selection。

## 第一批 P001 实际结果

|M|=113：59 BEND、38 IRREGULAR_PUNCH、16 CIRCLE_PUNCH。
最大距离求解 OPTIMAL，**D_max=79**，upper bound=79。
G1 enabled，seed=20261012，单 worker。

| Bucket | 整数范围 | Attempts | Unique pool | Stage A retained | Selected |
| --- | --- | --- | --- | --- | --- |
| NEAR | 1–19 | 80 | 80 | 56 | 20 |
| MEDIUM | 20–43 | 80 | 80 | 56 | 20 |
| FAR | 44–79 | 80 | 80 | 56 | 20 |

240 次 pool 求解均为 OPTIMAL，加最大距离求解共 241 次。
hard-validation failures、G1 rejections、duplicate rejections、UNKNOWN、exact infeasible 和 fallback 都为 0。
没有 bucket 短缺，没有配额 redistribution。
240 个 raw unique pool，**60 个 selected candidates**，expert 未计入。

| Bucket | Selected expert distance min / median / max | SoftPenalty min / median / max |
| --- | --- | --- |
| NEAR | 8 / 13 / 19 | 0.038105 / 0.065313 / 0.076324 |
| MEDIUM | 26 / 33 / 42 | 0.057342 / 0.086464 / 0.095678 |
| FAR | 44 / 50 / 64 | 0.065685 / 0.100828 / 0.116946 |

全部 selected SoftPenalty min/median/mean/max：0.038105 / 0.082409 / 0.081817 / 0.116946。
P1 min/median/max：0.019863 / 0.121918 / 0.193151。
P2 min/median/max：0.095238 / 0.141369 / 0.172619。
P3 全为 0，原权重未改变。

| Pair group | Count | d_norm min | Median | Mean | Max |
| --- | --- | --- | --- | --- | --- |
| 全部 | 1770 | 0.132743 | 0.389381 | 0.380946 | 0.592920 |
| bucket 内 | 570 | 0.132743 | 0.371681 | 0.333954 | 0.530973 |
| 跨 bucket | 1200 | 0.212389 | 0.407080 | 0.403267 | 0.592920 |

最接近的两个 selected 也相差 15 个 movable Operation。
NEAR/MEDIUM/FAR 中分别有 18/13/11 个 selected 不属于对应 pool 最低 penalty 的前 20 个，确认 diversity selection 没有退化为直接取最低分。

| Station | Expert | G1 upper | Selected min / median / max |
| --- | --- | --- | --- |
| OP10 | 53 | 93 | 52 / 58 / 64 |
| OP20 | 45 | 79 | 19 / 35.5 / 45 |
| OP30 | 63 | 111 | 61 / 68 / 78 |
| OP40 | 25 | 44 | 23 / 28 / 34 |
| OP50 | 36 | 63 | 22 / 28 / 35 |
| OP60 | 2 | 7 | 3 / 7 / 7 |
| OP70 | 0 | 5 | 0 / 0 / 0 |

最常移动：O137=31/60；O208=30/60；O073、O188、O200 各 29/60。
类型累计 changed assignment：BEND=706、IRREGULAR_PUNCH=919、CIRCLE_PUNCH=368，其余类型为 0。
常见方向：BEND OP50→OP40=307、OP50→OP60=255、OP40→OP50=144；IRREGULAR_PUNCH OP20→OP30=620、反向=299；CIRCLE_PUNCH OP20→OP10=324、反向=44。
这些是统计，不赋予工艺意义。
113 个局部 movable 中有 14 个在本批未变化，这不能仅凭本批统计解释为全局不可改变。

D1 depth inversion rate min/median/max：0 / 0.035451 / 0.061300。
D2 仍为 27 roots、36 leaves、max depth=2；60 Plan 的 1920 个 parent-child gap 中，gap1=1761、gap2=159。
D3 59 个有效 region，每 Plan 同样归入 186 个 Operation；完整 region Station 分布保存在报告。

## 测试、冻结与导出复核

原有 180 tests 加新增 40 tests，**220 tests 全部通过，0 failures/errors/skipped**。

| 新测试模块 | Count |
| --- | --- |
| test_m3_candidate_model | 8 |
| test_m3_candidate_distance | 10 |
| test_m3_candidate_generator | 12 |
| test_m3_candidate_selector | 10 |

覆盖用户要求的 28 项测试，并增加：小模型可行域穷举对照、exact fallback、空集合/空间不足、MODEL/Hamming/G1 一致性、decimal rounding、诊断不影响选择、JSONL tamper 检测与 pool shortfall redistribution。
人工测试用内存 dataclass，不改原始 Excel。
相同 seed 两次生成结果完全相同；不同 seed 在有自由度的测试中得到不同 pool。

本轮开始记录的 **57 个 frozen 文件全部 SHA-256 一致**，包括原始 Excel、M1/M2/M3 v0.1 源码/测试/配置/规范及 v0.1 报告。
`batch_validation.json` 还验证了实际输出：

- 240 个 pool 的 H1–H6、G1、hash 和 frozen evaluator score 全部吻合。
- selected 是 pool 的 60 个唯一子集，selection 从已保存 pool 重放完全一致。
- assignment matrix 为 224 行、62 列（Operation、expert、60 candidates），所有 assignment 与 JSONL 相同。
- summary 为 60 行；movement、D2/D3 aggregate 数量完全对账。

## 参数观察与下一阶段

当前参数已达到数量、可行性和多样性目标，本轮不需要修改参数。
但记录两点后续审阅边界：

1. pool 的 FAR 距离覆盖 44–79，Stage A 覆盖 44–77，而最终 diversity selection 覆盖 44–64；不能声称最终 60 个覆盖了每个整数距离或最高距离端。
2. OP60 在 43/60 selected 中到达 G1 上限 7，说明该生成 guardrail 对搜索空间有实际影响；不能把这个上限解释为生产 capacity。

后续可以在明确的新任务中比较 prefilter fraction/G1 ablation，并观察距离极端端点的保留情况。本轮没有自动调参或增加 endpoint 配额规则。
建议进入小规模人工 expert comparison，先审阅差异和专家判断一致性，再决定 preference-data 获取流程。
本批没有 preference label，不能直接用 penalty 或“生成成功”建立质量标签。
完成到此停止，等待下一步指令。
