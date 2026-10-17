# PDT Selection（几何节点）

> 分类：`PDT` ｜ 节点 ID：`GeometryNodePDTSelection` ｜ 添加方式：`Shift+A` → **PDT** → **PDT Selection**

把一行**选择字符串**变成 `Boolean` 字段。选择字符串的完整语法见
[`../reference/selection_syntax.md`](../reference/selection_syntax.md)（规范）。

它相当于"用一个可读的表达式描述一批元素"，然后用这个描述去驱动任何吃选择的节点
（`Delete Geometry`、`Set Position`、`Separate Geometry`…）。

---

## 输入

| 名称 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| **Geometry** | Geometry | — | 选择所参照的几何。**同时原样直通到输出**，所以可以直接串在链路中间。 |
| **Selection** | String | `*` | 选择字符串。见 [语法规范](../reference/selection_syntax.md)。 |
| **Domain** | 节点属性（菜单） | `Vertex` | 索引指的是哪种元素：`Vertex` / `Edge` / `Face` / `Control Point` / `Spline` / `Point` / `Instance`。**画在节点体上，不是 socket**（和 `Delete Geometry` 的 `Domain` 一样）。 |

### Domain（元素种类）

`Domain` 说的是**索引指向哪种元素**，也顺带决定**这个节点作用在哪个组件**上
（L0 `spec/pdt_selection.md` PDS-8 / PDS-9）。它不是 Blender 的属性域枚举：网格顶点、
曲线控制点、点云点在 Blender 里是**同一个** `AttrDomain::Point`，所以这里把它们分成三种。

| 选项 | 标识符 | 索引指的是 | 对应组件 | Blender 属性域 |
| --- | --- | --- | --- | --- |
| **Vertex** | `VERTEX` | 网格的**顶点** | Mesh | `Point` |
| **Edge** | `EDGE` | 网格的边 | Mesh | `Edge` |
| **Face** | `FACE` | 网格的面 | Mesh | `Face` |
| **Control Point** | `CONTROL_POINT` | 曲线的**控制点**（跨样条扁平） | Curve | `Point` |
| **Spline** | `SPLINE` | 曲线的**样条**（一根样条一个序号） | Curve | `Curve` |
| **Point** | `POINT` | **点云**的点 | Point Cloud | `Point` |
| **Instance** | `INSTANCE` | **顶层实例**（一个实例一个序号） | Instances | `Instance` |

`Domain` 决定**索引与 `@ptnum`/`@elemnum`** 的含义，也让输出字段在这个域上求值。
组属性与 `@属性` 若位于别的域，交给 Blender 的字段系统做域转换
（行为与 `Named Attribute` 一致）——也就是说，用 `Vertex` 域也能读到面域上的组，
Blender 会自己插值。

> `Spline` 在 Blender 里叫 **curve domain**（`AttrDomain::Curve`）：这里用 "Spline"
> 当界面标签，是为了不和组件 `Curve` 混淆。
> `Vertex` / `Control Point` / `Point` 三个选项值相同（都是 `Point` 域），
> 区别只在**作用组件**：这是本节点的刻意设计（一份菜单同时说清"哪种元素"和"哪个组件"）。

### 作用范围：只在 Domain 对应的组件上

一个几何集合里可以**同时**有多个组件（比如 `Join Geometry` 把网格和曲线拼在一起）。
**选择只在 `Domain` 对应的组件上求值，其余组件恒为假**
（L0 PDS-8 / PDS-9）。`Vertex` 只选网格顶点、`Control Point` 只选曲线控制点、
`Point` 只选点云的点、`Instance` 只选顶层实例：

| 组件 | 可用的 Domain |
| --- | --- |
| Mesh | `Vertex` / `Edge` / `Face` |
| Curve | `Control Point` / `Spline` |
| Point Cloud | `Point` |
| Instances | `Instance` |

**不搭配不是错误**：在别的组件上就是"什么都没选中"（与越界序号同一种处理），
几何照常直通、也不报警告（PDS-9）。想要同时命中网格和曲线，就用两个节点。

> 组与 `@属性` 的**引用告警**只看 `Domain` 圈定的那个组件（选 `Control Point` 时不再因为
> 网格上没有那个属性而报警，反之亦然）——`group:` 与 `@属性` 在实例域上真的存在
> （`Store Named Attribute` 的 `Instance` 域），查不到就照常告警。

> ⚠️ **实例域与其它域之间没有插值**：Blender 的字段系统里实例域是**孤立**的，
> `adapt_domain()` 对任何跨域转换都返回空数组。所以 `Domain = Instance` 接到点域消费者
> （`Set Position` 等）**什么都不会选中**，反过来 `Vertex` 在实例上也一样——
> 这是 Blender 的语义，不是本节点的选择，也不会报错。要选实例就把它接到实例域的消费者上
> （`Translate/Rotate/Scale Instances`、`Realize Instances.Selection`、`Delete Geometry`
> 的 `Instance` 域、`Instances to Points.Selection` …）。

---

## 输出

| 名称 | 类型 | 说明 |
| --- | --- | --- |
| **Geometry** | Geometry | 输入几何**原样**输出，方便把节点串在链路里。 |
| **Selection** | Boolean（字段） | 选中的元素为真。在 `Domain` 指定的域上求值；`Component` 之外的分量恒为假。 |

---

## 行为

### 求值

