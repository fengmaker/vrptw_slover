# PyVRP 基准实验说明书（Solomon VRPTW）

此基线只供算法实验比较；`vrptw` 求解和验证本身不依赖 PyVRP。
本地 PyVRP 源码在相邻的 `PyVRP-main/`，版本由其 `pyproject.toml` 记录。
原始逐次结果按时间预算保存在 [`results/`](results/)；每例一行的 PyVRP 成绩见
[`0p5s.csv`](results/0p5s.csv) 和 [`5s.csv`](results/5s.csv)，
与自研 M5 的论文式 0.5 秒对照见
[`0p5s_m5_comparison.csv`](results/0p5s_m5_comparison.csv)。
`benchmarks/` 顶层只保留历史 M5 报告；本目录收纳 PyVRP 对照脚本、车辆上限快照和结果。

## 文件分别做什么

| 文件 | 作用 | 由谁更新 |
| --- | --- | --- |
| `README.md` | 本说明书：运行命令、数据口径和字段解释 | 人工维护 |
| [`caps.json`](caps.json) | 56 例的 PyVRP 可用车辆上限 `vehicle_cap`、上限来源 `source`，以及自研 M5 的最好车辆数和距离快照 | 实验配置；运行脚本只读取 |
| [`run.py`](run.py) | 运行本地 PyVRP；核对输入、独立验证结果、按预算保存记录并生成汇总 | 运行时写入 `results/` |
| [`compare.py`](compare.py) | 将自研 `batch_summary.csv` 与**相同预算、共同种子及车辆上限**的 PyVRP 原始记录比较，输出逐实例车辆数优先的 gap 表 | 仅在提供 `--out` 时写对照表 |
| `results/0p5s_runs.csv`、`5s_runs.csv`、未来的 `60s_runs.csv` | 原始记录：每个实例、种子、时间预算、车辆上限各一行；含状态、实际车数、距离、耗时和校验结果 | `run.py` 追加，供续跑及 `compare.py` 使用 |
| [`results/0p5s.csv`](results/0p5s.csv)、[`results/5s.csv`](results/5s.csv)、未来的 `results/60s.csv` | 阅读用成绩表：当前每实例一行，在已有种子中按实际车辆数、距离依次选最好可行解 | `run.py` 在相应预算完成后重建 |
| [`results/0p5s_m5_comparison.csv`](results/0p5s_m5_comparison.csv) | 已保存的自研 M5 与 PyVRP 的 0.5 秒论文式对照；先比车辆数，仅同车数时算距离 gap | 过去用 `compare.py` 生成的历史快照 |

文件名中的 `0p5s` 表示 0.5 秒。旧的混合 `baseline.csv` 已拆分并删除；编辑器中若还开着它，那是失效的旧标签页。运行 `run.py` 不会自动重算自研求解器，也不会生成新的双方 gap 表；双方对照由 `compare.py` 单独生成。

## `local_bks` 和 `m5_best_of_seeds_0_1_2` 是什么

`caps.json` 中的 `source` 会原样写到原始记录的 `cap_source` 列及汇总表中。它说明**给 PyVRP 设置的最大可用车辆数从哪里来**，不是 PyVRP 的实际用车数，也不是求解结果的来源。

| `cap_source` | 覆盖实例 | `vehicle_cap` 的取法 |
| --- | --- | --- |
| `local_bks` | 17 个 C 类实例 | 取相邻项目 [`PyVRP-main/data/solomon_bks.json`](../../../PyVRP-main/data/solomon_bks.json) 的 `vehicles`。例如 C101 为 10，C201 为 3。`local_bks` 是本地参考文件的标签，不表示本实验已经证明其为全局最优。 |
| `m5_best_of_seeds_0_1_2` | 其余 39 个 R/RC 类实例 | 本地参考文件没有这些实例，便取自研 M5 的 0.5 秒、种子 0/1/2 中最好的可行解所用车辆数。例如 R103 为 14。 |
| `batch_sha256:<前12位>` | 以后用 `--caps-from-batch` 补跑的新上限 | 根据指定自研批量结果更新对应车辆上限；哈希前缀标记那份批量文件。旧上限的原始记录仍保留。 |

例如，R103 的 `vehicle_cap=14`、`cap_source=m5_best_of_seeds_0_1_2` 表示 PyVRP **最多可用 14 辆车**，因为自研 M5 曾以 14 辆得到可行解。它不表示 PyVRP 已找到 14 车解：当前 5 秒、种子 0 的 R103 行是 `not_found`。实际用车数看原始表的 `vehicles` 或汇总表的 `best_vehicles`，只有可行解才有可比较的距离。`caps.json` 里的 `ours_m5_best_vehicles` 和 `ours_m5_best_distance_ticks` 则是当时自研 M5 的快照，**不是** PyVRP 的结果。

## 对照口径

- 使用同一批 56 个原始 Solomon TXT。PyVRP 读取对应的转换版 VRPLIB，
  运行前逐例核对坐标、需求、时间窗、服务时长、车容量，以及完整距离和时间矩阵；
  结果路线再交给本项目 `validate_solution` 独立复核。数值规则为
  `solomon_exact_1000_v1`。
