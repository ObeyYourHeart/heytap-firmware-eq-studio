# HeyTap Firmware EQ Studio — Fork / Claude Code 开发交接

> 状态核对：2026-10-11（以 GitHub 远端当前内容和 Actions 日志为准）  
> 原仓库：<https://github.com/uselessbug/heytap-firmware-eq-studio>  
> **应接手的分支**：`feat/python-desktop-studio`；HEAD `e62d5d161d4436146ec9cd71b653c7050ba32214`  
> 上游草稿 PR：<https://github.com/uselessbug/heytap-firmware-eq-studio/pull/1>（Draft，未合并）  
> 最新成功 CI：<https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655>

## 0. 给接手者的三句话

本项目不是一个空的固件解析脚本：目前已有可以启动、可以调整频响、可以拟合、可以重新封包的 **Python / PySide6 + Qt WebEngine 内嵌 React/DSSSP** 桌面程序。首个可编辑机型为 OPPO Enco X4，产品目标是做成可扩展的本地固件 EQ 工作台。

**这轮最需要 Claude Code 的是 UI/UX、代码组织、交互流畅度和可维护性优化**，不是推倒重写声学算法或把桌面应用改成远程 Web 服务。请先运行现有程序并截图、梳理用户路径，再以小 PR 渐进修改。

**最容易踩的坑：默认 `main` 基本是空仓库；全部实质代码都在 `feat/python-desktop-studio`。** Fork 时务必选择复制所有分支，或在 fork 后从 upstream 显式抓取该分支。

## 1. 最新代码与 CI 的真实状态

| 项目 | 截至 2026-10-11 的事实 |
| --- | --- |
| 默认分支 `main` | 基本只有初始化 README，不是开发基线 |
| 代码分支 | `feat/python-desktop-studio`，HEAD `e62d5d1` |
| PR | #1，Draft、open、未合并 |
| 栈 | Python 3.11 / PySide6 6.8.3 / NumPy / SciPy；React 19、DSSSP 0.8.0、esbuild；Node 22 用于构建 |
| 最新 Actions | 五项 job 全成功：Linux 检查、Windows 检查、PyInstaller 打包、Nuitka 打包、两种 EXE 对比 |
| 自动测试 | **Linux 53 passed、Windows 53 passed**（请以本次日志为准；旧 README/PR 描述的 49 项已过时） |
| 其他检查 | Ruff、前端 Node 测试、Playwright/Chromium 鼠标交互、Qt WebChannel、源码 GUI 冒烟、实际冻结 EXE 冒烟 |
| Windows PyInstaller 包 | [Actions Artifact](https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655/artifacts/11688863706)，上传体积约 230.4 MB |
| Windows Nuitka 包 | [Actions Artifact](https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655/artifacts/11689701578)，上传体积约 194.9 MB |
| 两种包对比报告 | [Actions Artifact](https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655/artifacts/11690360145) |

同一 Windows runner 的实测中位数（预热后各测 3 次，交错执行）：

| 任务 | PyInstaller | Nuitka | 解释 |
| --- | ---: | ---: | --- |
| GUI 冒烟进程（含故意等待 1 秒） | 2.171 s | 1.921 s | Nuitka 约快 13%，**不是首帧时间** |
| 整个离线基准进程 | 3.108 s | 2.750 s | Nuitka 约快 13% |
| PEQ 拟合工作负载 | 0.328 s | 0.335 s | 几乎相同，Nuitka 略慢 |
| RAW 固件拟合负载 | 1.568 s | 1.557 s | 几乎相同 |

两者都是携带 Qt/Python/native 运行库的 **standalone directory**，不是把 QtWebEngine/所有动态库真正静态链接进一个 EXE。当前 `scripts/build_desktop.py` 的验证报告明确写有 `static_qt_webengine: false`。不应无证据声称 Nuitka 大幅提高数值计算速度。

**注意 CI 触发器**：`.github/workflows/desktop.yml` 的 push 事件目前只监听原分支名 `feat/python-desktop-studio`，PR 事件显式限定 opened/reopened/ready_for_review，未包含 synchronize。朋友 fork 出新的开发分支后，需要调整触发条件（例如启用手动 workflow_dispatch、通用 PR synchronize）才能持续收到更新检查。当前前端目录也未见 npm lockfile，后续应提交锁文件并评估 `npm ci`。

## 2. 产品目标与不能偏离的原则

