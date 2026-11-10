# 加工动作与特征几何字段设计 v2

2026-11-02 补充：用户已确认新版工作表采用“动作大类—细分类型”，冲孔大类统一为 PUNCH。新约定及当前接入范围见 [动作分类 v3](OPERATION_TYPE_CLASSIFICATION_V3.md)。本文后续原代码表保留历史设计含义，尚未由本次表格变更自动更新全部程序消费者。

日期：2026-10-19。状态：部分接入采集编辑器，完整字段设计仍含后续提案。已实现“先标注、后批量整理”、细分动作类型、多图元绑定、多闭合路径记录及拉包凸起侧；未实现任意曲线折弯中线/影响区、三维状态、全部专用属性或根项目新类型适配。具体实现边界见 [ANNOTATION_WORKFLOW_V2.md](ANNOTATION_WORKFLOW_V2.md)。

整图动作解释完成后，才显式运行按类型整理；保存和选择图元均不触发最终几何生成。全部类型允许多图元输入，不能因类型提前限制选中条数。同一个动作中的来源 ID 去重，多个独立轮廓各有独立路径 ID；不同动作共用图元须明确确认。本文后续未明确标为已实现的扩展字段，仍属于设计。

本次目的：让圆形与异形翻孔、非直线折弯、拉包等信息有准确的保存位置，并减少重复人工录入。所有扩展先属于 information_extract 的采集记录。本文不替换 PROJECT_SPEC.md，不改变既有 P001、Sheet1–5、Graph0/1/2、H1–H7 或评分定义。

## 1. 设计依据与当前缺口

已核对：PROJECT_SPEC.md 第 2、5、7 节；src/data/schema.py；src/constraints/context.py；本模块 extract_app/model.py、geometry.py、exporter.py。

| 现行契约 | 本次设计需要补充什么 |
| --- | --- |
| 决策单位是 Operation → Station | 保持。特征整体、几何片段数量均不自动决定动作数量 |
| PRE_PUNCH、FLANGING 的标准几何以圆为主 | 保留类型，扩展其几何描述；异形翻孔不改标为 IRREGULAR_PUNCH |
| BEND 使用一条直线；Sheet5 为轴向矩形影响区 | 保留旧表示；另行容纳曲线路径和确认后的非矩形区域 |
| 凸米为 DIMPLE；没有拉包类型 | 提议增加 DRAW_FORM（拉包/凸包），不混入 DIMPLE、BEND 或 BRIDGE |
| surface_side 是材料光/毛面的工艺语义 | 保持；形状凸起方向另有字段，不与相机朝向、工具方向混用 |
| Sheet2 没有一组几何中各条轮廓的角色 | 新采集层支持多条轮廓及其角色，避免投影、剖面和独立孔混在一起 |

DRAW_FORM 是本项目提议的名称，不宣称它是通用标准或已经确定的具体成形机理。截图不能确定成形次数、工具结构、材料流动或所有曲线的含义。

## 2. 三层记录：动作、几何、专用属性

1. **动作表**：沿用动作编号、类型、表面侧和已经明确的关系。
2. **几何集**：一个动作可以关联多条路径、多个轮廓或若干参考图元；保留原实体来源。
3. **专用属性**：翻孔、折弯、拉包等按需展开，不让用户为每个动作填写全部字段。

`feature_id` 是可选的成品特征归组标识。例如一个翻孔特征关联 PRE_PUNCH 与 FLANGING 两个动作。它不替代 operation_id、process_group_id，也不自动产生先后关系。一个动作可关联多个 feature_id，支持来源中一个动作覆盖多个局部位置的情况；由明确资料确认，不按距离自动合并。

不要求用户逐个命名三维 CAD 面，不要求输入中间状态、模具或有限元材料参数。几何属性优先由图元计算；高度、角度等有来源才录入，未知可以保存。

## 3. 通用字段字典

“录入”表示可以由特征树/表格导入或在页面确认，并不要求手敲。来源记录与派生计算由系统保存。

