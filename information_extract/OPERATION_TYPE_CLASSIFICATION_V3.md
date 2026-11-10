# 动作大类与细分类型

确认日期：2026-11-02。

用户确认：P002 当前出现的动作按冲孔、预孔、翻孔、折弯、凸米、拉包六个大类组织。圆形、异形、一般折弯、曲线折弯属于细分类型。

## 字段约定

| 动作大类 | operation_type | 当前 feature_type |
| --- | --- | --- |
| 冲孔 | PUNCH | CIRCLE_PUNCH / IRREGULAR_PUNCH |
| 预孔 | PRE_PUNCH | PRE_PUNCH_CIRCULAR / PRE_PUNCH_NONCIRCULAR |
| 翻孔 | FLANGING | FLANGING_CIRCULAR / FLANGING_NONCIRCULAR |
| 折弯 | BEND | BEND_STRAIGHT / BEND_CURVED |
| 凸米 | DIMPLE | DIMPLE，目前未进一步细分 |
| 拉包（凸包） | DRAW_FORM | DRAW_FORM，目前未进一步细分 |

保留已有细分代码，避免为改名重新编号或重建动作。geometry_shape 继续描述几何形状，surface_side、protrusion_side、bend_angle_deg 是独立属性。

例如：一个圆形冲孔的 operation_type=PUNCH、feature_type=CIRCLE_PUNCH；一个曲线折弯的 operation_type=BEND、feature_type=BEND_CURVED。

这六类描述 P002 当前所涉类别，不删除 P001 已有的 TRIM、BRIDGE、MARKING 等类别。

## 本轮落实范围

已在桌面与项目副本的 P002、P003 工作表中将原 operation_type=CIRCLE_PUNCH / IRREGULAR_PUNCH 改为 PUNCH；feature_type 原值不变。分别修改 55、30 个动作的大类。

P002 桌面表新保存的十道工序配置完整保留，未修改 Station 能力、动作关系、默认角度、几何或专家分配。

桌面工作表位于“图纸文件夹/P002”和“图纸文件夹/P003”。项目副本位于 outputs/process_tables_20261102。原表备份及逐项核对记录位于 .runtime/process_tables_20261102/before_coarse_types 和 coarse_type_verification.json。

## 程序兼容边界

当前采集界面、标注 project.json、既有导出器和冻结的根项目仍使用原动作类型代码。本轮没有修改这些消费者或声明新版表已能直接运行候选生成。

后续需要显式版本适配：把 PUNCH 与其 feature_type 一起传给消费者；对旧接口，可依据已记录的细分类型无损恢复 CIRCLE_PUNCH / IRREGULAR_PUNCH。不得从动作名称、邻近关系或专家方案猜测细分。

工序能力也需要保持表达精度。用户填写 PUNCH 时表示允许冲孔大类；若实际只允许某种细分类型，需要记录该限制。旧接口中只允许 CIRCLE_PUNCH 的工序不能仅改名为 PUNCH 而放宽成允许全部冲孔。

DRAW_FORM、曲线折弯的几何与下游适配缺口仍按原状报告。采集程序下一次导出时，在完成类型适配前仍会使用旧分类，不能把旧导出当作本次分类的自动更新结果。
