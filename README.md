# VRPTW 求解器

这是独立的标准 Solomon VRPTW 项目，已完成计划书的 **M0～M12**：解析与独立验证、可行解构造、局部邻域、ILS、限预算减车、批量报告、诊断、修复消融、精确增量评估、固定车数搜索调度、多轮相关减车、可选不可行搜索与原生热点后端。求解运行不依赖 PyVRP；Matplotlib 用于生成两张结果图。

M12 默认自动选择可用的 C++17／pybind11 可行局部搜索后端；未构建时使用 Python。348 项测试通过，912 条自研完整解独立重验，最终 CLI 168/168 可行。两轮同预算相对 Python 控制均为 55 例同车距离改善、0 例退步、1 例打平；最终固定上限 PyVRP gap 中位数 **8.9252%**、90 分位 **42.8325%**，仍有 3 例车辆更多。已达到同车数中位数 10% 目标，完整退步和性能边界见 [M12 报告](benchmarks/M12_REPORT.md)。M11 罚分搜索继续默认关闭。

M10 默认使用缓存重构与相关路线修复交替、75% 剩余预算减车；保持原四算子轮换、无限局部移动及完整邻域。264 项测试通过，1911 条自研路线重验，最终 CLI 168/168 可行。同批 M9 控制中车辆改善／退步 15／0，同车距离改善／退步／打平 30／4／7。固定上限 PyVRP 对照 gap 中位数为 21.0221%，3 例车辆更多；同初始上限显式减车另有 11 例差距。20% 目标尚未达到，详细退步与两个协议见 [M10 报告](benchmarks/M10_REPORT.md)。M9 与 M8 历史证据见 [M9 报告](benchmarks/M9_REPORT.md)和 [M8 报告](benchmarks/M8_REPORT.md)。

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
| `--fleet-strategy hybrid` | `hybrid` | `reconstruct` 使用原空间种子重构；`cached_reconstruct` 保持其插入决策并使用精确缓存；`route_removal` 移走一条路线并单轮 regret-2；`related` 加入相关移除和多轮修复；`hybrid` 交替使用缓存重构与相关修复。 |
| `--fleet-repair-rounds 5` | `5` | 每次路线移除试探最多修复轮数；第一轮直接插入，后续轮移除与未分配客户相关的客户再修复。 |
| `--fleet-related-count 8` | `8` | 相关移除的基础规模；后续轮逐步扩大。只影响 `related` 和 `hybrid` 的路线移除试探。 |
| `--fleet-time-fraction 0.75` | `0.75` | 构造完成后剩余时间中可用于减车的比例，范围 0～1；其余时间留给 ILS。只对限时运行生效。 |
| `--max-moves 2` | 无限制 | 每轮 ILS 候选解的局部搜索最多接受 2 次改进移动；省略时由局部最优或求解总截止时间停止。设为 `0` 跳过局部移动。 |
| `--operators relocate swap two_opt two_opt_star` | 原四项，按所列顺序 | 独立选择局部算子及扫描顺序；另支持实验项 `relocate_pair`、`exchange_pair_single`、`exchange_pairs`。 |
| `--operator-schedule cyclic` | `cyclic` | `fixed` 每次从首算子重新扫描；`cyclic` 接受移动后从该算子的下一项开始，仍完整检查全部启用邻域才判定局部最优。 |
| `--construction-order due` | `due` | 构造顺序：`due` 按截止时间，`slack` 按时间窗宽度，`id` 按客户编号。 |
| `--repair-order due` | `due` | 修复顺序：`input` 沿用减车或扰动产生的顺序，`due` 优先早截止，`slack` 优先窄时间窗。 |
| `--repair-strategy cheapest` | `cheapest` | `cheapest` 依次最便宜可行插入；`regret2` 动态优先可选路线少或第二选择代价高的客户。 |
| `--remove-min 3`、`--remove-max 8` | `3`、`8` | 每轮扰动抽取的移除规模范围；为保持固定车辆数，实际移除数可能更少。 |
| `--restart-after 20` | `20` | 连续多少轮没有改进最好解后，从最好解重新开始。 |
| `--evaluation-mode incremental` | `incremental` | 局部搜索评估：`full` 完整重算，`cached` 缓存原路线并只重算受影响路线，`incremental` 复用未改变的前缀／后缀。接受移动后均由完整验证器裁决。 |
| `--search-backend auto` | `auto` | 默认在扩展可用且配置／整数域兼容时使用原生可行局部搜索；否则使用 Python。`python` 强制原实现，`native` 要求扩展及兼容配置。构造、减车、修复和不可行搜索继续使用 Python。 |
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

### M9 固定车数邻域

`relocate_pair` 保持顺序搬移连续两个客户；`exchange_pair_single` 交换连续两客户与单客户；`exchange_pairs` 交换两个连续客户片段。三者支持同路线及跨路线移动，同路线交换的片段不能重叠。原有 `two_opt_star` 已提供与单趟 VRPTW `SwapTails` 对应的跨路线尾段重组。每个接受移动仍完整重验时间窗、容量、客户覆盖和实际车辆数。