| 字段 | 类型 / 值域 | 来源与必需性 | 含义 |
| --- | --- | --- | --- |
| schema_version | string，提议 `feature-fields-v2-draft` | 系统必填 | 新采集契约版本，不等于当前项目 JSON 的 schema_version=1 |
| part_id | string | 零件级必填 | 零件编号 |
| operation_id | string | 每动作必填 | 零件内唯一编号 |
| name | string | 每动作必填 | 用户能识别的动作名称；旧表 source_feature_ref 的来源 |
| operation_type | enum | 每动作必填 | 下表中的工艺动作类型 |
| source_feature_ref | string / null | 导入优先 | 原特征树的名称或标识，保留原文 |
| feature_ids | string[] | 可选 | 成品特征归组，不解释为依赖 |
| surface_side | SMOOTH / ROUGH / NOT_APPLICABLE / null | 有工艺侧别时确认 | null 是未确定；NOT_APPLICABLE 仅在明确不适用时使用 |
| cad_parent_operation_id | string / null | 显式来源 | 沿用既有含义；空值须结合字段状态区分无父级和未知 |
| required_predecessor_ids | string[] | 显式来源 | 已确认的工艺前置动作，不从曲线形状生成 |
| process_group_id | string / null | 根据明确 CAD 配对自动填写，或保留既有明确记录 | 仅用于现有预孔—翻孔配对；用户无需手输；不把任意特征组塞入此字段 |
| geometry_set_id | string / null | 系统建立 | 新版多轮廓几何集；不是直接改写旧 geometry_id |
| no_geometry_reason | string / null | 条件填写 | 确认无适用几何时说明；未绑定不能伪装为无几何 |
| notes | string / null | 可选 | 无法结构化的信息原文 |

每个零件另存 `length_unit`、`angle_unit`、材料坐标系、来源文件标识和 SHA256。单位未知就记录未知，不靠屏幕比例猜数值；计算前必须明确相关单位。

### 3.1 字段状态与来源

采用独立的 `field_metadata` 列表，不在每个业务字段中重复嵌套大对象。每项包含：

| 字段 | 定义 |
| --- | --- |
| field_path | 所属记录内的字段路径，例如 `form.protrusion_side` |
| status | CONFIRMED / DERIVED / UNKNOWN / NOT_APPLICABLE / PROVISIONAL |
| source_refs | 来源记录 ID 列表；来源记录包括文件、实体/表格行/文字说明或本轮用户确认 |
| derivation | 派生方法、输入字段及方法版本；非派生项可为空 |
| note | 冲突或待确认原因 |

UNKNOWN、NOT_APPLICABLE 的数值字段为 null，不能填 0。关系空列表/空父级只有 CONFIRMED 才代表确认没有该关系。DERIVED 不等于独立的人工工艺确认。支持状态另存于兼容性检查报告，不与数据真假混为一谈。

## 4. 类型与形状分开

| 用户名称 | operation_type | 几何表达 | 本次处理 |
| --- | --- | --- | --- |
| 冲孔—圆形 | CIRCLE_PUNCH | 圆形切口 | 保留 |
| 冲孔—异形 | IRREGULAR_PUNCH | 闭合曲线链 | 保留 |
| 预孔 | PRE_PUNCH | 圆或非圆闭合轮廓 | 保留类型；非圆作为表达扩展，不推断本图具体预孔形状 |
| 翻孔 | FLANGING | 圆形或非圆形的一组轮廓 | 同一个动作类型，不按形状新增工艺类型 |
| 凸米 | DIMPLE | 已确认的点位/局部轮廓 | 保留，不自动与拉包合并 |
| 折弯 | BEND | 直线、分段共线、折线路径、曲线路径或混合路径 | 保留动作类型；非直线路径的加工机理待来源确认 |
| 拉包（凸包） | DRAW_FORM（拟新增） | 局部成形覆盖轮廓，可附顶部投影、参考剖面等 | 新字段提案，当前生成器不支持 |
| 切边 | TRIM | 已有几何或确认无几何 | 保留旧定义及固定工序逻辑 |
| 折桥 | BRIDGE | 现有 footprint；原实体继续保留 | 保留现有起终点简化协议 |
| 刻线 | MARKING | 路径或确认无几何 | 保留 |

未知类型应保留原始名称并标记待映射，不能强制归到最像的旧类型。未来遇到攻丝等，再补充有明确含义的类型，不在本轮猜测拆分规则。

## 5. 几何集与路径字段

### 5.1 一组几何可以有多条路径

