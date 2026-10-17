[English](README.md) | **简体中文**

# Blender 的 PDT 几何节点（只含改动）

> **这不是一份 Blender 源码**。这里只有本项目**新增或修改**的文件：两个几何节点、两个
> 插件，以及几处对上游代码的改动。想要能跑的 Blender，得把这些文件拷到一份真实的
> Blender 源码上再编译（下面按步骤写）。

基于 Blender 5.2.3 LTS (rc)，上游基点提交 `e8b7fe175f6fc837e8a0db7444c5fe393cd0b5fb`，导出时本方提交 `aece7da5 docs: brand version = release tag, in the manual and the tag policy`。

## 这里有什么

| 东西 | 在 Blender 里的位置 | 干什么用的 |
| --- | --- | --- |
| **PDT Selection**（几何节点） | 节点编辑器 `Shift+A` → **PDT** → PDT Selection | 把你写的**选择字符串**（例如 `0-15 ^3 group:arm`）变成布尔场，喂给 Delete Geometry、Set Position 这类节点 |
| **PDT Wrangle**（几何节点） | `Shift+A` → **PDT** → PDT Wrangle | 在节点里写一小段类 VEX 代码（`@P.y += 1;`），按点 / 面 / 样条逐元素执行 |
| **PDT Geometry Selection**（插件） | 自己绑一个快捷键，光标停在节点的字符串输入框上 | 在 3D 视口里点几何体选元素，自动把索引写回那个输入框，不用手敲数字 |
| **PDT Node Info**（插件） | 自己绑一个快捷键，按住它并把光标放在节点上 | Houdini 那种信息浮层：显示流经这个节点的几何 |
| 几处上游改动 | Outliner 拖放、USD 导入器 | 见下面「文档」里的手册 |

除此之外，你的 Blender 和官方版一样。

## 动手之前先确认

* **Windows 10/11 x64**：`tools/pdt/` 里的辅助脚本是 PowerShell。Linux/macOS 上照样能用，
  只是第 2 步的拷贝要手动做（`cp` + 正常的 CMake 构建）。
