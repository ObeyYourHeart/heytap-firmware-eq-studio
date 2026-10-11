# Windows 双打包验证（2026-10-11）

代码 `e62d5d161d4436146ec9cd71b653c7050ba32214` 的 [Actions](https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655) 全部成功。Linux / Windows 各 53 项检查，无跳过；两个真实 EXE 均通过启动、Qt WebChannel、统一图表、六组参考数据和共享认证检查。两份启动截图的 PNG 字节一致。

[PyInstaller 便携包](https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655/artifacts/11688863706)（230.4 MB） · [Nuitka 便携包](https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655/artifacts/11689701578)（194.9 MB）

## 同一台 Windows runner 的三轮中位数

| 项目 | PyInstaller | Nuitka |
| --- | ---: | ---: |
| GUI 启动探针 | 2.171 s | 1.921 s |
| EQ 文本往返 1,000 次 | 0.359 s | 0.331 s |
| 频响计算 300 次 | 0.190 s | 0.191 s |
| 测量 PEQ 拟合 4 次 | 0.328 s | 0.335 s |
| RAW 拟合单条固件链 | 1.568 s | 1.557 s |
| 完整基准进程 | 3.108 s | 2.750 s |

各预热一次后交替测三轮，数值库固定单线程，计算结果一致。启动探针包含主动等待 1 秒与退出，不是首帧计时。此轮 Nuitka 启动探针耗时减少约 11.5%，文本处理减少约 7.9%，频响与拟合基本持平；两三个百分点的差异不作为性能优劣结论。ZIP 体积减少约 15.4%。

Nuitka 冷构建约 20 分钟，PyInstaller 约 35 秒；它们在不同构建 runner 上执行，构建耗时仅记录本轮成本。Nuitka 编译缓存已成功保存。

图形编辑器继续使用相同 JavaScript 代码；本基准不测试绘图帧率。Qt / NumPy / SciPy DLL 仍随完整便携目录分发。

[原始对照报告](https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655/artifacts/11690360145) · [完整验证记录](packaging-validation.json) · [打包说明](windows-packaging.md)