| 字段 | 类型 / 值域 | 说明 |
| --- | --- | --- |
| geometry_set_id | string | 几何集 ID |
| path_id | string | 每条独立路径/闭合环的 ID；不能仅靠 seq 区分两个轮廓 |
| role | enum，见下 | 路径所表达的内容 |
| view_kind | FLAT_PATTERN / FORMED_PROJECTION / SECTION / UNKNOWN | 展开图、成品投影、剖面或未确定 |
| coordinate_frame_id | string | 所属坐标系；剖面不能默认属于材料展开 XY |
| shape_kind | CIRCLE / CLOSED_CONTOUR / OPEN_PATH / POINT / UNKNOWN | 每条路径自己的形状 |
| closed | bool / null | 闭合性；以实际几何检查为准 |
| source_entity_ids | string[] | 多选的原始实体 ID，含稳定来源 |
| segments | list | 精确 LINE/ARC/CIRCLE 等有序片段 |
| derivation | object / null | 原图直接采用、中线推导、人工确认等来源 |

建议 role：CUT_CONTOUR（切口）、UNSPECIFIED_FLANGE_CONTOUR（未细分语义的翻孔轮廓）、FORM_FOOTPRINT（成形覆盖范围）、CROWN_PROJECTION（顶部投影）、BEND_REFERENCE_PATH（折弯参考路径）、BEND_BOUNDARY_A/B（双线来源）、REGION_BOUNDARY（影响区边界）、SECTION_PROFILE（剖面）、AUXILIARY、UNKNOWN。

角色 UNKNOWN 允许保存但不参与依赖该角色的计算。旧 P001 翻孔两个同心圆可以继续标为 UNSPECIFIED_FLANGE_CONTOUR，不强制补“内/外径”“预孔/成品孔”等未经确认的语义。

### 5.2 片段层沿用已有字段

`seq, segment_type, start_x, start_y, end_x, end_y, center_x, center_y, radius, sweep_angle` 保留精确值和有符号扫角。新路径把 seq 的作用域明确为单个 path_id。

非直线不等于一定是样条：可以是 LINE、ARC 的连续组合。若源文件确有 SPLINE/ELLIPSE，先保留原实体和不支持原因；后续支持需保存完整参数，不能只取端点连成直线。显示离散化必须单独记录误差设置，不冒充精确导出数据。

新版采集层可保留合法闭合链的稳定源起点，纯直线轮廓不必为了存储而发明起始圆弧；导出到旧 Sheet2 时仍必须通过旧起始段协议，协议不兼容应报告，不暗改旧规范。

系统可计算 bbox、路径长度、闭合区域面积、面积重心、圆心和半径；每个量注明所属路径/区域。凹形区域的重心不保证位于区域内；区域包含测试不能只使用该点。长宽默认是材料 XY 下的 bbox 尺寸，不当作截面尺寸或三维长度。

## 6. 各类专用字段

### 6.1 圆孔、异形孔、预孔

| 字段 | 必需性 | 说明 |
| --- | --- | --- |
| punch.cut_path_id | 几何完成时必需 | 当前动作自身的切口路径 |
| punch.shape_kind | 由路径派生 | CIRCLE 或 CLOSED_CONTOUR |
| punch.radius / center_x / center_y | 圆形时派生 | 只保存有定义的圆参数 |
| punch.contour_area / perimeter | 可派生 | 从精确闭合轮廓计算，不靠 bbox 面积替代 |

PRE_PUNCH 与 FLANGING 通过原 process_group_id 配对。预孔几何必须单独获取，不能复制翻孔轮廓或用统一比例缩放推算。孔处于某个拉包覆盖区内，不意味着它属于拉包动作或可以被当辅助线吸收。

### 6.2 翻孔：圆形与异形共用

| 字段 | 必需性 | 说明 |
| --- | --- | --- |
| flange.contour_path_ids | 几何完成时必需 | 一条或多条明确属于该动作的轮廓 |
| flange.opening_shape | 已知才填 | CIRCULAR / NONCIRCULAR / UNKNOWN；不由 operation_type 决定 |
| flange.opening_path_id | 可选 | 只有明确哪条是开口时才引用 |
| flange.base_path_id | 可选 | 只有明确哪条是根部范围时才引用 |
| flange.height | 可选 | 有图纸/模型依据才填；未知留空 |
| flange.extension_side | 可选 | SMOOTH / ROUGH / UNKNOWN；由明确资料确认 |

