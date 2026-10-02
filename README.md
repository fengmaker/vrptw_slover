# VRPTW 求解器

这是独立的标准 Solomon VRPTW 项目，已完成计划书的 **M0～M8**：解析与独立验证、可行解构造、四种局部搜索邻域、ILS、限预算减车、批量报告、分阶段诊断、修复消融及精确增量评估。求解运行不依赖 PyVRP；Matplotlib 用于生成两张结果图。

M8 默认使用前缀／后缀缓存，保留完整邻域；171 项测试通过，最终 168 条路线独立可行。同批冻结 M7 对照中 17 例距离改善、3 例退步；整体质量目标与历史车辆数退步见 [M8 报告](benchmarks/M8_REPORT.md)。

## 命令行使用（PowerShell）

项目文件夹叫 `vrptw_solver`，实际可执行的 Python 包叫 `vrptw`，所以入口是 `python -m vrptw`。源码在 `vrptw_solver/src`；没有安装包时，先设置 `PYTHONPATH`。以下命令从仓库根目录 `VRPTW-ALNS-main` 直接运行：

```powershell
$env:PYTHONPATH = (Resolve-Path .\vrptw_solver\src).Path
python -m vrptw solve .\vrptw_solver\data\C101.txt --seed 0 --max-iterations 20 --out .\vrptw_solver\runs\C101
python -m vrptw validate .\vrptw_solver\data\C101.txt .\vrptw_solver\runs\C101\solution.json
```

`PYTHONPATH` 只对当前 PowerShell 窗口生效。上述 `solve` 命令生成 `vrptw_solver/runs/C101/solution.json` 等结果文件；第二条命令独立复核结果。若已经安装了本项目，可以省略设置 `PYTHONPATH`。运行需要 Python 3.10+ 和 Matplotlib。

也可以先进入项目文件夹，再使用较短的相对路径：

```powershell
Set-Location .\vrptw_solver
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m vrptw solve .\data\C101.txt --seed 0 --max-iterations 20 --out .\runs\C101
python -m vrptw validate .\data\C101.txt .\runs\C101\solution.json
python -m vrptw batch .\data --seeds 0 1 2 --time-limit 0.5 --out .\runs\batch
```

所有相对路径都从执行命令时 PowerShell 所在的目录开始计算。`python -m vrptw_solver` 会报 `No module named vrptw_solver.__main__`，因为它只是项目目录，并没有这个命令入口。查看命令自带的帮助可运行 `python -m vrptw --help`、`python -m vrptw solve --help`、`python -m vrptw batch --help`。

### `solve`：求解一个实例

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `solve` | 必填子命令 | 求解单个标准 Solomon TXT 实例。 |
| `instance`，例如 `.\data\C101.txt` | 必填 | 实例文件路径，必须指向 Solomon TXT，不读取 `data_vrp/*.vrp`。 |
| `--seed 0` | `0` | 随机种子，控制减车与 ILS 的随机步骤；相同迭代预算和配置可复现路线。 |
| `--max-iterations 20` | 无 | 最多执行 20 轮 ILS 迭代；**不包括**初始构造和减车试探。设为 `0` 会跳过 ILS，但仍执行构造和减车。 |
| `--time-limit 10` | 无 | 求解阶段的墙钟时间上限，单位为秒；从构造开始计时，覆盖减车和 ILS。结果文件及图片的写入在求解后进行，不计入该上限。 |
| `--fleet-attempts 100` | `100` | 每尝试减少到一个目标车辆数时，最多进行 100 次随机修复试探；不是整个程序的总尝试次数。 |
| `--fleet-time-fraction 0.95` | `0.95` | 构造完成后剩余时间中可用于减车的比例，范围 0～1；其余时间留给 ILS。只对限时运行生效。 |
| `--max-moves 2` | `2` | 每轮 ILS 候选解的局部搜索最多接受 2 次改进移动；不是总迭代数。 |
| `--construction-order due` | `due` | 构造顺序：`due` 按截止时间，`slack` 按时间窗宽度，`id` 按客户编号。 |
| `--repair-order due` | `due` | 修复顺序：`input` 沿用减车或扰动产生的顺序，`due` 优先早截止，`slack` 优先窄时间窗。 |
| `--repair-strategy cheapest` | `cheapest` | `cheapest` 依次最便宜可行插入；`regret2` 动态优先可选路线少或第二选择代价高的客户。 |
| `--remove-min 3`、`--remove-max 8` | `3`、`8` | 每轮扰动抽取的移除规模范围；为保持固定车辆数，实际移除数可能更少。 |
| `--restart-after 20` | `20` | 连续多少轮没有改进最好解后，从最好解重新开始。 |
| `--evaluation-mode incremental` | `incremental` | 局部搜索评估：`full` 完整重算，`cached` 缓存原路线并只重算受影响路线，`incremental` 复用未改变的前缀／后缀。接受移动后均由完整验证器裁决。 |
| `--num-neighbours 20` | 无限制 | 按距离、最小等待和时间窗相关性筛选连接；正整数指定每客户 top-k，再对称化，所以实际邻居数可大于 k。省略时枚举完整邻域；达到客户数减一也使用完整邻域。 |
| `--diagnostics` | 关闭 | 记录阶段耗时、候选评估、路线重算、拒绝原因及接受数；生成 `diagnostics.csv`。计数有开销，质量实验默认关闭。 |
| `--out .\runs\C101` | `runs/<实例名>` | 输出目录，自动创建。已有同名结果文件会被更新。 |

