# P001 业务数据接入录入页

2026-11-11，用户明确授权“接入”。原空项目 revision 4 已接入并保存为 revision 5，入口 http://127.0.0.1:8766/ 。

## 接入范围与使用

- 原业务表 224 个动作全部保留；211 个动作的 689 段几何原值保留，59 个折弯影响区、7 道工序及原专家分配接入。
- O215–O224 十个切边仅作历史记录，不进入新分配或事实导出。新分配范围为 214 个动作。
- 尚缺几何：O001（L形压印1）、O026（L形压印2）、O175（刻线1）。左侧“仅看待补几何”直接筛出这三项；选择动作后在底图绑定图元、应用修改、保存标注。
- 已有几何的动作可以高亮、聚焦。业务表几何以 `business_geometry` 独立保存，不冒充原 DXF 图元绑定。元数据编辑保留已有几何；明确选择 DXF 图元会替换当前采用的几何，清空绑定可恢复保留的业务几何。改变动作细分类型后须重新确认适用几何。
- 底图 2253 段中 2225 段标为参考，28 段按原折桥区域收纳逻辑归入桥内。不会要求重录全部底图；底图参考不等于已建立逐段来源绑定。
- `inventory_confirmed` 仍为 false，未替用户确认整图完成。补齐三项后再确认整图、整理几何和导出；本次没有把不完整清单宣称为正式导出完成。

## 来源与可恢复性

来源：`data/parts/P001/钣金加工数据表_P001.xlsx`，SHA256 `7c5453471547a1fa56510bf650a53f5b8a31b8089c53505196bc045bfe794259`，未写入或覆盖。156 条圆形几何圆心与底图最大差为 0.0003885528354742 mm；孔径差异按原业务表保留，没有用 DXF 半径覆盖。

执行入口：`.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.import_p001_business_project`；默认预检，`--apply` 才保存。已有动作项目拒绝覆盖，不能在现项目上重复接入。

接入前备份和审计在 `outputs/p001_editor_import_v01/before_project.json`、`project_preview.json`、`audit.json`；项目修订历史在 `projects/P001/revisions`。原表、旧修订及原始来源保留。后续直接编辑项目，不需重跑导入。

实现：`extract_app/business_geometry.py`、`scripts/import_p001_business_project.py`；工作流、校验、导出及网页支持已有业务几何。原几何 ID、段序号、坐标、半径及扫角保持；专家分配继续独立导出，事实表不含专家列。冻结候选、评分、H7、Graph、reference 未修改。

## 本次验证与限制

- 84 项 Python 测试通过：business_geometry、process_config、api、exporter、drawing_model、annotation_workflow。新测试核对所有 689 段与 59 个区域的导出值、临时磁盘保存/重读、xlsx 数值、切边排除、原几何恢复及拒绝覆盖。
- 24 项前端、42 项 DOM 检查通过。
- `test_business_editor_browser.mjs` 验证实际 224 动作、7 序、四类已有几何显示/聚焦、元数据修改与保存/重读、三项待补筛选、真实底图点选并应用。保存请求被拦截，未将测试编辑写入真实项目；真实项目保持 revision 5。缺失项截图中 revision 6 是测试模拟保存。
- `test_process_editor_browser.mjs` 三件回归通过。没有重跑全仓或三维实体测试。
- 保护清单 108 项中 104 项未变；差异是本次获授权的 P001 project.json，加此前用户修订的 P003/P004 project.json 与删除的旧 P004_01.dxf。doctor 因这些已知差异失败，未更新期望哈希。普通 `git diff --check` 仍报告已有 Windows CRLF 为尾空白，不能报告全仓格式检查通过。
- 本轮及先前工作区修改未提交、未推送。