不强制每个翻孔必须有两个轮廓，也不把一对轮廓自动解读为预孔和成品孔。`surface_side` 不自动赋值给 `extension_side`，本轮用户确认的拉包方向约定不推广到翻孔。

### 6.3 凸米

| 字段 | 必需性 | 说明 |
| --- | --- | --- |
| dimple.footprint_path_id | 能可靠取得时填写 | 保留来源中的局部范围 |
| dimple.location | 可选/派生 | 来源为点时保存点；轮廓代表点需说明方法 |
| dimple.height | 可选 | 已知的局部尺寸，不要求补测 |
| dimple.protrusion_side | 可选 | 明确资料才填；本轮不按光/毛面名称自动推断 |

形状相近不作为 DIMPLE 与 DRAW_FORM 自动互换的依据。

### 6.4 折弯：先记录真实路径，再决定可计算内容

| 字段 | 必需性 | 说明 |
| --- | --- | --- |
| bend.reference_path_ids | 几何完成时必需 | 折弯的二维参考路径，允许多选和多个相关片段 |
| bend.path_kind | 可派生/确认 | STRAIGHT / COLLINEAR_DISJOINT / POLYLINE / CURVED / MIXED / UNKNOWN |
| bend.reference_method | 条件必需 | DIRECT_CONFIRMED / STRAIGHT_PAIR_MIDLINE / MANUAL_CONFIRMED / UNRESOLVED |
| bend.boundary_a_ids / boundary_b_ids | 双线方式时 | 两侧实际来源片段，保留跨间断多选 |
| bend.span_confirmed | 有跨间断合并时必需 | 是否确认将多个位置作为同一逻辑折弯路径 |
| bend.motion_model | 可选 | STRAIGHT_AXIS / UNSPECIFIED；曲线路径不能自动当作单轴转动 |
| bend_angle_deg | 可选，单位 ° | 已实现：一般/曲线折弯均可填；新建默认 90，可修改或留空。用户确认 P002/P003 既有折弯补为 90，不用于方向推断、影响区、约束或质量评分 |
| bend.inside_radius | 可选 | 有来源才填，不成为新增必填或质量项 |
| bend.influence_region_ids | 条件填写 | 已确认的影响区引用；未确定可以草稿保存 |

四处分段可能对应同一动作；一条折线路径也不自动拆成多个动作。动作数量来自已有特征/工艺定义，而非 CAD 片段数。

直线双边界仍按现有规则取中线。曲线双边界不能简单按两条曲线的端点平均生成中线；没有可靠配对算法时保存双边界并标 UNRESOLVED，允许选取图中已明确的参考路径。

### 6.5 拉包（凸包）

以“局部成形区域”记录，允许整体包含多个 CAD 面；不要求属于唯一平面。不凭视觉增加预成形、整形等动作。

| 字段 | 必需性 | 说明 |
| --- | --- | --- |
| form.footprint_path_ids | 能确认展开覆盖范围时填写 | 拉包涉及的二维材料区域，可为任意可靠闭合轮廓 |
| form.crown_path_ids | 可选 | 顶部轮廓/投影；缺失不阻塞基础记录 |
| form.section_path_ids | 可选 | 保存剖面示意为参考，使用独立视图/坐标系 |
| form.protrusion_side | 已知时确认 | SMOOTH / ROUGH / UNKNOWN，凸向材料哪一侧 |
| form.direction_basis | 有方向时必需 | EXPLICIT_USER / SOURCE_ANNOTATION / VERIFIED_MODEL / PART_CONVENTION |
| form.base_datum_ref | 高度有值时必需 | 高度测量基准；可引用明确的图纸基准，不要求新建三维面 |
| form.height | 可选 | 非负的凸起高度大小；没有依据留 null |
| form.profile_kind | 可选 | 来源中的形状名称或 UNKNOWN，不自动认定为某种拉深机理 |
| form.model_geometry_refs | 可选 | 可取得时关联三维几何集合，不要求人工逐面录入 |

几何集中已有精确轮廓时，不重复手填圆心、长宽、面积。高度不能从截图的透视或没有尺寸的剖面草图估计。若一项特征存在双向凸起，不强制压成单一正负值；保留局部说明并待明确表达，不自动拆动作。

