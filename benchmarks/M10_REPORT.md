# M10 减车搜索验收

M10 默认采用 **缓存重构与相关路线修复交替、75% 剩余预算减车、五轮修复、基础相关移除规模 8**。M9 的无限局部移动、原四算子轮换、精确增量评估及完整邻域继续使用。264 项测试通过；两轮各 840 条全量实验、63 条代表诊断及最终 168 条 CLI 路线均保存并独立重验，共 **1911 条自研路线**。

第二轮与同批冻结 M9 控制按每实例三个种子的最好 `(车辆数, 距离整数)` 比较，选定配置车辆改善／退步 **15／0**，同车数距离改善／退步／打平 **30／4／7**。R109 为 14→13、R112 为 12→11、RC107 为 14→13、RC108 为 14→12。有限预算的减车失败只记录未找到，不声称目标不可行。

## 实现与数值正确性

`route_removal` 从当前完整可行解移走一条路线，优先尝试客户少、需求少的路线，随后随机多样化。其客户进入待插入集合，regret-2 优先处理仅有一个可选路线的客户，再比较第二选择与最便宜选择的成本差。`related` 在阻塞时保留内部最好的客户覆盖状态，移除与未分配客户在空间、时间窗端点上相关的客户，再做多轮修复；移除规模逐步扩大。`hybrid` 在每个目标 K 上交替执行缓存空间重构和相关路线修复，两条随机流独立。原 `reconstruct` 保留为对照，`cached_reconstruct` 单独用于隔离缓存收益。

插入可行性使用已知可行路线的前缀出发时间，以及原后缀的最迟到达和精确回仓时间传递函数；成本是三条弧的整数差。选择客户和插槽后完整重算并重建受影响路线的缓存。后悔插入只刷新改变的路线；顺序 cheapest 插入为下一客户重新检查所有路线。完整修复必须再次由 `validate_solution` 核对客户覆盖、容量、时间窗、车辆数及距离。内部不完整状态不进入公开当前解，截止或失败始终保留完整 incumbent。

取整后的欧氏弧可能有 1 tick 三角不等式偏差。新增 `(1,1)→(2,2)→(3,3)` 边界测试：三步到达为 4242 ticks，直达为 4243 ticks，移除可能违反最后客户的紧窗。相关移除逐路线完整检查，对这种违反回滚移除。首轮快照尚无此保护，保存的全部实际 Solomon 路线仍经独立验证；第二轮及最终源码包含保护。

750 组 C103／R109／RC107 的随机插入最优位置与完整重算一致；24 组（48 次运行）固定种子重构对照检查 cheapest／regret-2 两策略结果。R101／R109／R112 有真实单次直接插入失败、后续相关修复成功的回归测试。还覆盖空槽、精确 due 边界、覆盖守恒、诊断开关、截止、预算超支与不重复种子。未直接复制 PyVRP 实质性源码；数值合同仍为 `solomon_exact_1000_v1`。

## 第一轮：机制消融

全部 56 例 × 种子 0／1／2 × 0.5 秒；诊断关闭，每个实例／种子轮换配置顺序，串行运行。控制是最终 M9 冻结源码 `f5dbbefc3ae22bb6d00b5d163622c793b753a410a3f9c2988d5f6174cecde83a`，新配置在运行前冻结源码；原四算子无限移动、轮换调度和 95% 剩余预算减车均相同。

| 配置 | 车辆改善／退步 | 同车数距离改善／退步／打平 |
| --- | ---: | ---: |
| 当前源码，原重构 | 0／1 | 3／7／45 |
| 单轮路线移除 | 1／33 | 4／11／7 |
| 多轮相关修复 | 12／8 | 1／24／11 |
| 重构＋相关修复 | 14／2 | 4／13／23 |

原重构算法未改变却出现少量限时差异，说明移动边界、试探边界和运行状态会影响成绩。单轮路线移除没有通过晋级；相关修复明显改善一些车数，但完整结果有退步。第一轮混合策略仍有 R104、RC207 的车辆退步，因此继续优化重构评估，未直接晋级。见 [首轮指标](m10/ablation/metrics.json)和[逐实例表](m10/ablation/best_by_instance.csv)。

## 第二轮：精确缓存与时间份额

相同全量协议，新增缓存重构和 75% 时间份额。缓存重构在不设截止时保持原完整重算的插入决策及随机序列，允许在相同墙钟预算内完成更多工作。

| 配置 | 车辆改善／退步 | 同车数距离改善／退步／打平 |
| --- | ---: | ---: |
| 当前源码，原重构 | 0／1 | 6／5／44 |
| 仅缓存重构，95% | 13／0 | 31／2／10 |
| 混合，95% | 15／0 | 12／9／20 |
| **混合，75%（选定）** | **15／0** | **30／4／7** |

