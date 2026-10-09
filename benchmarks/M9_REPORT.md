# M9 强化固定车数搜索验收

M9 已完成。默认配置采用 **原四算子轮换扫描、`max_moves=None`、M8 精确增量评估和完整邻域**。225 项测试通过；1008 条首轮消融、1176 条调度复验、144 条代表诊断及最终 168 条 CLI 路线全部独立可行，共 **2496 条**。新增连续两客户搬移、2↔1 交换和 2↔2 交换均有独立开关，未通过默认晋级。

同批冻结 M8 控制与最终选定配置按每实例三个种子的最好 `(车辆数, 距离整数)` 比较：车辆改善／退步 **1／0**，同车数距离改善／退步／打平 **34／1／20**。最终 CLI 对本轮 PyVRP 的同车数距离 gap 中位数为 **25.4863%**、最近秩 90 分位为 **64.6803%**，6 例车辆数更多。近期 20% 目标仍未达到，下一阶段为 M10。

## 实现与正确性

参考本地 [Exchange.h](../../PyVRP-main/pyvrp/cpp/search/Exchange.h) 的片段交换思想，自行实现 `relocate_pair`、`exchange_pair_single` 和 `exchange_pairs`，分别对应连续两客户搬移、2↔1 与 2↔2 交换。片段顺序保持不变；同路线交换允许邻接但拒绝重叠。搬移目标位置指移除片段后的插槽，交换位置指原路线。现有 `two_opt_star` 已提供单趟 VRPTW 中与 [SwapTails](../../PyVRP-main/pyvrp/cpp/search/SwapTails.cpp) 对应的跨路线尾段重组，继续独立可选。没有直接复制 PyVRP 实质性源码。

三个新算子接入完整重算、affected-route 缓存及精确前缀／后缀评估；邻居筛选只看新增连接，保留的片段内部边不作为放行理由。每个接受移动仍完整验证客户覆盖、时间窗、容量、实际车辆数及整数差值。审核发现搬移进入空路线可能增加车辆数，已修正搬移／尾交换枚举和公开差值检查，并加入 1 车、40000 ticks 被拆成 2 车、26000 ticks 时必须拒绝的回归测试。

`Config.max_moves` 和 CLI 默认由 2 改为 `None`；局部搜索运行到局部最优或共享的总截止时间，`--max-moves 2` 可恢复旧限制。`operator_schedule="cyclic"` 每接受一个移动，就从该算子的下一项开始下一次扫描，完整扫描全部启用邻域均无改善才判定局部最优。`fixed` 保留原顺序。所有开关均写入结果配置；不限时间的固定迭代运行可显式设置移动上限。

新增测试包括 1350 个 C103／R101／RC101 的随机真实片段移动：每个实例、每个新算子各 150 个，缓存可行性及整数差值与独立完整验证一致。结构字面预期覆盖同／跨路线、前后插槽、不同长度交换、邻接、重叠及越界；三评估模式的 first／best 非空接受序列一致，轮换组合、诊断开关、CLI 落盘与截止也通过检查。原 M8 的 2400 个随机移动及边界测试继续通过。数值合同仍为 `solomon_exact_1000_v1`。

## 首轮：片段算子与取消移动上限

协议为全部 56 个 TXT × 种子 0／1／2 × 0.5 秒，诊断关闭，每个实例／种子旋转配置顺序。控制加载冻结 M8；新配置从运行前的源码快照加载。构造、修复、95% 剩余预算减车及随机参数固定。除控制最多接受两次移动，其余使用无限移动、固定算子顺序；单项新增算子放在原四算子之前。

| 配置 | 车辆改善／退步 | 同车数距离改善／退步／打平 |
| --- | ---: | ---: |
| 原四算子，取消上限 | 0／1 | 14／8／33 |
| 连续两客户搬移 | 0／0 | 14／23／19 |
| 2↔1 交换 | 0／0 | 23／17／16 |
| 2↔2 交换 | 0／1 | 20／19／16 |
| 三新增算子组合 | 0／0 | 14／22／20 |

全部 1008 条路线重验。2↔1 在首轮最有希望，但代表诊断显示固定顺序取消上限会减少扰动轮数，并反复从首算子开始下降扫描，C103／C104 可出现明显退步。因此继续试验轮换调度；首轮没有据代表实例或这一批结果直接晋级。原始记录、配置和源码哈希见 [首轮清单](m9/ablation/experiment.json)、[逐实例比较](m9/ablation/best_by_instance.csv)及[指标](m9/ablation/metrics.json)。

