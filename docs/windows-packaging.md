# Windows 双打包

同一次 Actions 从同一源 SHA 生成 PyInstaller 和 Nuitka 两份便携目录。两份功能、Python 3.11、PySide6 6.8.3、NumPy 2.2.6、SciPy 1.15.3 和前端资源相同；共享测量认证继续内置，运行不需要 APK、Node 或另装 Python。

下载 `HeyTapFirmwareEQStudio-windows-pyinstaller-源SHA` 或 `HeyTapFirmwareEQStudio-windows-nuitka-源SHA`。解开产物里的 ZIP，运行文件夹中的 `HeyTapFirmwareEQStudio.exe`，保留完整目录，包括 DLL 和资源。目录内 `SOURCE_SHA.txt` 记录源码，`PACKAGER.txt` 标明构建器。

## 构建和运行时

`.github/workflows/desktop.yml` 先完成 Linux / Windows 检查，再通过 matrix 各自构建和实际启动两个 EXE；最后把两份 ZIP 下载到同一台 Windows runner 做兼容性和耗时对照。

- PyInstaller 6.13.0：onedir，包含 Python 解释器和应用资源。
- Nuitka 4.2：standalone，使用 MSVC 编译 Python 模块，启用 PySide6 插件，四个编译进程，关闭 LTO；编译依赖单独锁在 `requirements-nuitka.txt`。直接双击不会新建终端，命令行启动可连接已有控制台供诊断。
- `scripts/build_desktop.py` 统一成品目录和构建报告；`scripts/check_frozen.py` 启动真实 EXE，检查 Qt / WebChannel、统一曲线、参考配置、工程格式和内置认证。
- `src/heytap_eq/runtime.py` 分别识别 PyInstaller 的 `sys.frozen` 和 Nuitka 模块的 `__compiled__`；两份程序都能读取 EXE 旁的 `_flowmix_profile.json`，环境变量仍可覆写认证。
- 编译缓存由 Actions 保存和恢复。成品探针不继承源码检查用的 `PYTHONFAULTHANDLER`，入口将早期导入异常写进 JSON 报告，方便排查无控制台启动问题。

两份都是随目录分发的独立程序。Nuitka 不会将已编译的 NumPy / SciPy / Qt DLL 自动重新静态编译。当前使用的 [Qt 6.8 WebEngine 不支持静态构建](https://doc.qt.io/qt-6.8/qtwebengine-platform-notes.html)。

## 耗时对照的范围

`scripts/compare_packages.py` 在同一台 runner 上预热各一次，之后交替执行三轮，取中位数，并核对两份程序的数值结果一致。原生数值库线程数固定为 1，避免两台构建机器的性能差异干扰对照。

成品的 `--benchmark --report 路径.json` 使用合成数据，离线运行以下实际应用代码：

| 项目 | 工作量 |
| --- | --- |
| EQ 文本解析与导出 | 1,000 次 RAW + PEQ 往返 |
| 频响计算 | 300 次，1,024 点，三种采样率 |
| 测量拟合 PEQ | 4 次，127 点，2 个滤波器 |
| RAW 拟合固件滤波链 | 使用数值优化器，最多 35 次迭代；保留 HP / LP |

报告还记录完整基准进程耗时、GUI 启动探针耗时、EXE 哈希、成品字节数和编译时间。GUI 探针包含主动等待的 1 秒定时器与窗口退出时间，只能用作预热后端到端启动检查的对照，不代表首帧出现时间。

Nuitka 可能减少 Python 解析、对象操作和优化器回调的开销；数值库自身已经运行原生代码。图形编辑器运行在 QtWebEngine 的 JavaScript 中，本对照不测试帧率，也不代表耳机上实际 DSP 的性能。

最终构建结果和实测数据见 [双打包结果](packaging-results.md)、本轮 `package-comparison` 产物与 [完整验证记录](packaging-validation.json)。