- **离线优先、单体桌面应用**。Python 负责固件、DSP、拟合、封包；Qt 负责桌面窗口、文件对话框和任务；React/DSSSP 用于嵌入式可交互频响图。Node 只用于开发构建，不在最终用户机器运行，不引入必需的本地 HTTP 服务器、Electron 或云端后端。
- **简单主流程**：打开固件（可选）→ 获得原始频响（在线测量/本地文件）→ 选择目标 → 生成或编辑修正 EQ → 预览当前/编辑后/待导出估计 → 选择目标固件预设并拟合 → 导出新固件。无固件时仍可独立生成 Wavelet/Flowmix 修正 EQ。
- **曲线语义精确**：原始人工耳测量、数字滤波链频响、基于假设计算的声学估计、目标以及待写入计划必须分别命名，不能把数字模型说成实测。
- **第一机型 Enco X4，但架构应可扩展**：不要在 UI 里硬编码 EQ index 或把当前固件偏移推广到所有型号。未知固件先只读扫描；有足够适配器验证后才允许写入。
- **保留原输入与可恢复性**：所有固件修改生成新文件，绝不覆盖原厂输入。工程恢复、全局撤销/重做和差分报告不可丢失。耗时拟合应后台执行、支持取消、拒绝过期任务结果。

## 3. 代码地图（先读这些）

| 位置 | 内容 / 注意事项 |
| --- | --- |
| `README.md` | 当前启动方式与功能概述；部分“当前包”链接滞后 |
| `docs/unified-workspace.md` | **最准确的当前交互/差分模型说明** |
| `docs/handoff.md` | 历史研究及分阶段交接；上半部有新检查点，下半部有多条过时“未实现”陈述 |
| `docs/firmware-format.md` | OPKG、指针表、记录结构、预设/状态映射、版本字段 |
| `docs/firmware-editing.md` | 36 条完整预设、元数据和导出要求 |
| `docs/measurement-fitting.md`、`docs/flowmix-api.md` | 测量来源、拟合约束、接口及历史验证；部分令牌状态描述已过时 |
| `src/heytap_eq/gui.py` | 约 73 KB 的 Qt 主界面与工作流，**重构的首要候选，但先测再拆** |
| `src/heytap_eq/web_plot.py` | Qt WebEngine / WebChannel ↔ React 的 JSON 事件边界 |
| `frontend/src/editor.jsx`、`frontend/src/editor.css` | React/DSSSP 的唯一主图与编辑交互；双击、拖动、缩放、PEQ 参数浮窗 |
| `frontend/src/math.mjs` | 对数坐标、插值、重采样和视图数学；有 Node 测试 |
| `src/heytap_eq/session.py`、`clipboard.py`、`preferences.py` | 工程 v4、撤销/重做、完整调音/预设剪贴板、设备选择记忆 |
| `src/heytap_eq/flowmix.py`、`measurements.py`、`fitting.py` | 在线/离线测量、目标及 RAW/PEQ 生成（独立于 Qt） |
| `src/heytap_eq/estimation.py`、`firmware_dsp.py` | 参考固件差分、双单元估计模型、数字滤波传递函数 |
| `src/heytap_eq/adapters.py`、`configuration.py`、`discovery.py` | 已知固件语义校验、适配器映射、未知固件候选扫描 |
| `src/heytap_eq/firmware_edit.py`、`metadata.py`、`opkg.py` | 受限固件拟合/写入、元数据/版本、OPKG 完整性/封包 |
| `src/heytap_eq/data/enco-x4-reference.json` | **已内置、可打包**的 Enco X4 原版参考 EQ；不是原始固件 |
| `src/heytap_eq/service_profile.py` | 当前公共仓库中已有内置共享测量认证；**敏感配置，禁止打印或顺手覆盖** |
| `.github/workflows/desktop.yml`、`scripts/build_desktop.py`、`scripts/compare_packages.py` | 双打包、冻结启动验证与公平性能对比 |
| `tests/`、`frontend/test/` | 回归测试（合成固件、UI、协议、拟合、元数据、打包、交互） |
| `research/` | 历史 CLI、样本差异、反汇编证据、结构报告与两个工具包 ZIP。研究证据不等于现行应用源码 |

请尽量维持 Python 核心独立于 Qt，避免引入一套平行的 JavaScript DSP 算法。前端只负责交互和渲染，数据/改动通过明确的 WebChannel 消息交给 Python；必要时先为消息模式补契约测试。

