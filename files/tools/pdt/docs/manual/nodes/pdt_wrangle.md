# PDT Wrangle（在节点里写代码）

> **来源**：`../../spec/pdt_wrangle.md`（L0，用户手写；本页是细则与例子，冲突时以那份为准）。
> **本页按 L0 描述目标语义**：某条是否已经落地、实测数字与踩过的坑，看 `spec/coverage.md` 的状态列与
> `../../14-Wrangle节点实施记录.md`（L2，无规范效力）。

`PDT Wrangle` 让你在一个几何节点里直接写一小段类 VEX 代码，逐元素改写流过的几何，
而不是为了一个公式连七八个数学节点。位置：`Shift+A` → `PDT` → `PDT Wrangle`。

## 节点与 socket

| 方向 | socket | 类型 | 说明 |
| --- | --- | --- | --- |
| 输入 | `Geometry` | Geometry | 要处理的几何流（**输入 0**）。支持**网格**、**曲线**、**点云**与**实例** |
| 输入 | `Input 1` / `Input 2` / `Input 3` | Geometry | 另外三条几何流（输入 1…3）。**目前没有读它们的语法**（`@opinput<n>_` 属于 L0 的"明确不做"）；不连 = 空几何 |
| 输入 | `Selection` | Boolean（**字段**） | **作用范围**：代码只在求值为真的元素上执行；不连 = 全选。群组就是布尔属性，用 `Named Attribute` 接进来即可 |
| 输入 | `Run Over` | Menu | 逐哪种元素执行，九个取值见"Run Over 九档" |
| 输入 | `Code` | String | 代码；节点上是一个**多行编辑框**（12 行可见、可滚动、可拖高）。被连线时以连进来的字符串为准 |
| 输入 | **通道**（0 个或多个） | Float / Int / Vector / Rotation / Matrix | 用户自己在节点上加的**具名值插座**，代码用 `chf` / `chi` / `chv` / `chq` / `chm` 按名字读。加、改名、选类型、删除都在节点面板上做——见"通道" |
| 输出 | `Geometry` | Geometry | 同一条流（除非代码建/删了元素），属性已被代码改写 |

代码存在 String socket 里，因此**上限 1023 字节**，约 25 行；写长了会被截断。
空代码或只有注释时节点是一个**直通**。

## 语言速查

| 类别 | 写法 |
| --- | --- |
| 类型 | `float`、`int`、`vector`（3 个 float）、`quat`（4 个 float，w 在前）、`matrix`（4×4，16 个 float，四个列向量） |
| 属性 | `@P`（位置）、`f@name` / `i@name` / `v@name` / `q@name` / `m@name`、`@elemnum` / `@numelem`（只读整数） |
| 通道 | `chf("name")` / `chi("name")` / `chv("name")` / `chq("name")` / `chm("name")`——读本节点通道；名字是字面量，读不到给 `0`，见"通道" |
| 显式索引读写 | `fpoint` / `ipoint` / `vpoint` / `fprim` / `iprim` / `vprim` / `npoints` / `nprimitives`；`setpointattrib` / `setprimattrib` |
| 拓扑 | `addpoint` / `addprim` / `removepoint` / `removeprim`（写入类，`geo` 只能写 `0`） |
| 声明 | `float x = 1.0;`、`int i = 3;`、`vector n = {0, 1, 0};`、`quat q;`、`matrix m;`（可以不给初值；不给时旋转是单位旋转、矩阵是单位阵） |
| 赋值 | `= += -= *= /= %=`；自增自减 `i++` / `i--` |
| 分量 | `v.x` / `v.y` / `v.z`（也可写 `.r/.g/.b`）、`v[0]` / `v[1]` / `v[2]`；旋转是 `q.w` / `q.x` / `q.y` / `q.z` 与 `q[0..3]`；矩阵是 `m[row][column]`（单独写 `m[i]` 得到**第 i 列**的前三个分量，一个 vector——`float4x4[i]` 也是这个意思）——读写都行 |
| 字面量 | `{1, 2, 3}`（1～3 个分量，缺的补 0） |
| 运算 | `+ - * / %`、一元 `-`、`!`、`< <= > >= == !=`、`&& \|\|`；向量与标量混算时逐分量；`matrix * vector` 是把这个变换施加到向量上（含最后一列的平移） |
| 控制流 | `if` / `else` / `for` / 块 `{}`；`//` 与 `/* */` 注释 |
| 转型 | `float(x)`、`int(x)`、`vector(x)` 或 `vector(a, b, c)`（旋转与矩阵**不能**转型，只能用下面那组函数换算） |
| 数学（逐分量作用于向量） | `sin cos tan asin acos atan sqrt exp log log10 abs floor ceil round trunc fract sign radians degrees`、`min max step pow atan2`、`lerp clamp smoothstep` |
| 数学（向量专用） | `length lensq distance dot cross normalize` |
| 数学（其它） | `fit(x, oldmin, oldmax, newmin, newmax)`（标量）、`rand(x)`（0～1）、`noise(x)`（-1～1，接受 float 或 vector） |
| 旋转与矩阵 | `rotate_rotation` / `rotate_vector` / `axis_angle_rotation` / `euler_rotation` / `quaternion` / `to_euler` / `to_axis_angle` / `axes_rotation` / `invert_rotation` / `align_rotation_to_vector` / `combine_transform` / `matrix_multiply` / `invert_matrix` / `transpose_matrix` / `matrix_determinant` / `separate_transform` —— 见"旋转与矩阵" |

