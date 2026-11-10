# 旧 DXF 提取代码复用审查

日期：2026-10-18  
被审查目录：`C:/Users/hulin/Desktop/新建文件夹/dxf_extractor`  
目标模块：`D:/sheet_process_assignment/information_extract`

## 1. 结论

旧代码的 DXF 基础解析、坐标变换、原始来源追踪及部分显示逻辑可以复用。它是几何提取器和只读查看器，不是已经具备动作标注、双线中线合并、折桥收纳、Sheet5 编辑功能的应用。

建议抽取并迁移核心，新增项目模型与交互层；不直接运行旧批处理流程来制作新标注数据，不整体搬入 P001 专用修改脚本。

## 2. 本次实际检查与证据

已阅读 README、主入口、解析/变换/校验/局部坐标模块、重建显示与点击逻辑、Excel 适配层及相关测试；检查当前项目 Sheet1–Sheet5 字段、几何校验和无专家入口。

使用本目录审查脚本直接只读导入旧代码，实测结果：

| 检查 | 结果 |
| --- | --- |
| test_extractor.py | 与下两组共 42 项；全部通过，0 failures / errors / skipped |
| test_candidate_coordinates.py | 同上；覆盖局部坐标、嵌套、镜像、实例等 |
| test_reconstruction.py | 同上；覆盖有符号圆弧、显示映射、断链拒绝等 |
| 本目录 Plotview 1.dxf 实际解析 | 通过几何校验；无 issues、无拒绝实体 |
| 基础片段 | 2,253：LINE 1,813；ARC 280；CIRCLE 160 |
| 原始几何对象/旧 CAND | 1,317；不是加工动作或工艺候选方案 |
| 图纸单位和变换 | INSUNITS=4（mm）；Plotview 1 一处插入；270°，单位比例 |
| 坐标契约反例探针 | 局部半径 4 的圆经 2 倍插入后，输出局部圆心仍为 (2,3)，radius 为 WCS 下的 8；旧查看器拒绝该非单位比例视图 |
| 文件保护 | 审查清单中的旧源码/测试及本目录 DXF，运行前后 SHA 一致 |

可复核文件：

- [审查运行器](audits/check_legacy_extractor.py)
- [结构化结果与保护文件 SHA](audits/legacy_check_results.json)
- [42 项测试日志](audits/legacy_test_log.txt)

当前项目虚拟环境和本次 bundled Python 起初均未安装 ezdxf。为运行审查，仅在 `audits/runtime_deps/` 安装 ezdxf 1.4.4 及依赖；缓存关闭，临时文件位于 `audits/tmp/`，禁用源码目录 pycache 写入。未修改全局环境或根项目虚拟环境。

复跑方式（审查专用，不是未来应用启动方式）：

```powershell
& 'C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B -X utf8 'D:/sheet_process_assignment/information_extract/audits/check_legacy_extractor.py'
```

未运行旧 Excel 写入器、旧业务修改脚本或真实浏览器交互。本次测试通过不代表未来标注功能已通过，也未逐动作证明本图与当前 P001 工作簿完全一致。

## 3. 复用清单

