<!-- owner: user; agent: read-only -->
<!-- 条目格式见同目录 README.md：不变量一行一条，ID 写在行首粗体里。
     本文件初稿由用户依 docs/10-文档分层与协作规范.md 的示例落成（2026-09-26），
     之后由用户维护；agent 只在回复里提建议，不直接改。 -->

# PDT Wrangle（节点里写代码）最小规范

> 决定层。细则与例子在 `../docs/manual/nodes/pdt_wrangle.md`，
> 实测、性能与坑在 `../docs/14-Wrangle节点实施记录.md`；两份都不具有推翻本文件的效力。

## 目标

在一个几何节点里直接写类 VEX 代码，逐元素改写流过的几何，而不是连一堆小节点。

## 契约

- **触发**：节点被求值时执行 `Code`；`Code` 为空或只有注释时不执行，也不算错误。
- **输入**：4 条几何流（`Geometry` = 输入 0，`Input 1` / `Input 2` / `Input 3` = 输入 1…3；未连的是空几何）、`Selection`（可选的作用范围，字段）、`Run Over`（元素种类）、`Code`（多行文本，若被连线则以连进来的字符串为准）、一组通道（用户自己在节点上添加的具名值插座；每个通道一个名字与一个类型，数量由用户决定）。
- **输出**：一条几何流。
- **持久化**：`Code`、`Run Over` 与通道（名字、类型、值）随 .blend 保存；重开文件后节点仍可求值，不得退化成 Undefined。
- **出错**：编译错误 = 节点报错 + 空几何流出。