## 4. 已实现功能（不要重做一遍）

1. **固件读取与导出**：已知 Enco X4 的 OPKG 完整性检查、真实指针表读取、四张表识别、完整预设拟合、受限参数修改、重新封包/回读、差异和完整性验证。原版 112/116 与若干第三方样本已做离线比较，但**没有真实耳机 OTA/刷写成功的保证**。
2. **外部 EQ**：Wavelet `GraphicEQ`、Flowmix `[RAW]` + `[PEQ]`（含启用状态、稀疏 ID、多种滤波器类型）；可导入/编辑/导出，PEQ/RAW 曲线交互、滤波器数值编辑、一次拖动一次撤销。
3. **测量数据**：客户端动态读取 Flowmix 的来源/品牌/型号/测量/目标接口；验证时有 6 个来源（含 Woodenears）、21 个目标。支持本地 CSV、JSON、ReaLab HAR 和将另一副耳机测量作为目标。运行不依赖 APK。首次空选择、已确认机型唯一精确匹配、随后记住选择。
4. **修正生成**：原始测量与目标在共享频段做对数插值，支持 RAW/PEQ、平滑、限幅、电平对齐、强度、滤波器数量、取消；PEQ 由残差初始化 + SciPy 优化。算法参考 Flowmix APK 的流程，但**并非逐参数/逐采样复刻**。
5. **统一主图**：实测、目标、当前固件估计、编辑后估计、待导出估计、DAC1/2 数字链可在统一对数频率图内显示、缩放及显隐；Qt shell 中复杂设置可折叠。
6. **参考差分**：内置官方 112/116 的 184 条记录、6 组配置（5 个普通预设 + 1 个特殊配置）。可加载参考固件，比较各路复数传递函数变化，再给出**带假设的**测量 + 差分声学估计。
7. **元数据**：对已验证布局同步容器版本、`SW_VER`、Thumb getter，以及已确认的 `BUILD_DATE`、`REV_INFO` 等等宽字段；不允许随意修改结构地址、长度、自动校验位。
8. **完整工作流**：可复制/粘贴整个调音（RAW/PEQ/启用/关联）与整个固件预设（四表/状态/增益），粘贴按目标固件重新解析映射。工程 v4、旧工程兼容、自动恢复与全局撤销/重做已接入。

**验证精度边界**：曾使用真实官方 112/116 + Optimized EQ 对各 36 条记录拟合、导出、重读；有效频段三采样率复算最差约 RMS 0.128 dB、最大 0.851 dB（数字模型）。这些数值不能证明最终耳机声学频响/听感达到相同误差，详见 `docs/firmware-real-fit-validation.json`。

## 5. 固件研究里绝对不能“简化掉”的语义

- 已确认的 OPKG v1 是单段封包、LZMA-alone 分块；文件头有整包 SHA-256，块有 CRC/散列。**保留未修改压缩块的原字节**；导出后校验所有受影响范围。
- 普通 EQ 记录固定为 **300 字节**：`<ffI` + `18 × <Ifff>`，即两个 float32 整体增益、滤波器数、18 个 `(type_id, gain_dB, fc_Hz, Q)` 槽。不要把频率和增益字段顺序弄反。类型 5 为 **All pass**，不是 Notch。
- 四表顺序：`other_output2`、`india_output2`、`other_output1`、`india_output1`。**output1/2 不等于已证实的左右耳**；界面目前称 DAC1/DAC2，仅为输出别名，物理 DAC 编号未完全确认。复选显示不控制最终拟合的写入范围。
- 普通预设五组：丹拿原声 `[0..8]`、清亮高音 `[9..17]`、纯享人声 `[18..26]`、澎湃低音 `[27..35]`、丹拿高解析 `[36..44]`；每组 4 张表 × 9 状态 = 36 条。内部状态映射见 `docs/firmware-format.md`。
- **索引 45** 是特殊配置、不是第六个普通音效预设。用途尚未完全逆向证实；目前由 Enco X4 适配器的配置语义提供，不能在通用算法/界面里硬编码为所有固件通用。
- 在已检查的 112/116/第三方 113 中，`other` 与 `india` 的 EQ 滤波器相同，`india` 两个整体增益各高 4 dB。不能将所有地区数据去重合并；未来不同固件可能每表滤波不同。
- 固件滤波器上限 **18 槽**，拟合保留原链 HP/LP/AP；分别核验 44.1/48/96 kHz 和 float32 写入后的稳定性/容量/误差。默认门限 RMS ≤ 0.45 dB、Max ≤ 1.5 dB（有效频段）。
- **声学差分不是真实重测**：如果两路复数传递变化一致可取共同变化；两路不一致时目前采用可调整交接频率的非相干功率权重近似，默认 15 kHz。真实双单元声学贡献、相位、交叉带和 gain0/gain1 含义并未全部确定；需要明确标识假定关系与不确定频段。
- 未知固件的候选指针扫描只证明“结构可能相像”；没有产品/运行时代码指纹/适配器语义验证之前不可自动授予写入权限。自定义映射要有明确的风险标识与完整验证。