相对仅缓存重构，混合 75% 的车辆改善／退步为 **6／2**，同车距离改善／退步／打平 **16／18／14**；其车辆退步为 R103 的 13→14、RC201 的 4→5。选择混合 75% 是按全量车辆优先的获益实例数及预留距离搜索的效果，不能说它逐实例支配纯缓存。`--fleet-strategy cached_reconstruct --fleet-time-fraction 0.95` 可复现后者。

选定配置相对冻结 M9 的四例同车距离退步为 C201 **+0.443576%**、R111 **+5.048837%**、R210 **+0.724928%**、RC205 **+12.198790%**。C201 未进行减车，属于限时边界波动；其余仍保留完整记录，不能只报告改善。见 [复验指标](m10/confirmation/metrics.json)和[逐实例表](m10/confirmation/best_by_instance.csv)。

## 逐目标统计与最终输出

每个目标 K 的 `target`、`trials`、`repair_rounds`、`elapsed_seconds`、`first_feasible_seconds`、`status` 写入 `solution.json` 的 `fleet.targets`，实验汇总为 `fleet_targets.csv`。耗时从目标开始计时，首次可行为 `null` 表示未找到；`fleet.milestones` 仍从求解开始计时。旧 M9 只有试探次数，旧控制的目标耗时留空，不编造数值。

七个代表实例 C103／C104／R101／RC101／R109／R112／RC107 各三种子、三配置，共 63 条诊断路线。诊断有计数开销，不作为正式质量成绩。详见 [目标统计](m10/diagnostics/fleet_targets.csv)及[阶段 CSV](m10/diagnostics/batch_diagnostics.csv)。最终普通 CLI 168/168 次具有 JSON、SOL、history CSV 和两张 PNG；RC108 路线图、R112 减车轨迹已查看。

最终 CLI 的目标统计示例（耗时从该目标开始，单位秒）如下；完整三种子记录见 [最终目标 CSV](m10/delivery/fleet_targets.csv)：

| 实例／种子 | 目标 K | 试探数 | 修复轮数 | 耗时／首次可行 | 状态 |
| --- | ---: | ---: | ---: | --- | --- |
| R109／0 | 13 | 6 | 17 | 0.041722／0.041722 | found |
| R109／0 | 12 | 34 | 102 | 0.251760／无 | time_limit |
| R112／1 | 11 | 4 | 11 | 0.030075／0.030075 | found |
| R112／1 | 10 | 34 | 100 | 0.275713／无 | time_limit |
| RC107／0 | 13 | 6 | 14 | 0.035710／0.035710 | found |
| RC107／0 | 12 | 34 | 102 | 0.268813／无 | time_limit |

三个种子累计 repair 阶段中，R109 的控制候选数／完整路线重算为 121683／122155，纯缓存为 585965／15874；R112 为 106981／107277 对 633620／14624；RC107 为 113162／113552 对 609152／13981。缓存显著减少完整重算，使减车阶段能完成更多试探。统计包含该阶段实际发生的修复，不能当作独立位置评估的硬件微基准。

## 两种 PyVRP 对照

固定上限距离基线重新运行原上限，并按全部实验中实际找到的较小车数补跑；原上限保留。只有同车数时计算距离 gap。

显式减车另使用 `m10_fleet_baseline.py`：双方初始上限来自相同的确定性 due 构造，名义总预算都是 0.5 秒；PyVRP 调用 `minimise_fleet(MaxRuntime(0.5 × 0.75))` 后，按实际已消耗时间给 `solve()` 分配剩余预算。该 API 只返回车辆类型，未把找到的路线交给后续 solve；成绩只取最终独立验证的完整路线，失败记录 `not_found`。其指标不混入固定上限距离表。

输入解析、转换检查和确定公共上限属于实验准备。自研计时从自身构造开始，PyVRP 从显式减车调用开始；PyVRP 两次 solve 的初始化与停止检查可能超出名义预算，两套实际墙钟耗时单列，不据毫秒级差异作严格速度排名。限时失败不证明不可行，车辆上限也不保证实际使用同样多的车。

固定上限共 **234 条记录**（原上限 168＋补跑 66），**208 条可行路线**通过独立验证，26 次限时未找到。最终 CLI 对照为 **3 例车数更多、53 例同车数**；三例分别是 R112 11 对 10、RC107 13 对 12、RC201 5 对 4，均差一辆。R109 和 RC108 的原有车数差在这套固定上限基线中消除。

| 同批／配置 | 同车数实例 | 自研车数更多 | gap 中位数 | gap 最近秩 90 分位 |
| --- | ---: | ---: | ---: | ---: |
| 第二轮冻结 M9 控制 | 50 | 6 | 25.5231% | 64.6803% |
| 第二轮混合 75% | 53 | 3 | 21.0221% | 58.0062% |
| 最终默认 CLI | 53 | 3 | 21.0221% | 58.0062% |

最终同车数 gap 的 C／R／RC 中位数为 **9.7212%／23.14555%／25.5845%**，90 分位为 **69.1651%／53.8436%／58.0062%**。整体 20% 近期目标仍未达到。完整表见 [最终固定上限对照](m10/delivery/vs_pyvrp.csv)。

