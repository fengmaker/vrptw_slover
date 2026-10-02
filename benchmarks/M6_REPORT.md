# M6 对照与分阶段诊断验收

M6 已完成。0.5 秒、56 实例、种子 0/1/2 的普通批次和诊断批次各有 **168/168** 个自研结果通过输出路线独立重算；PyVRP 重跑 **162/168** 次找到可行解，162 条保存路线也再次通过本项目验证。数值规则保持 `solomon_exact_1000_v1`。关闭诊断时，56 个三种子最好目标值与 M5 逐实例相同。下一阶段按这些诊断数据调整搜索预算。

## 实验协议与证据

全部运行读取同一批 `data/*.txt`。自研使用原始车辆上限，PyVRP 使用冻结的 [caps.json](pyvrp/caps.json)，沿用 M5 固定上限距离基线，只调用 PyVRP `solve()`。双方核对原始 TXT 哈希；PyVRP 在求解前逐例核对转换输入的客户字段、容量、时间窗、服务时长以及完整距离/时间矩阵。每次自研预算为 0.5 秒、每目标车数最多 100 次减车试探、每 ILS 候选最多 2 次局部移动，其余完整配置保存于 JSON。三个批次顺序运行；文件与图片写入在求解预算之外。

| 证据 | 文件 |
| --- | --- |
| 普通批次 168 次结果、实际耗时、输入/代码哈希和配置 | [control_runs.csv](m6/control_runs.csv)、[control_summary.json](m6/control_summary.json) |
| 诊断批次 168 次结果、1344 条阶段记录和完整配置 | [diagnostic_runs.csv](m6/diagnostic_runs.csv)、[diagnostic_phases.csv](m6/diagnostic_phases.csv)、[diagnostic_summary.json](m6/diagnostic_summary.json) |
| 按实例汇总的阶段耗时、评估率及拒绝数 | [phase_summary.csv](m6/phase_summary.csv) |
| 双方保存路线重验、计数/计时核对 | [verification.json](m6/verification.json)、[control_verification.json](m6/control_verification.json) |
| PyVRP 本次 168 行原始结果、56 行最好成绩及环境/哈希 | [0p5s_m6_runs.csv](pyvrp/results/0p5s_m6_runs.csv)、[0p5s_m6.csv](pyvrp/results/0p5s_m6.csv)、[m6_manifest.json](pyvrp/results/m6_manifest.json) |
| 普通批次与本次 PyVRP 逐实例对照 | [0p5s_m6_comparison.csv](pyvrp/results/0p5s_m6_comparison.csv) |
| 固定工作量的诊断开销、计数及配置 | [fixed_work_overhead.csv](m6/fixed_work_overhead.csv)、[fixed_work_phases.csv](m6/fixed_work_phases.csv)、[fixed_work_manifest.json](m6/fixed_work_manifest.json) |
| M5 与 M6 普通批次的逐实例最好目标 | [m5_vs_m6.csv](m6/m5_vs_m6.csv) |

完整路线、历史和图片位于忽略版本管理的 `runs/m6_control_seed012_0p5s/`、`runs/m6_diagnostics_seed012_0p5s/`；PyVRP 可行路线在 `runs/m6_pyvrp_seed012_0p5s/solutions/0p5s/`。快照均由这些实跑文件产生，历史 M5/PyVRP 基线保留。另有 [diagnostic_vs_pyvrp.csv](m6/diagnostic_vs_pyvrp.csv)用于观察测量开销。

环境：自研 Python 3.12.4；PyVRP Python 3.13.5、版本 0.13.0a0；Windows 11 10.0.22621、AMD64 Family 25 Model 68、16 个逻辑 CPU，求解单线程。2026-10-01 北京时间，普通批次 13:17:01～13:19:05、诊断批次 13:19:21～13:21:25、PyVRP 13:22:12～13:23:40；原始 UTC 时间见 JSON。

自研源码 SHA-256：`3db77f4aec675bd9467937da2dc168bbd3d4632dc8e14fbee006ca51dd7fbfc9`。PyVRP Python/C++/本地扩展文件整体 SHA-256：`23d876b8c4ea6e2e49837541d4c9ec474d333fdd9358a491f9dc02206f4ece90`。每条自研 JSON 保存完整配置、原始车辆上限和减车里程碑；PyVRP 调用清单保存冻结上限、脚本和验证器哈希。

## 正确性、预算与质量

| 检查 | 普通批次 | 诊断批次 |
| --- | ---: | ---: |
| 成功并从输出路线再次独立验证 | 168/168 | 168/168 |
| 首次可行耗时中位数 / 最大值 | 0.081261 / 0.220379 秒 | 0.092021 / 0.235487 秒 |
| 求解耗时中位数 / 最大值 | 0.511834 / 0.616250 秒 | 0.516894 / 0.659108 秒 |
| 进入 ILS 的运行数 | 46/168 | 46/168 |
| 总 ILS 迭代数 | 88 | 87 |