`min`/`max`/`clamp` 接受向量与标量混用（标量会广播）；`lerp` 的两端要么都是标量、要么都是向量。
`@ptnum` / `@primnum` **不存在**：元素编号一律用 `@elemnum` / `@numelem`。
显式索引读那一族**没有**旋转/矩阵的版本（`qpoint` / `mpoint` 之类不存在）：旋转与矩阵只能通过
`@`（`q@` / `m@`）或局部变量经手。

## 旋转与矩阵（`WR-43`…`WR-58`）

这两类值**不是新数据类型**：旋转就是 Blender 的 `QUATERNION` 属性（`math::Quaternion`，四个 float，
**w 在前**），矩阵就是它的 `FLOAT4X4` 属性（`float4x4`，十六个 float，四个列向量）。所以 `q@name`
写出来的东西，Spreadsheet 里选 "Quaternion" 就能看见；`m@name` 与实例的 `instance_transform` 是同一种
存储。两条规则跟着来：

* **没有隐式转换**：`float(q)`、`vector(m)` 之类都是编译错误，两个变换类型之间也不能转（`quat`
  与 `matrix` 互不相干）；`+ - * /` 这类算术同样不接受它们，唯一的例外是 `matrix * vector`
  （把向量按这个变换搬到目标位置，**含最后一列的平移**）。
* **一个属性一种类型**（`WR-11`）：同一个名字既当 `q@` 又当 `m@` 用是编译错误。

每个函数对应 Blender 的一个**函数节点**，参数顺序就是那个节点的插座顺序（节点的按钮排在最前，
和它在节点上画的位置一致）。名字也是照节点语义取的，不是照 Houdini 的同类函数取的。

### 旋转

| 函数 | 对应节点 | 说明 |
| --- | --- | --- |
| `quaternion(w, x, y, z)` | Quaternion to Rotation | 由四个分量拼一个旋转；默认 `(1,0,0,0)` 即单位旋转 |
| `axis_angle_rotation(axis, angle)` | Axis Angle to Rotation | 由"轴 + 绕轴角度"构造；轴默认 `{0,0,1}`，**角度是弧度**；轴长度为 0 时给单位旋转（`WR-45`） |
| `euler_rotation(euler)` | Euler to Rotation | 由三个欧拉角构造（XYZ 序，弧度） |
| `rotate_vector(v, r)` | Rotate Vector | 把旋转 `r` 施加在向量 `v` 上（只转方向，不带任何平移） |
| `rotate_rotation(space, base, by)` | Rotate Rotation | `space` ∈ `{"global"`,`"local"}`；global = `by × base`，local = `base × by` |
| `to_euler(r)` | Rotation to Euler | 转回欧拉角（弧度） |
| `to_axis_angle(r, &axis, &angle)` | Rotation to Axis Angle | **出参**：轴写进 `axis`，角度（弧度）写进 `angle` |
| `axes_rotation(primary_axis, secondary_axis, primary, secondary)` | Axes to Rotation | 两个轴字 ∈ `{"x","y","z"}`；主轴精确对齐，副轴尽力；两轴相同 → 单位旋转 |
| `invert_rotation(r)` | Invert Rotation | 反向旋转 |
| `align_rotation_to_vector(axis, pivot, rotation, factor, v)` | Align Rotation To Vector | `axis` ∈ `{"x","y","z"}`，`pivot` ∈ `{"auto","x","y","z"}`；把 `rotation` 的本地主轴对齐到 `v`，零向量 → 原样返回 |

### 矩阵

| 函数 | 对应节点 | 说明 |
| --- | --- | --- |
| `combine_transform(translation, rotation, scale)` | Combine Transform | 合成 4×4；`M * v = t + R·S·v` |
| `matrix_multiply(a, b)` | Multiply Matrices | 返回 `a × b`（`float4x4 * float3` 同一个约定） |
| `invert_matrix(m)` | Invert Matrix | 求逆；不可逆时**给单位阵**，不报错、不出 NaN（`WR-55`） |
| `transpose_matrix(m)` | Transpose Matrix | 转置：`(row, column)` 与 `(column, row)` 互换。所以**平移会从最后一列搬到最后一"行"**——`combine_transform` 出来的 `m` 里平移读作 `m[0..2][3]`，转置后读作 `t[3][0..2]`，而 `t[0..2][3]` 变成原来的第 0/1/2 列。转置两次回到原矩阵（已实测，含这条交叉校验） |
| `matrix_determinant(m)` | Matrix Determinant | 行列式，一个 float |
| `separate_transform(m, &translation, &rotation, &scale)` | Separate Transform | **出参**：平移（vector）、旋转（quat）、缩放（vector），顺序照节点的输出 |

出参写法是 C 风格的 `&name`：`&` 后面只能是**前面声明过的变量**或整个 `@属性`（不能是分量，
也不能是字面量）。出参这一格不会被当成输入值，函数签名决定它写回什么。

矩阵的**下标是先行后列**（`m[row][column]`，与纸面写法一致），而存储是列主序（与 `float4x4` 一致），
所以一个条目的偏移是 `row + column * 4`。平移在**最后一列**，因此它读作 `m[0][3]`、`m[1][3]`、
`m[2][3]`（已实测，三种都落在了平移上）。

