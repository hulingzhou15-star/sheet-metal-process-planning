# 信息提取模块首版实现与验收

日期：2026-10-18  
范围：`D:/sheet_process_assignment/information_extract`  
页面：<http://127.0.0.1:8766/>  
启动和操作说明：[README.md](README.md)

## 1. 已交付

已把计划推进为可运行的本地工具：DXF 精确解析 → 多选创建加工动作 → 属性与工艺关系编辑 → 折弯中线/折桥简化/影响区 → 保存恢复 → Sheet1–5 草稿或正式导出。

默认项目为空白 P001，保留用户正式标注的位置。本次 QA 示例均已单独备份，不能作为真实工艺识别或完整 P001 数据集。未自动导入 reference、现有几何答案或候选分配。

## 2. 功能与操作边界

| 功能 | 当前行为 |
| --- | --- |
| DXF | 复用经过审查的解析核心，支持 LINE/ARC/CIRCLE/LWPOLYLINE 与嵌套 INSERT；保留原始来源 |
| 材料坐标 | 将端点、圆心、半径、有符号扫角统一到选定材料块坐标；含缩放/镜像回归 |
| 图纸 | 显示完整源图；默认 270° 熟悉视角，可切换原方向；缩放、平移、全图、聚焦当前 |
| 选择 | 点选、Shift 多选/减选、框选、同源子段扩选、密集实体选择列表/Alt 切换 |
| 动作 | 创建、修改、删除、类型筛选和搜索；名称、编号、表面侧、父动作、明确前置、加工组 |
| 稳定身份 | 动作内部 UID 与可编辑编号分开；实体绑定使用源 SHA/handle/插入路径/段号 |
| 折弯 | 多处分段双线合为一个 BEND_LINE；跨度明确确认；不把单侧边界当中线 |
| 折桥 | 完整轴对齐外围生成 BRIDGE footprint；完整包含的未绑定 LINE/ARC 收纳；跨界线与已绑定动作保护 |
| 影响区 | 用户确认影响侧、远边界、父级或明确无父级；计算 Sheet5，不推断 H6 |
| 处理状态 | 直接绑定、区域收纳、背景/忽略、尚未处理分别统计，删除/撤销重新计算 |
| 持久化 | 原子更新项目文件，保存修订；旧 revision 拒绝覆盖；源 SHA 不符拒绝重新绑定 |
| 数据导入 | JSON 项目、CSV/TSV/XLSX 动作清单、用户 Sheet3/4；清单不按行顺序制造加工先后 |
| 草稿导出 | 始终保留缺项和检查结果；不要求提供专家答案 |
| 正式导出 | 标注完成后调用现有 facts loader 和单次 H1–H7 可行性见证；可行/不可行/未决分开 |

删除动作可撤销。可能放弃未保存内容的操作使用页面内异步提示；避免原生 confirm/prompt 对内置浏览器自动化造成阻塞。

## 3. 新增/修改文件

模块核心：

- `extract_app/drawing.py`：材料坐标与稳定来源适配。
- `extract_app/geometry.py`：普通曲线、轮廓、中线、footprint、完整曲线包含、影响区。
- `extract_app/model.py`：项目数据、关系校验、覆盖统计、修订保存。
- `extract_app/exporter.py`：标准工作簿、导入、独立事实与硬可行性检查。
- `extract_app/server.py`：本机服务及页面/API/导出文件路由。
- `extract_app/legacy/`：迁入六个解析核心文件，改为包内导入；`migration_manifest.json` 保留旧源 SHA。

界面与入口：

- `web/index.html`、`web/app.js`、`web/style.css`。
- `configs/app.json`。
- `scripts/serve.py`、`scripts/run_tests.py`、`start.ps1`。

验证与文档：

- `tests/test_drawing_model.py`、`tests/test_geometry.py`、`tests/test_exporter.py`、`tests/test_api.py`。
- `tests/test_frontend.mjs`、`tests/test_ui_dialogs.mjs`。
- 本报告、README、DEVELOPMENT_PLAN 状态更新。
- `.runtime/deps/` 为模块独立 DXF 运行依赖；`.runtime/` 还存放临时目录、服务日志和保护基线。
- `projects/P001/` 与 `outputs/` 为模块运行产物。

旧 Desktop 源码、根项目业务模块与原始数据没有修改。生产入口不依赖旧 Desktop 路径或审查目录中的依赖。

## 4. 测试证据

本模块 Python **74 项全部通过**：