本机 Python 3.12 的原 `monotonic()` 使用 `GetTickCount64`，分辨率为 15.625 毫秒。M6 统一用 `perf_counter()` 的高精度单调时钟测量阶段、实际运行时间和截止时间；本机实现为 `QueryPerformanceCounter`，报告分辨率 0.1 微秒。低层接口显式传入 `deadline`/`started_at` 时，使用 `perf_counter()` 的绝对时刻。

当前减车在试探之间检查截止时间，一次正在执行的修复可能跨过截止时间，故实际耗时全部记录。M7 可依据这些数据调整预算分配和截止检查。PyVRP `solve()` 墙钟耗时中位数 / 最大值为 0.508023 / 0.535444 秒，`MaxRuntime` 计时边界与自研不同。未找到可行解的六次为 R101/1、R103/0、R103/1、R111/2、RC101/2、RC105/1，均只说明此次预算内未找到。

每实例分别取共同种子 0/1/2 中按 `(车辆数, 距离整数)` 最好的可行解：3 例 PyVRP 用车更少，52 例同车数但 PyVRP 距离更短，C101 打平。53 个同车数实例的距离 gap 中位数为 **27.5899%**，90 分位为 **86.8496%**。90 分位采用最近秩，即排序后取 `ceil(0.9 × N)` 位；只有车数相同才纳入距离统计。

| 组别 | 同车数实例数 | 距离 gap 中位数 |
| --- | ---: | ---: |
| C | 17 | 25.8284% |
| R | 21 | 27.6058% |
| RC | 15 | 29.5395% |

本次车辆差距为 R109 的 14 对 12、R112 的 12 对 10、RC107 的 14 对 13。PyVRP 事先获得冻结上限，其显式减车能力实验仍按 M10 另设。普通批次的 56 个最好目标值全部与 M5 相同。

## 三类代表实例的阶段解释

下表列出诊断开启时三个种子的**平均自身耗时**及其占全部求解耗时的比例。减车与扰动中的插入耗时单列为修复；包含子阶段的耗时另存 `inclusive_seconds`。入口验证、ILS 控制和其余开销也有单独阶段，八阶段自身耗时之和逐次等于求解耗时。

| 实例 | 阶段 | 平均耗时（秒） | 占比 | 三种子工作量合计 |
| --- | --- | ---: | ---: | ---: |
| C103 | 构造 | 0.088652 | 17.71% | 17,145 个位置 |
| C103 | 减车自身 | 0.000190 | 0.04% | 0 次试探 |
| C103 | 扰动自身 | 0.000279 | 0.06% | 11 次扰动 |
| C103 | 修复 | 0.021197 | 4.24% | 3,565 个位置 |
| C103 | 局部搜索 | 0.388860 | 77.70% | 19,846 个移动 |
| R101 | 构造 | 0.060856 | 11.47% | 20,262 个位置 |
| R101 | 减车自身 | 0.030026 | 5.66% | 45 次试探 |
| R101 | 扰动自身 | 0 | 0% | 0 次扰动 |
| R101 | 修复 | 0.439059 | 82.74% | 158,868 个位置 |
| R101 | 局部搜索 | 0 | 0% | 0 个移动 |
| RC101 | 构造 | 0.069807 | 13.67% | 19,404 个位置 |
| RC101 | 减车自身 | 0.018385 | 3.60% | 34 次试探 |
| RC101 | 扰动自身 | 0 | 0% | 0 次扰动 |
| RC101 | 修复 | 0.421815 | 82.59% | 128,636 个位置 |
| RC101 | 局部搜索 | 0 | 0% | 0 个移动 |

构造/修复候选是实际重算的插入位置，局部搜索候选是一个移动。源路线、候选路线及完整验证中的路线重算另计 `route_evaluations`。容量预筛选按客户/路线对记录，未重算的位置不算评估。每候选可违反多个约束，各原因至多记一次，原因计数不能相加当作拒绝总数。扰动用调用数和实际移除数表示，减车用试探数表示，其插入候选归入修复。

**C103 的主要瓶颈是固定车数局部搜索。** 构造直接得到 10 车容量下界，三个种子均不试探减车。ILS 分别运行 2、5、4 轮，扰动实际移除 59 个客户，修复执行 55 次插入，1 轮修复未完成。局部搜索共评估 19,846 个移动、重算 76,586 条路线、接受 17 次改进，约 17,012 候选/秒。19,198 个移动触发时间窗拒绝（96.73%），9,736 个触发容量拒绝（原因可重叠）。最好 10 车、1809.960，对 PyVRP 10 车、828.065 的距离 gap 为 118.5770%。M8/M9 应实验候选筛选、增量评估和搜索深度。