单独写 **`m[i]` 取的是第 i 列**（前三个分量，一个 vector）——这与 Blender 自己的 `float4x4[i]`
一致，也让平移能读成 `m[3]`。注意它和"两个下标时的第一个"**不是同一个轴**：这不是笔误，而是
"拿一整列"比"拿一整行"更有意义（这一族没有 float4，行拿不全）。写代码时不要按行去猜 `m[i]`。

## 契约与执行

### 直通

`Code` 为空、只含注释、或只声明了不用的局部变量时：不执行、不报错，输出与输入**逐属性相等**。

### Code 报错

代码报错（编译错误或运行期错误）时：节点报错，输出**空几何**——即使输入完全合法，失败的那一次
也不会把输入原样漏到下游，免得下游节点拿着"代码从没产出过"的数据继续算。编译错误带**行号列号**；
只有拿到几何才知道的问题（属性存储类型不合、图元建不出来、`WR-3` 的指令上限）给出说明，不带行号。
`Code` 为空或只含注释是**直通**，不是错误（见上）。

### 有界执行

单个元素上跑不完的代码必须被中止并报错（每个元素有指令上限），不允许挂住界面。

### 逐元素独立

每个元素各算各的，代码里没有跨元素访问，所以结果与线程数、划分方式、元素处理顺序**无关**；
同一棵树求值两次，属性值**逐位一致**。

### 多几何流

节点有 4 条几何输入，编号 0…3；未连的输入是**空几何**。输出仍然只有一条流
（= 输入 0 被代码改写后的结果），代码改不到别的输入。

## 运行范围

### Run Over 九档

`Run Over` 决定**代码跑几次**：等于该域上的元素数目（按**输入的**几何算，不受本次代码改动元素的影响）；
元素数为 0 时**什么都不做**。`Detail` 档只跑一次。

| `Run Over` | 逐什么元素 | 属性域 | `@P` |
| --- | --- | --- | --- |
| `Vertex` | 网格顶点 | `Point` | 读+写 |
| `Edges` | 网格边 | `Edge` | **没有** |
| `Faces` | 网格面 | `Face` | **没有** |
| `Face Corners` | 网格面角（Houdini 叫 "vertex"） | `Corner` | 只读（= 所属点的位置） |
| `Control Point` | 曲线控制点 | `Point` | 读+写 |
| `Splines` | 曲线样条 | `Curve` | **没有** |
| `Point` | 点云的点 | `Point` | 读+写 |
| `Instances` | 实例 | `Instance` | 读+写（= 实例的**平移**） |
| `Detail` | 整条流跑一次 | 无 | **没有** |

一档只对应**一个组件**（`Vertex` 是网格、`Control Point` 是曲线、`Point` 是点云），
所以"一条流里有多种组件"时不会互相牵连。`Detail` 档没有 `@` 操作符，用它是编译错误。

### 菜单取值兼容性

`Run Over` 存进 `.blend` 的是菜单的 int 值；改值或重排菜单后，老文件里存的选择会指向另一个档位
（按上表的顺序走）。这是已知且接受的代价。

### 元素编号

`@elemnum` = 当前元素序号，`@numelem` = 元素总数；两者只读，赋值是编译错误。
序号按上表的域各自数：`Vertex` 是网格顶点号、`Face Corners` 是面角号、`Splines` 是样条号、
`Control Point` 是曲线控制点号（跨样条扁平）等等。

### Selection

连了 `Selection` 时，代码**只在被选中的元素上执行**；未选中的元素既不写也不建。
不连 `Selection` = **全选**。
`@elemnum` / `@numelem` 仍然是域里的**真实编号与总数**，不因筛选而重新编号或改变总数。
`Detail` 档没有可求值的域，`Selection` 被**忽略**（不报错、也不改变行为）。

## 通道

**通道**是节点上一个**有名字的值插座**，代码在 `Code` 里按名字读它。用处和 Houdini wrangle 上的
spare parameter 一样：把"公式里要用的那个数"留在节点上，而不是写死在代码里——同一段代码换一个值
不用改代码。

### 通道与它的名字和类型

每个通道有**一个名字**与**一个类型**：`Float`、`Int`、`Vector`、`Rotation` 或 `Matrix`。

三种加法：节点面板上的列表里点加号；把一根线拖到节点上的**扩展插座**（列表下方那个）上——新通道
按线那一头的类型定型、并按来源 socket 取名；不要了就删掉。列表里可以**改名**、**改类型**、上下移动。

* **名字唯一**：加一个与已有通道同名的通道时，新通道自动改成不撞的名字（后缀 `.001`、`.002`…，
  与 Blender 自己的重名规则一致），不报错、不覆盖、也不影响已有的那个。
* **改类型 = 值重置**：把一个通道从一种类型改成另一种时，值重置为新类型的默认值
  （`Float` / `Int` 是 `0`，`Vector` 是 `{0, 0, 0}`）——**不做数值转换**，`3.7` 改成 `Int` 不会变成 `3`。
  改名不重置值：名字与值是两件事。
* **一个通道都没有时**，节点与没有这个功能时**完全一样**：`Code` 为空照旧直通，不报错。
* 通道与 `Code`、`Run Over` 一样**随文件保存**；老文件里的 wrangle 节点读回来时补一个空通道表。
* 通道是**整条流共用的一个值**，不是逐元素数据：它不随元素变化，也不受 `Selection` 影响。
  逐元素的值只能用 `@` 属性（见"`@` 操作符"）；把字段接进通道不是支持的用法。