## 6. 数据与历史输入交接（重要：多数不在 GitHub）

| 资源 | 用途与位置 |
| --- | --- |
| `sAnN-16128-1_all_112_0` | 官方 112（软件版本 1.1.2），**不在公开 repo** |
| `sAnN-16128-1_all_116_0` | 官方 116（软件版本 1.1.6），**不在公开 repo** |
| `1_all_113_0_oppo_enco_x4_06EC10(6).bin`、`1_all_101_0_oppo_enco_x4_06EC10(7).bin` | 第三方修改样本，来自原始 RAR；需要拥有者单独提供 |
| “112 替换丹拿高解析为 Optimized AZ80”验证固件 | 另一份回归输入，哈希/差分在 `docs/modified-sample-validation.json`；二进制需单独传递 |
| `欢律_全能版17.6.5-深色模式.apk` | 研究名称/协议，第三方修改 APK；不应成为软件运行依赖 |
| `Flowmix-Beta-5-10.apk` | 接口和拟合流程的研究样本；不应成为软件运行依赖 |
| `www.realab.com_Archive [26-10-07 08-25-02].har` | ReaLab 原始频响 HTML/响应，可提取多条实测；不得公开带 Cookie/请求头的原 HAR |
| `Technics-AZ80.txt` | Wavelet GraphicEQ，127 点，已在 `research/enco_x4_eq_toolkit/examples/` |
| `Technics-AZ80-Optimized.txt` | Flowmix RAW + 5 PEQ，已在同一 examples 目录 |
| `src/heytap_eq/data/enco-x4-reference.json` | 经原作者授权公开的参考参数模板，已随程序打包，不含完整固件 |
| `research/archives/*.zip` | 旧研究的结构证据、测量 CSV、图、脚本、部分反汇编；已在 repo |

**不要承诺能从 GitHub clone 自动获得原厂固件、完整 APK 或 HAR。** 需要真实固件回归时由原作者私下、按许可条件提供；公开 CI 继续只使用合成夹具及获准公开的参考参数。HAR/接口日志应剥离认证、Cookie 和个人信息。

## 7. 优化优先级（建议按 PR 拆分）

### P0 — 接手与安全基线（先于大规模 UI 重做）

1. Fork 时连工作分支一起复制；记录 baseline SHA 和截图；复现 53 项 Python 测试、前端数学/浏览器交互、Qt WebChannel 和现有 Windows 成品。
2. 修改 CI 触发器，使 fork 的新 feature 分支/PR 更新可持续验证；独立保留两种打包及冻结启动，必要时将耗时较长的全量打包设为手动/指定事件。加前端锁文件，减少 npm 安装漂移。
3. 核查现有 **公共仓库内置测量认证**（`service_profile.py`）的公开传播风险：fork 会复制该代码；不要在日志、报告、截图、issue、PR diff 中输出令牌。原作者许可内置共享令牌的事实≠第三方 API 提供商永久允许再分发。推荐设计外部凭据覆盖、轮换和无网降级；要移除或轮换请先与项目所有者协调。
4. 明确“测试通过 ≠ 实机固件刷写安全”。保留只读默认、输出新路径、保存 SHA/差分报告、手动风险确认。

### P1 — 用户体验（本轮最重要）

