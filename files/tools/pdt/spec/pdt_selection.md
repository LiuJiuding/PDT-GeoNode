<!-- owner: user; agent: read-only -->
<!-- 条目格式见同目录 README.md：不变量一行一条，ID 写在行首粗体里。
     本文件初稿由 用户 依 docs/10-文档分层与协作规范.md 的示例落成（2026-09-14），
     之后由用户维护；agent 只在回复里提建议，不直接改。 -->

# PDT Selection 最小规范

## 目标
把一行选择字符串（`0-8:2`、`arm*`、`@position.x>8`）变成 `Boolean` 字段，供 `Delete Geometry`、
`Set Position`、`Separate Geometry` 等吃选择的消费者使用；同一行字符串也是视口拾取器写回的唯一产物。

## 契约
* 输入：`Geometry`（可空）、`Selection`（String，默认 `*`）、`Domain`（Menu，默认 `Vertex`，下拉菜单，节点属性）。
* 输出：`Geometry` 原样直通；`Selection` 在 `Domain` 上求值的 `Boolean` 字段。
* `Geometry` 空着时从字段消费者取几何；求值时把 `@prop` 展开成节点 ID 属性里的原文。

## 不变量
- **PDS-1** 求值模型：记号在**一个累加器上从左到右**合并，无优先级、无括号结合律；初始集合为空集，除非顶层记号里一个"加"记号都没有（此时为全集）。
- **PDS-2** 四个运算符：`P` 并、`^P` 减、`&P` 交、`!P` 并入补集（`!` 不是取反）。
- **PDS-3** 记号：序号与含端点区间 `a-b`、步长 `a-b:s`（s≥1）、全集 `*`、组名与通配（`*` / `?` / `[...]`，组即布尔属性 `group:<名字>`）、属性模式 `@名[.分量|[i]] 运算符 值`、子表达式 `{ ... }`（内部独立求值、整体作为一个操作数）。
- **PDS-4** 非法写法（倒序/缺端/步长 0/组名含 `-` `:`/括号不配对/缺运算符或缺值等）一律**语法错误**，不静默变成"选空"。
- **PDS-5** 安全失败：语法错误，`Geometry` 原样直通、`Selection` 全 false，节点上显示 Error（带出错原文）。
- **PDS-6** 引用缺失（组或属性不存在、组属性不是 `BOOLEAN`）只给 Warning，其余记号照常生效；**语法错是 Error、引用缺失是 Warning**。
- **PDS-7** 越界序号不是错误，只是匹配不到任何元素。
- **PDS-8** 范围：一个几何集合里的多个组件，只对`Domain`范围内的组件索引求值；`Domain` 之外的分量为假。
- **PDS-9** `Domain` 有 `Vertex`/`Edge`/`Face`/`ControlPoint`/`Spline`(=`AttrDomain::Curve`)/`Point`/`Instance`(=`AttrDomain::Instance`)；`Vertex`/`Edge`/`Face`是Mesh下的元素，`ControlPoint`/`Spline`是Curve下的元素，`Point`是Point Cloud的元素，`Instance` 是 Instances 下的元素;这 7 个是契约，改动等于改接口。
- **PDS-10** `Spline` 与 `Instance` 的序号跨样条/跨顶层实例**扁平**；嵌套实例不单独编号。
- **PDS-11** 索引是**声明式**的：参数里只有序号，上游拓扑一变含义就漂。
- **PDS-12** 超长选择：内容存进节点自己的 ID 属性，socket 里只留 `@prop` / `@prop:<名字>`，求值时可逆展开；展开不了是 Error（走 PDS-5）。
- **PDS-13** 同一字符串，C++ 节点与 Python 解析器**要么都接受、要么都拒绝**；拾取器的预选只需要满足 **预选集合 ⊆ 节点选择集合**。

## 验收
- 判据按 L1 走；执行方式：`. tools\pdt\run_tests.ps1`（解析 / 模式 / 手册例子逐行执行）与 `python tools\pdt\pdt_spec.py check`。

## 明确不做
不做运算符优先级或括号运算优先级；不把引用缺失升级为错误；不递归进嵌套实例；不做组名前缀回退；不因语法错误清空或改写几何；Face Corner / Layer / Grids不在本菜单范围内；不做"任意点元素"域；同时命中网格与曲线需要两个节点;Domain不做旧文件迁移。
没有 `@P` / `@x` / `@y` / `@z` 简写（读位置写 `@position.x`）；不支持字符串属性（`@name="hello"` 是语法错误）；组只认 `group:<名字>`，没有前缀回退或猜测；实例域与其它域之间**没有插值**，跨域接线是"什么都没选中"且不报错；组/属性匹配失败绝不上报成 Error，也绝不清空几何。
