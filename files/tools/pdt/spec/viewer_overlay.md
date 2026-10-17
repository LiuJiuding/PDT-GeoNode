<!-- owner: user; agent: read-only -->
<!-- 条目格式见同目录 README.md：不变量一行一条，ID 写在行首粗体里。
     本文件初稿由 用户 依 docs/10-文档分层与协作规范.md 的示例落成（2026-09-13），
     之后由用户维护；agent 只在回复里提建议，不直接改。 -->

# Enhanced Viewer Overlay（增强属性视窗可视化）最小规范

## 目标

视口里把活动 viewer 快照的 `Float3 = FLOAT_VECTOR`（UI 叫 Vector）属性画成"从元素位置出发的方向线段"， `Quaternion = QUATERNION`（UI 叫 Rotation）画成"三轴 tripod"，`Float4x4 = FLOAT4X4`（UI 叫 Matrix）画成"具有三轴缩放的 tripod"

## 不变量

- **EVO-1** 作用域：只对活动 viewer 的快照几何生效，与现有颜色/文字 overlay 完全同域（含 mid-tree、实例、各组件）。
- **EVO-2** 开关：复用 Overlay ▸ Viewer Node 面板；关闭后不画。
- **EVO-3** Float3： 模式1方向，线段长度 = 1 × 用户缩放（世界单位）；模式2向量，线段长度 = |v| × 用户缩放（世界单位）。矢量线段的默认色是黄色，用户可调，显示线段时不刷当今float3属性的颜色。
- **EVO-4** Quaternion： 方向，tripod三轴轴长相等 = 1 × 用户缩放（世界单位）。轴用主题 TH_AXIS_X/Y/Z
- **EVO-5** Float4x4： 方向，tripod三轴轴长 = matrix三个轴向的scale × 用户缩放（世界单位）。轴用主题 TH_AXIS_X/Y/Z
- **EVO-6** 规模上限：元素数超过 1,000,000 时，本 overlay 什么都不画，只允许一行提示文字
- **EVO-7** 绘制样式：引导线不做深度遮挡（永远可见）；退化值（零向量/零轴 scale）不画、起点小点不画。

## 验收

- 验收判据按L1走

## 明确不做

不做交互拖拽（只读显示）；不自研 viewer 节点；不进入渲染/导出（仅视口 overlay）； 不改变现有颜色/文字 overlay 的语义。