* 通道**只读**：代码不能给通道赋值，也不能在代码里新建通道。

### chf chi chv chq chm

| 函数 | 读哪种通道 | 例子 |
| --- | --- | --- |
| `chf("name")` | `Float`（也读 `Int` 通道：按"类型转换"） | `@P.y += chf("height");` |
| `chi("name")` | `Int`（也读 `Float` 通道：截断） | `i@n = chi("count");` |
| `chv("name")` | `Vector` | `v@o = chv("offset");` |
| `chq("name")` | `Rotation` | `@P = rotate_vector(@P, chq("turn"));` |
| `chm("name")` | `Matrix` | `v@p = chm("place") * {0,0,0};` |

* `name` 必须是**字符串字面量**（和 `fpoint(0, "mesh", "P", i)` 里的字面量同类）；变量不能当名字。
  只接受**一个**参数：`chf("a", 1)` 是编译错误（Houdini 收下它并返回 0，这里宁可写错就报）。
* 名字**区分大小写**、按**精确匹配**：`"Height"` 不是 `"height"`。
* 读到的是通道**本次求值时**的值——通道被上游连线驱动时，读到的是驱动之后的值
  （已实测：驱动源写 `4.0`、通道自己的默认值写 `0.25`，代码读到 `4.0`）。
* 五个函数**只读**：不修改通道、也不创建通道。
* 所有 `Run Over` 档都能读，**`Detail` 档也一样**（那里没有 `@`，通道是唯一能读到的外部值）。
* **没有分量语法**：`chf("offset.y")` 不是"读 `offset` 的 y"，它就是一个不存在的名字。
  想要一个分量，就把那个通道设成 `Float`。

### `Rotation` 与 `Matrix` 通道的形状（已实测）

* `Rotation` 通道在节点上是一个 `NodeSocketRotation`，它的值是**三个欧拉角**（不是四元数）；
  主机把它转成四元数再交给 `chq`。节点面板上把角度设成 `(0, 0, 90°)`，`chq("turn")` 得到的是
  `w=0.7071, z=0.7071`（实测）。
* `Matrix` 通道是一个 `NodeSocketMatrix`，**Python 里没有 `default_value`**（实测：该属性不在插座的
  RNA 上），所以矩阵通道只能靠**连线**喂进来——`Object Info` 节点的 `Transform` 输出就是现成的一个。
  这不是本节点的限制，是 Blender 那边这个插座类型本身没有默认值属性。

### 通道取值

通道的值在**一次求值的开始**取一次，这次求值里**所有元素读到同一个值**（与"逐元素独立"一致）；
`Selection` 筛掉哪些元素不改变这个值。通道被连线驱动时同样如此：读的是这次求值算出来的那个值。

### 通道读不到时

读不到就是 `0`，**不报错**（这是 Houdini 的行为，我们照它——下表是 H21 上实测出来的规则）：

| 情况 | 结果 |
| --- | --- |
| 名字不存在 | `chf` / `chi` → `0`；`chv` → `{0, 0, 0}`；`chq` → `(0,0,0,0)`；`chm` → 十六个 `0` |
| 名字是一个路径（如 `"../grid1/sizex"`） | 同上——通道只读**本节点**的，不做跨节点参数引用 |
| 名字大小写不符 | 同上（按不存在的名字处理） |
| 名字里带分量（如 `"offset.y"`） | 同上 |
| 用 `chf` / `chi` 读 `Vector` / `Rotation` / `Matrix` 通道 | `0` |
| 用 `chv` 读 `Rotation` / `Matrix` 通道 | `{0, 0, 0}` |
| 用 `chq` / `chm` 读一个**不同类型的**通道 | `(0,0,0,0)` / 十六个 `0`——和标量读向量的规则一致，都是 0 |
| 用 `chv` 读 `Float` / `Int` 通道 | `{值, 0, 0}` |
| `chi` 读 `Float` 通道 | 按"类型转换"截断（`3.7` → `3`） |

这些情况都**不改变输出**：节点照常求值，`Code` 为空时照旧直通。

## 逐元素的读写

### `@` 操作符

`@name` 读写**当前元素**在那个域上的属性，读的是**冻结的输入视图**：本次代码对几何做的修改
（建/删元素、改属性）不影响同一段代码后面的 `@` 读值。

### `@` 属性写入与前缀

* 给一个**不存在**的属性赋值就**创建**它，类型由前缀决定：`f@` float、`i@` int、`v@` vector。
* **必须写前缀**：裸名（例如 `@weight`）是**编译错误**，错误信息会给出三种前缀的写法。
  例外是类型本来就不需要猜的名字：`@P`、`@elemnum`、`@numelem`。
* 前缀与几何上已有属性的类型**不兼容**时报错（见"类型转换"）。
* 新属性建在**当前 `Run Over` 的域**上。

### `@` 属性读取

读一个**不存在**的属性得到 **0**，不是错误，也不创建它。
读已有属性同样要写前缀（编译期看不到几何）；前缀与已有属性的类型不兼容时报错。

### 别名

`P` 是 `position` 的别名；其余属性名与 Blender 的属性名一一对应，不另设别名。

### `@P` 的适用范围

