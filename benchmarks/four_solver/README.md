# M12、PyVRP、OR-Tools、Gurobi：0.5 秒与 5 秒对比

在 `vrptw_solver` 目录运行以下 PowerShell 命令。默认是全部 **56 个 Solomon 实例 × 种子 0 × 两档预算 × 四个求解器 = 448 次**，串行搜索约 20 分钟，另有建模和记录开销。

本轮已完成主实验 448 次、固定上限距离诊断 112 次、阶段诊断 8 次；530 份完整可行解独立重验，13 项基准测试通过。结果见 [中文报告](../FOUR_SOLVER_REPORT.md)、[逐实例四方表](results/per_instance_wide.csv)、[固定上限对照](distance/comparison.csv)、[阶段表](diagnostics/phases.csv)。`archive/` 保存主实验的输入、源码/二进制快照和全部逐次路线，`verification.json` 保存交付收据。原始中断批次没有混入这些结果。

## 一条命令运行并生成表格

```powershell
.\benchmarks\four_solver\run.ps1 -Out runs/four_solver_repeat
```

脚本运行、独立重验全部路线，随后生成 CSV 和 Markdown 表格。同一命令、同一输出目录可续跑已中断的实验，已有记录不会覆盖。要做全新实验，请换输出目录。输入、源码、PyVRP 构建、种子、预算或求解器集合改变时，脚本拒绝续跑，防止不同协议混表。时限停止受机器负载影响，同种子限时结果不保证逐次相同。

```powershell
# 只检查三个代表实例
.\benchmarks\four_solver\run.ps1 -Out runs/four_solver_smoke_repeat -Instances C101,R101,RC101

# 后续扩展至三个种子，搜索约 1 小时
.\benchmarks\four_solver\run.ps1 -Out runs/four_solver_seed012_repeat -Seeds 0,1,2

# 重新验证已保存的结果并重建表格，不调用四个求解器
.\benchmarks\four_solver\run.ps1 -Out runs/four_solver_seed0_0p5_5s_v2 -VerifyOnly
```

## 分步命令

```powershell
$python = ".\.venv-comparison\Scripts\python.exe"
& $python benchmarks/four_solver/run.py run --seeds 0 --budgets 0.5 5 --out runs/four_solver_repeat
& $python benchmarks/four_solver/run.py verify --out runs/four_solver_repeat
& $python benchmarks/four_solver/analyse.py --run-dir runs/four_solver_repeat
```

`run` 已自动执行最后一次重验；单独的 `verify` 便于日后审计。只生成表格时，`analyse.py` 仅需要 Python 标准库，机器上不必安装四个求解器。

## 环境和依赖

本机已有 `.venv-comparison`，使用 CPython 3.12.4；自研 M12 的 `_native.cp312-win_amd64.pyd` 已构建。Gurobi 13.0.0 使用本机账户的现有许可证；须在持证用户账户运行，不修改或复制许可证。

OR-Tools 9.15.6755 安装在项目隔离环境，未替换全局依赖。本机环境以全局 Python 的系统包为只读依赖来源；重新创建环境可运行：

```powershell
D:\python\python.exe -m venv --system-site-packages .venv-comparison
.\.venv-comparison\Scripts\python.exe -m pip install -r benchmarks/four_solver/requirements.txt
```

自研原生扩展缺失时，请先按主 [README 的 M12 构建说明](../../README.md)构建，或使用兼容的 Python 3.12 解释器。本实验显式要求原生后端，防止无意中把 Python 后端放进 M12 表。

PyVRP 使用旁边的 `../PyVRP-main` 本地源码与已有 CPython 3.13.5 二进制构建，版本 **0.13.0a0**。它与当前线上文档版本可能不同，不会通过 `pip install pyvrp` 悄悄替换。默认路径：

| 参数 | 默认路径（相对于 `vrptw_solver`） |
| --- | --- |
| `--python` | `.venv-comparison/Scripts/python.exe` |
| `--pyvrp-root` | `../PyVRP-main` |
| `--pyvrp-site` | `../PyVRP-main/.venv/Lib/site-packages` |
| `--pyvrp-python` | `../PyVRP-main/.uv-python/cpython-3.13.5-windows-x86_64-none/python.exe` |

换机器时可以显式传入路径；PowerShell 包装器对应参数为 `-Python`、`-PyvrpRoot`、`-PyvrpSite`、`-PyvrpPython`。不同解释器和编译版本会影响表现，版本与扩展哈希保留在原始记录中。

## 数学与计时协议

