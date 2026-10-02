# M7 搜索预算与基础修复

## 实现与固定合同

M7 添加了构造顺序 `due/id/slack`、修复顺序 `input/due/slack`、修复策略 `cheapest/regret2` 和减车时间份额 `fleet_time_fraction`。移除规模、每轮局部搜索接受移动数、目标车辆数的试探上限和重启阈值均可从 CLI 配置，完整配置写入每个解及批量元数据。

时间份额按**构造完成后的剩余预算**计算，减车截止时间为 `now + fraction * max(0, total_deadline - now)`。ILS 始终使用总截止时间。达到容量下界时不做减车试探；提前用尽减车份额记 `fleet_budget`。修复在客户、路线和插入位置之间检查截止时间，未完成的修复被丢弃，完整可行 incumbent 保留。

`regret2` 比较客户在不同路线上的两个最好可行插入成本，先处理只有一条可行路线的客户，然后处理第二好与最好插入成本差最大的客户。每个插入位置仍调用原有 `evaluate_route` 完整重算；只复用未改变路线上的插入选择，改变路线后重新评估所有剩余客户在该路线上的选择。30 个随机小实例与无缓存的逐轮完全重算逐项一致。

数值规则仍为 `solomon_exact_1000_v1`，目标仍为 `(实际车辆数, 整数距离)`；外部 C101 参考路线未参与搜索。所有正式输出从保存的访问顺序独立验证。构造不被强行中断，以便在很小预算下仍可返回完整可行解；因此记录实际耗时，不承诺任何输入都能严格在 0.5 秒内结束。

## 实验协议与来源

- 56 个原始 Solomon TXT，种子 0、1、2，每次总搜索预算 0.5 秒，单线程；质量实验关闭诊断。
- 首轮十组独立消融位于 `runs/m7_ablation_seed012_0p5s/`；每组 168 次。首轮的部分配置与代表实例筛选在 05:26:32～05:27:42 UTC 重叠，这轮保留为探索证据。
- 控制、截止时间修复、95% 减车预算、组合配置在 `runs/m7_confirmation_seed012_0p5s/` 单独串行复跑，每组 168 次。源码固定使用已归档的包，避免后续默认配置变化影响正在运行的实验。
- 最终组合与控制按每个实例/种子相邻运行，顺序交替，位于 `runs/m7_paired_balanced_seed012_0p5s/`。两次 C103 热身排除在正式统计之外；各配置再各运行 168 次。
- 实验实现源码哈希为 `fb89464a08a8315316a993462b898339a1874a83f2825c9a1b6ff87072390d28`。批量记录保留输入、代码、配置和预算；冻结源码副本保存在实验目录的 `source/<hash>/vrptw/`。
- 距离差仅在双方实际车辆数相同时统计，90 分位采用最近秩 `sorted(values)[ceil(0.9*n)-1]`。限时结果随系统状态变化，历史 M6 对照与当前交替控制对照分别报告。

## 结果与采用决定

默认采用 `repair_order="due"`、`fleet_time_fraction=0.95`，保留构造后剩余时间的 5% 给 ILS，其余搜索默认值不变。当前源码的最终批次 `runs/m7_verified_delivery_seed012_0p5s/` 已完成 56×3 次独立验收：168/168 可行，117/117 次 R/RC 进入 ILS，135 项测试通过。与历史 M6 比较，3 例减少车辆、2 例增加车辆；同车数距离 25 例改善、11 例退步、15 例打平。匹配上限的 PyVRP 距离 gap 中位数仍为 28.1031%，未达到 20% 的近期目标。

采用组合的交替复验共 336 次保存路线也通过再次核验，逐实例最好目标为 32 例改善、8 例退步、16 例打平。首轮及本会话两个探索轮存在额外 VS Code Codex 进程重叠；旧安装包的交付记录不用于当前源码验收。完整原始记录、退步清单、测量来源审计、源码归档及重跑命令见 [M7 当前源码交付验收](M7_DELIVERY_REPORT.md)。下一实施阶段为 M8。

## 补充复验数据

四组串行复验的逐实例最好目标相对控制变化如下；每组 168 次全部独立可行。这轮是交替复验之前的候选筛选，最终采用仍以上述相邻交替结果为依据。

| 配置 | 改善 / 退步 / 打平 | 少车 / 多车实例 | R/RC 进入 ILS |
| --- | ---: | ---: | ---: |
| 控制 | 0 / 0 / 56 | 0 / 0 | 0 / 117 |
| 只改截止时间修复 | 19 / 8 / 29 | 6 / 0 | 0 / 117 |
| 只改 95% 减车预算 | 19 / 3 / 34 | 5 / 0 | 117 / 117 |
| 两者组合 | 29 / 5 / 22 | 10 / 0 | 117 / 117 |