只有 `Vertex` / `Control Point` / `Point`（该组件的位置）与 `Instances`（实例的平移）能**读写** `@P`；
`Face Corners` 上 `@P` **只读**（= 所属点的位置）；`Edges` / `Faces` / `Splines` / `Detail` 上
**没有 `@P`**，用它就是编译错误。

### 一个属性一种类型

同一段代码里，同一个属性只能按一种类型使用；用了两种（例如先 `f@x` 后 `v@x`）是编译错误。

### 类型转换

代码里的类型与几何上已有属性的**存储类型**对不上时：

* 可以互相转换的照常工作：`FLOAT` / `INT` / `BOOLEAN` 之间，`FLOAT_VECTOR` / `FLOAT2` 之间
  （`FLOAT2` 当 vector 用时第三个分量读作 0、写回时丢弃）；
* 不能转换的（例如把 `f@` 打到一个 `FLOAT_VECTOR` 属性上）报错，错误信息写出属性的实际类型；
* 存储类型完全不认识的（例如 `FLOAT_COLOR`、`QUATERNION`）也报错，并列出支持的类型。

## 显式索引读

读写函数用 `domain` 参数指定**组件**；`geo`、`domain`、`"名字"` 都必须是**字面量**。
`domain` 的取值按**表类**分，写错的词、或该表类没有的词，都是**编译错误**：

| 表类 | `domain` 取值 |
| --- | --- |
| point 表 | `"mesh"` / `"curve"` / `"pointcloud"` / `"instance"` |
| prim 表 | `"mesh"`（面）/ `"curve"`（样条） |

### point() 与 npoints()

`fpoint` / `ipoint` / `vpoint(int geo, string domain, string name, int index)`、
`npoints(int geo, string domain)`：读点表。返回类型由**函数名**决定。
读的是**冻结的输入视图**，不受本次代码修改几何的影响。

### prim() 与 nprimitives()

`fprim` / `iprim` / `vprim(int geo, string domain, string name, int index)`、
`nprimitives(int geo, string domain)`：读图元表（网格的面 / 曲线的样条）。

### 显式索引读异常静默

下面这些读**返回 0**，不报错、也不创建任何东西：
`domain` 指的那个组件不在流里、`name` 属性不存在、`index` 越界、读的是**本次代码新建**的元素。

### 显式索引读异常报错

下面这些是**报错**：`geo` 取值不是 `0/1/2/3`、`domain` 不在该表类的词表里、
`name` 属性存在但存储类型与代码用的类型不兼容。

## 显式索引写

写入类函数的第一个参数 `geo` **只能写 `0`**（只能改输入 0 这条流）。

### setpointattrib()

`setpointattrib(int geo, string domain, string name, int index, int|float|vector value)`：
写点表；`name` 不存在就**创建**它。

### setprimattrib()

`setprimattrib(int geo, string domain, string name, int index, int|float|vector value)`：
写图元表（网格的面 / 曲线的样条）；`name` 不存在就创建它。

### 显式索引写异常静默

下面这些**不报错、也不做任何事**：`domain` 指的组件不在流里、`index` 越界、
`value` 与已有属性的类型不一致。

### 显式索引写异常报错

下面这些是**报错**：`geo` 取了非 0 的值、`domain` 不在该表类的词表里。

## 拓扑

拓扑函数改的是元素本身。它们与 `@` 写一样遵守"冻结的输入视图"与"有序并行"两条。

### addpoint()

`int addpoint(int geo, string domain, vector pos)`：在点表上加一个点，位置是 `pos`。
`domain` 取 `"mesh"` 或 `"pointcloud"`；流里没有该组件时**新建**它。
返回新点的编号。

### addprim()

`int addprim(int geo, string domain, string pointdomain, int p0, int p1, …)`：
在 `domain` 的表上加一个图元（`"mesh"` 加**面**、`"curve"` 加**样条**），点取自 `pointdomain` 表；
流里没有该组件时**新建**它。返回新图元（面 / 样条）的编号。

* **引用还是复制**：`domain` 与 `pointdomain` **都是 `"mesh"`** 时，面直接**引用**那些顶点
  （不新建点）；**其它组合都是复制**——把来源点的位置与该点域属性复制成目标组件的新点，
  所以**目标的点表会增长**（`"curve"` 拿不到共享控制点，只能复制）。
* **点数下限**：`"mesh"` 至少 3 个点、`"curve"` 至少 1 个点，少于下限报错。

### removepoint()

`int removepoint(int geo, string domain, int index)`：删点。

* `"mesh"`：连带删掉引用它的边和面（相当于 `Delete Geometry` 的 *Point* + *All* 模式）；
* `"curve"`：只从所属样条里删这一个控制点；该样条因此剩 0 个点时，**样条一并删掉**；
* `"pointcloud"` / `"instance"`：删点 / 删实例。

`domain` 不在词表里或 `geo` 非 0 → 报错；组件不在流里或 `index` 越界 → **静默**。

### removeprim()

`int removeprim(int geo, string domain, int index)`：删图元（网格的面 / 曲线的样条）。
`"mesh"` 时照 `Delete Geometry` 的 *Face* + *All* 模式级联（面所用、且没有别的用户用到的点与边一并删掉）。

`domain` 取 `"mesh"` / `"curve"`；不在词表里或 `geo` 非 0 → 报错；组件不在流里或 `index` 越界 → **静默**。