## 调度全量复验与默认晋级

相同协议下再跑七配置，共 1176 次。此批源码包含空路线修复和可选轮换调度；控制仍是同一个冻结 M8。首轮源快照与复验快照分别保存，所有实验解的路线均为非空，不涉及首轮版本的空槽边界问题。两个全量批次串行，测试安排在批次之间。

| 配置 | 车辆改善／退步 | 同车数距离改善／退步／打平 |
| --- | ---: | ---: |
| 原四算子，无限移动，固定顺序 | 0／0 | 13／12／31 |
| 2↔1，无限移动，固定顺序 | 0／1 | 20／21／14 |
| **原四算子，无限移动，轮换** | **1／0** | **34／1／20** |
| 2↔1，无限移动，轮换 | 0／0 | 30／11／15 |
| 2↔2，无限移动，轮换 | 1／0 | 27／12／16 |
| 三新增算子组合，无限移动，轮换 | 0／0 | 22／16／18 |

轮换原四算子的距离改善按 C／R／RC 为 **14／10／10**；唯一同车数退步是 R207，增加 **97 ticks（0.097，+0.009368%）**。C103 从 1659.401 降至 1658.323（−0.064963%），C104 从 1546.725 降至 1400.897（−9.428179%）。R104 的 11→10 发生在同一减车实现的限时试探中，不能归因为固定车数局部移动。

新增算子必须进一步与轮换原四算子控制比较：轮换 2↔1 的车辆改善／退步为 0／1，同车距离改善／退步／打平 21／20／14；轮换 2↔2 为 0／0、14／26／16；轮换全组合为 0／1、8／32／15。它们没有稳定超过已选控制，故保留实验开关，默认主线不启用。2↔1 首轮的优势也未在固定顺序复验中稳定重现。

默认选择依据完整车辆优先结果，未仅依据 C103／C104。完整数据见 [复验清单](m9/confirmation/experiment.json)、[指标](m9/confirmation/metrics.json)和[逐实例表](m9/confirmation/best_by_instance.csv)。

## 搜索行为诊断

另在 C103、C104、R101、RC101 各三个种子跑两批诊断，共 144 次。下表是第二批中三个种子的局部搜索累计接受数；诊断有计数开销，不作为正式质量成绩。

| 实例 | M8 两次移动限制 | 仅取消上限 | 取消上限＋轮换 |
| --- | ---: | ---: | ---: |
| C103 | 47 | 46 | **133** |
| C104 | 57 | 53 | **161** |
| R101 | 4 | 6 | **10** |
| RC101 | 1 | 2 | **5** |

C103 的局部搜索累计时间分别为 1.003672／1.205974／1.199135 秒，C104 为 0.985814／1.200070／1.204824 秒。轮换提高接受移动数，并不依赖候选速率提高：C103 轮换候选总数为 94249，单纯取消上限为 105940；C104 分别为 96795 和 102521。这说明 M9 的收益来自搜索次序和有效移动量。原始阶段记录包括首次可行耗时、减车试探、候选、时间窗／容量拒绝及耗时，见 [诊断 CSV](m9/diagnostics/batch_diagnostics.csv)和[首轮诊断](m9/screen_diagnostics/batch_diagnostics.csv)。

## 最终 CLI 与 PyVRP

最终默认 CLI 的 168/168 次输出再次从保存的客户顺序完整重算，每次具有 JSON、SOL、history CSV 和两张 PNG。全部 168 次进入 ILS，共 175 轮；实际求解时间中位数 **0.500448 秒**、最大 **0.502038 秒**；首次可行中位数 **0.0860855 秒**、最大 **0.235915 秒**。图和文件写入不计入求解预算，候选边界截止不构成硬实时保证。代表路线图与收敛图已查看。

本轮重新运行 PyVRP 原上限的 168 次，另补 R104／R105／R108／RC207 的 10／16／10／3 车上限各三次，原上限记录保留。180 条记录中 **173 条可行路线**通过独立验证，7 次限时未找到可行解；“未找到”不证明该车数不可行。PyVRP 仍只调用固定上限下的 `solve()`，单独的减车实验属于 M10。输入字段、比例、矩阵及结果核验沿用既有基准协议。

