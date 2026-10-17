# PDT Wrangle 语言与函数参考

> **L1 细则页。** 权威是 `../../spec/pdt_wrangle.md`（L0，用户手写，只读）；本页把**当前实现**的
> 语法、属性操作符与函数逐条列出，写法参照 Houdini 的 VEX 函数文档（签名 + 一句说明）。
> 数值与函数清单同时由两处断言钉住：`../../tests/test_pdt_wrangle.py`（`E-1`…`E-67`）与
> `../../tests/wrangle_engine_selftest.cpp`（145 项，含"39 个数学函数"这条）。
> 页面级的语义讲解见 [`../nodes/pdt_wrangle.md`](../nodes/pdt_wrangle.md)；本页只做速查。

## 约定

| 项 | 规则 |
| --- | --- |
| 类型 | `float`、`int`、`vector`（3 个 float）。没有其它类型：没有字符串、数组、矩阵、结构体 |
| 精度 | 全部 `float32`（`WR-13`）；`int` 只作**静态**类型与 `i@` 属性，表达式里一律按 float 计算 |
| 逐分量 | 数学函数对 `vector` 逐分量作用（除非该行另有说明） |
| 参数顺序 | 与 Houdini 的 VEX 一致（`WR-31` 的读函数就是按 Houdini 实测的 `(geo, 名称, 编号)` 定的） |
| 篇幅 | `Code` 是 String socket，**上限 1023 字节**（约 25 行）；空代码或只有注释 = 直通（`WR-1`） |

## 属性操作符

| 写法 | 含义 |
| --- | --- |
| `f@name` / `i@name` / `v@name` | float / int / vector 属性；读写都可以，**类型前缀必须有**（`WR-3`） |
| `@name`（裸名） | 只读，类型由存储类型决定；对**写**是编译错误（`WR-3`） |
| `@P` | `position` 的别名（`WR-10`）。在 Edges / Faces / Splines 档下不存在；在 Face Corners 档只读；在 Instances 档是实例平移，读写都行（`WR-9` / `WR-23`） |
| `@elemnum` / `@ptnum` | 当前元素序号（只读整数），`@ptnum` 是同义写法 |
| `@numelem` | 本次求值**开始时**该域的元素数量（只读整数；运行中不会因新建元素增长，`WR-34`） |
| `@opinput1_name` / `@opinput2_name` / `@opinput3_name` | 读输入 1…3 的同名属性（**只读**，`WR-29`/`WR-30`）；域取本次 `Run Over` 的域，组件**只认同类型**；不连、没有该组件或没有该属性都读 0（`WR-32`）。`Run Over = Detail` 下没有 `@`（`WR-8`） |

存储类型与代码用法不合（例如 `f@x` 读一个 `FLOAT_VECTOR`）是**编译错误**（`WR-6`）；
跨流读（`@opinput`）的同类情况是**节点错误 + 原样输出**（`WR-32`）。

## 数学函数

一元族（同一签名）：`float sin(float x)`、`cos`、`tan`、`asin`、`acos`、`atan`、`sqrt`、`exp`、
`log`、`log10`、`abs`、`floor`、`ceil`、`round`、`trunc`、`fract`、`sign`、`radians`、`degrees`
——逐分量作用于 `vector`。

| 签名 | 说明 |
| --- | --- |
| `float min(float a, float b)` | 也接受 `vector`（与标量混用时标量广播） |
| `float max(float a, float b)` | 同上 |
| `float clamp(float x, float min, float max)` | 同上，接受向量与标量混用 |
| `float step(float edge, float x)` | `x < edge` 得 0，否则 1 |
| `float pow(float base, float exponent)` | 幂 |
| `float atan2(float y, float x)` | 双参数反正切 |
| `float lerp(float a, float b, float amount)` | 线性插值；两端要么都是标量、要么都是向量 |
| `float smoothstep(float edge0, float edge1, float x)` | 平滑阶跃 |
| `float length(vector v)` | 向量长度 |
| `float lensq(vector v)` | 长度的平方 |
| `float distance(vector a, vector b)` | 两点距离 |
| `float dot(vector a, vector b)` | 点积 |
| `vector cross(vector a, vector b)` | 叉积 |
| `vector normalize(vector v)` | 单位化 |
| `float fit(float x, float oldmin, float oldmax, float newmin, float newmax)` | 线性重映射（标量） |
| `float rand(float x)` | 确定性伪随机，返回 0…1 |
| `float noise(float x)` / `vector noise(vector p)` | 确定性噪声，返回 -1…1；接受 float 或 vector |

没有梯度/导数函数（如 `Du`、`curvature`），也没有任何依赖邻接的查询（`WR-16` 明确不做
`neighbours()` / `pcopen()`）。

## 显式索引读函数

```
float  fpoint(int geo, string name, int index)      // 点表
int    ipoint(int geo, string name, int index)
vector vpoint(int geo, string name, int index)
float  fprim (int geo, string name, int primnum)    // 图元表
int    iprim (int geo, string name, int primnum)
vector vprim (int geo, string name, int primnum)
int    npoints(int geo)
int    nprimitives(int geo)
```

* `geo` 与 `name` **必须是字面量**（`geo` 是 `0`…`3`：0 = 本流，1…3 = `Input 1`…`Input 3`）；
  返回类型与**域**都由函数名决定，与 `Run Over` 无关（`WR-31`）。
