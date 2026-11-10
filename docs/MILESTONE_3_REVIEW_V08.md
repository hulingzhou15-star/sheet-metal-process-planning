# M3 v0.8：P001_R02 无 reference 候选盲评包

日期：2026-10-15。本批次消费已冻结的 outputs/m3_reference_free_v07 中16个 selected 和13组局部对照。不重新求解、不改分配或评分，不引入专家方案，不覆盖旧24对评审。

## 比较设计

整体部分：16个 selected 按已记录seed排成一个环，相邻成对并闭环，形成16对。每个候选在整体部分出现两次，不使用软分、专家距离或历史答案选择胜负对象。

局部部分：完整纳入13组去重的同序/分序对照，保留来源focus pair于内部记录，不给出自动标签。相同分配用hash去重，和整体部分共享方案身份；最终涉及29个不同方案和29对不重复比较。

方案ID统一重编为R02_PLAN_01…29，比较ID为R02_CMP_001…029。整体与局部混排，A/B随机交换，seed与全部映射只保存在internal。新ID也使浏览器暂存与旧批次隔离；签名还包含完整方案分配，错批次暂存不能恢复到本包。

局部构造共享一个已生成base，重复方案并非独立观察；后续训练和评价仍按零件分组，不把这些比较解释为29个独立零件或足够监督。本批次没有插入专家方案作标准答案。

## 页面与答案

沿用既有二维UI、完整轮廓资产、默认270°旋转、配色、同步切序/缩放/平移、difference-only、region filter、Operation详情和单方案视图。完整方案、Station load和客观分配差异可见；生成角色、LOCAL/WHOLE类别、focus pair、seed、候选原ID、软分和内部映射不下发到公共payload。

评审填写A/B/C/D/E、confidence和可选备注，原定义不变。原始表单全部空白，没有预选答案或把握程度。HTML本地暂存不改写随包CSV；用户点击导出得到comparison_pairs_completed.csv，说明文档建议改名P001_R02_completed.csv返回。

唯一既有源码修改是src/visualization/geometry_adapter.py：为build_geometry_catalog增加可选region_assignments。旧调用默认行为保持，新的无reference入口显式传入已验证候选分配，用于D3统计。几何membership只由代表点和既有区域定义决定，已测试换方案后membership一致；不伪造ExpertReferencePlan或读取隐藏专家字段。

## 包含内容与隔离

交付文件：outputs/m3_review_v08/P001_R02_review_pack.zip。

ZIP根目录含review_pack.html、使用说明.md、空白comparison_pairs.csv、plan_cards内29个单方案HTML及29份分配CSV、previews内4张SVG及4张PNG。完全离线，无外部资源请求。只打包expert_facing子树，不包含internal、构造角色、测试下载或已填写答案。

internal保存映射、比较设计、完整来源分配、来源hash、几何审计、视图、构建报告、浏览器截图及测试结果。browser_export_check.csv是自动测试填写结果，不是专家标签，未进入ZIP。

构建器校验事实指纹和方案hash、独立复核H1–H7、核验局部角色及全部变化。目标目录不允许覆盖旧里程碑；检测到已填写原表或公共目录中的新下载/未知文件时拒绝重建，保护人工结果。

## 参数与运行

参数入口：configs/m3_review_v08.json。包含新批次ID、盲号前缀、三个seed、整体选对方式及预览工序；不含质量阈值和新的工艺规则。

```powershell
.venv-m3/Scripts/python.exe -B scripts/build_reference_free_review_v08.py
& 'C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' tests/review_v08_browser_qa.cjs outputs/m3_review_v08
.venv-m3/Scripts/python.exe -B scripts/verify_review_v08.py
```

浏览器QA使用新建的headless Edge配置，通过file://直接打开交付HTML，不接触用户现有浏览器配置。检查视角、切序、动作详情、区域、差异、CSV实际下载和刷新恢复。自动填写结果仅在该临时浏览器上下文与internal测试文件中保存。

## 文件清单和验证记录

修改：src/visualization/geometry_adapter.py（可选分配输入）。

新增：

- configs/m3_review_v08.json
- src/review/reference_free_pack.py
- scripts/build_reference_free_review_v08.py
- scripts/verify_review_v08.py
- tests/test_m3_review_v08.py
- tests/review_v08_browser_qa.cjs
- docs/MILESTONE_3_REVIEW_V08.md
- outputs/m3_review_v08/ 产物。

新增14项测试覆盖比较覆盖率、随机身份/左右方向、局部来源、无专家访问、membership稳定性、公共字段白名单、真实JS表单导出与恢复、图形交互、ZIP隔离及人工结果保护。完整套件和逐文件冻结核验以internal/test_results.json、internal/frozen_integrity.json为准；历史5项快照失败不改写旧manifest掩盖。

下一步等待本批次真实评审结果，按批次与规则版本入库。D保留为双方都有问题的反馈，不自动变成A/B、平局或具体Operation pair标签。未开始模型训练。