`max_moves=None` 使每轮下降搜索由局部最优或共享总截止时间结束；`--max-moves 2` 可恢复旧限制。`cyclic` 在每次接受后轮换扫描起点，使其他启用邻域更早参与搜索。Python 的 `Move.index_b` 在搬移中指移除片段后的目标插槽，在交换中指原路线位置。

```powershell
# 单独启用 2↔1 交换，并轮换原四算子
python -m vrptw solve data/C103.txt --time-limit 0.5 --operators exchange_pair_single relocate swap two_opt two_opt_star --operator-schedule cyclic --out runs/m9_C103
# 显式恢复 M8 搜索配置
python -m vrptw solve data/C103.txt --time-limit 0.5 --max-moves 2 --operators relocate swap two_opt two_opt_star --operator-schedule fixed --out runs/m8_control
# 六配置原始消融；冻结源码、旋转运行次序、保存并重验全部路线
python benchmarks/m9_experiment.py run --out runs/m9_repeat
python benchmarks/m9_experiment.py analyse --out runs/m9_repeat
# 调度复验
python benchmarks/m9_experiment.py run --out runs/m9_cyclic_repeat --variants baseline uncapped exchange_pair_single cyclic cyclic_single cyclic_pairs cyclic_combined
python benchmarks/m9_experiment.py analyse --out runs/m9_cyclic_repeat
```

各配置均使用 56 例、种子 0/1/2 和 0.5 秒预算。代表诊断可加 `--instances C103 C104 R101 RC101 --diagnostics`；它用于解释行为，默认选择依据完整全量结果。

最终默认为 `operators=("relocate", "swap", "two_opt", "two_opt_star")`、`operator_schedule="cyclic"`、`max_moves=None`。三种新增算子未稳定超过轮换四算子的全量控制，因此没有默认启用；完整改善、退步及归档见 [M9 报告](benchmarks/M9_REPORT.md)。

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

### M10 减车机制与独立对照

路线移除优先试探客户数少、需求少的路线。其客户进入待插入集合，regret-2 按可选路线数和第二选择成本分配；插入可行性使用精确前缀／后缀状态。阻塞客户留在内部，后续轮移除空间和时间窗相关的已分配客户，再尝试修复。完整解始终经 `validate_solution` 重验后才成为当前解；超时或未修复完成时保留原可行解。取整距离引起的移除后时间窗违反会回滚。

`solution.json` 的 `fleet.targets` 保存每个目标车数的 `target`、`trials`、`repair_rounds`、`elapsed_seconds`、`first_feasible_seconds` 和 `status`。两个耗时都从该目标开始计时；未找到时首次可行时间为 `null`。`found`、`not_found` 和 `time_limit` 描述本次搜索结果，有限预算的失败不构成不可行性证明。`fleet.milestones` 的时间仍从整个求解开始计时。

```powershell
python benchmarks/m10_experiment.py run --out runs/m10_repeat --variants baseline reconstruct cached_reconstruct hybrid hybrid_75
python benchmarks/m10_experiment.py analyse --out runs/m10_repeat
```

该工具冻结最终 M9 控制、当前源码、输入和配置哈希；按实例／种子轮换配置执行顺序，保存路线并独立重验，输出每目标车数的 `fleet_targets.csv`。PyVRP 显式减车另用 `benchmarks/m10_fleet_baseline.py`：从自研确定性构造的同一车辆上限开始，在同一总预算内先调用 `minimise_fleet()`，再按实际剩余时间调用 `solve()`。其路线成绩与原来的固定上限距离基线分开比较。

### M11 不可行候选与自适应罚分

`--infeasible-search` 在固定车数 ILS 中允许暂时超载或违反时间窗的完整候选。容量和 time warp 分别计罚，按候选近期可行率调整两项整数权重。`best` 包含搜索途中出现的完整可行解，输出仍由原独立验证器裁决。默认关闭；是否晋级以 [M11 报告](benchmarks/M11_REPORT.md)的同预算消融为准。

```powershell
python -m vrptw solve data/C103.txt --time-limit 0.5 --infeasible-search --diagnostics --out runs/m11_example
python -m vrptw solve data/C103.txt --time-limit 0.5 --infeasible-search --fixed-penalties --out runs/m11_fixed_example
python benchmarks/m11_experiment.py run --out runs/m11_repeat
python benchmarks/m11_experiment.py analyse --out runs/m11_repeat
```

`--penalty-update-interval` 默认 10，`--penalty-target-feasible` 默认 0.5；固定罚分仍记录可行率。每个维度低于目标减去 0.05 时乘 1.25，高于目标加上 0.05 时乘 0.85，否则保持；整数半偶舍入，权重限制在 100～100000000。初始容量权重按平均弧距离／平均客户需求缩放，初始时间权重为 1000，全部权重除以 1000 才是每单位违反量对应的距离 ticks。