`--max-iterations` 和 `--time-limit` 至少提供一个；同时提供时，以先达到的条件停止。迭代数为非负整数，秒数为非负有限数字。时间限制控制求解搜索，但文件读取和结果写入还会花时间。车辆数优先于距离；结果是找到的可行解，不代表证明了全局最优。

### `validate`：复核结果

```powershell
python -m vrptw validate .\data\C101.txt .\runs\C101\solution.json
```

`validate` 后面依次是原始 Solomon TXT 实例路径和 `solve` 生成的 `solution.json` 路径；两个参数都必填。它根据 JSON 里的访问顺序重新计算时间窗、容量、车辆数和距离，忽略 JSON 自报的可行性与成本。输出 `feasible` 时进程返回码为 0；输出 `infeasible` 时返回码为 1。

### `batch`：批量实验

```powershell
python -m vrptw batch .\data --seeds 0 1 2 --time-limit 0.5 --out .\runs\batch
```

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `batch` | 必填子命令 | 逐实例、逐种子运行求解。 |
| `directory`，例如 `.\data` | 必填 | 实例目录；只读取顶层 `*.txt` 文件并按文件名排序。 |
| `--seeds 0 1 2` | `0 1 2` | 空格分隔的一个或多个整数随机种子，每个实例分别运行每个种子。这里是复数 `--seeds`；单实例用 `--seed`。 |
| `--max-iterations`、`--time-limit` | 无 | 每次实例与种子的运行预算；至少提供一个，含义与 `solve` 相同。示例的 `0.5` 表示每次求解约半秒的搜索预算，不是整个批次半秒。 |
| `--fleet-attempts`、`--max-moves` 及其他搜索配置 | 同 `solve` | 减车预算、插入策略、移除规模与重启阈值逐次应用。 |
| `--diagnostics` | 关闭 | 与 `solve` 相同，另生成逐实例、逐种子、逐阶段的 `batch_diagnostics.csv`。 |
| `--out .\runs\batch` | `runs/batch` | 批量输出目录，单次结果位于 `<实例名>/seed-<种子>/`，汇总文件位于输出目录顶层。 |

单个失败会记入汇总并继续处理其他实例。批量结束后，`batch_summary.csv/json` 记录每次运行的状态、种子、预算、输入和代码哈希、环境及可比的参考差值；没有参考值时明确标记缺失。

### 安装与测试（可选）

在 `vrptw_solver` 目录中可以运行：

```powershell
python -m pip install --no-build-isolation -e ".[test]"
python -m pytest -q -p no:cacheprovider --basetemp=tests/.pytest-local
```

安装后从其他目录也能使用 `python -m vrptw`，但实例和输出仍需写成相对于当前目录的正确路径。

每次成功的输出包含 `solution.json`、`routes.sol`、`history.csv`、`routes.png` 和 `convergence.png`。`runs/` 已加入忽略规则。

### M6 分阶段诊断