* 图元表按 `WR-35`：网格 → 面表、曲线 → 样条表、点云与实例 → **没有表**（读 0、`nprimitives` 为 0）；
  `prim` **永不映射到 `Edge`**。`Run Over = Detail` 时按"网格 → 曲线"优先级取表。
* 读不到一律 **0** 且**不创建任何东西**（`WR-32`）：未连的流、没有同类型组件、没有该表、没有该属性、
  序号越界（含负数）。属性在、但存储类型不合 → **节点错误 + 原样输出**。
* `npoints` / `nprimitives` 报的是**本次求值开始时**的表大小。

## 写入与拓扑函数

```
setpointattrib(int geo, string name, int index, float|vector value)     // 写点表
setprimattrib (int geo, string name, int primnum, float|vector value)   // 写图元表
int  addpoint(int geo)                                     // 返回新点号
int  addprim(int geo, string type, int p0, int p1, …)      // type: "poly" | "polyline"
void removepoint(int geo, int index)
void removeprim (int geo, int primnum)
```

* **第一个参数只接受字面量 `0`**；其它一律编译错误（`WR-33`）。两个 `remove*` **只接受两个参数**（`WR-37`）。
* `setpointattrib` / `setprimattrib`：属性已存在时**保持其存储类型**（`WR-6` 的转换照用），不存在时按
  value 的类型新建；类型不合是**节点错误 + 原样输出**；序号越界**丢弃该次写**；目标表不存在（点云/实例的
  图元表）时整条写丢弃。
* `addprim`：`"poly"` → **网格面表**（要求流里已有网格组件，参数 ≥3 个，点号必须存在）；`"polyline"` →
  **曲线样条表**（流里已有曲线就追加，否则新建组件；参数 ≥2 个）；不满足都是**节点错误 + 原样输出**，
  其它 type 字符串是**编译错误**（`WR-36`）。
* `removepoint` / `removeprim` 的级联**按组件不同**（`WR-37`）：网格删面留点、删点连带删引用的面；
  曲线删样条连控制点、删点后样条**即使只剩 1 个控制点也保留**（沿用 Blender 自己的删除）。
* **并行但确定**（`WR-34`）：本次运行**看不到**自己新建的元素（读到 0 或起始数量），新建元素只在下一次
  求值可见；写入按**元素序号**合并，所以多元素写同一元素时结果固定、反复求值一致；`addpoint` / `addprim`
  的编号按"源元素序号 → 元素内调用顺序"，同一元素内连续调用得到**相邻编号**。

## 控制流与语法

| 类别 | 写法 |
| --- | --- |
| 声明 | `float x = 1.0;`、`int i = 3;`、`vector n = {0, 1, 0};`（可不给初值） |
| 赋值 | `=` `+=` `-=` `*=` `/=` `%=`；自增自减 `i++` / `i--` |
| 分量 | `v.x` / `v.y` / `v.z`（也写 `.r/.g/.b`）、`v[0]` / `v[1]` / `v[2]`，读写都行 |
| 字面量 | `{1, 2, 3}`（1～3 个分量，缺的补 0） |
| 运算 | `+ - * / %`、一元 `-`、`!`、`< <= > >= == !=`、`&& \|\|`；向量与标量混算逐分量 |
| 控制流 | `if` / `else` / `for` / 块 `{}`；`//` 与 `/* */` 注释 |
| 转型 | `float(x)`、`int(x)`、`vector(x)`、`vector(a, b, c)` |
| 内置常量 | 没有（需要时写 `PI` 的字面值） |

## 不支持（会得到编译错误或节点错误）

* 字符串、数组、自定义函数、`printf` / `warning` / `error`（没有输出通道）。
* `point()` / `prim()` / `vertex()` / `detail()` 这些 Houdini 取名：本节点用 `fpoint` / `fprim` 系列（`WR-31`）。
* 邻接与几何查询：`neighbours()`、`pcopen()`、`nearpoints()`（`WR-16` 明确不做）。
* 面角档写 `@P`（只读，`WR-9`）；`Detail` 档使用任何 `@`（编译错误，`WR-8`）。
* 写入别的几何流（第一个参数不是字面量 `0`，`WR-33`）。

## 判据索引

| 主题 | 断言 |
| --- | --- |
| 语言与数学函数（39 个）、类型检查、错误信息 | `wrangle_engine_selftest.cpp`（145 项） |
| 逐元素语义、域与编号、`@P` 规则、数值与有界执行 | `test_pdt_wrangle.py` `E-1`…`E-20` |
| 多组件、`Detail`、`Instances`、`Selection` | `E-19`…`E-28` |
| 跨流读（`@opinput<n>_`） | `E-29`…`E-34` |
| 显式索引读与图元表 | `E-35`…`E-49` |
| 缓冲写入与确定性 | `E-50`…`E-59` |
| `addpoint` 与编号 | `E-60` |
| `addprim("poly")` 加面 | `E-61` |
| 删除级联（网格 / 曲线） | `E-62` / `E-63` / `E-66` |
| `addprim("polyline")` 新建 / 追加 / 点云来源 | `E-64` / `E-65` / `E-67` |
