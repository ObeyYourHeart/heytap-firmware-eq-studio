# DSSSP 图形编辑区

原始频响和目标频响卡片直接提供在线来源、品牌、型号与目标库；选择后自动载入。
工程管理和 EQ 导出位于“工程”菜单，版本编辑和配置映射位于“固件详情”。
参数表默认收起，通过“参数表”展开。

唯一“调音”主图统一显示声学与数字曲线；DAC1／DAC2 可同时勾选。
内部状态、区域、采样率和估算模型在“计算详情”中，不控制拟合的完整写入范围。

- 双击空白图形：在鼠标频率处添加 PEQ；增益按该处现有曲线计算增量。
- 拖动 PEQ 圆点（强调色）：频率和增益实时预览；松手后记录一次撤销。
- 在 PEQ 圆点上滚轮：调 Q；右键：打开类型、频率、增益、Q、启用和删除面板。
- 黄色小点：只编辑 RAW 增益，维持原采样频率。
- 空白处滚轮：围绕鼠标位置缩放频率；Shift＋滚轮：纵向缩放。
- 拖动空白处：平移；「复位视图」恢复 20 Hz—20 kHz 和所选 dB 范围。
- 普通编辑和曲线显隐不复位视野；切换测量和声压刻度也保留视野。

Python 计算修正与固件滤波器响应。DSSSP 0.8.0 负责 SVG 绘图与交互，
没有使用其内置滤波器公式代替 Python DSP。传入的非等距测量曲线会按当前
对数频率视野重新插值；坐标标签随视野重新生成。空工程没有示例曲线。

主图统一叠加实测、当前固件估计、编辑后估计、目标、修正 EQ 与 DAC1/DAC2；两路可独立勾选，并切换完整滤波链／相对参考差分。绝对 SPL 通过同一 dB 比例的右轴显示，不改变 EQ 的零增益基准。

绑定参考固件后，Python 比较复数传递变化；两路不同则使用明确标注的双单元功率近似，并显示交叠频段。橙色虚线来自实际待写入参数。详见 [统一工作区](unified-workspace.md)。

## 构建与检查

Windows 便携包已经包含网页资源和 Qt WebEngine，使用者不需要 Node 或浏览器服务器。
源码开发先在 `frontend` 运行 `npm install`、`npm run build`，再启动 Python。
Actions 自动构建资源，再执行 Node 数学检查、Linux Chromium 鼠标交互检查、
Linux/Windows Python 与真实 Qt WebChannel 检查，最后启动实际冻结 Windows EXE。

打包使用 `--collect-data heytap_eq`；启动检查要求网页握手完成并实际生成 SVG 曲线，
不能只凭窗口打开判定成功。截图与 JSON 检查报告随 Actions 产物保存。

前端依赖为 DSSSP（MIT）、React/React DOM（MIT）；构建时保留依赖许可说明，
并将 DSSSP LICENSE 随本地资源一起打包。引入 Qt WebEngine 后便携包会比原版大。

源码：`frontend/src/editor.jsx`、`frontend/src/math.mjs`、`src/heytap_eq/web_plot.py`。