| 代码 | 建议 | 理由/改造点 |
| --- | --- | --- |
| entity_parser.py | 复用核心及测试 | LINE 使用正确坐标语义；ARC/CIRCLE 处理 OCS；polyline bulge 保留符号和顶点顺序 |
| transform.py | 复用核心及测试 | 复合变换、圆形保真判断、有限数/非平面检查、镜像方向；拒绝将椭圆冒充圆 |
| extractor.py | 迁入后适配 | 递归 INSERT、MINSERT、图层属性、来源路径可用；需要选择材料坐标、稳定来源键与更独立的数据接口 |
| dxf_loader.py | 复用读取/审计思想 | 在另一份内存 document 上 audit，避免修复污染提取对象；去掉新流程不需要的 reference 关联 |
| candidate_coordinates.py | 复用矩阵逆变换，补齐坐标契约 | 点字段是 local，radius/sweep 明确仍是 WCS；多实例返回相同定义坐标也不能丢失实例身份 |
| validate.py | 保留原始层校验，新增动作层校验 | 旧校验要求 raw→candidate 一一映射和不跨独立实体合并；不能直接用于一个动作选多条线的新层 |
| reconstruct.py | 复用显示映射/部分曲线绘制，替换入口 | 当前依赖已保存的三位小数候选 Excel，采用展示专用圆弧端点校正；新流程应读取精确原始模型，不把展示近似导出回几何 |
| reconstruction_viewer.js / .html | 复用缩放、平移、曲线距离命中思路 | 目前单选、只读；需多选、框选、候选切换、动作编辑、保存与恢复；点击容差等参数配置化 |
| tests/test_extractor.py 等三组 | 迁入适用测试，扩充 | 已实跑 42 项；后续必须增加材料坐标半径、编辑生命周期与浏览器验收 |
| main.py | 不直接用作应用入口 | 默认块名、旧输出位置、样本核验、历史导出流程有专用假设；新入口写入只允许本模块 |
| export_excel.py / .mjs | 不直接作为 Sheet1–Sheet5 导出器 | 输出旧 raw/candidate 表，实际数值 round 到 3 位；会按旧位置建立 Node 依赖链接，需独立导出适配 |
| manual_coordinate_validation.py | 只参考方法/样本 | 是特定 BEND/BRIDGE 对照核验，不能当成自动识别/自动生成实现 |
| target_holes*、bend_hole_relations*、surface_side*、sweep_angle* | 不并入核心流程 | 特定历史孔集、ID、名称和修改任务；尤其扫角绝对值处理与当前有符号 ARC 契约不兼容 |

## 4. 必须处理的适配问题

### 4.1 坐标字段不能混用

旧模块本身公开注明 radius/sweep 使用 WCS，旧查看器也主动拒绝不适用的变换。因此这不是本次发现“P001 提取错误”，而是新工具复用时必须处理的接口限制。

新模块必须让输出坐标、半径、有符号扫角处于同一材料坐标系；无法可靠转换时明确限制导入。不能因为点的正逆变换通过，就认为曲线参数已全部转换。

### 4.2 旧 CAND 不是 Operation

旧实现为一个独立几何对象或一个 polyline 分配 CAND，并保留片段 seq。它没有把多组分隔双线合成同一 BEND，也没有根据外边界生成 BRIDGE 和收纳内部线。新应用必须增加动作层和派生几何层，不能改名 CAND→Operation 就宣称完成。

### 4.3 稳定来源与恢复

旧 E/CAND 编号依赖遍历次序，改变源文件可能改变编号。旧代码已经保留 handle、insert_path 和 polyline_seq，可以据此构建来源键，再绑定源文件指纹。恢复标注不得只靠旧临时编号。

### 4.4 精度、圆弧和输出格式

旧 raw/JSON 保存完整精度，Excel 适配有独立的三位小数舍入；重建层还有仅供显示的端点校正。新项目状态和正式 Sheet2 使用完整精度，显示格式另设，导出后用当前 M1 静态规则回读验证。保留局部负扫角，禁止应用旧专项扫角取正流程。

### 4.5 选择与范围统计

旧查看器只选一个片段，临近曲线通过屏幕空间距离判断。新工具必须提供多选集合、同一动作的来源分组、编辑撤销、BRIDGE 收纳关系和去重统计；未选择不能简单解释为未处理。

### 4.6 文件边界和历史来源

旧 main 默认写旧目录 outputs，Excel helper 可能在旧源码旁建立依赖链接。新入口需要明确工作根和路径约束，不复用这些默认副作用。此次未执行这些导出路径。

尝试对旧 Desktop 的同名 DXF 做哈希对照时，该文件被其它进程占用，未强制解锁。因此没有声称两个同名 DXF 字节一致。后续开发以本模块内已实测和记录 SHA 的文件为准；这不阻塞开发。

## 5. 后续迁移顺序

1. 按 DEVELOPMENT_PLAN 的 E1，将通过审查的解析/变换核心及适用测试复制到本模块，记录源 SHA 和迁移修改。
2. 建立精确材料坐标适配与稳定实体来源，先验证本样本和合成坐标用例。
3. 重建可多选的查看层；直接读取原始精确数据，不经过三位小数 Excel 中转。
4. 新增动作编辑与持久化，再做双线中线、BRIDGE 收纳、Sheet5、标准导出。
5. 旧脚本继续作为历史工具保留；新应用运行不依赖它们的位置或历史产物。

本次只完成复用审查、验证和开发计划，未复制生产核心、未实现标注应用、未改 P001 业务数据。