## 7. 拉包方向：记录本轮已确认的含义

用户于 2026-10-19 结合本图和三维模型确认：观察方向面向光面时，光面拉包向观察者凸起，毛面拉包背离观察者凸起。

将它转为与视图无关的记录：

| 本图标注 | surface_side | form.protrusion_side | 沿材料光面法向的符号 |
| --- | --- | --- | --- |
| 光面拉包 | SMOOTH | SMOOTH | +1 |
| 毛面拉包 | ROUGH | ROUGH | -1 |

材料参考法向指向光面，符合 PROJECT_SPEC 中展开态的侧别约定。符号是相对于该材料参考，不是屏幕向上、世界 Z 或成形后所有位置共用的法向；旋转、翻转相机不改变字段。

本次确认适用于用户指出的拉包及该图的已确认标注约定，先记为零件/来源级约定；不能默认所有供应方所有零件同名标注都遵循它。当前没有正式新零件编号，记录中应使用临时来源标识，不能写成 P001 事实。

若 h 为有依据的高度、s 为上述符号，可以保存相对明确基准的 `signed_height = s * h` 作为派生量。它不等于工序中的世界坐标，也不是整片曲面的高度。h 未知时 signed_height 仍未知，但凸起侧可以已知。

该规则不自动用于折弯、翻孔、凸米，不推断冲头运动方向；材料凸起方向与工具进入方向分开。新增方向也不自动产生同序禁止、先后关系或罚分。

### 本次两处拉包的最小记录示意

| 字段 | 左侧光面拉包 | 右侧毛面拉包 |
| --- | --- | --- |
| name | 光面拉包 | 毛面拉包 |
| operation_type | DRAW_FORM（拟新增） | DRAW_FORM（拟新增） |
| surface_side | SMOOTH | ROUGH |
| form.protrusion_side | SMOOTH | ROUGH |
| form.direction_basis | EXPLICIT_USER | EXPLICIT_USER |
| form.height | null，未给尺寸 | null，未给尺寸 |
| form.footprint_path_ids | 待 DXF 绑定确认 | 待 DXF 绑定确认 |
| form.section_path_ids | 待区分并绑定剖面示意 | 待区分并绑定剖面示意 |

以上不是正式动作记录：没有编造零件编号、动作编号、尺寸、加工次数或 Station。截图中的黄色轮廓与横向剖面线不能直接合为一条材料平面轮廓。仅 FLAT_PATTERN 且坐标系明确、角色适用的几何才能参与展开态空间计算；成品投影和 SECTION 不自动参与。

## 8. 折弯影响区保留并分级扩展

| 层次 | 表达 | 当前用途 / 兼容性 |
| --- | --- | --- |
| LEGACY_AXIS_RECT | 现有水平/竖直直线 + 影响侧 + 远边界 | 完整保留现有 Sheet5 计算语义和 H6 |
| CONFIRMED_PLANAR_REGION | 人工确认的闭合区域，可含多块区域及内环 | 新采集表达；当前生成器暂不消费 |
| UNRESOLVED | 来源线和说明已保存，但范围尚不明确 | 草稿记录；不伪造矩形或默认无影响 |

新版区域字段建议：

| 字段 | 说明 |
| --- | --- |
| influence_region_id / bend_operation_id | 稳定区域 ID 和唯一绑定的 BEND |
| region_model | 上述三级表达 |
| region_components | 每项 outer_path_id 与 hole_path_ids；允许多块连通分量 |
| parent_region_id | 显式已确认的父区域；未知与确认根分开记录 |
| hierarchy_status | CONFIRMED / UNKNOWN；确认根时 parent_region_id=null |
| boundary_status | CONFIRMED / UNKNOWN |
| legacy_parameters | 完整现有 Sheet5 字段，仅 LEGACY_AXIS_RECT 使用 |
| planar_region_area | 新区域明确后派生的实际二维区域面积；排除内环 |
| reference_path_length | 几何路径长度；不能直接解释为实际成形长度 |

现有 affected_depth、affected_side、far_boundary_coord、affected_area_proxy 不删除；但不强制曲线路径套用单一深度或 POSITIVE_X/Y。多边形面积不直接替代旧 affected_area_proxy 进入 P1，避免改变原评分定义。

