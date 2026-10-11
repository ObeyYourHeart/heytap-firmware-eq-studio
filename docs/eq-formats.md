# EQ 输入与频响处理

## 两个附件的含义

`Technics-AZ80.txt` 为 Wavelet 的 `GraphicEQ` 文本，包含 127 个频率 / 增益点。它是在 Enco X4 丹拿原声基础上拟合 Technics AZ80 频响的修正曲线。

`Technics-AZ80-Optimized.txt` 为 Flowmix 格式，以前述曲线作为 RAW，再增加五个个人偏好的 PEQ。它不是单纯的另一个 GraphicEQ 文件。

| 输入 | 目标响应构成 | 导入后应保留的内容 |
| --- | --- | --- |
| Wavelet GraphicEQ | 对数频率插值后的增益曲线 | 原始频率、增益和输入来源 |
| Flowmix RAW + PEQ | RAW 增益加所有启用 PEQ 的频率响应 | RAW、滤波器类型 / 参数 / ID 和选择信息 |

Flowmix 文件中的 `RAW_BANDS`、`PEQ_COUNT` 需要与实际条目一致；PEQ ID 可以不连续。`SELECTED_PEQ` 表示编辑器选择，不应误当作仅应用该滤波器。

APK 中观察到的 PEQ 类型为 PEAK、LS、HS、LP、HP、NOTCH、BAND_PASS、ALL_PASS。没有显式类型的三参数 PEQ 行按 Peak 解释。解析应拒绝重复 ID、数量声明不匹配以及无效频率 / Q / 数值。

## 第三方 EQ 与测量文本

“导入 EQ”和“粘贴调音”还接受 Equalizer APO 参数文本，即 AutoEQ 的 `ParametricEQ.txt` 与 REW “Generic” 滤波器导出：

| APO 类型 | 导入为 | 要求 |
| --- | --- | --- |
| PK / PEQ / Modal | PEAK | Fc、Gain、Q |
| LSC / LS、HSC / HS | LS、HS | Fc、Gain、Q；Fc 为 RBJ 中点频率，与固件系数公式一致 |
| LP / LPQ、HP / HPQ、BP、NO、AP | LP、HP、BAND_PASS、NOTCH、ALL_PASS | Fc、Q；不接受非零 Gain |

- `OFF` 滤波器保留为未启用；REW 的 `OFF None` 空槽和文件开头说明被跳过。APO 不解释滤波器编号，导入后按文件顺序编号。
- `Preamp` 只记录为元数据 `PREAMP_DB`，不计入修正形状；固件整体电平仍由预衰减控制设置。
- 省略 Q、斜率写法（`LSC 10.8 dB`）、拐角频率搁架（`LS 6dB` / `LS 12dB`）、`BW Oct` 以及 Channel / Include / Copy 等路由命令语义不同，直接拒绝，避免多声道配置被静默合并。
- 只含 PEAK / LS / HS 的 AutoEQ 参数在槽位足够时与固件逐级串接，不经优化器（与已有 Flowmix PEQ 路径相同）。

测量与目标文件除原 `frequency_hz,spl_db` CSV、JSON、HAR 外，还接受 AutoEQ CSV（`frequency,raw`）、REW / FRD 文本（`*` 注释）、squig.link 两列文本、无表头两列数字；分隔符可为逗号、分号、制表符或空格，非逗号分隔时允许小数逗号。表头可带单位（如 `Freq(Hz)`、`SPL [dB]`）；找不到频率 / SPL 列、行不完整或频率不递增时拒绝。

## 从外部 EQ 到固件

不能将 127 个 GraphicEQ 点直接写入 18 槽的固件结构，也不能把外部滤波器类型编号直接套用到固件。

推荐转换过程：

1. 计算外部目标修正响应，Flowmix 同时叠加 RAW 和 PEQ。
2. 以相同路径和状态的丹拿原声记录为基线，复制完整基线滤波链。
3. 保留已确认的 high-pass / low-pass / all-pass 结构，用固件支持的 Peak / Shelf 拟合修正。
4. 用共享参数检查 44.1 / 48 / 96 kHz 下的响应、稳定性、容量和增益。
5. 对所有 36 条目标记录分别验证，再输出拟合误差和预衰减结果。

直接在“清亮高音”或“丹拿高解析”的已有响应上叠加输入 EQ，会引入该预设原有的声音差异，偏离用户提供的丹拿原声基准。

GraphicEQ 文本只提供幅度目标；由此拟合固件 IIR 不代表完整复现 Wavelet 的实际滤波实现、相位或平台音频链路。

## 图形编辑

桌面界面应提供：

- 原始 / 当前固件数字响应和导入目标曲线；
- 可拖动 RAW 节点与 PEQ 控制点；
- 滤波器表格：类型、频率、增益、Q，以及两个整体增益字段；
- 单条记录编辑与整个预设拟合；
- 容量、稳定性、各采样率拟合误差、预计峰值和预衰减；
- 撤销 / 重做及与输入固件 SHA 绑定的工程保存。

## 测量曲线和声学估计

固件 EQ 响应是数字滤波增益，不是耳机完整声学频响。

用户提供的 ReaLab HAR 中，HTML 初始数据含 Enco X4 的五条频响：丹拿原声、丹拿高解析、纯享人声、清亮高音、澎湃低音。每条为 957 个有效点，约 20..19,897 Hz，记录为 B&K 5128、ANC 开启，测量日期 2026-09-15。数据没有给出对应固件版本或内部 ANC 子状态。

可用原始声学测量叠加数字 EQ 修正，展示修改后的估计响应。图例应分别标明“测量”和“估计”。不能把数字 EQ 曲线直接称为人工耳测量，也不能将不同测量装置或归一化方式的曲线无条件比较。

HAR 导入应只取所需 HTML / 频响数值，排除请求头、Cookie 和其他网络凭据。CSV / JSON 导入应验证频率排序、数组长度和有限数值。
