# 曲线翻边现有方法检索与P002简化方案

2026-11-16，用户要求联网查找现有方法，明确“套着用用就行了，不需要那么严格”。本轮完成来源检索与路线选择，未修改运动实现或执行新的碰撞检查。

## 可借用的来源

1. Wang、Kinzel、Altan（1995），*Failure and wrinkling criteria and mathematical modeling of shrink and stretch flanging operations in sheet-metal forming*：曲线翻边的拉伸/收缩近似理论，实验支持并实现过FLANGE程序。本轮取得出版方摘要，未取得全文公式或可运行源码。[出版方](https://www.sciencedirect.com/science/article/pii/092401369401766T)
2. Zhang等（2003），*Shrink Flanging with Surface Contours*：摘要说明以折弯角度和几何条件预测周向应变，包含实验支持的起皱判据。可作为曲率/角度参数化背景，本轮未取得全文公式。[出版方](https://www.sciencedirect.com/science/article/pii/S1526612503700498)
3. Hu等（2003），*Analytical models of stretch and shrink flanging*：出版方预览说明采用总塑性应变、膜应变及塑性不可压缩等假设，忽略弹性应变，小工具间隙，接近直角翻边。可供以后加入应变指标，不照搬材料参数或失效阈值。[出版方](https://www.sciencedirect.com/science/article/abs/pii/S0890695503001500)
4. SOLIDWORKS官方 **Swept Flange / Edge Flanges**：截面沿路径构建复合翻边；曲边翻边支持相切直线/曲线组合。2020版说明扫掠翻边展开采用线性计算，不考虑压缩和拉伸。其建形思路适合本项目几何近似，但该功能本身不是成形时间仿真。[扫掠翻边](https://help.solidworks.com/2020/english/SolidWorks/sldworks/t_Swept_Flanges.htm)、[曲边翻边](https://help.solidworks.com/2024/English/SolidWorks/Sldworks/c_edge_flanges.htm)、[创建步骤](https://help.solidworks.com/2025/english/solidworks/sldworks/t_creating_swept_flanges.htm)

访问范围：部分出版方页面直开403，搜索索引返回原始摘要/预览；SOLIDWORKS搜索索引有正文，直开返回导航壳。另阅读作者大学网站全文 *Effective Models for Prediction of Springback In Flanging*，验证重点是直线翻边，未误当曲线运动的完整验证。[作者机构全文](https://nanomechanics.berkeley.edu/wp-content/uploads/2016/12/Paper27.pdf)

## 选定适配路线

先采用“曲线导向的截面扫掠＋角度插值”几何近似。借用现有CAD建形思路；时间进度、DXF/STEP端态约束及O094随动是本项目适配，不声称完整复现论文力学模型。

- 利用已有解析路径、STEP壁面/根部半径、厚度和高度建立局部截面；连接处共享网格，整个O088不当单一刚体。
- 每个动作依候选真实工序的共同进度，先用线性角度插值逐步从展开截面转到目标角度。需要有限半径时用圆弧弯曲段连接剩余直壁。
- DXF约束起始轮廓、STEP约束完成态；中间图纸差异保留记录，不再作为几何原型接入前置阻断。
- O094先完成自身直折，再随O088附着点的局部截面坐标系运动。H7仍严格子先父后，同序同时。
- 首轮采用厚度不变、忽略回弹/起皱/裂纹的几何假设。非刚体中间网格须适配碰撞检查，不能仍传一个刚体矩阵假装完成。
- 基本检查端态、网格连续性与倒置，再重查16候选。记录模型假设和采样密度；输出近似运动诊断，不是制造安全证明。

文献的曲线翻边/stretch或shrink flanging指P002既存BEND_CURVED动作，不改成项目业务中的FLANGING孔翻边类型。

实读p002_stage_motion.py：O088开始运动后C007仍未知，这是下一版接入点。当前v02仍251812已检/70352未知对×状态，调研不改变计数。此次仅文档更新，无新增测试、模型运行、提交推送；此前未提交修改、人工输入、候选评分、冻结基线和旧输出签名保留。