以下命令在 `vrptw_solver` 目录运行。求解开销与文件/图片写入分开计时；构造、减车试探和 ILS 共用高精度单调时钟。

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m vrptw batch data --seeds 0 1 2 --time-limit 0.5 --diagnostics --out runs/m6_diagnostics_seed012_0p5s
python benchmarks/diagnose.py summarise --batch runs/m6_diagnostics_seed012_0p5s --out runs/m6_diagnostics_seed012_0p5s/analysis
python benchmarks/diagnose.py fixed-work --out runs/m6_fixed_work
```

Python 接口使用 `Config(diagnostics=True)`，从 `result.diagnostics.phases` 获取不可变统计。`solution.json` 记录相同统计、完整配置、原始车辆上限和减车里程碑。`elapsed_seconds` 是排除子阶段的耗时，各阶段之和等于求解耗时；`inclusive_seconds` 包含子阶段，不能再相加。例如减车中的插入归入 `repair`，但同时包含在减车的 `inclusive_seconds` 中。未执行的阶段保留零计数。

`candidates` 在构造/修复中计每个实际重算的插入位置，在局部搜索中计每个移动；`route_evaluations` 另计源路线、候选路线和完整验证中的全部路线重算。容量预筛选按客户/路线对记 `capacity_prefilter_skips`，不算候选评估。`accepted` 在构造/修复中是成功插入的客户数，在局部搜索中是接受的改进移动数。拒绝原因按每候选、每种原因至多一次计数，多种原因可能重叠。扰动用 `calls` 和 `removed_customers` 描述；减车用 `trials`/`failures` 描述。额外的 `ils_control`、`validation`、`other` 记录控制逻辑、入口完整验证及其余开销。

`diagnose.py summarise` 从输出路线再次独立验证，同时核对覆盖、输入/代码哈希、预算、配置、车辆上限和计数/计时的一致性。`fixed-work` 交替运行关闭/开启诊断的相同固定工作量，检查路线和试探相同并测量开销。验收结果和 C103/R101/RC101 的诊断结论见 [M6 报告](benchmarks/M6_REPORT.md)。

### M7 搜索预算与基础修复

`--fleet-time-fraction` 控制构造之后的剩余预算分配；ILS 使用整个求解的总截止时间。减车达到容量下界时直接进入距离搜索。修复截止时丢弃未完成候选，保留完整可行解；阶段截止记为 `fleet_budget`。

```powershell
python -m vrptw batch data --seeds 0 1 2 --time-limit 0.5 --fleet-time-fraction 0.95 --repair-order due --out runs/m7
python benchmarks/m7/experiment.py --experiments control repair_due fleet_95 balanced --out runs/m7_confirmation
python benchmarks/m7_confirm.py --out runs/m7_paired --set repair_order=due --set fleet_time_fraction=0.95
```

`slack` 按时间窗宽度排序；`regret2` 每轮选择可选路线少或第二选择成本高的客户，所有插入位置仍由完整路线评估器裁决。实验矩阵显式冻结控制配置，覆盖全部 56 例及三个种子；交替复验在每个实例/种子上相邻运行两个配置。实验工具省略求解计时之外的绘图，普通 CLI 仍写五种结果文件。采用结论、逐实例比较和重跑命令见 [M7 报告](benchmarks/M7_REPORT.md)。

M7 默认采用 `fleet_time_fraction=0.95`、`repair_order="due"`，其余搜索默认值保留。交替复验中 32 例改善、8 例退步、16 例打平；车辆数减少 5 例、增加 2 例，全部 117 次 R/RC 运行进入 ILS。若要复现旧控制配置，显式指定 `--fleet-time-fraction 1 --repair-order input`。限时结果会随系统状态变化，完整退步记录保留在报告中。

## Python 接口

```python
from vrptw import Config, read_solomon, solve, validate_solution

instance = read_solomon("data/C101.txt")
result = solve(instance, Config(seed=0, max_iterations=20))
report = validate_solution(instance, result.routes)
print(report.feasible, report.vehicles, report.distance / 1000)
```

`construct(instance)` 依紧时间窗优先顺序枚举所有可行插入位置；若失败，`ConstructionError` 给出无法插入的客户、已分配数量和已尝试的车辆。`improve(instance, routes)` 支持 relocate、swap、2-opt、2-opt*，只接受固定车辆数、保持全部约束的严格距离改善。`solve` 接着逐车试探到容量下界，并用破坏修复、接受阈值与重启进行 ILS；公开结果再次完整验证。

`evaluate_route` 返回逐客户到达、等待、开始与结束服务时间、累计载重和回仓时间；`validate_solution` 检查全体客户覆盖、车辆上限和每条路线。时间与距离的内部单位是千分之一；可行完整解的 `report.objective` 是 `(车辆数, 距离内部整数)`。`report.distance` 在不可行时为 `None`。

在本机 C101 的固定种子 0、100 次/目标车数设置下，自研构造得到 12 辆、983.072；减车试探找到 **10 辆、828.937**，达到总需求给出的 10 辆容量下界。距离只作为本地参考，不宣称全局最优。批量实验和热点分析见 [M5 报告](benchmarks/M5_REPORT.md)。

原始 TXT 位于 `data/*.txt`，其中 56 个实例均由测试覆盖。`data/data_vrp/*.vrp` 是另一种格式，当前不读取。完整数学与结果格式见 [SPEC.md](SPEC.md)；里程碑状态见 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

## PyVRP 对照实验

本地 PyVRP 的 0.5 秒、56 实例、3 种子对照记录，以及以后运行 5/60 秒档和逐次比较自研求解器的命令，见 [PyVRP 对照基线](benchmarks/pyvrp/README.md)。这是独立实验工具；正常求解仍不依赖 PyVRP。
