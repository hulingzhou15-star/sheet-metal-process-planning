# Milestone 3 v0.5：visual review UI 可读性增强

本轮仅修改展示层。依据用户明确确认的三项需求：补齐源HTML外轮廓背景、默认270°熟悉视角、重做专业配色/透明度。
M1/M2/M3 v0.1–v0.4既有业务定义不变；原Excel、reference、候选、代表选择、pair设计/顺序、盲号、约束、评分及所有既有输出文件不变。
v0.4的默认方向/无外轮廓展示边界由本次明确请求覆盖，旧v0.4规范与产物保留作对照。

## 新增和修改

新增6个文件：

```text
src/visualization/outline_adapter.py
src/visualization/display_transform.py
tests/test_m3_visual_readability.py
tests/test_m3_visual_ui_interactions.py
tests/visual_ui_dom_harness.js
docs/MILESTONE_3_VISUALIZATION_V05.md
```

修改9个既有可视化文件：

```text
configs/m3_visualization.yaml
src/visualization/config.py
src/visualization/geometry_adapter.py
src/visualization/svg_renderer.py
src/visualization/preview.py
src/visualization/review_visualization.py
src/visualization/review_ui.js
scripts/build_visual_review_pack.py
tests/test_m3_visual_svg_renderer.py
```

旧SVG测试只更新查找层级以适配统一旋转父层，未降低原断言。
没有新增dependency，复用标准库、既有Pillow和bundled Node。

```powershell
.venv-m3/Scripts/python.exe -B scripts/build_visual_review_pack.py
.venv-m3/Scripts/python.exe -B -m unittest discover -s tests -v
```

默认新输出目录为 `outputs/m3_visual_review_v05/`，明确拒绝覆盖旧 `outputs/m3_visual_review/`。

## A：完整可恢复外边界

只读 `data/raw/reconstructed_local.html`，不执行其script，不复制原metadata、candidate IDs或其它标签到公开页面。
源SHA-256：`ba03173e4a62abe7c6b13eb0bbb954458f5d4580539deceabcbef6a84869c11d`。

解析方法：

1. HTMLParser只读取 `data-role=segment` 的可见LINE/ARC path，忽略透明hit副本。
2. 沿用已保存的M/L/C向量路径。ARC保留原SVG cubic控制点，不根据源data-sweep-angle的无符号值猜测旋转方向。
3. 使用源metadata中已保存的 `local_to_view_linear`、`pixels_per_mm`、margin和view_bounds反变换全部路径点/控制点到局部工程坐标，再转原生SVG `(x,-y)`。
4. 与每段 `data-local-start/end-*` 交叉验证，最大误差 `2.099360442982168e-10`。
5. 使用原始局部端点的精确相等连通关系追踪已有闭合环，不按距离补点或跨间隙连接。每个可用环的端点节点度为2。
6. 排除完全位于其它闭合曲线内的环，复用外部闭合边界。贝塞尔展平只用于显示层的包含判断及PNG便利预览，SVG向量曲线不变。

实际成功读取2093个LINE/ARC源段。恢复93个闭合分量，保留32条外部闭合边界、385个原段，排除61条内部闭合曲线。
另32组开放/分叉路径经检查完全位于这些外边界内部，因此不进入外轮廓；没有未解决的外部开放边界。
改变显示包含展平精度为0.0001/0.001/0.01时，32条边界及385个源段的选择完全相同。

**背景呈现的是源文件已有的多条展开外边界，保留原有分段、独立面和间隙。没有做physical union，也没有发明一个单一实体外环。**
不是凸包、bbox矩形，不推断新的CAD或工艺语义。

外轮廓层为统一显示父层的第一个子层，位于region/Operation图元之下，pointer-events=none，没有Operation/Station属性。
不进入PartData、StaticPartGraph、ConstraintContext、Plan view model或blind mapping；公开JSON中也不嵌入轮廓来源metadata。
13个Plan共用相同轮廓，背景只扩大展示viewBox，不改变D3归区或Geometry归一化。
原没有geometry的10个TRIM与3个MARKING仍没有Operation空间位置，背景不替它们补造Geometry。

轮廓资产：`internal/assets/outline_display_asset.json`、`outline_0.svg`、`outline_270.svg`；源段ID和提取方法单独保存于 `internal/outline_extraction_report.json`。

当前提取status=available，fallback未使用。对于缺少metadata、坐标校验失败或不可靠路径，报告明确原因；不补造轮廓，保留Operation-only背景。
对于可恢复但不完整的外边界，仅导出已有闭合曲线并标partial，不填开放间隙。

## B：统一270°显示映射

原始工程坐标与M2 primitive不变。原生SVG点为 `(x,-y)`。
工程逆时针270°表示 `(x,y)→(y,-x)`；再补偿屏幕y向下，最终显示为 `(y,x)`。
因此在原生SVG之上使用 `matrix(0 1 -1 0 0 0)`，与源HTML保存的270°CAD朝向一致。

所有轮廓、region矩形、Operation可见图元和透明hit图元均在同一个 `display-scene` 父层里。
viewBox对四角应用相同变换，交换显示宽高；没有分别旋转图元而造成位置漂移。
默认270°，可用“熟悉视角270°／原始视角”切换。切换时两边共同重置到同方向全图，保留选中Operation详情与当前Station。

Station、difference-only、region filter继续按原assignment/region membership处理。
点击按稳定Operation ID解析，命中层跟随同一父层旋转；不从旋转后的坐标猜Operation。
zoom/pan使用旋转后的共同display viewBox；fit-region先把原有region矩形变换到当前显示坐标，再以共同比例适配，不使用旧方向的bbox。

## C：视觉语义