区域 parent 仍需要显式确认；包围、相交、面积和路径曲率均不自动生成 H6/H7。若未来允许同一 BEND 多个区域，要另行确定约束去重与同动作区域父链检查，当前不输出到旧管线。

## 9. 其它保留类型与实体处理

- TRIM、MARKING 沿用允许确认无几何的规则，不增加强制三维信息。
- BRIDGE 沿用现有轴对齐 footprint、起终点及内部辅助实体收纳。非矩形或斜向范围先保留原始几何，当前简化适配器不支持时明确报告。
- 拉包、翻孔、折弯影响区不自动继承 BRIDGE 的“包围即收纳”行为。内部可能有独立孔或其它动作；只有明确的辅助图元才可标记忽略，并可恢复。
- 切口、剖面、尺寸线、折弯双线、工艺文字保留不同角色。截图颜色只用于查看，不能单凭颜色确定角色。

## 10. 从采集到候选输入的兼容性边界

建议逻辑记录集合为 `operations`、`geometry_sets/paths`、`operation_details`、`bend_regions`、`sources/field_metadata`。先设计模块内版本化保存，再实现适配器；不立即往旧工作簿塞新列，也不预先决定新增 Sheet 编号。

| 数据 | 旧管线处理 |
| --- | --- |
| 完全符合 P001 契约的旧类型/几何/区域 | 经现有导出和回读验证输出 Sheet1–5 |
| 圆形/异形翻孔的通用新结构 | 圆形兼容项可有适配器；异形翻孔即便含 LINE/ARC，也不能因此宣称图特征和邻域逻辑已支持 |
| 非圆预孔、非直线 BEND、非矩形影响区 | 可保存新采集记录；旧约束/几何下游未经验证则阻止正式投递 |
| DRAW_FORM | 需要明确 Station capability 与下游处理，当前标不支持；不能映射成 DIMPLE 或 BEND 绕过 |
| 方向、高度等新元数据 | 保存但不自动成为 Graph 特征、硬约束或软罚分 |

三个状态要分别报告：信息已保存、几何/关系已确认、当前候选入口支持。不会因为能导出一张 Excel 就称整个新零件已可生成候选。旧数据缺少新可选字段不会被反过来判为失效。

## 11. 给用户的最小操作与系统职责

通常只需：选择动作实体 → 选择/导入名称、编号、类型、侧别 → 确认已有父关系与配对。对应类型才出现专用面板。

系统承担 ID、来源、单位换算记录、路径排序、几何检查、长度/面积等可确定计算。高度、成形角度、圆角、面 ID 不作为通用必填，不要求用户为了保存去学习 VISI 的逐面查询。

几何角色不明时可以暂存：选中的线确实属于某特征，并不表示已经确认这些线是覆盖范围、中线或剖面。正式使用某种几何计算前，再完成相应角色确认。

## 12. 实现进度与后续验收条件

1. 已实现多路径记录、预孔/翻孔形状选择和旧项目读取；逐路径角色编辑与完整新契约适配尚未实现。
2. 已实现 DRAW_FORM 记录及光/毛面凸起侧；专用剖面引用、可选高度等仍为后续字段。
3. 已实现曲线折弯原始图元保存；可靠中线推导、专用影响区仍待规则确认。
4. 已实现旧下游适配状态报告；新区域表达及其消费规则仍需另行讨论。

应覆盖的验收用例：

- 圆形翻孔旧数据往返不变；异形翻孔不变成异形冲孔；预孔保持独立几何与配对。
- 同一动作多轮廓不串接；剖面线不计入材料 XY 邻域或覆盖面积。
- 四处分段直线仍可对应一个 BEND；曲线不被替换为端点直线或按段拆动作。
- 拉包方向随相机旋转保持；未知高度保持 null；本图约定不污染其它零件/类型。
- 新拉包不自动吸收顶部独立孔；新类型未接入 capability 时不能正式进入旧生成器。
- 旧 Sheet5、H6、P1 的数值与含义保持；非矩形区域不偷换旧面积罚分。
- 来源/角色/边界变化使相关派生量失效；保存不触发推导，需显式重新整理后才能导出表格。

P002 图纸已接入。尚待实际数据解决：特征树动作清单、各条投影/剖面线的角色、非直线折弯的实际工艺含义、拉包对应的真实动作数和 Station capability。不根据截图补答案。