- 四方读取同一原始 Solomon TXT、原始车辆上限；时间窗与服务时长均为千分位整数，容量和需求保持原整数。直接传入自研解析器生成的矩阵，统一 `round(1000*hypot(dx,dy))`，不让各库重新舍入。
- 完整解按 `(非空车辆数, 距离整数)` 排序。自研采用现有字典序目标；其余三方采用每车固定成本 `B=(N+min(N,cap))*max_arc+1`。完整解最多有 `N+min(N,cap)` 条弧，故 B 严格大于任何可行总距离，标量目标 `B*K+D` 与字典序相同。公开距离不包含 B。
- 均为冷启动，不给任何后端参考路线或自研热启动。此表独立于 M12 历史“预设车辆上限、固定成本 0”的距离基线。
- 自研使用 M12 原生后端、默认 M10 减车及可行 ILS，取消迭代次数上限；PyVRP 使用本地默认 `solve()` ILS 与上述固定成本，**未调用独立的 `minimise_fleet()`**；OR-Tools 使用 RoutingModel 的 `PARALLEL_CHEAPEST_INSERTION + GUIDED_LOCAL_SEARCH`；Gurobi 使用本项目编写的二下标弧 MILP，含时间、载荷与独立 MTZ 子回路约束，单线程、`MIPGap=0`、`MIPGapAbs=0`。
- 0.5/5 秒是求解调用内的搜索预算，包含算法构造初解。PyVRP 的 `MaxRuntime` 在进入 `solve()` 前启动，初始构造与搜索初始化也占预算。Gurobi/OR-Tools 外部建模单独记录。输入读取、矩阵创建、导入、进程启动和报告写入排除在外；四个常驻工作进程轮换调用，始终只有一个后端搜索。
- `preparation_seconds` 为外部建模，`search_seconds` 为实际求解调用，`solver_runtime_seconds` 为库内计时，`postprocessing_seconds` 含适配器提取和清理，`total_seconds` 为整个适配调用加独立验证。时限检查粒度可能导致超出名义预算；表中报告实际时间，不作毫秒级严格速度排名。
- Gurobi 设 `Threads=1, Seed=seed`；OR-Tools 是一次 CP Routing 搜索，使用底层 `Solver.ReSeed`，但它没有 RoutingSearchParameters 的完整随机流种子承诺。单种子只是一轮观察，不是统计稳定性结论。
- 所有返回路线由 `validate_solution` 从客户顺序重新裁决；核对 PyVRP、OR-Tools 内部标量目标以及 Gurobi 提取整数路线的目标与 `B*K+D`。Gurobi 二进制变量有浮点容差，原始 `ObjVal`、路线整数目标和两者差值分别保留；`optimal` 是库在其数值容差下的状态。超时没有完整可行解只记未找到。Gurobi 的 bound/MIP gap 属于标量目标，不是同车数距离 gap。

OR-Tools 的策略与状态见[官方 Routing Options](https://developers.google.com/optimization/routing/routing_options)；Gurobi 的时限可能在完成收尾计算后返回，见[官方 TimeLimit 参数](https://docs.gurobi.com/projects/optimizer/en/current/reference/parameters.html#parameter:TimeLimit)。PyVRP 行为以本地已哈希源码为准。

## 输出与读表

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 协议、输入/源码哈希、机器和解释器信息、执行会话 |
| `inputs/`、`source/` | 原始输入、自研代码/扩展及适配脚本快照 |
| `results/<预算>/<实例>/<后端>-seed-0.json` | 原始路线、独立裁决、实际耗时、完整后端元数据 |
| `runs.csv` | 全部逐次原始记录；可用 Excel 打开 |
| `verification.json` | 重验数量和无效路线数 |
| `tables/report.md` | 自动汇总表和分组表 |
| `tables/report_zh.md` | PowerShell 包装器生成的中文汇总表 |
| `tables/summary.csv` | 各预算的可行率、车数、同车数距离 gap、实际耗时 |
| `tables/per_instance_wide.csv` | 112 行逐实例四方并列表及对 PyVRP 的差距 |
| `tables/group_summary.csv` | C/R/RC 分组 |
| `tables/improvement_0p5_to_5.csv` | 每个后端从 0.5 秒到 5 秒的变化 |
| `tables/gurobi_bounds.csv` | Gurobi 终止状态、下界及 gap |

车辆不同的两份解不计算距离 gap。某后端有缺失解时不填全部实例的车辆总和，也不把不同可行子集的均值直接排名。完整原始数据和路线支持继续追查特定实例。

## 补充诊断命令

主实验完成后再运行，避免与计时实验抢占 CPU。

```powershell
# PyVRP 固定为主实验自研找到的车辆上限；只传上限、不传路线，约 5 分钟
& ..\PyVRP-main\.uv-python\cpython-3.13.5-windows-x86_64-none\python.exe benchmarks/four_solver_distance.py --primary-run runs/four_solver_seed0_0p5_5s_v2 --out runs/four_solver_distance_seed0

# 自研四个代表实例的阶段计时；不参与正式质量排名，约 22 秒
& .\.venv-comparison\Scripts\python.exe benchmarks/four_solver_diagnose.py --out runs/four_solver_diagnostics_seed0

# 把主表、固定上限表、阶段诊断合成一份中文表格报告
& .\.venv-comparison\Scripts\python.exe benchmarks/four_solver_report.py --primary-run runs/four_solver_seed0_0p5_5s_v2 --distance-run runs/four_solver_distance_seed0 --diagnostic-run runs/four_solver_diagnostics_seed0 --out runs/four_solver_seed0_0p5_5s_v2/tables/report_zh.md
```

固定上限补充实验的车辆上限来自各预算自研结果，是条件距离问题，不用于替换主表的原始上限减车比较。`comparison.csv` 保留每例的主实验自研成绩、PyVRP 可行性与同车数 gap。该脚本也支持断点续跑，并在结束时重验所有已保存路线。