- 本批 Solomon 输入没有车辆固定成本；`run.py` 只调用 PyVRP `solve()`，其可行解
  在这里按距离费用搜索，不会自动调用 PyVRP 单独提供的 `minimise_fleet()`。
  本项目的完整求解流程则先减车，再在所得车数下优化距离。为了比较同车数
  的路线质量，先设置 PyVRP 的可用车辆上限：C 类取本地 17 个参考车辆数，
  R/RC 类取 M5 三种子中最好的车辆数。逐例上限和来源保存在
  [`caps.json`](caps.json)。PyVRP 实际用车数仍单独记录，
  可以小于上限。
- 每次运行给 PyVRP 的 `MaxRuntime` 指定预算，并另记从调用 `solve()` 到返回的
  墙钟耗时。`MaxRuntime` 的计时边界与本项目的计时边界不完全一样；
  0.5 秒档的 `solve_wall_seconds` 通常略高于 0.5 秒。比较结果用于指导实验，
  不把微小耗时差异解释为严格速度排名。
- 时间预算内未找到可行解只记为 `infeasible`，不代表该车辆数下数学上不可行。
  同车数时才计算距离差；若车辆数不同，先按车辆数比较。

对照表在相同预算、相同种子集合内，分别取两个求解器最好的可行解。
`vehicle_gap = ours_vehicles - pyvrp_vehicles`，正数表示自研求解器多用车。
只有 `vehicle_gap = 0` 才计算
`distance_gap = ours_distance - pyvrp_distance` 和
`distance_gap_percent = 100 × distance_gap / pyvrp_distance`；
正数表示自研距离更长。不同车数时距离仍列出供阅读，但两个距离 gap 留空。
表内保留最佳解对应的种子和各自找到可行解的次数。

### 看 CSV 时先看哪些列

| 位置 | 关键列 | 含义 |
| --- | --- | --- |
| `*_runs.csv` 原始表 | `budget_seconds`、`seed`、`vehicle_cap`、`cap_source` | 此次运行的预算、随机种子、最多可用车辆数及上限来源。 |
| `*_runs.csv` 原始表 | `status`、`own_validator_feasible`、`vehicles`、`distance` | 本项目独立验证后的状态、实际使用车辆数和距离。原始 `infeasible` 表示此次限时搜索未找到可行解，不是不可行性证明。 |
| `*_runs.csv` 原始表 | `distance_ticks`、`pyvrp_runtime_seconds`、`solve_wall_seconds` | 千分位整数距离、PyVRP 报告的求解时间、外层测量的 `solve()` 墙钟时间。`distance = distance_ticks / 1000`。 |
| `0p5s.csv` 等成绩表 | `runs`、`feasible_runs`、`best_vehicles`、`best_distance`、`best_seed` | 已运行次数、可行次数及按 `(车辆数, 距离)` 选出的最好可行解。若没有可行解，则 `status=not_found`，最好成绩留空。 |
| `*_comparison.csv` 双方对照 | `ours_vehicles`、`pyvrp_vehicles`、`vehicle_gap`、`distance_gap_percent` | 先按实际车数判断；两者车数相同才计算距离 gap。正 gap 表示自研求解器较差。 |

`input_sha256` 用于确认双方读取的是同一个原始 TXT；它不是成绩。当前每个预算只有一个预设车辆上限，因此成绩表每例一行；若后来为同一实例补跑另一个上限，成绩表会按 `(实例, 车辆上限)` 各保留一行，不能把不同上限的距离直接混合。

## 已完成的 0.5 秒档

运行种子为 `0 1 2`，共 168 次。PyVRP 有 162 次找到并通过独立验证的可行解；
其余 6 次在预算内未找到可行解。现有 M5 批量记录的 168 次均可行。
每例在共同的三个种子里分别取最好结果后，得到：

| 实例组 | 实例数 | PyVRP 车辆数更少 | 同车数 PyVRP 距离更短 | 打平 |
| --- | ---: | ---: | ---: | ---: |
| C | 17 | 0 | 16 | 1 |
| R | 23 | 2 | 21 | 0 |
| RC | 16 | 1 | 15 | 0 |

PyVRP 在 3 例使用更少车辆、在 52 例同车数下距离更短、
在 1 例与当前结果相同。这里的“更好”
只针对上述固定车辆上限和 0.5 秒预算；未来若本项目找到更少车辆，
应新增相应上限的 PyVRP 对照，不能沿用旧上限判断新结果。
PyVRP 事先得到了固定车辆上限，因此“PyVRP 更少车”不能单独解释为
它的减车策略更强；此表首先是固定车辆上限条件下的路线质量基线。
如果新批量实验找到更少车辆，对比脚本会要求先补齐新车辆上限的
PyVRP 记录，避免把不同上限的距离混在一起。

## 已完成的 5 秒档

单种子 `0` 的 56 例结果在 [`5s.csv`](results/5s.csv)，每次运行的记录在
[`5s_runs.csv`](results/5s_runs.csv)。其中 55 例找到并通过独立验证的可行解；
R103 在固定 14 车上限下、这次 5 秒运行中未找到可行解，表内标为 `not_found`。
这张表只记录 PyVRP 成绩：自研求解器尚未运行对应的 5 秒批量实验，因此不能把它当成双方的 5 秒 gap 表。

