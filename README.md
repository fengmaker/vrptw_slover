# VRPTW 求解器

这是独立的标准 Solomon VRPTW 项目，已完成计划书的 **M0～M5**：解析与独立验证、可行解构造、四种局部搜索邻域、ILS、限预算减车及批量报告。求解运行不依赖 PyVRP；Matplotlib 用于生成两张结果图。

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
| `--max-moves 2` | `2` | 每轮 ILS 候选解的局部搜索最多接受 2 次改进移动；不是总迭代数。 |
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
| `--fleet-attempts`、`--max-moves` | `100`、`2` | 与 `solve` 相同，逐次运行应用。 |
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
