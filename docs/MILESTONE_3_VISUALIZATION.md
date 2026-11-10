# Milestone 3 v0.4：2D Process Visualization Prototype

本轮是 Scheme B 人工比较的展示层。只复用冻结 Operation/Geometry/Station、60 selected、12 representatives、24 comparison 与 13 个盲号，不生成 candidate、工艺关系、质量评价或 preference label。
M1/M2/M3 v0.1/v0.2/v0.3、Sheet1–Sheet5、Graph0/1/2、reference、blind mapping 和人工答案保持冻结。

## 新增文件

新增 18 个源码、配置、文档和测试文件；既有源文件修改数为 0：

```text
configs/m3_visualization.yaml
src/visualization/__init__.py
src/visualization/config.py
src/visualization/geometry_adapter.py
src/visualization/plan_view_model.py
src/visualization/diff_view_model.py
src/visualization/svg_renderer.py
src/visualization/preview.py
src/visualization/review_visualization.py
src/visualization/review_ui.js
scripts/build_visual_review_pack.py
tests/visual_fixtures.py
tests/test_m3_visual_geometry_adapter.py
tests/test_m3_visual_plan_view_model.py
tests/test_m3_visual_diff_view_model.py
tests/test_m3_visual_svg_renderer.py
tests/test_m3_visual_review_pack.py
docs/MILESTONE_3_VISUALIZATION.md
```

没有新增项目依赖。SVG/HTML 生成使用标准库；PNG 便利预览使用现有 Pillow。单元测试使用已配置的 bundled Node 执行纯 JS 显示状态函数，避免只检验 HTML 字符串。

```powershell
.venv-m3/Scripts/python.exe -B scripts/build_visual_review_pack.py
.venv-m3/Scripts/python.exe -B -m unittest discover -s tests -v
```

CLI 支持 `--input`、`--candidate-dir`、`--review-dir`、`--config`、`--output`。只写新的独立 outputs 目录，拒绝覆盖旧 milestone 目录及 alias。

## 冻结输入和盲化

复用 v0.3 `read_source_batch` 校对 selected JSONL、summary、assignment matrix 与 reference，并重新验证既有 H1–H6/G1。不调用 solver，不调用代表选择或 pair 选择函数。
直接读取 v0.3 representative JSON、blind mapping、comparison JSON、公用 CSV 和 13 个公用 assignment CSV，校对 ID/hash、每个 Operation 的 assignment、pair 数量、顺序与 A/B 朝向。
盲化编号、comparison 编号和 A/B 顺序逐项沿用，不重新打乱。

原始 baseline 共观察到 190 文件。工作期间用户明确确认删除了 `data/raw/review_pack.html` 临时副本；其原 SHA-256 与冻结 v0.3 对外评审页完全相同。
原 baseline 不删除或重写这条记录。删除确认、原 hash、用户说明单独保存为 `internal/acknowledged_input_changes.json`。
正式冻结校验继续检查其余 189 文件；未确认的任何改变仍使构建失败。只接受这一个已确认副本的删除，不接受该副本的修改，不允许用 acknowledgment 隐藏其它输入变化。

公开 HTML 从 allow-listed visual view model 构建，不嵌入 source candidate 或内部 audit。整个 source，包括 JSON/script/comment/attribute，检查真实 ID、bucket、role、SoftPenalty、P1/P2/P3、expert/reference、generator 和 solver。
Internal HTML 可显示真实 ID、role、distance、soft components；不链接到公开页面中。
ZIP 只包含新的公开 HTML/CSV，不包含 mapping、内部报告或 preview 审计。

## 几何协议与方向

直接复用 `src.graph.geometry_primitives.geometry_primitives` 和解析 bbox。不解析 CAD，不重新拟合圆弧，不增加轮廓或空间位置。

| OperationType | 绘制来源 |
| --- | --- |
| CIRCLE_PUNCH / PRE_PUNCH / DIMPLE | 现有真实圆心和 radius |
| FLANGING | 已记录的同心圆，数量和半径不变 |
| IRREGULAR_PUNCH | 原 seq 的 LINE / signed ARC |
| BEND | 原 BEND_LINE，当前序使用配置的较粗线宽 |
| BRIDGE | M1 记录的对角点，M2 原有四边 footprint |
| TRIM / MARKING | 有记录 geometry 则绘制；P001 当前无 geometry，保留列表和 metadata |

每个 LINE/ARC 使用自身解析端点独立输出 SVG path，按冻结 seq 排列。不会用强制 Z 闭合、snap 或新连接段改变源坐标；已验证闭合的轮廓由原段重现。235 ARC 的符号沿用已经冻结的 M1/M2，实现不采用 PROJECT_SPEC 中已被后续人工确认覆盖的旧全部非负 sweep 约定。

期望方向沿用现有 geometry 复验图的工程坐标：x 向右、y 向上。SVG 使用 `(x_svg,y_svg)=(x,-y)`，只补偿屏幕 y 向下；没有工程镜像或旋转。正工程 sweep 对应 SVG sweep flag 0，负对应 1，major arc 使用绝对 sweep>π。完整圆使用 circle。
global bbox 包括全部有效 Geometry，ARC 极值由冻结 M2 计算。viewBox 只加配置 padding。A/B 同一 viewBox、同一 preserveAspectRatio、相同面板尺寸。

## 显示状态和交互

Station 始终按 Station.order 产生连续 rank，包括 OP70；不使用 OP 名称字符串排序或数值解析。