## 运行 PyVRP 的 0.5、5、60 秒基准

以下三条 PowerShell 命令都在 `vrptw_solver` 和 `PyVRP-main` 的共同父目录 `VRPTW-ALNS-main` 执行。脚本使用 **PyVRP-main 自己的虚拟环境**，所以不需要切换当前 PowerShell 的激活环境。`--budgets` 指**每个实例、每个种子**的求解秒数，不是整个批次的总时长。0.5 秒档用种子 `0 1 2` 与 M5 对齐；5 秒、60 秒档先用种子 `0`。

```powershell
& '.\PyVRP-main\.venv\Scripts\python.exe' '.\vrptw_solver\benchmarks\pyvrp\run.py' --budgets 0.5 --seeds 0 1 2
& '.\PyVRP-main\.venv\Scripts\python.exe' '.\vrptw_solver\benchmarks\pyvrp\run.py' --budgets 5 --seeds 0
& '.\PyVRP-main\.venv\Scripts\python.exe' '.\vrptw_solver\benchmarks\pyvrp\run.py' --budgets 60 --seeds 0
```

脚本按 `(实例, 预算, 种子, 车辆上限)` 跳过已完成记录；中断后重跑同一命令即可续跑。
全部 56 例、单种子 5 秒约需 5 分钟，60 秒约需 1 小时。当前 0.5 秒和 5 秒结果已经存在；再次运行对应命令不会重新求解已记录的组合。60 秒还没有结果文件，须等这条命令实际运行后才会生成。
每个预算分别写入 `results/<预算>s_runs.csv`，并自动生成每例一行的
`results/<预算>s.csv`，如 `0p5s_runs.csv`/`0p5s.csv`、`5s_runs.csv`/`5s.csv`、`60s_runs.csv`/`60s.csv`。长任务每跑完一条就刷新原始 CSV；汇总 CSV 在该预算批次正常结束后生成。若中途停止，重跑同一命令续跑。

先试代表实例时，可追加 `--instances C101 C103 C104 R101 RC101`；
这些记录写入对应预算的 CSV，之后全量运行会自动跳过。

## 每次优化后比较

以下命令在 `vrptw_solver` 目录执行。先以相同的 `0.5` 秒和种子
`0 1 2` 运行自己的求解器，再比较输入哈希、数值规则和每一组
`(实例, 种子)` 的可行性、车辆数与距离。为每次实验选一个新的输出目录和
对比文件名，保留可追溯记录。对比结果放在该实验的 `runs/` 目录，
避免把一次次实验报告堆入 `benchmarks/`。

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m vrptw batch .\data --seeds 0 1 2 --time-limit 0.5 --out .\runs\next_experiment
python .\benchmarks\pyvrp\compare.py --ours .\runs\next_experiment\batch_summary.csv --budget 0.5 --out .\runs\next_experiment\vs_pyvrp.csv
```

如果也想生成 **5 秒**的双方对照，在 `vrptw_solver` 目录执行下面两条。现有 PyVRP 5 秒只运行了种子 `0`，所以自研批量也选种子 `0`：

```powershell
python -m vrptw batch .\data --seeds 0 --time-limit 5 --out .\runs\self_5s_seed0
python .\benchmarks\pyvrp\compare.py --ours .\runs\self_5s_seed0\batch_summary.csv --budget 5 --out .\runs\self_5s_seed0\vs_pyvrp.csv
```

**60 秒**同理，但先在共同父目录跑完上面的 PyVRP 60 秒命令，再回到 `vrptw_solver` 目录执行：

```powershell
python -m vrptw batch .\data --seeds 0 --time-limit 60 --out .\runs\self_60s_seed0
python .\benchmarks\pyvrp\compare.py --ours .\runs\self_60s_seed0\batch_summary.csv --budget 60 --out .\runs\self_60s_seed0\vs_pyvrp.csv
```

只有**相同实例、相同预算、相同种子**的记录才应比较。对照脚本只匹配双方共有的种子，不会从自研批量 CSV 自动识别设定的 `--time-limit`；因此运行时务必按上面的预算配对。R103 等未找到可行解的情况会保留状态，不填无意义的距离 gap。对比脚本不会修改 PyVRP 原始基线。每轮新优化应使用新的自研 `runs/<实验名>` 目录，并以 0.5 秒档作为主要晋级依据。

如果新批量实验把某个实例的最好车辆数降得更低，先在共同父目录运行：

```powershell
& '.\PyVRP-main\.venv\Scripts\python.exe' '.\vrptw_solver\benchmarks\pyvrp\run.py' --caps-from-batch '.\vrptw_solver\runs\next_experiment\batch_summary.csv' --budgets 0.5 --seeds 0 1 2
```

各预算的逐次基准 CSV 允许同一实例保留不同车辆上限的记录，已完成的
`(实例, 预算, 种子, 车辆上限)` 会自动跳过；然后重跑比较命令即可。