### 有序并行

拓扑与写入类程序照旧**并行**执行，但结果确定，五条承诺：

1. 每个元素各自缓冲，最后按**元素序号**合并；
2. **新增元素的编号确定**：先按源元素序号、再按该元素内的调用顺序（同一元素里连调两次 `addpoint`
   得到的是相邻编号）；
3. **写刚创建的元素有效**（用 `addpoint` / `addprim` 的返回号调 `setpointattrib` / `setprimattrib`）；
4. **同一个元素被多个元素写入**时，按元素序号顺序应用；
5. **新元素不参与本次迭代**：本次新建的元素对本轮的所有读都不可见（读到 0 或**起始数量**）。

`npoints` / `nprimitives` 报的是**本次求值开始时**那两张表的大小：本次建出来的不算进去，
本次删掉的也照旧算。`npoints` / `nprimitives` 只是数量，属性读取仍是实时的。

## 数值与限制

### 数值

标量按 **32 位浮点**计算；整数属性在 2^24 以内精确，超出后不保证。
`noise()` / `rand()` 是确定且连续的哈希噪声，**数值与 Houdini 不同**，不要拿它复现 Houdini 的结果。

### 限制与现状

**L0 明确不做的**：

* 不支持 `@opinput<n>_<name>` 跨流读取（4 条输入插座在，但没有读法）；
* 不做蜡笔图层（`AttrDomain::Layer`）：输入是蜡笔时不属于任何一档，不会被处理；
* 不保证与 Houdini VEX 数值一致；
* 不做脚本沙箱：代码在 Blender 进程内执行，能写死循环（由"有界执行"的指令预算兜住）。

**实现上还差一点的（记录在案，不静默）**：

* `addprim` 的 `pointdomain = "instance"` 时只复制**位置**（实例的平移）；实例域上的属性没有复制过去。
* 同一个程序里的曲线请求必须点名**同一个** `pointdomain`（一次复制写一段点范围），点名不同会明确报错。

**现状（不是承诺，改 L0 之前不变）**：

* 没有用户自定义函数；
* 没有数组属性、字符串属性、字典；
* 没有 `printf` 之类的调试输出——要看中间值就把它写进一个属性，再用 Spreadsheet 看；
* 通道只读、且类型只有 `Float` / `Int` / `Vector` / `Rotation` / `Matrix`：没有字符串通道，也没有 `chs` / `chramp`（曲线通道）；
* 通道不做跨节点参数引用：`chf("../节点/参数")` 在 Houdini 里能读到别的节点，在这里只是一个不存在的名字（→ `0`）；
* 代码上限 1023 字节；要写更长的代码，先把片段拆成多个 Wrangle 节点。

## 例子

本页不给"代码 → 结果"的第二份真值表：可执行的断言都在测试里，避免手册与实现各说一套。

| 想做的事 | 代码 |
| --- | --- |
| 逐点抬高 | `@P.y += 1;` |
| 用位置算一个新属性 | `f@mask = @P.y * 2;` |
| 建一个颜色属性 | `v@Cd = {1, 0, 0};` |
| 用元素序号 | `i@id = @elemnum * 3;` |
| 读几何上已有的属性 | `f@read_back = f@pdt_weight * 10;` |
| 归一化后的点积 | `v@n = normalize(@P); f@d = dot(v@n, {0, 1, 0});` |
| 按位置分支 | `if (@P.z > 0) { f@up = 1; } else { f@up = 0; }` |
| 循环累加 | `float acc = 0; for (int i = 1; i <= 4; i++) { acc += i; } f@sum = acc;` |
| 面角 / 边 / 面域上的数值 | `f@corner_mask = f@pdt_corner + 1;`（`Face Corners`）、`i@edge_tag = @elemnum;`（`Edges`） |
| 移动曲线的控制点 | `@P.x += 10;`（`Run Over = Control Point`） |
| 每条样条写一个值 | `f@only_curve = @elemnum;`（`Run Over = Splines`） |
| 按实例编号推移 | `@P.y += @elemnum;`（`Run Over = Instances`） |
| 只动前 4 个点 | `@P.y += 1;`（`Selection` 接 `Index < 4`） |
| 读网格上某个点的属性 | `f@w = fpoint(0, "mesh", "pdt_weight", 3);` |
| 按通道抬高 | `@P.y += chf("height");`（先在节点上加一个 `Float` 通道叫 `height`） |
| 按通道选方向 | `v@n = normalize(chv("axis"));`（`Vector` 通道 `axis`；名字不存在就是 `{0,0,0}`） |
| 按曲线控制点建一条样条 | `addprim(0, "curve", "curve", 0, 1);` |
| 按网格顶点建一个面 | `addprim(0, "mesh", "mesh", 0, 1, 2);` |
| 删掉 5 号点 | `removepoint(0, "mesh", 5);` |
| 把每个点绕 Z 转 90° | `q@rot = axis_angle_rotation({0,0,1}, radians(90)); @P = rotate_vector(@P, q@rot);` |
| 用欧拉角摆一个朝向 | `q@rot = euler_rotation({0, radians(45), 0});` |
| 存一个"平移 + 旋转 + 缩放" | `m@xform = combine_transform({0,0,2}, q@rot, {1,1,1});` |
| 读回一个矩阵的平移 | `v@t = {m@xform[0][3], m@xform[1][3], m@xform[2][3]};` |
| 把矩阵拆成三件 | `vector t; quat r; vector s; separate_transform(m@xform, &t, &r, &s);` |
| 求一个旋转的反向 | `q@back = invert_rotation(q@rot);` |
| 把局部 Z 轴对准一个方向 | `q@aim = align_rotation_to_vector("z", "auto", q@rot, 1.0, v@dir);` |
| 把一个旋转拆成轴与角 | `vector axis; float angle; to_axis_angle(q@rot, &axis, &angle); f@deg = degrees(angle);` |