1. **主界面围绕单一工作流**：原始测量、目标、修正 EQ、实时估计、拟合预设、导出一屏串联。未导入固件时也可完整操作外部 EQ；导入后不让用户迷失在 P1/P2、路径、状态、区域和版本字段里。
2. **图形应始终是主角**：让统一频响图占据剩余空间；优先适配 1028×749 / 1366×768 / 1920×1080；缩放、颜色/图例、曲线显示和悬停值一致。避免面板挤压图形或遮住提示、交互节点。
3. **高级项按需展开**：采样率、内部状态、`other/india`、gain0/1、参考关系、特殊配置、元数据等默认折叠，但必须易找。DAC1/DAC2 同时勾选显示，文案明确“仅控制显示，不改变最终拟合覆盖范围”。
4. **编辑反馈即时可信**：拖动 RAW/PEQ 不冻结，滤波器增益/Q/频率有可编辑数值与合理约束；一次手势一次撤销；同时呈现“原始测量”“当前固件估计”“编辑后估计”“待导出估计”，配合未知参考关系的警示，不再只画数字滤波增益冒充最终声音。
5. **来源/目标选择稳健**：首次空值、已知设备精确匹配、后续记忆；异步来源与目标独立加载；断网退缓存；切换条件不出现幽灵数据或陈旧结果。错误要在对应控件旁可理解地展示。
6. **预设操作清晰**：能选择“拿哪个预设当基底”“要写入哪个预设”，不混淆修正 EQ 与固件已有预设；支持完整调音/固件预设复制粘贴、撤销、计划快照和状态提示。特殊 45 应独立显示，避免误触。

建议先做 **可运行的 UI 原型 + 同尺寸前后截图 + 一份状态流说明**，再重排 `gui.py`。不要为了改皮肤引入另一个完整桌面框架。

### P1 — 保持数值/模型正确性的架构整理

1. `gui.py` 过大，可循序拆为 Qt shell / 数据源视图 / 拟合任务控制 / 固件动作 / 会话状态；`editor.jsx` 拆独立组件但保留同一个 WebChannel schema。
2. 把 Python ↔ React 的事件（`add`、`filter`、drag phase、参考/图形 payload 等）形成版本化契约和边界校验；补 Playwright + Qt 双侧测试，防止“窗口开了但 SVG 为空”。
3. 建立独立对照：当前预设基底、参考固件差分、跨输出/地区、索引 45、其他机型自定义映射、剪贴板跨固件粘贴；保留工程 v1-v4 升级路径和整体增益字段。
4. 不能把“较低拟合 RMS”当作唯一优化目标；保留固定结构、全部采样率稳定性和导出后二次验证。

### P2 — 可选增强（完成 P1 后评估）

- 针对用户掌握的原固件 + 修改版输出 + 实际人工耳重测，建立可追溯的声学估计误差评估；有双单元频响和相位信息时再改进默认功率近似。
- 针对其他 HeyTap 机型抽取固件表描述符，支持验证过的配置适配器和候选扫描，而非复制 Enco X4 的偏移/状态。
- 跟踪启动时间、首次渲染、交互 FPS、拟合耗时、打包体积；按受控对比决定使用 PyInstaller/Nuitka，不因“静态编译”口号重写 Qt/Python 栈。
- 补充许可与依赖归档：repo 当前没有看到根目录 LICENSE；内嵌 DSSSP 的许可文本已随 web 资源生成，新增第三方依赖需记录其许可证。
- 整理 README / 文档“当前”指针：`docs/handoff.md`、`docs/flowmix-api.md` 等含历史阶段的相互矛盾陈述；可保留考古证据，但须明确标注其**不是 2026-10-11 的当前状态**。

## 8. 建议验收标准

- Windows 新用户解压一份 ZIP（保留随包目录）即可启动：不要求 Node、APK、服务端或人工配置本地 Web 服务。离线也能打开软件、导入 EQ 和编辑工程；在线测量断网时有可理解的退化。
- 1028×749 小窗口仍能操作频响主图；至少不低于目前已验证的约 253 px 图形可用高度。需附 1028×749 / 1366×768 / 1920×1080 实际截图；不要只给 React 单页浏览器截图，要测 **Qt 壳 + 内嵌图 + 冻结 EXE**。
- 无固件流程可完成 Wavelet/Flowmix 导入、RAW/PEQ 编辑、在线或离线测量、目标拟合、EQ 导出；有固件流程可选择基底/目标预设、后台拟合、取消、预览、版本编辑、保存工程、导出、重开验证。
- 原有 53 项 Python 测试在 Windows/Linux 不回退；前端数学、浏览器真实鼠标交互、Qt WebChannel、Ruff、冻结 EXE 检查通过。更改协议/数学时补测试。CI 记录构建 SHA、打包器、失败截图及脱敏报告。
- 普通预设修改覆盖实际四表×九状态，特殊配置不误作普通预设；**不覆盖原输入**，未知/未验证固件不得无提示写入；输入与输出的完整性、差分及拟合误差有可复核报告。
- 文案区分“实测 / 数字滤波 / 模型估计”；有跨固件参考假定时显式标注，不能以估计曲线作为已经验证的刷写效果。
- 任何 Token/Cookie/完整 APK/原厂固件/原始 HAR 均不随无必要的截图、日志、公开工件意外扩散。