## 不变量
- **WR-1** `Code`直通：`Code` 为空或只含注释时，输出几何与输入逐属性相等，且不报错。<!-- wr:id=code-passthrough -->
- **WR-2** `Code`报错：并给出带行列号的错误，输出空几何。<!-- wr:id=code-error-output -->
- **WR-3** 有界执行：单个元素上跑不完的代码必须被中止并报错（每元素有指令上限），不允许挂住界面。<!-- wr:id=bounded-execution -->
- **WR-4** 逐元素独立：结果与线程数、元素处理顺序无关；同一棵树求值两次，属性值逐位一致。<!-- wr:id=elementwise-determinism -->
- **WR-5** 别名：`P` 是 `position` 的别名；其余属性名与 Blender 属性名一一对应，不另设别名。<!-- wr:id=position-alias -->
- **WR-6** `@`操作符：在输入流选中的元素上读写属性，不受当前代码修改几何的影响。。<!-- wr:id=at-operator -->
- **WR-7** `@`操作符属性写入：给一个不存在的属性名称赋值就创建它，类型由前缀决定（`f@` float / `i@` int / `v@` vector / `q@` quat `QUATERNION`/ `m@` matrix `FLOAT4X4` ），类型与blender内置的属性类型一致，quaternion按(w, x, y, z)写；没写前缀、前缀与几何上已有属性的类型不兼容 → 报错。<!-- wr:id=at-write -->
- **WR-8** `@`操作符属性读取：读一个不存在的属性得到 0，不是错误，也不创建它；`@`操作符前缀与几何上已有属性的类型不兼容 → 报错；四元数与矩阵同规则。<!-- wr:id=at-read -->
- **WR-9** 元素编号：`@elemnum`是当前元素序号，`@numelem` 是元素总数；两者只读，赋值是编译错误；不支持`@ptnum`和`@primnum`。<!-- wr:id=element-numbering -->
- **WR-10** `@P` 的适用范围：只有`Vertex` / `Control Point` / `Point` / `Instances`（实例的平移）能读写 `@P`；`Face Corners` 上 `@P` 只读（= 所属点的位置）；`Edges` / `Faces` / `Splines` / `Detail` 上没有 `@P`，用它就是编译错误。<!-- wr:id=at-p-scope -->
- **WR-11** 一个属性一种类型：同一段代码里同一属性只能按一种类型使用，冲突是编译错误，四元数与矩阵同规则。<!-- wr:id=one-type-per-attribute -->
- **WR-12** 类型转换：float/int/bool 之间之间可自动转换，不能自动转换的情况报错。<!-- wr:id=type-conversion -->
- **WR-13** `Run Over`：`Run Over` 的取值是 `Vertex` / `Edges` / `Faces` / `Face Corners` / `Control Point` / `Splines` / `Point` / `Instances` / `Detail`；`Run Over`决定代码运行次数（= 输入流这个域上元素数目，不受当前代码修改元素的影响），次数为0时什么都不做，`Detail` 档只运行一次； `Vertex` / `Control Point`/`Point`分别是mesh/curve/pointcloud的点属性域，剩下五个对应`Edge` / `Face` / `Corner` / `Curve` / `Instance`属性域；`@`操作符读写的就是作用域上的属性，`Detail` 档没有`@`操作符，使用该操作符算错误。<!-- wr:id=run-over-kinds -->
- **WR-14** `Run Over`菜单取值兼容性：改 int 值或重排，老文件里存的选择会指向另一个档位。<!-- wr:id=menu-value-compat -->
- **WR-15** 数值：标量按 32 位浮点计算；整数属性在 2^24 以内精确，超出后不保证。<!-- wr:id=numeric-model -->
- **WR-16** `Selection`：连了 `Selection` 时，代码只在被选中的元素上执行；不连 `Selection` = 全选；`@elemnum` / `@numelem` 仍然是域里的真实编号与总数，不因筛选而重新编号或改变总数。使用`Detail`模式`Selection` 被忽略。<!-- wr:id=selection-scope -->
- **WR-17** 多几何流：节点有 4 个几何输入，编号 0…3；未连的输入是空几何。输出仍然只有一条流（= 输入 0 被改写后的结果）。<!-- wr:id=geometry-inputs -->
- **WR-18** `point()`和`npoints()`：fpoint / ipoint / vpoint / qpoint / mpoint(int geo, string domain, string name, int index)、npoints(int geo, string domain), geo、domain、name 都是字面量，domain ∈ {`"mesh"`,`"curve"`,`"pointcloud"`,`"instance"`}，返回类型由函数名决定；读的是冻结的输入视图，不受当前代码修改几何的影响；四元数与矩阵同规则。<!-- wr:id=point-read-domain -->
- **WR-19** `prim()`和`nprimitives()`：fprim / iprim / vprim / qprim / mprim(int geo, string domain, string name, int index)、nprimitives(int geo, string domain)，geo、domain、name 都是字面量，domain ∈ {`"mesh"`,`"curve"`}， 返回类型由函数名决定；读的是冻结的输入视图，不受当前代码修改几何的影响；四元数与矩阵同规则。<!-- wr:id=prim-read-domain -->
- **WR-20** 显式索引读异常静默：domain目标组件不在流里、name属性不存在、index序号越界、读取的元素是当前代码新建的 → 返回 0。<!-- wr:id=indexed-read-silent -->
- **WR-21** 显式索引读异常报错： geo取值非0/1/2/3、domain不在词表里、name属性存在而存储类型与代码用的类型不兼容。<!-- wr:id=indexed-read-error -->
- **WR-22** `setpointattrib()`：setpointattrib(int geo, string domain, string name, int index, int|float|vector value)，geo、domain、name 都是字面量，geo只能取0，domain ∈ {`"mesh"`,`"curve"`,`"pointcloud"`,`"instance"`}，name不存在的属性就创建它；四元数与矩阵同规则。<!-- wr:id=set-point-attrib -->
- **WR-23** `setprimattrib()`：setprimattrib(int geo, string domain, string name, int index, int|float|vector value)，geo、domain、name 都是字面量，geo只能取0，domain ∈ {`"mesh"`,`"curve"`}，name不存在的属性就创建它；四元数与矩阵同规则。<!-- wr:id=set-prim-attrib -->
- **WR-24** 显式索引写异常静默：domain确定的组件不在流里、index序号越界、value和已有属性类型不一致 → 不报错、不做任何事。<!-- wr:id=indexed-write-silent -->
- **WR-25** 显式索引写异常报错：geo取非0值、domain不在词表里。<!-- wr:id=indexed-write-error -->
- **WR-26** `addpoint()`：int addpoint(int geo, string domain, vector pos)，geo、domain都是字面量，geo只能取0，domain ∈ {`mesh`,`pointcloud`}，没有domain组件时需要新建组件；geo取非0值、domain不在词表里 → 报错；<!-- wr:id=add-point -->
- **WR-27** `addprim()`：int addprim(int geo, string domain, string pointdomain, int p0, int p1, …)，geo、domain、pointdomain都是字面量，geo只能取0，domain ∈ {`"mesh"`,`"curve"`}，pointdomain ∈ {`"mesh"`,`"curve"`,`"pointcloud"`,`"instance"`}，没有domain组件时需要新建组件；domain = `"mesh"` 且 pointdomain = `"mesh"`时可以直接复用`Vertex`域的点，其他组合都应该复制pointdomain点和属性来构建新几何；geo取非0值、domain或pointdomain不在词表里、domain = `"mesh"`时点数小于3、domain = `"curve"`时点数小于1→ 报错；。<!-- wr:id=add-prim -->
- **WR-28** `removepoint()`：int removepoint(int geo, string domain, int index)，geo、domain、都是字面量，geo只能取0，domain ∈ {`"mesh"`,`"curve"`, `"pointcloud"`, `"instance"`}；domain = `"mesh"` 时，连带删除引用点的边和面，参考delete_geometry节点的point+all模式；domain = `"curve"` 时，只从所属样条里删掉这一个控制点，如果样条剩余0点，则同时删掉样条，参考delete_geometry节点的point+all模式；geo取非0值、domain或pointdomain不在词表里 → 报错；domain确定的组件不在流里、index序号越界 → 静默。<!-- wr:id=remove-point -->
- **WR-29** `removeprim()`：int removeprim(int geo, string domain, int index)，geo、domain、都是字面量，geo只能取0，domain ∈ {`"mesh"`,`"curve"`}；domain = `"mesh"` 时，连带删除引用点的边和面，参考delete_geometry节点的face+all模式；geo取非0值、domain或pointdomain不在词表里 → 报错；domain确定的组件不在流里、index序号越界 → 静默。<!-- wr:id=remove-prim -->
- **WR-30** 有序并行：每个元素各自缓冲、最后按元素序号合并。新增元素的编号确定，先按源元素序号、再按该元素内的调用顺序（同一个元素内连续调两次 `addpoint`，得到的是相邻编号）；写刚创建的元素有效；同一个元素被多个元素写入时，按元素序号顺序应用；新元素不参与本次迭代。<!-- wr:id=ordered-parallelism -->
- **WR-31** 语法高亮：`Code` 框按词法给代码上色，类别取 Houdini VEX 框实测的那九类——普通标识符、关键字、类型名、函数名、引用（`@P`、`f@height` 这类整体算一个引用）、数字、字符串（引号与其内容同色）、注释、错误；颜色取 Blender 主题的语法配色，不写死颜色值，类别到主题色槽的映射是细则（见 `../docs/manual/nodes/pdt_wrangle.md`）；只有本节点的 `Code` 框启用高亮，Blender 其余文本框保持单色；`Code` 被连线时该框显示连入值、不显示编辑器，因此没有高亮；四元数与矩阵同规则。<!-- wr:id=code-highlight -->
- **WR-32** 高亮与语义无关：高亮本身不做语法判断（代码是否合法只由编译决定，见 `WR-2`），唯一依赖编译结果的是"错误"类别的位置——用编译报错的行列号标出（Houdini 用行标记，细则在 L1）；高亮不改变求值结果（同一棵树无论是否高亮，输出几何与属性逐位一致），也不改变 `Code` 的存储内容；四元数与矩阵同规则。<!-- wr:id=code-highlight-lexical -->
- **WR-33** 注释快捷键：`Code` 框里按 `Ctrl+/` 切换注释——目标行是光标所在行（无选区时），或被选区覆盖到的所有行（有选区时，含只被部分覆盖的行）；这些行若都已以 `//` 开头，就去掉该前缀，否则在保留原有缩进的前提下统一加上 `// `；只作用于 `Code` 框，Blender 其余文本框不变。<!-- wr:id=code-comment-shortcut -->
- **WR-34** Tab 缩进：`Code` 框里 `Tab` 插入 4 个空格（不移动焦点、不插入制表符）；无选区时在光标处插入这 4 个空格，有选区时改为在覆盖到的每一行行首插入 4 个空格；`Shift+Tab` 反向——把覆盖到的行（无选区时为光标所在行）行首最多 4 个空格去掉（不足 4 个就全去掉，不跨到非空格字符）；只作用于 `Code` 框。<!-- wr:id=code-tab-indent -->
- **WR-35** 通道：节点上有一组用户维护的具名通道，每个通道有唯一的名字与一个类型（ int / float / vector / quaternion / matrix），可增可删、数量由用户决定；节点上一个通道都没有时，行为与没有这个功能时完全一致。<!-- wr:id=channel-set -->
- **WR-36** 通道读取：`chi(name)` / `chf(name)` / `chv(name)` / `chq(name)` / `chm(name)`按 int / float / vector / quaternion / matrix 读本节点通道 `name` 的值，`name` 是字符串字面量；三个函数只读——不修改通道、也不创建通道；所有 `Run Over` 档（含 `Detail`）都能读。<!-- wr:id=channel-read -->
- **WR-37** 通道取值：读到的是该通道本次求值时的值（通道被上游连线驱动时同样如此）；同一次求值里每个元素读到同一个值（与 `WR-4` 一致），与 `Selection` 无关；通道不是逐元素数据。<!-- wr:id=channel-value -->
- **WR-38** 通道读不到就是 0：名字不存在、大小写不符、名字里带分量（如 `offset.y`）→ 返回 0（float / int）或 `(0,0,0)`（vector），不报错、不创建属性、不影响输出。<!-- wr:id=channel-missing-zero -->
- **WR-39** 函数与通道类型不合也是 0：用标量函数读 vector 通道 → 0；用 `chv` 读标量通道 → `(值, 0, 0)`；int 与 float 通道互相读取按 `WR-12` 转换（float → int 截断）——都不报错。<!-- wr:id=channel-type-mismatch -->
- **WR-40** 通道名唯一：同一个节点上不允许两个同名通道，添加一个与已有通道同名的通道时自动改名（细则在 L1）。<!-- wr:id=channel-name-unique -->
- **WR-41** 行号：`Code` 框在左侧显示行号，样式与 Blender 文本编辑器一致——行号数字、行号区底色、光标所在行的行号高亮三者都取主题颜色，不写死颜色值；按逻辑行从 1 开始编号，一行被自动折行时只有它的第一段带行号；行号区不参与文本——它占的宽度不算进折行宽度，也不影响 `@` 之外的任何语义；没有进入编辑态（只显示、不编辑）时同样显示行号；只有本节点的 `Code` 框有行号，Blender 其余文本框保持原样。<!-- wr:id=code-line-numbers -->
- **WR-42** 回车与生效时机：`Code` 框里 `Enter`（含小键盘回车、含 `Shift+Enter`）插入换行，不再承担"确认"；`Ctrl+Enter` 把当前内容立即写回节点（该写回与离开编辑态的写回同一条路径），并且不退出编辑态；离开编辑态（点击框外、点击别的节点等）时照旧写回；`Esc` 仍是取消——回到进入编辑态之前的内容。框未处于编辑态时按 `Enter` 只把编辑态打开、不改动代码。以上只作用于 `Code` 框，Blender 其余文本框的回车行为不变。<!-- wr:id=code-enter-apply -->
- **WR-43** `rotate_rotation`：rotate_rotation (string space, quat base, quat by)，space ∈ {`"global"`, `"local"`},参数和顺序严格按blender节点走，包括socket和按钮的输入。<!-- wr:id=func_rotate_rotation -->
- **WR-44** `rotate_vector`：rotate_vector(vector v, quat r)，把旋转 `r` 施加在向量 `v` 上，返回转向后的向量；参数与顺序严格按 Blender 的 Rotate Vector 节点（Vector、Rotation）。<!-- wr:id=func_rotate_vector -->
- **WR-45** `axis_angle_rotation`：axis_angle_rotation(vector axis, float angle)，由"轴 + 绕轴角度"构造旋转；参数与顺序严格按 Blender 的 Axis Angle to Rotation 节点（Axis 默认 `{0,0,1}`、Angle）；角度是弧度。<!-- wr:id=func_axis_angle_rotation -->
- **WR-46** `euler_rotation`：euler_rotation(vector euler)，由三个轴上的欧拉角构造旋转；参数与顺序严格按 Blender 的 Euler to Rotation 节点（Euler 一个向量输入）；角度是弧度。<!-- wr:id=func_euler_rotation -->
- **WR-47** `quaternion`：quaternion(float w, float x, float y, float z)，由四个分量构造旋转；参数与顺序严格按 Blender 的 Quaternion to Rotation 节点（W 默认 1、X/Y/Z 默认 0）。<!-- wr:id=func_quaternion -->
- **WR-48** `to_euler`：to_euler(quat r)，把旋转换成欧拉角（一个向量），与 Blender 的 Rotation to Euler 节点一致；角度是弧度。<!-- wr:id=func_to_euler -->
- **WR-49** `to_axis_angle`：to_axis_angle(quat r, &vector axis, &float angle)，把旋转拆成"轴 + 角度"两个返回值，与 Blender 的 Rotation to Axis Angle 节点一致（输出顺序 Axis、Angle）；角度是弧度；出参原地写。<!-- wr:id=func_to_axis_angle -->
- **WR-50** `axes_rotation`axes_rotation(string primary_axis, string secondary_axis, vector primary, vector secondary)，根据主副轴把两个矢量组成旋转，主轴是完全对应的，副轴尽可能对应，与 Blender 的 Axes to Rotation 节点一致。<!-- wr:id=func_axes_rotation -->
- **WR-51** `invert_rotation`：invert_rotation(quat r)，返回反向旋转，与 Blender 的 Invert Rotation 节点一致。<!-- wr:id=func_invert_rotation -->
- **WR-52** `align_rotation_to_vector`：align_rotation_to_vector(string axis, string pivot, quat rotation, float factor, vector v)，把 `rotation` 的本地主轴对齐到向量 `v`；参数与顺序严格按 Blender 的 Align Rotation To Vector 节点——前两个是节点的两个按钮（Axis ∈ {`"x"`,`"y"`,`"z"`}，默认 `"z"`；Pivot ∈ {`"auto"`,`"x"`,`"y"`,`"z"`}，默认 `"auto"`），随后是 Rotation、Factor（0…1，默认 1）、Vector（默认 `{0,0,1}`）。<!-- wr:id=func_align_rotation_to_vector -->
- **WR-53** `combine_transform`：combine_transform(vector translation, quat rotation, vector scale)，合成一个 4×4 变换矩阵；参数与顺序严格按 Blender 的 Combine Transform 节点（Translation、Rotation、Scale 默认 `{1,1,1}`）。<!-- wr:id=func_combine_transform -->
- **WR-54** `matrix_multiply`：matrix_multiply(matrix a, matrix b)，按 Blender 的 Multiply Matrices 节点相乘（返回 `b × a`，即后输入者作用在前输入者之前），参数与顺序严格照该节点的两个 Matrix 输入。<!-- wr:id=func_matrix_multiply -->
- **WR-55** `invert_matrix`：invert_matrix(matrix m)，返回 `m` 的逆矩阵；参数与顺序严格按 Blender 的 Invert Matrix 节点——它的 `Invertible` 布尔输出本节点不产出（不可逆时返回单位矩阵，与那个节点的 Matrix 输出一致）。<!-- wr:id=func_invert_matrix -->
- **WR-56** `transpose_matrix`：transpose_matrix(matrix m)，返回 `m` 的转置，与 Blender 的 Transpose Matrix 节点一致。<!-- wr:id=func_transpose_matrix -->
- **WR-57** `matrix_determinant`：matrix_determinant(matrix m)，返回 `m` 的行列式（一个 float），与 Blender 的 Matrix Determinant 节点一致。<!-- wr:id=func_matrix_determinant -->
- **WR-58** `separate_transform`：separate_transform(matrix m, &vector p, &quat r, &vector s)，返回 `m` 的位移旋转和缩放，与 Blender 的 Separate Matrix节点一致，出参原地写。<!-- wr:id=func_seperate_matrix -->
## 验收

- 无头（节点）：`tools\pdt\tests\test_pdt_wrangle.py` 全绿（契约、持久化、逐元素语义、报错与直通、通道读取）。
- 无头（引擎）：`tools\pdt\tests\wrangle_engine_selftest.cpp` 全绿（语言、类型检查、错误信息）。
- 回归：`tools\pdt\run_tests.ps1` 全绿，`pdt_spec.py check --strict` 0 警告。

## 明确不做
- 不支持`@opinput<n>_<name>`（n = 1…3）跨流读取
- 不做蜡笔图层（AttrDomain::Layer）。
- 不保证与 Houdini VEX 数值一致（`noise()` / `rand()` 只保证确定性）。
- 不做脚本沙箱：代码在 Blender 进程内执行，能写死循环。