不可行搜索沿用已选算子、扫描顺序和邻居筛选，受影响路线完整软重算，其余路线复用汇总。`--penalty-max-moves` 默认每轮下降最多接受 8 次移动，让候选注册和罚分更新能在短预算内重复；显式 `--max-moves` 覆盖该上限，Python 的 `Config(penalty_max_moves=None)` 可恢复无限下降。原 M10 默认搜索仍为无限移动。`evaluation_mode` 的硬约束前缀／后缀缓存只用于原可行搜索，`repair_order`／`repair_strategy` 只用于构造后的减车及原 ILS。软扰动使用相关／随机移除和罚分增量最小插入，中断修复返回完整原解。罚分改变时接受阈值重新开始累计，避免混用不同权重下的分数。

`history.csv` 记录当前／候选违反量、权重、可行率和当前罚分；`solution.json.penalty_search` 保存摘要。诊断 `penalized_candidates` 计软邻域评估，`accepted_infeasible` 计已应用的不可行局部移动；允许通过的违反不计入 `rejected_*`。实验工具冻结单一源码，在每个实例／种子内轮换 baseline、固定罚分、自适应罚分的串行顺序，保存原始路线及哈希，分析前再次完整验证。

### M12 可选 C++ 热点后端

`solve` 和 CLI 默认 `search_backend="auto"`。本机已构建原生扩展，可直接运行；未构建时普通安装继续使用 Python。原生实现一次接管候选枚举、邻居筛选、七种移动应用和路线前缀／后缀合并，保持原整数矩阵、扫描顺序、first／best 和轮换调度。每个接受的移动仍由 Python 完整验证。低层 `improve` 默认保留 Python；可显式传入 `search_backend="native"` 或 `"auto"`。

在 `vrptw_solver` 目录、安装了 Windows C++ Build Tools 与 Windows SDK 的环境中构建：

```powershell
python -m pip install "pybind11==3.0.1" "setuptools==75.6.0"
$env:VRPTW_BUILD_NATIVE = "1"
python setup.py build_ext --inplace
$env:PYTHONPATH = (Resolve-Path src).Path
python -m vrptw solve data/C103.txt --time-limit 0.5 --out runs/m12_C103
# 强制使用原 Python 搜索，便于对照
python -m vrptw solve data/C103.txt --time-limit 0.5 --search-backend python --out runs/m12_python
```

编译和运行须使用相同版本与架构的 CPython；本轮验证为 Windows x64、CPython 3.12.4、pybind11 3.0.1、MSVC C++17。生成的 `.pyd` 不纳入源码管理。非 Windows 构建入口相同，但本轮仅验证了 Windows。显式 `native` 不支持 `full`／`cached` 或 `--infeasible-search`；`auto` 对这些配置使用 Python。原生整数安全域及结果元数据见 [SPEC.md](SPEC.md)，构建细节见 [native/README.md](native/README.md)。

```powershell
python benchmarks/m12_profile.py --out runs/m12_profile_repeat
python benchmarks/m12_fixed_work.py --out runs/m12_fixed_work_repeat
python benchmarks/m12_experiment.py run --out runs/m12_repeat
python benchmarks/m12_experiment.py analyse --out runs/m12_repeat
```

实验工具串行交替 Python／原生配置，保存 Python 源码、C++ 源码、构建输入、扩展二进制、完整配置和输入／输出哈希，分析时再次独立验证全部路线。性能、全量收益、逐次退步和默认晋级见 [M12 报告](benchmarks/M12_REPORT.md)。

### M12 四方对比：0.5 秒与 5 秒

已完成 56 例、种子 0 的自研 M12 / PyVRP / OR-Tools / Gurobi 对比，另有固定车辆上限距离对照和阶段诊断。共 568 次正式运行、530 份可行解独立重验，13 项基准测试通过。自研相对 OR-Tools 的同车数距离 gap 中位数为 6.873% → 1.413%；固定自研车辆上限后，相对 PyVRP 为 12.976% → 7.602%。车数差、可行率、长尾和两种 PyVRP 协议分别列在 [中文报告](benchmarks/FOUR_SOLVER_REPORT.md)。

在本目录重跑并生成 CSV、中文/英文 Markdown 表格：

```powershell
.\benchmarks\four_solver\run.ps1 -Out runs/four_solver_repeat
# 重验已保存的正式结果并重建主表，不重新搜索
.\benchmarks\four_solver\run.ps1 -Out runs/four_solver_seed0_0p5_5s_v2 -VerifyOnly
```

同一命令和输出目录支持中断续跑；新实验用新目录。环境配置、分步 Python 命令、三种子扩展和补充诊断命令见 [实验说明](benchmarks/four_solver/README.md)。[逐实例四方 CSV](benchmarks/four_solver/results/per_instance_wide.csv)可直接用 Excel 打开；主实验的搜索预算排除外部建模，表中另报完整实际耗时。PyVRP 车辆固定成本配置、基础 Gurobi MILP 的限制见报告，不能用本轮成绩概括库的最佳能力。
