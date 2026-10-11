# HeyTap Firmware EQ Studio

面向 OPPO / HeyTap 耳机固件的本地 EQ 编辑项目，首个研究对象为 Enco X4。

当前已支持统一主图、两路输出复选、原版参考差分估算、特殊配置、完整调音／预设复制粘贴和包含元数据的全局撤销。

界面按工作流侧栏（原始频响 → 目标 → 修正 EQ → 固件预设 → 导出）加全高统一频响图组织，支持跟随系统／浅色／深色、高对比度主题和强调色，见 [界面审查与重构记录](docs/ux-review.md)。

当前有 **Python / PySide6 本地桌面工程** 与原 **Python CLI 研究原型**。DSSSP 图形编辑区支持双击添加 PEQ、实时拖点、滚轮调 Q、参数浮窗和鼠标位置缩放，操作见 [统一工作区](docs/unified-workspace.md) 与 [图形编辑](docs/dsssp-editor.md)。桌面支持固件校验、Wavelet / Flowmix EQ 编辑、RAW / PEQ 拖动、通用频响拟合，以及已确认 Enco X4 的完整预设替换、元数据/版本修改和重新封包。工程 v4 保存测量、目标、固件拟合计划和元数据修改，支持撤销重做与自动恢复。

测量来源动态读取（当前六个，含 Woodenears），可以选在线目标、本地目标或另一副耳机的测量，生成 RAW / PEQ 修正。首次使用选择为空，之后记住上次和各固件的选择；已确认机型可精确匹配测量。独立 HTTP 客户端在运行和构建时均不依赖 APK。深色大图使用稳定的音频坐标，实测 / 目标 / 估计可独立显示。原 CLI 保留固件拟合和重新封包能力。

桌面采用 **Python + PySide6 + 内嵌 DSSSP / React 编辑器**，核心独立于 Qt；网页资源随程序打包，使用者不需要 Node 或运行浏览器服务器。源码和 Windows 便携 ZIP 由 GitHub Actions 检查/构建；每个产物记录源 SHA，并实际启动冻结 EXE。桌面固件导出和版本同步已接入，操作见 [固件编辑](docs/firmware-editing.md)。此前丢失源码阶段的测试报告未作为当前实现的验证。

## 桌面启动

```bash
cd frontend
npm install
npm run build
cd ..
python -m venv .venv
# Windows: .venv\Scripts\activate；Linux: source .venv/bin/activate
python -m pip install -r requirements-ci.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m heytap_eq.app
```

Windows 可下载同一源码的 [PyInstaller 便携包（230 MB）](https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655/artifacts/11688863706) 或 [Nuitka 便携包（195 MB）](https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655/artifacts/11689701578)，解开产物内的 ZIP，保留完整目录并运行 EXE。用户已在 `b23c92f` 内置共享认证，新成品自动连接测量服务，在原始频响区域直接选择来源／品牌／型号，无需 APK 或 Action Secret；配置文件/环境变量仍可覆写默认值。详细操作见 [本地验证步骤](docs/local-checks.md)，测量和拟合见 [说明](docs/measurement-fitting.md)，两种构建和实测比较见 [双打包结果](docs/packaging-results.md)。

## 接手开发

请先阅读 [交接文档](docs/handoff.md) 和 [恢复文件清单](research/recovery-manifest.json)。

- [EQ CLI 与说明](research/enco_x4_eq_toolkit/README.txt)
- [OPKG 结构工具](research/enco_x4_structure/README.txt)
- [全部恢复源码](research)
- [完整 EQ 工具包](research/archives/enco_x4_eq_toolkit.zip)
- [完整结构工具包](research/archives/enco_x4_structure_toolkit.zip)

## 快速检查

```bash
cd research/enco_x4_eq_toolkit
python -m pip install -r requirements.txt
python eq_tool.py mapping
python eq_tool.py inspect examples/Technics-AZ80-Optimized.txt
```

计划生成、应用、原输入要求和验证命令见 CLI 的 README。原厂固件未包含在仓库中。当前 CLI 不修改版本号，只支持附件所用的连续编号 Peak PEQ。

## 当前桌面验证

当前代码 `e62d5d161d4436146ec9cd71b653c7050ba32214` 的 [Actions](https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655) 全部成功：Linux / Windows 各 53 项 Python 检查，无跳过；Ruff、3 项前端坐标检查、Chromium 实际鼠标交互、Qt WebChannel 编辑，以及 PyInstaller / Nuitka 两个真实 EXE 启动均通过。两份成品截图字节一致，三条 SVG 曲线共用主图，小屏图形高度 253 px。报告确认六组内置参考配置、工程 v4、共享认证可读取，APK 依赖为 false。

两份 EXE 已在同一台 Windows runner 上预热后交替测三轮，计算结果一致。Nuitka 的 ZIP 约小 15%，启动探针从 2.17 s 降到 1.92 s，频响计算与拟合基本持平。原生数值库与 JavaScript 图形编辑器保持相同。见 [本轮验证](docs/packaging-validation.json)、[实测范围](docs/packaging-results.md) 和 [使用说明](docs/unified-workspace.md)；[统一工作区阶段记录](docs/unified-validation.json) 保留原始验证范围。

GUI 的“完整预设拟合 → 版本修改 → 导出 → 重开 → 工程恢复”已通过；getter 000..999 的对齐/未对齐写入独立仿真 2,000 次。主功能和真实输入验证的原始来源提交为 `8878dfd`，见 [记录](docs/firmware-edit-validation.json)；[此前测量阶段记录](docs/desktop-validation.json) 保留其原始范围。实际测量配置已验证六个来源和 21 条目标。

真实原始 112/116 的 Optimized EQ 完整预设替换也已通过：各 36 条记录，默认误差门限内，重新封包与重开识别通过。读取导出文件后在 8,192 点、三采样率复算最差 RMS 0.128 dB、最大误差 0.851 dB（有效频段）；详见 [真实输入拟合记录](docs/firmware-real-fit-validation.json)。

## 恢复 CLI 的历史验证

恢复后重新跑通两个脚本：112 / 116 各修改 72 条记录并验证重新封包；四个固件样本共 135 个块通过完整性、字节一致回包、指针和隔离修改检查。新输出保持原版本，尚未实机刷写。

- [当前可重跑验证](docs/recovered-validation.json)
- [前一阶段桌面实现的历史记录](docs/local-validation.json)（源码未恢复，不能当作当前 CI 结果）

## 研究文档

- [固件结构与名称映射](docs/firmware-format.md)
- [EQ 格式与频响](docs/eq-formats.md)
- [Flowmix 测量接口](docs/flowmix-api.md)
- [相关开源项目](docs/related-projects.md)

后续需要更多机型的参数语义适配与实机 OTA/声学验证。当前固件编辑通过完整代码指纹和四表识别，不以整包哈希作为唯一条件，因此支持已修改 EQ/版本的已确认布局。原厂固件和完整 APK / HAR 不提交到本公开仓库；用户已自行提交 Flowmix 共享测量认证，后续修复保留该默认值。