按 [语法规范 §1](../reference/selection_syntax.md#1-求值模型一个累加器从左到右) 的
"一个累加器、从左到右"模型求值。一句话：**没有运算符优先级**，
`A &B 0` 是 `(A ∩ B) ∪ {0}`。

### 只接 `Selection` 输出（不接 `Geometry`）

节点可以在**不接 `Geometry` 输入**的情况下工作。此时它会从**下游消费者**那里拿到几何：

```
Group Input ──┬──────────────────────────▶ Delete Geometry.Geometry
              │                                   ▲
              └──▶ PDT Selection.Selection ───────┘   （Geometry 输入空着）
```

取几何的顺序是：**输入上游 → 字段消费者的几何 → 自己的 Geometry 输出**。

这条路径是拾取器最常用的接线方式（拾取时它需要临时求值几何，见
[操作符文档](../operators/selection_pick.md)）。

> ⚠️ 但**警告检查**需要看到几何：`Geometry` 空着时，节点无法在声明阶段知道
> `typo` 这个组到底存不存在，于是"引用缺失"的警告要等到求值阶段、在字段真正拿到几何时才可能发出。
> 想要"写错了马上看到警告"，把 `Geometry` 接上。

### 语法错误时（G1 安全失败）

选择字符串有**语法错误**时：

* `Geometry` 输出 **原样直通**；
* `Selection` 输出 **全 false**（什么都不选）；
* 节点上显示 **Error**，内容是 `Invalid selection '<原文>': <原因>`。

**几何绝不会被清空。** 理由：下游若是 `Delete Geometry`，一个笔误不该把模型删光。

### 引用缺失时（G2 警告但不失败）

组或属性不存在、或组属性不是 `BOOLEAN` 时，只给 **Warning**：

* `0-10 typo` 仍然选中 `0-10`；
* `typo*` 单独出现时什么都不选。

**语法错是 Error，引用缺失是 Warning**——这条分界是规范的一部分。

### 超长选择（`@prop`）

字符串 socket 有长度上限。选择太长时，节点把完整内容存进**节点自己的 ID 属性**，
socket 里只留 `@prop`；有多个长选择时用 `@prop:<属性名>` 指定。
求值时会把它替换回完整字符串，对使用者透明。

---

## 示例

### 删除一部分点

```
Group Input ──▶ PDT Selection ──▶ Delete Geometry ──▶ Group Output
                     │                   ▲
                     └─── Selection ─────┘
```

`Selection = 0-7:2`、`Domain = Vertex` → 删掉第 0、2、4、6 个**顶点**。

### 组 + 排除

```
{arm* ^arm2}       ← 名字匹配 arm* 的组，去掉 arm2
```

### 按位置选

```
@position.x>8      ← x 大于 8 的点
@position.x&3      ← x 的二进制最低两位非零的点
```

### 接 `Set Position`（做出"抬起来"的效果）

`Selection = {arm*}`（手臂的所有组），把 `Selection` 接到 `Set Position` 的 `Selection`，
`Offset` 给一个向量。改选择字符串就能改"抬哪些"。

> 上面这些表达式都取自 [语法规范 §5 的示例表](../reference/selection_syntax.md#5-完整示例)，
> 那张表被 `tests/test_manual_examples.py` 逐行执行（保证 G6）。

---

## 限制

| 限制 | 说明 |
| --- | --- |
| 索引是**声明式**的 | 参数里存的是序号，上游拓扑一变，同一串数字指的是别的元素。想要稳定就固化成 `BOOLEAN` 属性。 |
| 目标修改器**之下**的修改器看不到 | 索引对齐的是"进入该修改器的几何"。 |
| 拾取支持 **mesh / curve / point cloud / instances 四种组件** | 拾取哪种组件由节点的 `Domain`（元素种类）决定，或由面板里钉住的组件决定：`Vertex`/`Edge`/`Face` 进 `EDIT_MESH`，`Control Point`/`Spline` 进 `EDIT_CURVE`（序号 = 扁平控制点序号），`Point` 进 `EDIT_POINTCLOUD`（序号 = 点序号），**`Instance` 有两种方式**（面板 `Instance pick:`）：`Points` 烤成"每个顶层实例一个点"的点云（点序号 = 实例序号，看到的是**原点**），`Realized` 把实例展开成真几何后选**点/线/面/样条**并折回实例序号（带 100 万元素预算闸门）。`../../archive/08-实例支持评估.md` §4/§5 是实测，§12/§13 是实施记录。 |
| 实例上的**求值**已经可用 | `Domain = Instance` 时序号 = **顶层实例**序号（0..N-1），`@ptnum`/`@elemnum`、`group:<名字>`、`@属性` 都可用；`@position` 也支持（实例位置由 `instance_transform` 派生）。消费端用实例域的 socket（见上面那条"没有插值"的说明）。 |
| 上游是**多组件几何**时只喂一份数据块 | 临时树的 `Group Input` 只能接一份数据块，所以上游同时有多种组件时只喂其中一种（mesh 优先，已知边界，`../../archive/07-曲线支持评估.md` §13.4）。 |
| 曲线上的**求值**已经可用 | `Domain = Control Point` 时序号就是曲线**控制点**序号（POLY/BEZIER/NURBS 一致，跨样条**扁平**；贝塞尔手柄是另外的属性，不占序号）。`Edge`/`Face` 在曲线上不属于任何组件 → 选空，但不报错。实测见 `../../archive/07-曲线支持评估.md` §1。 |
| **CATMULL_ROM** 曲线拾取时按 POLY 显示 | legacy `Curve` 没有 Catmull-Rom 类型，控制点与序号不变，只是插值显示不同（`../../archive/07-曲线支持评估.md` §5.2 B1）。 |
| 不支持字符串属性 | `@name="text"` 是语法错误。 |
| 没有 `@P`/`@x`/`@y`/`@z` | 写 `@position.x`。 |
| 组只认 `group:<名字>` | 没有前缀回退。 |
