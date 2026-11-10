# 二维图与几何证据审查（2026-10-16）

这是独立探索版本，延续完整距离audit和翻孔尺寸trial，不替换其中任一历史定义或结果。原始事实以 `PROJECT_SPEC.md` 为准；H1–H7、Graph0/1/2、旧候选、旧评审、reference保持原样。

## 当前完成范围

所有224个Operation有显式状态；211个有几何，22155个无向组合有解析边界距离、源曲线上的最近位置及图元索引。59个BEND、38个IRREGULAR_PUNCH可以作为查询中心，查看与其余210个几何动作的关系。不是只从对称kNN边截取k条。

当前没有为所有动作确定最终邻域。“有证据可查”“存在试验范围判断”“工艺上需要邻近检查”是不同结论。13个动作缺少几何，不写成不需要邻居；62个PRE_PUNCH/CIRCLE_PUNCH在本次试验作为被检查对象，不因此宣布它们永久无需邻域；DIMPLE/BRIDGE的主动类型政策尚未确定。

## 组合处理

| 组合 | 新增证据 | 当前范围判断 |
|---|---|---|
| BEND—圆形动作 | 孔中心到有限线段、未夹紧投影参数、段内/端点位置 | 2/3/4倍记录孔径试验，主展示3倍；保留旧轮廓距离口径对照 |
| IRREGULAR_PUNCH—圆形动作 | 真实记录轮廓距离及两侧最近点 | 2/3/4倍圆形动作记录直径试验；不造异形孔等效直径 |
| BEND—IRREGULAR_PUNCH | 解析最近点、所在图元及沿两侧轮廓的距离分布 | 长度阈值默认null，待判断；曲线相交不自动等于冲突 |
| IRREGULAR_PUNCH—IRREGULAR_PUNCH | 同上 | 长度阈值默认null，待判断 |
| BEND—BEND | 解析最近点、夹角、沿各自轴线的投影重叠、既有先后关系 | 默认null，待判断；不推导新precedence |

圆形类型在本试验指FLANGING、PRE_PUNCH、CIRCLE_PUNCH，且几何必须是记录的同心完整圆。FLANGING的直径取最大记录圆，未把它解释为模具直径。非同心圆不猜中心。圆形—圆形分支延续翻孔作为尺度中心的试验，两个尺度中心的结果可不同，不强行平均。

尺寸倍数、相对跳变倍数、三类未定长度阈值分别配置。UI中的临时阈值仅用于本页探索，不保存到源配置或JSON。它不是已确认的工艺阈值。

## 内外部关系

仅明确同process_group的PRE_PUNCH—FLANGING作为内部工艺组单列，保留46对。它们不参与外部距离断层排序，但仍为两个Operation。CAD parent和直接硬先后单独展示；CAD关联本身不合并特征、不排除外部对象。

外部对象有完整排序。初始显示前20名仅为页面显示限制，可切换全部；不等于候选近邻数量。相对3倍跳变只列候选位置，不自动截断。

## 精确距离与辅助观察

M2的解析距离是唯一权威距离。新增最近点证据通过线段/圆弧端点、交点及内部驻点求得，并逐pair检查其距离与M2一致。返回一个最小值实现点；平行线、重合曲线可能有多个最小位置，本版本不声称穷举。

最近位置与真实Geometry使用相同的270°显示变换。完整外轮廓复用reconstructed_local.html的既有提取器，仅作背景。Sheet5 influence region可选显示，不能当作干涉范围。

沿轮廓按弧长取24个中点样本，记录距离q10/q50/q90等，仅辅助观察。采样可能错过真实交点，不能替代解析最短距离；不能仅据全轮廓分位数断言局部持续接近的精确长度。实际延伸范围和工艺阈值仍待定义。

浮点排序和阈值比较沿用M2的9位数值桶；最近点验证容差单列为纯数值参数，不是工艺余量。

## 参数、入口和输出

- 参数：`configs/proximity_geometry_review.json`。
- 构建：`.venv-m3/Scripts/python.exe -B scripts/build_proximity_geometry_review.py`。
- 测试：`.venv-m3/Scripts/python.exe -B -m unittest tests.test_proximity_geometry_review tests.test_scale_aware_proximity_trial tests.test_relative_proximity_audit tests.test_geometry_distance`。
- 浏览器检查：用bundled Node运行 `tests/proximity_geometry_browser_qa.cjs outputs/proximity_geometry_review`。
- 独立核验：`.venv-m3/Scripts/python.exe -B scripts/verify_proximity_geometry_review.py`。
- 产物：`outputs/proximity_geometry_review/geometry_review.html`、`geometry_evidence.json`、`REVIEW_REPORT.md`、`verification.json`及四张局部图。

构建读取不含专家分配的InferenceContext，与原距离audit核对工作簿SHA和事实指纹。证据没有专家工序、候选身份或盲化映射。不接入训练标签、SoftPenalty、候选生成或过滤。