**R101 的减车修复用尽时间，距离搜索未开始。** 初始构造为 23 车；三个种子分别运行 16、16、13 次试探，得到 22、21、22 车，以时间限制停止。减车包含修复的平均耗时为 0.469085 秒。158,868 个插入位置中 143,498 个因时间窗拒绝（90.33%），45 次修复中 41 次失败；ILS 迭代和局部移动均为 0。最好 21 车、1948.827，对 PyVRP 同为 21 车、1666.366 的距离 gap 为 16.9507%。M7 的预算保留与修复顺序实验有直接依据。

**RC101 同样由减车修复主导。** 初始构造为 20 车；诊断下分别试探 11、11、12 次，得到 18、18、19 车，ILS 均未开始。减车包含修复平均耗时为 0.440199 秒；128,636 个位置中 114,920 个因时间窗拒绝（89.34%），34 次修复中 29 次失败，容量预筛选只跳过 42 个客户/路线对。普通批次种子 0 能找到 17 车、2197.881，诊断开启后停在 18 车，测量成本会影响限时搜索量。M7 先检验修复成功率与预算分配。

## 测量开销与回归测试

固定工作量使用种子 0、1 次 ILS、每目标车数 10 次试探、每候选 1 次局部移动。每实例预热关闭/开启两条路径，再交替顺序测量三对。**9/9 对的路线、车辆/距离整数和减车试探相同**。固定候选数/路线重算数为 C103 的 7,930/13,303、R101 的 41,304/43,476、RC101 的 81,807/82,856。

| 实例 | 三次成对耗时增加比例的中位数 |
| --- | ---: |
| C103 | 11.51% |
| R101 | 16.71% |
| RC101 | 14.22% |

这是本机三次测量的观察值。诊断默认关闭；质量晋级继续使用关闭诊断的同预算批次。开启诊断时，更新计数和拒绝原因集的成本计入对应阶段。

`python -m pytest -q -p no:cacheprovider --basetemp=tests/.pytest-local`：**112 passed**。新增测试覆盖独立时钟下的嵌套计时、容量预筛选与位置评估、移动枚举及路线重算数、时间窗/空路线拒绝、接受数、固定工作量的开关一致性、零预算阶段、CSV/JSON 输出，以及比较工具的预算和车辆上限检查。全量实验另核对 336 个自研输出的完整路线、输入/代码哈希、配置、预算、原始车辆上限；诊断计数与 CSV/JSON 一致，耗时逐次守恒。PyVRP 保存的全部 162 条可行路线也独立重算。

## 重跑命令

从 `vrptw_solver` 目录执行，使用新的输出目录保留快照：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
& '.\.venv\Scripts\python.exe' -m vrptw batch data --seeds 0 1 2 --time-limit 0.5 --out runs/m6_control_replay
& '.\.venv\Scripts\python.exe' -m vrptw batch data --seeds 0 1 2 --time-limit 0.5 --diagnostics --out runs/m6_diagnostics_replay
& '.\.venv\Scripts\python.exe' benchmarks/diagnose.py fixed-work --out runs/m6_fixed_work_replay
```

本地 PyVRP 的 `pyvenv.cfg` 仍指向搬迁前路径。本次使用当前目录已存在的 Python 3.13 和原环境依赖目录启动；命令执行后恢复 `PYTHONPATH`：

```powershell
$m6PreviousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (Resolve-Path '..\PyVRP-main\.venv\Lib\site-packages').Path
    & '..\PyVRP-main\.uv-python\cpython-3.13.5-windows-x86_64-none\python.exe' benchmarks/pyvrp/run.py --budgets 0.5 --seeds 0 1 2 --out-dir runs/m6_pyvrp_replay
} finally {
    $env:PYTHONPATH = $m6PreviousPythonPath
}
```

从双方保存路线重验，并要求比较表每个实例/种子都有基准记录：

```powershell
& '.\.venv\Scripts\python.exe' benchmarks/diagnose.py summarise --batch runs/m6_control_replay --out runs/m6_control_replay/analysis
& '.\.venv\Scripts\python.exe' benchmarks/diagnose.py summarise --batch runs/m6_diagnostics_replay --pyvrp runs/m6_pyvrp_replay/0p5s_runs.csv --out runs/m6_diagnostics_replay/analysis
& '.\.venv\Scripts\python.exe' benchmarks/pyvrp/compare.py --ours runs/m6_control_replay/batch_summary.csv --pyvrp runs/m6_pyvrp_replay/0p5s_runs.csv --budget 0.5 --require-complete --out runs/m6_control_replay/vs_pyvrp.csv
```

固定工作量检查确定性与计数开销；限时实验的搜索量受机器状态影响。后续实验沿用冻结输入、种子、数值合同和快照记录，按计划书单项消融。