交替组合在 49 个同车数实例上的距离变化中位数为 −0.1345%、最近秩 90 分位为 +4.0831%。少车实例为 R101、R108、RC101、RC104、RC207；多车实例为 C205（3→4）、RC201（4→5）；同车数距离退步为 C107、R109、R110、R201、RC106、RC108。改善和退步全部保留。

另一次当前源码 CLI 运行 `runs/m7_delivery_current_seed012_0p5s/` 在 05:54:52～05:56:54 UTC 完成，168/168 可行、117/117 次 R/RC 进入 ILS。其求解耗时中位数/90 分位/最大值为 0.500432/0.500495/0.571607 秒，最大值出现在 R106；这一批有并发进程活动，成绩分别保存，没有与最终交付批次拼成更多种子。相同上限匹配的 PyVRP 比较中，它有 50 个同车数实例、距离 gap 中位数/90 分位为 27.5861%/86.8496%。

[补充 verification.json](m7/verification.json) 从保存的客户顺序再次验证首轮 1,680 次、串行复验 672 次、交替复验 336 次、代表筛选 132 次、阶段诊断 24 次及这一批 CLI 168 次，共 **3,012 条路线**。控制/采用配置的代表诊断保留 192 条阶段记录；C103 局部搜索占 77.34%，R101/RC101 修复仍占 77.11%/79.63%，但已分别获得 2.44%/2.15% 总耗时用于局部搜索。

完整补充记录见 [逐配置汇总](m7/m7_summary.csv)、[同期控制/M6 逐实例比较](m7/m7_own_comparison.csv)、[各批次 PyVRP 分组 gap](m7/m7_vs_pyvrp_summary.csv)、[阶段汇总](m7/diagnosis_adopted/phase_summary.csv)。每个批次都保存自己的 `*_vs_pyvrp.csv`，输入、配置、源码和导出文件哈希均已核对。实验包归档为 [frozen_solver.zip](m7/frozen_solver.zip)，与实验哈希 `fb89464a…` 一致，解压后可用 `--solver-src` 加载。

## 重跑

以下命令从 `vrptw_solver` 目录运行。所有性能实验依次运行，避免同时启动其他求解批次。

```powershell
python -m pytest -q -p no:cacheprovider --basetemp=tests/.pytest-m7
python benchmarks/m7_experiment.py run --out runs/m7_ablation_reproduce
python benchmarks/m7_experiment.py analyse --experiment runs/m7_ablation_reproduce --m6-csv benchmarks/m6/control_runs.csv

python benchmarks/m7/experiment.py --experiments control repair_due fleet_95 balanced --out runs/m7_confirmation_reproduce
python benchmarks/m7_confirm.py --out runs/m7_paired_reproduce --set repair_order=due --set fleet_time_fraction=0.95

$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m vrptw batch data --seeds 0 1 2 --time-limit 0.5 --out runs/m7_delivery_reproduce
python benchmarks/m7/experiment.py --experiments control balanced --instances C103 C104 R101 RC101 --diagnostics --out runs/m7_diagnosis_reproduce
```

`experiment.py --solver-src <目录>` 可加载包含 `vrptw/` 的冻结源码目录；同一输出目录只会跳过已通过覆盖、配置、哈希及路线重验的完整批次。普通 CLI 仍输出五种结果文件；消融脚本在求解计时之外省略绘图，保存 JSON、SOL、history 和诊断数据。

PyVRP 固定上限及环境说明见 [pyvrp/README.md](pyvrp/README.md)。新达到的较小车辆上限须补跑，原始 M6 基线保留。0.5 秒下未找到指定车数的可行解不代表该车数不可行；本阶段也未宣称消除了 PyVRP 距离差距。

`benchmarks/m7/baselines.py --batches <批次目录...> --out <新目录>` 保留 M6 原始记录，并用 PyVRP Python 3.13 环境补跑所有缺失的新上限；`benchmarks/m7/collect.py --group <标签=实验目录> ... --delivery <CLI批次> --pyvrp <原始CSV> --out <证据目录>` 重新验证保存路线、按冻结协议选择配置并生成全部比较表。环境命令参见 PyVRP README；不同 Python 环境的 `PYTHONPATH` 应在当前窗口中切换后恢复。