## 验收

* 无头（节点）：`tools\pdt\tests\test_pdt_wrangle.py` 全绿（契约、持久化、逐元素语义、报错与直通、通道读取、旋转与矩阵的两类属性）。`E-98`…`E-107` 覆盖 `q@` / `m@` 的创建、分量顺序、下标、缺属性读 0、一个名字不能既是旋转又是矩阵、存储类型不符报错，以及 `rotate_vector` 作用在存下来的旋转上。
* 无头（引擎）：`tools\pdt\tests\wrangle_engine_selftest.cpp` 全绿（语言、类型检查、错误信息、16 个旋转/矩阵函数的数值与退化输入、`&` 出参）。
* 回归：`tools\pdt\run_tests.ps1` 全绿，`pdt_spec.py check --strict` 0 警告。

## 语法高亮

`Code` 框按**词法**给代码上色。类别不是我们发明的，是 Houdini 的 VEX 框实测出来的九类
（`config/ColorSchemes/*.colors.json` 里的 `ParmSyntax*`，用 `hou.ui.colorFromName` 逐个验证过，
并在活的 Houdini 里对着屏幕确认；证据截图在 `../../qa/houdini/vex_parm_printwindow.png`）：

| 类别 | 例子 | Blender 主题槽位 |
| --- | --- | --- |
| 普通标识符 | `h`、`k`、运算符、括号 | 文本框正文色（不占语法槽位） |
| 关键字 | `if` / `else` / `for` | `Syntax Reserved` |
| 类型名 | `float` / `int` / `vector` / `bool` | `Syntax Special` |
| 函数名 | `noise`、`sin`、`fpoint`、`setpointattrib`、`addpoint`… | `Syntax Built-In` |
| 引用 | `@P`、`f@height`、`v@Cd`（前缀与名字**一类**） | `Syntax Preprocessor` |
| 数字 | `2.0`、`1e-3` | `Syntax Numbers` |
| 字符串 | `"ridge"`（**引号与内容同色**，和 Houdini 一样） | `Syntax String` |
| 注释 | `// …`、`/* … */` | `Syntax Comment` |
| 错误 | 编译报错的位置 | 主题的 `Red Alert`（Blender 没有"错误"语法槽） |

* **颜色一律取自主题**（Preferences → Themes → "Text Editor"，8 个语法槽位，截图见
  `../../qa/houdini/blender_theme_text_editor.png`），没有任何写死的颜色值 —— 换主题，高亮跟着换。
* **函数名与编译器共用一张表**：数学函数来自 VM 的 `kFunctions()`，显式索引/拓扑那一族
  （`fpoint`…`removeprim`）单列，但自测会断言"高亮为函数的每个名字真的能编译"。
* **只有本节点的 `Code` 框启用**高亮，Blender 其余文本框保持单色；`Code` 被连线时那个框只显示
  连入的字符串、不显示编辑器，因此没有高亮。
* 高亮**不做语法判断**：代码合法与否只由编译决定；唯一取自编译的是报错的行列号（用来标"错误"类）。
* 像素本身没法用断言覆盖（门禁只断言**词法分类**），上色效果靠人眼验收。

## Code 框快捷键

`Code` 框里 `Tab` / `Shift+Tab` / `Ctrl+/` 按下面这张表工作（L0 `WR-33`/`WR-34`）：

| 按键 | 动作 |
| --- | --- |
| `Tab`（无选区） | 在光标处插入 4 个空格 |
| `Tab`（有选区） | 覆盖到的每一行行首各插入 4 个空格 |
| `Shift+Tab` | 覆盖到的每一行行首最多去掉 4 个空格（不足 4 个就全去掉；行首是制表符或非空格则那一行不动） |
| `Ctrl+/` | 覆盖到的行**整体**切换注释：全都没注释就在保留缩进的前提下各加 `// `，全都有就去掉 `//`（连它后面的一个空格）；`//` 落在字符串里的不算注释（这一条问分词器，不靠字符串比较） |

* **"覆盖到的行"**：有选区时是选区**碰到**的每一行（含只被覆盖一部分的行）；没有选区时是**光标所在行**。
  "没有选区"的判据是 `sel_start == sel_end`，而这种状态下光标（`Button::pos`）**未必**等于 `sel_start`：
  普通打字和 `Tab` 走的 `textedit_insert_buf()` 只推进光标、不动 `selsta`/`selend`。
  所以"哪一行"必须用**光标**算，用 `sel_start` 会作用在上一次的位置上。
* **变换之后，选区仍盖住同样的行**（无选区时光标被映射到新位置，仍然没有选区）。因此连按同一个键是
  **幂等**的：不会每按一次就多盖一行。旧实现拿**变换前**的偏移在**变换后**的代码上重新找行，
  而反缩进会把选区前面的文本缩短，于是选区每按一次往下多吃一行 —— 这就是 GUI 报回来的
  "每回缩一次都多选下面一行"。