- 36 项几何派生测试。
- 13 项材料坐标、状态和持久化测试。
- 16 项工作簿及现有业务接口测试。
- 9 项真实本机 HTTP 集成测试。

另有前端纯函数/DOM 契约 **14 项通过**，页面异步提示和编辑状态回归 **14 项通过**，合计 28 项前端检查；JavaScript 语法检查通过。DOM-stub 检查不冒充真实浏览器端到端操作。

关键覆盖：

- 四处不连续双线生成一条中线；单侧、错选、非平行和跨度未确认被识别。
- 圆弧端点在区域内但弧本身出界时，不整条收纳。
- BRIDGE 内部辅助线收纳、跨界排除、已绑定保护、撤销恢复。
- 全精度数值、负扫角、空字段、稳定几何编号与 Sheet3/4 原值保留。
- 正式输出实际调用现有无专家入口和 H1–H7；不可行保留诊断草稿，UNKNOWN 不等于无解。
- 保存冲突和修订写入失败不覆盖当前项目；动作改名同时更新父/前置与固定分配引用。

日志与环境：[Python 测试日志](outputs/acceptance/python_test_results.txt)、[前端测试日志](outputs/acceptance/frontend_test_results.txt)、[异步提示回归日志](outputs/acceptance/dialog_test_results.txt)、[运行版本](outputs/acceptance/runtime_versions.json)。这些是新模块测试，未宣称根项目全部历史测试通过。

## 5. 浏览器实际操作

在 P001 原图上执行并核对：

1. 密集同心圆选择列表中选取一个圆，创建 QA_CIRCLE，保存后刷新恢复。
2. Shift 多选四处共八条双侧片段，创建 QA_BEND，预览只有一条逻辑中线；填写并保存一个轴向影响区。
3. 创建和修改矩形外围 QA_BRIDGE，内部 1 条辅助线自动收纳；撤销为 0、重做恢复为 1。
4. 原方向/270° 切换保持原始 path 坐标不变。
5. 导出含三种示例几何的草稿，页面清楚列出尚未完成清单、未处理片段及 Sheet3/4 缺项。
6. 检查当时浏览器控制台没有 error/warn。

QA_BRIDGE 是用原图中的矩形作工具验收，不代表自动确定该实体真实加工类型。示例影响区也只验证编辑/计算链，不写回真实 P001 元数据。

验收后将示例独立保存在 [browser_example_project.json](outputs/acceptance/browser_example_project.json)，默认项目恢复 0 个动作、2,253 个尚未处理片段。原先使用原生确认框的旧验收页出现自动化阻塞，已将相关功能替换为页面内异步提示，并完成确认/取消/输入/键盘、动作切换、预览不产生假修改、删除与撤销等 14 项 DOM-stub 回归；旧会话的阻塞不作为新功能浏览器端到端通过的证据。

页面预览：[整件编辑页](outputs/acceptance/editor_preview.png)、[局部范围示例](outputs/acceptance/bridge_absorption_detail.png)。几何验收来源见 [p001_geometry_cases.json](outputs/acceptance/p001_geometry_cases.json)。

## 6. 冻结范围核查

本轮开始前，对根项目 `src/configs/docs/data/tests` 以及 PROJECT_SPEC/README 共 182 个既有源文件和数据文件记录 SHA；结束比对没有变化。

[frozen_integrity.json](outputs/acceptance/frozen_integrity.json) 记录具体结果。这个清单不是根 outputs 所有历史文件的完整快照；本轮各写入路径均在 information_extract，没有运行修改历史候选/盲化/评分结果的入口。

输入 DXF SHA 始终为：`180a7761767ed99402df43516d71403b61fa1dcc725363a190aa4e51d16365ef`。

## 7. 待用户试用及已知限制

- P001 的完整动作清单、几何绑定、真实影响区及 Sheet3/4 仍需要用户标注/填写；本轮没有用既有答案替用户完成。
- 本图能否覆盖当前工作簿后续修订的全部几何，尚未逐动作对照。
- 纯直线异形孔的起始段协议未定义，程序明确报告；多个可选起始弧需人工指定，不新增自动排序规则。
- BRIDGE 目前要求完整、轴对齐外围；斜桥、非矩形外围不能直接套用现有 footprint。
- Sheet5 仅支持现有水平/竖直、轴向矩形表示；不支持的斜折弯影响区需另议表示方式。
- 多材料块实例或不支持 DXF 实体需要明确处理，不静默忽略。

首版已具备用 P001 开始真实数据整理的功能基础。下一步是实际标注过程中记录操作问题与具体几何表达缺口，再针对实例改进。