显式减车共 **168 条记录，165 条独立可行，3 次未找到**；同初始上限和相同种子完整核对。在 56 例三种子最好解中，自研车数更多／更少／相同为 **11／1／44**；更多的 11 例均差一辆：R109、R110、R112、R204、R207、RC103、RC106、RC107、RC108、RC201、RC206。RC105 自研 14 对 PyVRP 16，但这不是 PyVRP 14 车不可行的证据。44 个同车数实例的距离 gap 中位数为 **16.843354%**、90 分位 **59.325256%**；这一子集与固定上限的 53 例不同，不可直接用两个中位数做算法排名。详见 [同初始上限减车对照](m10/delivery/vs_pyvrp_fleet.csv)。这也表明固定上限表中的三例差距未穷尽真实减车缺口。

自研最终墙钟中位数／最大为 **0.500426／0.500616 秒**，首次可行中位数／最大为 **0.0825685／0.223061 秒**，168/168 进入 ILS。PyVRP 显式流程实际墙钟中位数／最大为 **0.5089095／0.540738 秒**。初始化和候选边界的超支已计入记录，0.5 秒仍是名义搜索预算而不是硬实时保证。下一阶段为 M11，保留全部车辆和距离退步作为后续对照。

最终 CLI 再与第二轮同批 M9 控制比较，车辆改善／退步仍为 **15／0**，同车距离改善／退步／打平为 **31／5／5**。五例距离退步为 C109 **+0.553204%**、C201 **+0.443576%**、R111 **+4.852006%**、R210 **+0.724928%**、RC205 **+10.753100%**。最终运行与消融的接受边界有所波动，因此分别记录，没有用消融的 30／4／7 代替最终 CLI 结果。

## 归档与复现

[verification.json](m10/verification.json)保存独立重验、配置、预算及输入核对；[archive_manifest.json](m10/archive_manifest.json)保存归档文件哈希。两轮源码快照、各自运行脚本及清单均保留，原始完整自研输出在忽略源码管理的 `runs/m10_*`。归档同时包含两个 PyVRP 协议的保存路线和记录。

最终求解源码哈希为 `3356bdb31018267a03c9a865509206c85bc3fb0867b885cddddb15d2e448a902`；运行脚本有各自的独立哈希。第一轮运行脚本保留原版本，后续新增缓存对照没有覆盖原始实验来源。

从 `vrptw_solver` 目录运行，输出目录须为新的空目录：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=tests/.pytest-local
.venv/Scripts/python.exe benchmarks/m10_experiment.py run --out runs/m10_repeat --variants baseline reconstruct cached_reconstruct hybrid hybrid_75
.venv/Scripts/python.exe benchmarks/m10_experiment.py analyse --out runs/m10_repeat
.venv/Scripts/python.exe -m vrptw batch data --seeds 0 1 2 --time-limit 0.5 --out runs/m10_default_repeat
.venv/Scripts/python.exe benchmarks/m10_experiment.py run --out runs/m10_diag_repeat --instances C103 C104 R101 RC101 R109 R112 RC107 --variants baseline cached_reconstruct hybrid_75 --diagnostics
.venv/Scripts/python.exe benchmarks/m10_experiment.py analyse --out runs/m10_diag_repeat
$env:PYTHONPATH = (Resolve-Path ../PyVRP-main/.venv/Lib/site-packages).Path
& '../PyVRP-main/.uv-python/cpython-3.13.5-windows-x86_64-none/python.exe' benchmarks/m10_fleet_baseline.py --out-dir runs/m10_fleet_repeat
& '../PyVRP-main/.uv-python/cpython-3.13.5-windows-x86_64-none/python.exe' benchmarks/pyvrp/run.py --budgets 0.5 --seeds 0 1 2 --out-dir runs/m10_distance_repeat
# 新车数上限应按本次 batch_summary.csv 补跑，不能永久沿用本轮集合。
& '../PyVRP-main/.uv-python/cpython-3.13.5-windows-x86_64-none/python.exe' benchmarks/pyvrp/run.py --budgets 0.5 --seeds 0 1 2 --caps-from-batch runs/m10_default_repeat/batch_summary.csv --out-dir runs/m10_distance_repeat
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python.exe benchmarks/m10_collect.py --ablation runs/m10_ablation --confirmation runs/m10_confirmation --diagnostics runs/m10_diagnostics --delivery runs/m10_delivery --pyvrp runs/m10_pyvrp_distance/0p5s_runs.csv --pyvrp-fleet runs/m10_pyvrp_fleet --out benchmarks/m10
```

归档多个全量配置时，必须给每个配置实际要求的不同上限各补齐三种子，单独补最终 CLI 可能不够。仍可显式 `--fleet-strategy reconstruct --fleet-time-fraction 0.95` 恢复旧减车机制。