* **`Tab` 不移动焦点**，哪怕这一行已经没有可缩进/可反缩进的东西：这时按键被**吃掉**，什么都不发生。
  旧实现会让按键漏到下面的"焦点循环"分支，焦点离开 `Code` 框，而 Blender 激活一个文本框时会**全选**
  它的内容 —— 用户看到的就是"回缩到头之后整段代码被选中"。`Ctrl+/` 同理：变换无事可做时也要吃掉按键，
  否则那个 `/` 会当普通字符打进代码里。
* **只作用于本节点的 `Code` 框**（判据就是 `ButtonTextBox::syntax_highlight` 那个开关，只有
  `draw_code_socket()` 传 `true`）：Blender 其余文本框的 `Tab` 照旧切焦点、`Ctrl+/` 照旧与它无关。
  （这条把两个函数挪出了 `WITH_INPUT_IME` 块：键处理会调用它们，而它不是 IME 功能。）
* **验收**：纯变换与「选区不漂移」由引擎自测断言（`tools\pdt\tests\wrangle_engine_selftest.cpp`，
  `WR-33`/`WR-34` 那一节）；**按键行为与像素断言不了**，靠人眼验收 —— 步骤见
  `../../qa/wrangle/code_shortcuts_checklist.md`（一次点击 + 几次按键，逐条判据写在那里）。

### 回车与生效时机

| 按键 | 动作 |
| --- | --- |
| `Enter` / `Shift+Enter` / 小键盘回车 | 插入换行（**不再**是"确认"） |
| `Ctrl+Enter` | 把当前内容写回节点（节点随之重新求值），**保持编辑态** |
| 点击框外 / 点击别的节点 | 离开编辑态，照旧写回 |
| `Esc` | 取消：回到进入编辑态之前的内容 |

* 框**没在编辑态**时按 `Enter` 只把编辑态打开、不改代码（通用路径先激活文本框）——鼠标悬停时误按
  `Enter` 不会改代码。
* `Ctrl+Enter` 走的就是"离开编辑态"那条写回路径（`apply_but_TEX()`，含随后触发的
  `RNA_property_update`），所以效果与点框外完全一样，只是不退出；`data->applied_interactive`
  故意不动 —— 动了的话之后退出时会**跳过**写回，把 `Ctrl+Enter` 之后敲的字丢掉。
* 只作用于本节点的 `Code` 框：Blender 其余文本框仍是 `Shift+Enter` 换行、`Enter` 确认。

### 行号

左侧显示行号，样式照 Blender 文本编辑器，**一个写死的颜色都没有**：

| 部件 | 颜色取自 |
| --- | --- |
| 底色带 | 文本框自己的内底色（主题 `wcol_text.inner`）**暗一档** |
| 数字 | 主题 Text 空间的 `line_numbers`（Text Editor 的 "Line Numbers"） |
| 光标所在行的行号 | 同空间的 `hilite` |

* 底色带为什么要"暗一档"：Text Editor 画它自己的带子用的是 `TH_GRID`，而工厂主题里那个值
  （0.114）**就是文本框的内底色**（实测：编辑器自身是带 29 / 正文底 35，`wcol_text.inner` 也是 29）——
  直接照抄等于没有带子。所以带子取文本框内底色减 `6/255`，正是主题自己那一对的差。
  想换观感就改主题里 Text Editor 的 "Line Numbers Background"，数字与高亮色也一样跟着主题走。
* **底色带的位置**：左边缘**贴合文本框的左边缘**（文本框正文本身是"矩形内缩一个内边距"画的，带子把这
  个内边距也盖住，否则看起来像缩在里面），右边缘**离代码一个字符宽**；数字**右对齐**在带子内"总行数
  位数"那么宽的字段里、**两侧各留一个字符宽**（`TXT_NUMCOL_PAD` 的做法）。
  位数从 1 变 2 时（9 → 10 行）**整段代码与选区一起向右移一格**。
* 按**逻辑行**从 1 开始编号：一行被自动折行时只有第一段带行号，续行留空。
* 行号区**不属于文本**：折行宽度里已经减掉它；点击落在行号区时按该行行首处理；选中高亮与光标
  跟着整体右移（绘制、折行、鼠标映射三处用的是同一个偏移函数
  `textbox_line_number_inset()`，数字字段用 `textbox_line_number_digit_width()`）。
* 没有进入编辑态（只显示、不编辑）时同样显示行号，但不做"当前行"高亮（没有光标）；`Code` 被连线时
  那个框退回标准画法，因此没有行号。
* **验收**：编号规则（哪一行拿哪个号、折行留空、位数增长、越界钳位）是**无 Blender 依赖的纯函数**
  `pdt_wrangle::line_number_rows()` / `line_number_digits()`，由引擎自测断言；接线与按键分支由
  `tools\pdt\tests\test_wrangle_code_box.py` 结构守卫；**像素与手感靠人眼** —— 清单
  `../../qa/wrangle/code_gutter_checklist.md`，场景 `qa/wrangle/code_gutter_demo.blend`
  （由 `qa/wrangle/code_gutter_shot.py` 生成；GUI 截图统一走 agent 插件里的 `dsh_blender_screenshot.py`，见 `tools/pdt/README.md`）。