* **一份 Blender 源码**（上面那个基点提交），**并且带预编译库**（`lib/windows_x64/` 不能是空的，
  见下一节），外加**能编译 Blender 的环境**（VS2022 + CMake）。也就是说：你得先能自己编 Blender。
  不能的话先照
  [Blender 官方构建文档](https://developer.blender.org/docs/handbook/building_blender/) 做一遍，
  本仓库不负责这一步。
* **磁盘与时间**：光预编译库就有约 **6.5 GB**；从零构建又大又慢（几十分钟到几小时，看机器）。
  之后改一个节点文件，增量编译大约 20–30 秒。

## 准备源码：预编译库（这一步最容易漏）

**下载来的 Blender 源码里没有编译需要的那些库。** 如果你拿的是 ZIP（或 GitHub 的
"Download ZIP"），`lib/windows_x64/` 这个目录**在，但是空的**，CMake 会直接拒绝配置。
这些库放在一个单独的、用 git-lfs 存的仓库里
（`https://projects.blender.org/blender/lib-windows_x64.git`，分支 `blender-v5.2-release`），
官方用一个 update 步骤把它们拉下来：

```powershell
# 在 Blender 源码根目录执行。需要 PATH 里有 git、Python、Git LFS。
make update          # Windows 上用根目录的 make.bat update，它转给同一个脚本
```

这条命令会把 `.gitmodules` 里对应平台的 `lib/<平台>_<架构>` 子模块启用并下载下来。
它**很慢**——Blender 自己的脚本提示"网速慢时可能要一个多小时"。

**如果你的源码是 ZIP，`make update` 用不了**——它驱动的是 git 子模块，而 ZIP 里没有 `.git`。
这时改成把库仓库直接 clone 进那个空目录：

```powershell
git lfs install
git clone -b blender-v5.2-release https://projects.blender.org/blender/lib-windows_x64.git C:\work\blender\lib\windows_x64
```

（我们自己在用的 5.2.3-rc 检出是那个仓库的提交 `60d6e96b9`，正好是该分支的 tip；
要完全对齐就 `git -C C:\work\blender\lib\windows_x64 checkout 60d6e96b9`。）

然后接着走下面的构建步骤。这两条路都是 Blender 官方机制，没有本项目专有的东西。

> **用 ZIP 时特别注意版本。** 我们的文件是相对 `e8b7fe175f6fc837e8a0db7444c5fe393cd0b5fb`（Blender 5.2.3 LTS (rc)）这一版写的。
> 别的 5.2.x 的 ZIP 也能用——`apply.ps1` 就是纯文件拷贝——但它会**覆盖**我们改到的那几个
> 文件里的上游代码，于是 5.2.0 的树会变成"5.2.0 减去几个 5.2.1–5.2.3 的修正"。
> 能用 git 就用 git：克隆 release 分支再 `checkout` 到那个提交。
> 另外我们顺带带了 `BKE_blender_version.h`（把文件子版本钉在 46）和我们的
> `versioning_520.cc`，所以套到别的 5.2.x 上，编译出来的版本号会**自报 5.2.3 LTS (rc)**——
> 这只是版本字符串变了，那棵树仍然不是我们跑过测试的那一棵。

## 一步一步来（可以照抄）

```powershell
# 0. 把这个仓库克隆到 Blender 源码之外的地方
git clone <本仓库地址> C:\work\pdt-changes
cd C:\work\pdt-changes

# 1. 取到基点提交的 Blender 源码（兼容的 5.2.x 也行）……
git clone https://projects.blender.org/blender/blender.git C:\work\blender
git -C C:\work\blender checkout e8b7fe175f6fc837e8a0db7444c5fe393cd0b5fb
#    ……再加上它的预编译库（官方 update 步骤）。Windows 上：
C:\work\blender\make.bat update
#    （Linux/macOS：源码根目录下 `make update`。ZIP 源码没有 git，这步跑不了——
#      改成手工 clone lib\windows_x64，见上面那一节。）

# 2. 把我们的文件拷进去。先跑预览，它只列出会改哪些文件，不动磁盘
pwsh -File apply.ps1 -Target C:\work\blender -DryRun
pwsh -File apply.ps1 -Target C:\work\blender

# 3. 编译。第一次需要一个「已配置好」的构建目录，两条路选一条：
#    (a) 照 Blender 官方说明配置一个，目录名用 build_windows_x64_vc17_Release，
#        放在源码旁边——脚本默认就找这个名字；
#    (b) 或者让这个脚本配一个精简版（关掉 Cycles/USD，只适合开发，别拿去发布）：
pwsh -File C:\work\blender\tools\pdt\configure_geo_fast.ps1 -Build   # 一次性的长任务
#    走 (b) 的话目录是 C:\work\build_geo_fast，脚本猜不到这个名字，
#    每个 PowerShell 窗口里先告诉它一次（否则会报 project not found）：
$env:PDT_BUILD_DIR = 'C:\work\build_geo_fast'
#    ……以后每次改代码只要：
. C:\work\blender\tools\pdt\build_dev.ps1 -Target install

# 4. 跑测试（无头；加 -Gui 才跑真窗口那几项）
. C:\work\blender\tools\pdt\run_tests.ps1
```

成功的标志：`run_tests.ps1` 最后一行是 `== summary: 0 failure(s) ==`，并且编译出来的
`blender.exe` 在几何节点 `Add` 菜单里有一个 **PDT** 分类。

## 怎么用

1. 启动编译出来的 Blender，给任意物体加一个 Geometry Nodes 修改器。
2. 在节点编辑器里 `Shift+A` → **PDT** → 选一个节点。
3. 两个插件要手动开：`编辑 > 偏好设置 > 插件`，勾上 **PDT Geometry Selection** 与
   **PDT Node Info**，然后**自己去 `偏好设置 > 键位映射` 给它们绑键**——它们故意不抢键，
   所以永远不会和你现有的键位打架（那两条条目默认是未绑定的）。
4. 在**官方** Blender 里打开带这些节点的文件，节点会显示成"未定义节点"——这是正常的，
   它们只存在于用本仓库编出来的版本里。

## 出问题了对症下药

| 现象 | 原因 / 怎么办 |
| --- | --- |
| 配置 CMake 时报 `lib/windows_x64` 里的外部库缺失，或者那个目录干脆是空的 | 源码是 ZIP、没带预编译库。见「准备源码：预编译库」：git 检出的跑 `make update`，ZIP 的手工 clone 库仓库 |
| 拉库时 `git lfs` 报错 | 先装 Git LFS（`git lfs install`）；库仓库里的二进制是 LFS 存的 |
| `apply.ps1` 说目标"not a Blender source checkout" | `-Target` 要指到包含 `source\blender\` 的那一层目录 |
| 编译报某个工程/节点类型不存在 | 构建目录是在拷文件**之前**配置的——用 `-Reconfigure` 重新构建 |
| `run_tests.ps1` 警告 `binary hash ... != HEAD` | `blender.exe` 比源码旧，先重编，否则测试结论不可信 |
| 测试报 `key "test_object" not found` | 命令行里 `.blend` 必须写在 `--python` **前面** |
| `run_tests.ps1 -Gui` 没有 PASS | 那几项要真实桌面会话（无桌面的远程/服务会话跑不了） |
| 在开发仓库里 `git commit` 报 `sh.exe` 之类的错 | 那是开发流程的钩子，本仓库（纯文件快照）不需要 |

## 文档

* `tools/pdt/docs/manual/` —— **规范性**用户手册（中文）：选择字符串的完整语法、Wrangle
  语言、拾取器与 Node Info 的行为。`run_tests.ps1` 会逐行执行手册里的例子，所以它和代码
  永远同步。
* `tools/pdt/spec/` —— 手册对应的手写最小规范（每条都有编号，测试直接引用）。
* `tools/pdt/README.md` —— 工具链自身怎么组织的。

设计过程记录、构建耗时实测、本机环境笔记**有意没有带出来**：它们描述的是开发这台机器。

## `files/` 里都是什么

| 路径 | 内容 |
| --- | --- |
| `source/blender/nodes/geometry/nodes/custom/` | 两个节点、Wrangle 引擎、节点清单 |
| `scripts/addons_core/pdt_geo_select/`、`pdt_mmb_info/` | 两个插件 |
| `tools/pdt/tests/` | `run_tests.ps1` 背后的断言 |
| `tools/pdt/spec/`、`tools/pdt/docs/manual/` | 规范与手册 |
| 三个上游文件 | 几何 CMake 列表、节点 RNA 注册、"Add" 菜单项——只改其中标记好的 `PDT` 区块 |
| `tests/files/usd/...` | 两处 USD 导入器改动的参考文件更新 |

每个文件都与我们工作区里的**逐字节相同**，路径也和 Blender 里一致。

## 许可

Blender 是 GPL-2.0-or-later。`source/` 下的文件与两个插件同样适用该许可；把它们发布出来
即满足分发构建产物时的源码可得性要求。`tools/pdt/` 的脚本与文档同许可。