## 9. Fork 和本地复现

### 推荐 Fork 操作

在 GitHub Web 端 Fork 时取消勾选 **“Copy the DEFAULT branch only”**，以包含工作分支。若已经 fork 且只复制了 `main`，则在本地显式抓取上游分支：

```bash
git clone https://github.com/YOUR_GITHUB_USER/heytap-firmware-eq-studio.git
cd heytap-firmware-eq-studio
git remote add upstream https://github.com/uselessbug/heytap-firmware-eq-studio.git
git fetch upstream feat/python-desktop-studio
git switch -c feat/claude-ux upstream/feat/python-desktop-studio
git push -u origin feat/claude-ux
```

不要在上游 `main` 上直接进行开发，也不要把未验证的 fork 结果自动合并到原作者 PR #1。

### 开发环境（Python 3.11 + Node 22）

```bash
cd frontend
npm install --no-audit --no-fund
npm run build
npm test
# 首次安装浏览器：npx playwright install chromium
npm run test:browser
cd ..

python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS shell: source .venv/bin/activate
python -m pip install -r requirements-ci.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m ruff check .
python -m pytest
python -m heytap_eq.app
```

Linux 无图形桌面的 GUI 测试需要仓库 workflow 所列的 Qt/X11 依赖和 `xvfb-run`；最终 Windows PyInstaller/Nuitka 包均建议继续由 GitHub Actions 构建、验证，避免各人电脑环境不一致。

### 需要优先阅读的可复核证据

- 最新 Actions：<https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655>
- 当前使用流程：<https://github.com/uselessbug/heytap-firmware-eq-studio/blob/feat/python-desktop-studio/docs/unified-workspace.md>
- 固件结构：<https://github.com/uselessbug/heytap-firmware-eq-studio/blob/feat/python-desktop-studio/docs/firmware-format.md>
- 真实拟合验证：<https://github.com/uselessbug/heytap-firmware-eq-studio/blob/feat/python-desktop-studio/docs/firmware-real-fit-validation.json>
- 特殊第三方样本验证：<https://github.com/uselessbug/heytap-firmware-eq-studio/blob/feat/python-desktop-studio/docs/modified-sample-validation.json>
- 历史研究与完整交接：<https://github.com/uselessbug/heytap-firmware-eq-studio/blob/feat/python-desktop-studio/docs/handoff.md>

## 10. 给 Claude Code 的首轮任务提示词（可直接粘贴）

> 我正在维护 fork 自 `uselessbug/heytap-firmware-eq-studio` 的项目。请先阅读仓库根目录的 `CLAUDE.md`（如有）、本交接文档、`docs/unified-workspace.md`、`.github/workflows/desktop.yml` 和当前代码。基线为 `feat/python-desktop-studio` 的 `e62d5d161d4436146ec9cd71b653c7050ba32214`，不是默认 main。
>
> **第一阶段只做调查和可验证的 UI/UX 方案，不要直接重写固件核心。** 请梳理 Python/Qt/React 的交互边界，运行/测试当前桌面程序，输出主工作流、关键交互问题、实际桌面截图与分阶段重构计划。重点是主图空间、原始测量/目标/修正/预设/导出的一体化流程、DAC1/2 显示逻辑、特殊 index 45 的隔离、高级参数折叠、异步进度和模型估计标签。
>
> 第二阶段请按可审查的小提交落地 UI 改善，每次保持 Qt WebChannel 契约、工程 v4、全局撤销、完整预设复制粘贴、两种 Windows 打包和现有测试。任何声学模型或固件写入变化必须提供数值回归和输出验证，不得用未经证实的假设冒充实测。CI 不通过则不交付。每项工作说明改了什么、截图对比、测试结果、剩余风险；只提交到 fork 的新分支，未经确认不要合并上游 main。

---

**授权与边界**：本交接仅总结远端仓库、已有文档、CI 和项目方已经提出的需求；没有替开发者进行真实耳机刷写或本机 Win11/其他 Windows 版本的最终体验验证。原作者保留对最终合并、第三方数据传播、固件样本使用与测试决策的控制。
