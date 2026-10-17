<!-- owner: user; agent: read-only -->
<!-- 条目格式见同目录 README.md：不变量一行一条，ID 写在行首粗体里。
     本文件初稿由 用户 依 docs/10-文档分层与协作规范.md 的示例落成（2026-09-15），
     之后由用户维护；agent 只在回复里提建议，不直接改。
     写作参考：tools/pdt/templates/L0-参考.md -->

# Selection Pick（视口拾取器） 最小规范

> 决定层。细则与例子在 `../docs/manual/operators/selection_pick.md`，
> 实测、坑与历史在 `../docs/archive/04-视口选择.md`；那两份不具有推翻本文件的效力。

## 目标

在 3D 视口里用手挑元素，把结果写回节点的字符串参数，便于后续用户使用 PDT Selection 节点自行解码。

## 契约

- **触发**：节点编辑器里鼠标悬停于含有几何输入节点的目标字符串参数，快捷键激活operator（用户自定快捷键）；必须是对象模式（编辑模式内拒绝）。
- **输入**：当前编辑的树；一级pie menu传入的`Domain` 有 `Vertex`/`Edge`/`Face`/`ControlPoint`/`Spline`(=`AttrDomain::Curve`)/`Point`/`Instance`(=`AttrDomain::Instance`)；二级pie menu传入的`Instance Mode`有`Point Cloud`/`Point Cloud with Reference`/`Realized Instance`；模态输入 RET / NUMPAD_ENTER / SPACE = 确定，ESC / RIGHTMOUSE = 取消，其它事件一律透传（PASS_THROUGH，pick_event_action() 返回 pass）。
- **输出**：字符串参数（一个选择字符串）写回节点。
- **持久化**：只写节点字符串参数与节点自己的 ID 属性（@prop 长选择）。

## 不变量
- **PICK-1** 识别目标是字符串参数：需要字符串参数 socket /名称label/输入框 命中，检查所在的节点上是否有几何输入，无几何输入使用operator拒绝执行并给出提示；有几何输入时则弹出一级pie menu选择要pick的`Domain`，如果`Domain`是`Instance`，弹出二级pie menu选择`Instance Mode`，根据`Domain`和`Instance Mode`烤出临时对象和参考对象（如果有）。pie menu 图标用 blender 自带 icon，当前节点上没有的类型不画，各menu应该放在原有的位置上。
- **PICK-2** 预选集合 ⊆ 节点选择集合：如果字符串参数非空，preselect_indices() 只按序号与 * 展开，组名与 @属性 记号不解析也不贡献预选点，不是复刻求值器。
- **PICK-3** 按编辑模式对应的类型烤临时对象并拾取：`Vertex`/`Edge`/`Face`烤成网格；`Control Point`/`Spline` 烤成曲线；`Point` 烤成点云；`Instance` 实例分三种情况`Point Cloud`点云，`Point Cloud with Reference`点云和原Instance参考物体，`Realized Instance`先实现instance再判断烤成网格，曲线还是点云，如果是混合类型或instance（嵌套）则拒绝执行并给出提示；识别节点输入几何里没有这个组件就拒绝执行并给出提示。
- **PICK-4** 首选项和快捷键：首选项不设置默认快捷键，提醒用户自己到 Keymap 里搜到operator并手改快捷键，提醒文字“Assign one shortcut yourself: Preferences>Keymap>Operator Name”，替换Operator Name为实际名称，不需要其他提示信息；keymap新建未绑定条目、不写 default 表、unregister() 不动用户绑的键；
- **PICK-5** 现场保护：会话的 ok / paused / other_object / missing 四种状态（pick_session_state()），暂停后能回编辑模式继续、也能直接 Apply；中途编辑了几何或切到别的（不相关）对象 → 拒绝写回并说明原因（_geometry_signature() / _signature_matches()）；拒绝时会话保持存活，用户可以 Ctrl+Z 撤销而不是丢工作；崩溃/强退留下的临时对象由面板 Clean Up Leftovers（cleanup_leftovers()）清理，启动时也收拾一次。
- **PICK-6** 只动自己的临时物：临时对象/临时节点组/临时数据块全部带固定前缀（PDT_PICK_obj_* / PDT_PICK_group_*），会话结束或失败时全部回收；原对象的修改器栈与面包屑原样恢复（含编辑器 Pin 状态）。

## 验收

- 无头：tools\pdt\tests\test_pick_operator.py 全绿、test_selection_parser.py 里"预选 ⊆ 节点"的交叉断言全绿。
- 真窗口：tools\pdt\tests\gui_pick_modal.py 全绿，且必须在用户配置下跑（不加 --factory-startup）——键位派发与 UI 缩放只有现场才现形。
- 现场：qa\ 下的只读探针能对自己正在编辑的文件跑通（临时物自清理、无残留）。

## 明确不做
- 不读取被识别节点的domain属性，烤什么对象完全由pie menu用户选择决定
- 不做节点视窗插件侧面板。
- 字符串序号索引是声明式的，上游拓扑一变含义就漂。
- 不记录操作过的字符串参数用了什么选择模式
- 拾取对准的是进入目标修改器的几何：目标修改器之下的修改器不参与。
- 不替用户改节点参数（识别字符串以外的参数一律不动）——除了写目标字符串本身与 @prop 长选择。
- 不做"固化为 bool 属性"的一键按钮（可手接 Store Named Attribute）。
- 不允许编辑模式下开始、不允许嵌套会话（begin_pick() 直接拒绝）。
- 不新建 Node Editor；不改动用户打开的编辑器（只临时 Pin/还原因拾取而变动的状态）。