- CURRENT_ONLY：当前 Station 最强，其余浅灰；默认。
- CUMULATIVE：当前最强，前序中等 opacity，后序浅灰。
- FULL_PLAN_BY_STATION：全方案可见，按 Station palette 编码；该模式优先 Station 编码。
- BY_OPERATION_TYPE：在当前/累计模式中使用配置的类型映射；未加工动作仍浅灰。
- 只看差异：只有 A_station≠B_station 的动作可强高亮，unchanged 动作极低 opacity；不会因被点击而把筛掉的动作伪装为当前差异。

两个面板共用 Station index、display mode、color mode、region filter、selected Operation 和唯一 viewport。
工具条有上一序/下一序/七个 Station、缩放/全图/聚焦所选区域；鼠标滚轮缩放和拖拽平移同步更新两侧 viewBox，不提供独立缩放。
无几何动作仍在当前序列表可点击。选中详情显示既有 group/predecessors/CAD parent/surface side、A/B Station 和 rank delta；轨迹条只标 A/B，不展示 reference 位置。
BEND 的原 metadata 和此序未加工状态在 hover title 与点击详情中均可查阅。

对于可点击图形，透明 hit stroke 不增加可见几何。target ID 为 prefix 和 Operation ID 的 UTF-8 hex 组合，A/B 不重复。键盘 Enter/Space 也可选中。

## Region 与客观摘要

影响矩形直接复用 M3 D3 `influence_rectangle`。默认关闭，可显示半透明矩形。
Operation 展示归区直接复用冻结 D3 `local_region_distribution` 的 smallest-containing-region 结果，不重新实现 containment 或 representative point。
无 geometry 为 NO_GEOMETRY；有 geometry 且不属于 region 为 OUTSIDE_REGION。D3 无法唯一归区的情况明确显示 UNRESOLVED_REGION，不猜测区域。
region filter 强调归区成员和对应 BEND；已有 parent/child 链中的 BEND 可弱显示，只作为上下文，不创建前置关系或 graph edge。

差异和 Station/type 汇总复用 v0.3 comparison_diff，仅额外展示 B−A 的 rank delta、类型/区域/Station 转移计数和 BEND 在 A 中更早/更晚数量。只描述事实，不生成“更合理”的结论。
每个 changed Operation 在区域差异汇总中恰好出现一次；BEND 的自身影响区域 metadata 和其 D3 展示归区分开保存。

## UI 和文件

首页标题“方案比较记录”；CMP_001–CMP_024 导航。第一屏是同步二维图和工序控件，Station 动作数/类型/列表在图下方。
第二层为折叠的 type、load、changed Operation、BEND、region 和转移表；最后是五选一判断、confidence 和 reason_note。全部为空，切换 comparison 不清空本次浏览已填写的 DOM 控件。
没有网络、CDN、后端、自动保存或提交；可用空白 CSV 手工记录。若新输出目录中的 CSV 已有人工内容，重建会拒绝覆盖。
复用 v0.3 html_document/html_table/form_html，只有可视化区域及交互新实现。展示模式、geometry opacity/line width、类型和 Station 颜色、UI overlay、zoom/pan、布局和 preview 参数集中在 m3_visualization.yaml；保留项目 JSON 子集的 YAML 格式。

```text
outputs/m3_visual_review/
  visual_review_pack_report.md / .json
  plan_comparison_visual_pack.zip
  expert_facing/
    review_pack.html
    comparison_pairs.csv
    plan_cards/PLAN_A01.html ... PLAN_A13.html
  internal/
    review_pack_internal.html
    plan_cards/PLAN_A01.html ... PLAN_A13.html
    geometry_audit.json
    visual_view_models.json
    frozen_input_manifest.json
    acknowledged_input_changes.json
    frozen_integrity_check.json
    blinding_leakage_check.json
    test_results.txt / .json
    previews/
      single_OP20.svg / .png
      single_OP50.svg / .png
      comparison_OP20.svg / .png
      comparison_OP50.svg / .png
      comparison_difference_only.svg / .png
      comparison_influence_regions.svg / .png
      comparison_region_filter.svg / .png
```

SVG 是真实解析 primitive 的权威矢量预览。PNG 仅是用同一 Geometry/catalog/显示状态产生的方便观察副本，并非浏览器 HTML 截图；不采样或改变业务 Geometry。
静态预览覆盖用户指定的六种情形，另加 region filter。

## 当前 P001 的可视化边界

224 个动作，211 个可绘制：BEND 59、BRIDGE 4、CIRCLE_PUNCH 16、DIMPLE 2、FLANGING 46、IRREGULAR_PUNCH 38、PRE_PUNCH 46。
10 个 TRIM 和 3 个 MARKING 无 geometry，不可靠地推断其位置。全部59影响矩形有效；186动作唯一归区，25为OUTSIDE_REGION，13为NO_GEOMETRY，没有最小面积并列。
当前输入没有独立可靠的板料外轮廓，不以凸包或矩形冒充板料边界。全部动作 geometry 提供背景上下文；同位置的 PRE_PUNCH/FLANGING、重叠区域和相邻圆弧按原位置绘制，列表选择和局部同步 zoom 可辅助查看。
下一步可增强动作分组、可点击 region 聚类、局部导航和人工 annotation。此轮不进入下一阶段，不添加语义或偏好监督。

## 验证结果

原256项加新增56项，312项全量测试通过，0 failures/errors/skipped。
新模块：geometry_adapter=12、plan_view_model=11、diff_view_model=9、svg_renderer=11、review_pack=13。
覆盖27项指定验收点，另覆盖有符号major ARC、D3最小面积并列、输入不变、filled answer防覆盖、输出路径保护与被确认临时副本删除审计。
Node 对比 Python/JS 显示状态、颜色/opacity/线宽、region filter 和 zoom界限；不是完整浏览器端到端测试。
当前59条BEND的M2端点与Sheet5完全一致，没有geometry映射问题。全部influence矩形都在带padding的公共viewBox内。