| 批次／配置 | 同车数实例 | 自研车数更多的实例 | gap 中位数 | gap 90 分位 |
| --- | ---: | ---: | ---: | ---: |
| 同批复验 M8 控制 | 50 | 6 | 26.9805% | 77.2196% |
| 同批复验，轮换原四算子 | 50 | 6 | **25.4988%** | **64.6803%** |
| 最终默认 CLI | 50 | 6 | **25.4863%** | **64.6803%** |

最终同车数 gap 按 C／R／RC 的中位数为 **15.7568%／26.1449%／28.8545%**，90 分位为 **70.6799%／63.7153%／64.6803%**。车数差为 R105 +1、R109 +2、R112 +2、RC107 +1、RC108 +1、RC201 +1。逐实例对照见 [最终表](m9/delivery/vs_pyvrp.csv)，其他每个全量配置也各有自己的 `vs_pyvrp.csv`。

历史 M8 的 17 例车数差距不能直接与本轮 6 例相减来归因于 M9：本轮冻结 M8 控制已经只有 6 例，构造耗时与历史批次也不同。同批证据支持距离搜索改善；同车数 gap 中位数仍高于 20%，长尾及六例车数差距仍需继续处理。

## 归档与复现

[verification.json](m9/verification.json)记录 2496 条自研路线、173 条 PyVRP 可行路线和完整预算／配置／输入核验；[archive_manifest.json](m9/archive_manifest.json)保存归档文件哈希。各批次冻结求解源码和清单已归档，原始完整输出位于忽略源码管理的 `runs/m9_*`。最终源码哈希为 `f5dbbefc3ae22bb6d00b5d163622c793b753a410a3f9c2988d5f6174cecde83a`，M8 控制为 `5d3f56a8df0d9ba91e368d7f3a64e9028fef5c9e1a9bdeff889848baf2b8ab27`。

从 `vrptw_solver` 目录运行，实验输出需使用未占用的新目录：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=tests/.pytest-local
.venv/Scripts/python.exe benchmarks/m9_experiment.py run --out runs/m9_repeat
.venv/Scripts/python.exe benchmarks/m9_experiment.py analyse --out runs/m9_repeat
.venv/Scripts/python.exe benchmarks/m9_experiment.py run --out runs/m9_cyclic_repeat --variants baseline uncapped exchange_pair_single cyclic cyclic_single cyclic_pairs cyclic_combined
.venv/Scripts/python.exe benchmarks/m9_experiment.py analyse --out runs/m9_cyclic_repeat
.venv/Scripts/python.exe -m vrptw batch data --seeds 0 1 2 --time-limit 0.5 --out runs/m9_default_repeat
.venv/Scripts/python.exe benchmarks/m9_experiment.py run --out runs/m9_diag_repeat --instances C103 C104 R101 RC101 --variants baseline uncapped cyclic cyclic_single cyclic_pairs cyclic_combined --diagnostics
.venv/Scripts/python.exe benchmarks/m9_experiment.py analyse --out runs/m9_diag_repeat
```

本地 PyVRP 的虚拟环境启动器引用搬迁前路径；本轮直接使用现存 Python 3.13.5 和原 site-packages，未修改或安装环境：

```powershell
$env:PYTHONPATH = (Resolve-Path ../PyVRP-main/.venv/Lib/site-packages).Path
& '../PyVRP-main/.uv-python/cpython-3.13.5-windows-x86_64-none/python.exe' benchmarks/pyvrp/run.py --budgets 0.5 --seeds 0 1 2 --out-dir runs/m9_pyvrp_repeat
& '../PyVRP-main/.uv-python/cpython-3.13.5-windows-x86_64-none/python.exe' benchmarks/pyvrp/run.py --budgets 0.5 --seeds 0 1 2 --instances R104 R105 R108 RC207 --caps-from-batch runs/m9_repeat/baseline/batch_summary.csv --out-dir runs/m9_pyvrp_repeat
$env:PYTHONPATH = (Resolve-Path src).Path
```

新限时批次的最好车数可能不同，应按 `compare.py` 要求补齐自己的新上限；不能把上述四例当作永久固定补跑集合。归档命令为 `benchmarks/m9_collect.py --ablation <首轮目录> --confirmation <复验目录> --diagnostics <诊断目录> --delivery <最终CLI目录> --pyvrp <0p5s_runs.csv> --out <归档目录>`；可追加 `--screen-diagnostics` 保存首轮诊断。