| 状态 | 颜色 | opacity/线宽 | 理由 |
| --- | --- | --- | --- |
| 外轮廓 | #707A84，中性灰 | 0.92，1.4px | 稳定识别板料外形，不竞争加工高亮 |
| 轮廓内背景 | #F4F5F7 | 0.55 | 非纯白的轻微结构层次 |
| 非当前序 | #748496，灰蓝 | 0.55，1.2px | 全图结构清楚，减少过浅阅读负担 |
| 当前序 | #2E6DAE，工程蓝 | 1.0，普通1.6px/BEND3px | 主色清楚、克制，替换墨绿色 |
| 累计前序 | #597796 | 0.60 | 保留已完成参考，与当前强度分层 |
| 累计未来序 | #748496 | 0.48 | 尚未加工仍可见 |
| 选中 | #C56A1F，暖橙 | 1.0，4px | 与工序高亮区分，保持专业色域 |
| hover/键盘focus | #3C82BE | 0.95，3.5px | 提示可交互；选中时保留暖橙 |
| 只看差异时unchanged | 灰蓝 | 0.18 | 保留空间参考，重点仍是changed Operation |
| region filter外部 | 灰蓝 | 0.14 | 轻描背景，不误认作过滤范围内动作 |
| parent/child辅助BEND | 原显示色 | 0.55 | 仅保留已有关系上下文 |
| influence region | #829AB2，低饱和灰蓝 | 默认0.06，选区0.14 | 半透明辅助，避免遮挡动作 |

A/B相同映射；BY_OPERATION_TYPE和FULL_PLAN_BY_STATION使用同步的配置palette，不引入质量编码。
唯一参数入口仍为 `configs/m3_visualization.yaml`，新增outline/style与default_rotation_degrees设置；旧坐标方向字段继续代表工程坐标语义。

## D：验证与输出

新增23项测试，原312项继续保留。覆盖源曲线/外层选择、源script不复制、不补开放间隙、反变换失败、稳定性、270°映射、共用父层、命中ID/详情、颜色可见性和冻结scope。
加入实际UI script的模拟DOM回归，触发点击/键盘、Station、orientation、difference-only、region、zoom/pan/reset/fit和display-mode事件。
此测试验证实际handler和状态同步，但不是完整真实浏览器端到端测试。
Plan与comparison的view model逐项等于冻结v0.4 JSON；D3与assignment逻辑不变。

新输出：

```text
outputs/m3_visual_review_v05/
  visual_review_pack_report.md / .json
  plan_comparison_visual_pack.zip
  expert_facing/review_pack.html
  expert_facing/comparison_pairs.csv
  expert_facing/plan_cards/PLAN_A01.html ... PLAN_A13.html
  internal/review_pack_internal.html
  internal/outline_extraction_report.json
  internal/assets/outline_display_asset.json
  internal/assets/outline_0.svg
  internal/assets/outline_270.svg
  internal/input_baseline_manifest.json
  internal/frozen_integrity_check.json
  internal/test_results.txt / .json
  internal/previews/*.svg / *.png
```

8组预览：单方案OP20/OP50、比较OP20/OP50、只看差异、显示region、region filter，以及原始方向对照。
公开包不包含内部轮廓来源、审计、source IDs或mapping；所有判断项仍为空，没有网络和后端。
本轮开始保存262个文件baseline，允许的可视化源码变化单独列出。非可视化业务文件及旧outputs的245个文件全量检查，不自动接受其它输入变化。

## 后续补充：直接在 HTML 填写和导出人工评审

用户指出手抄 CSV 不便后，补齐纯展示层的评审结果保存功能。原有 24 对比较、A/B 朝向、13 个盲号、五个答案选项和 confidence 1–5 均沿用冻结定义。

专家在 `review_pack.html` 直接填写答案、把握程度和备注，导航显示已判断标记及总进度。“填写本对判断”跳转到当前比较的填写区；“导出评审 CSV”下载 `comparison_pairs_completed.csv`，专家返回此文件即可。

本地暂存使用浏览器 localStorage，按完整的公开比较序列与方案分配区分评审包；每次恢复还校验完整签名及 A/B 盲号，内部审阅页与公开页暂存分开。相同浏览器、相同文件位置且允许存储时可恢复；移动文件、换浏览器或清理浏览器数据不保证保留暂存。禁止存储、存储空间不足、记录损坏时明确显示提示，仍可填写和导出。CSV 是可返回的持久结果，页面不会改写磁盘上的原始空白 CSV 或 HTML，也没有网络和自动提交。

导出 CSV 保持冻结字段 `comparison_id,plan_A,plan_B,answer,confidence,reason_note` 和全部 24 行原顺序。未填项仍为空；答案由人工明确输入，不推断偏好。使用 UTF-8 BOM、CRLF 和 CSV 引号转义，中文、逗号、引号及多行备注可往返恢复。只有公开比较标识、盲号和人工填写字段进入结果；内部评分与身份不进入导出。

新增 `src/visualization/review_answers.js`、`tests/visual_review_answers_harness.js`、`tests/test_m3_visual_review_answers.py`，更新 HTML 构建、构建报告和既有测试 DOM 的浏览器方法支持。新增 17 项实际 handler 回归，覆盖导出、暂存恢复、存储失败、错误记录、包身份隔离、下载失败、CSV 往返、空白初始状态及盲化。

补充后的完整测试为352项，0 failures/errors/skipped。另以独立 headless Chrome 在实际 file:// 专家页面验证空白初始状态、定位填写区、填写、刷新恢复、真实CSV下载及禁止localStorage时仍可填写/下载；无脚本错误和网络请求。浏览器检查、下载样本和截图只保存于 `internal/browser_answer_check/`，不会打入专家ZIP。公开15文件盲化检查通过；245个受保护文件不变。
