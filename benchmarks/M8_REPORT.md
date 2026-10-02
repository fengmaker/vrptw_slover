# M8 邻域筛选与快速评估验收

M8 已完成。主线采用精确前缀／后缀评估（`evaluation_mode=incremental`），默认完整邻域（`num_neighbours=None`）；20／40 邻居筛选保留为实验开关。171 项测试通过，最终默认 CLI 的 168/168 条路线独立可行。0.5 秒整体质量目标仍未达到：本轮新 PyVRP 对照的同车数距离 gap 中位数为 **28.8956%**，90 分位为 **86.6042%**，17 个实例车辆数更多。

本轮同批冻结 M7 控制与增量评估的 56 个三种子最好结果，车辆数全部相同，距离改善 **17** 例、退步 **3** 例、打平 **36** 例。这是采用增量评估的质量证据；历史 M7 成绩与本轮运行速度存在明显差异，另列比较。

## 实现与正确性

`route_cache.py` 缓存路线距离、载重、每个前缀的出发时间，以及每个后缀的距离、载重、时间变换和最迟可行到达时间。候选先找相同前缀／后缀，只扫描变化中段。后缀把首节点到达时间 `t` 精确映射到回仓时间 `max(t + duration, release)`；客户窗检查与仓库截止检查分开进行。所有量都使用原千分位整数矩阵，没有近似或数值合同变更。

`CachedMoveEvaluator` 仅复制和评估一条或两条受影响路线；拒绝移动不修改缓存，接受后仅重建受影响缓存。`cached` 模式只消除原路线反复评估和整解复制，仍完整重算变化路线；`incremental` 再复用时间片段。完整初始／接受验证的结果直接用于缓存构建，避免重复评估。每个接受移动和最终公开解仍由 `validate_solution` 从客户顺序重算；公开 `move_delta` 保留完整重算作为参考。

`neighbourhood.py` 参考本地 [PyVRP neighbourhood.py](../../PyVRP-main/pyvrp/search/neighbourhood.py) 的距离、最小等待和时间窗相关性。方向分数为 `5 * distance + wait + 5 * timewarp`，相当于距离、0.2 等待和 1.0 timewarp 权重；先取两个方向的较小分数，再按客户 ID 稳定排序选 top-k，最后对称化。所以实际邻居数可能大于 k。仓库没有客户邻居；新仓库连接可放行，避免漏掉路线端点移动。四算子按新增连接筛选，保留原枚举次序；完整 k 使用未筛选路径。

内部快评估器用于已验证、客户 ID 已知且唯一的结构移动。外部任意路线、重复／未知客户和全解覆盖检查继续使用公开验证器。

测试覆盖 C103、R101、RC101 各 800 个随机移动，共 **2400** 个，四算子的可行性和整数差值与完整重算一致；另有随机等待／紧窗实例、载重上限、仓库截止、非零仓库开始时间、空路线、缓存更新和违规原因核对。`first`／`best` 下三评估模式的非空接受序列、路线和距离一致，完整邻域等价，诊断开关和计数也通过验证。

## 0.5 秒全量消融

协议：56 个原始 TXT × 种子 0/1/2 × 0.5 秒，单进程，逐实例／种子旋转五配置顺序，诊断关闭；保留 M7 的 95% 剩余预算减车、due 修复和每轮最多两次移动。M7 和 M8 都从冻结源码加载。源码和数值规则见 [experiment.json](m8/experiment.json)，逐次记录见 [runs.csv](m8/runs.csv)，逐实例车辆优先比较见 [best_by_instance.csv](m8/best_by_instance.csv)。

| 配置 | 车辆改善／退步 | 同车距离改善／退步／打平 | C / R / RC 的 ILS 总迭代数 |
| --- | --- | --- | --- |
| 冻结 M7 | 0 / 0 | 0 / 0 / 56 | 69 / 71 / 50 |
| affected-route cached | 0 / 0 | 7 / 4 / 45 | 79 / 73 / 48 |
| incremental，完整邻域 | 0 / 0 | **17 / 3 / 36** | **121 / 75 / 54** |
| incremental，20 邻居 | 0 / 0 | 10 / 11 / 35 | 117 / 24 / 25 |
| incremental，40 邻居 | 0 / 1 | 8 / 11 / 36 | 108 / 24 / 24 |

增量评估的改善按 C/R/RC 分组为 8/5/4 例；退步为 R109（+1.5218%）、R210（+0.4591%）和 RC106（+3.9617%）。完整表保留所有种子的结果。17 例改善与 3 例退步并不保证 gap 中位数下降：同批对新 PyVRP 的 gap 中位数从控制的 31.2990% 变为 31.9602%，90 分位从 90.2105% 降为 86.6042%。

邻居图在减车后、总预算内建立，消耗 R/RC 较短的 ILS 预留时间：20 邻居仅 24/69 次 R、25/48 次 RC 进入 ILS；完整增量模式为 69/69 和 48/48。代表实例的单独局部搜索收益不能替代整机质量门槛，因此邻域筛选不默认开启。车辆试探实现和预算不变，限时车数波动单独保留，不归因为筛选的减车机制。

原始 840 次全部保存并验证。追加一个边界测试时发现短暂 Python 测试进程重叠尾部，随后单独重跑 R211/RC101 的全部种子／配置（30 次），用新目录组合主数据；原始和重跑数据均保留。替换记录、原时间戳及原因见 [recheck_experiment.json](m8/recheck_experiment.json) 和 [original_experiment.json](m8/original_experiment.json)。主数据 840 条再次独立重验。

## 候选与接受速率

从已保存的 M7 可行路线出发，在 C103/C104、R101、RC101 各三个种子上做独立 0.5 秒局部搜索，诊断开启、first improvement、此诊断不限制接受次数。表中按阶段总时间加权，包含缓存、完整验证、筛选／建图成本。这不是整机加速倍数，也不改变正式默认的两次移动限制。全部 60 条结束路线再次验证，原始计数见 [rates.csv](m8/rates.csv)。

| 类别 | 完整重算候选/秒 | cached 候选/秒 | incremental 候选/秒 | 增量/完整 | 完整接受/秒 | 增量接受/秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| C | 14,818 | 25,386 | 76,744 | **5.18×** | 6.00 | 30.33 |
| R | 18,414 | 30,450 | 66,880 | **3.63×** | 8.00 | 18.66 |
| RC | 15,305 | 26,206 | 57,305 | **3.74×** | 6.00 | 14.66 |

新诊断字段包括缓存构建、增量评估次数、复用前缀／后缀客户数、邻居过滤数。`route_evaluations` 包含完整和增量调用，不再全部代表逐客户完整重算；两者由新增字段区分。

## 最终默认 CLI 与 PyVRP

[最终批次](m8/delivery/batch_summary.json)使用当前源码，168/168 次可行并重验，每次具有 JSON、SOL、history CSV 和两张 PNG。全部 168 次进入 ILS，共 290 轮；求解实际时间中位数 **0.5005045 秒**、最大 **0.546175 秒**；首次可行解中位数 **0.1237085 秒**、最大 **0.405953 秒**。图片／报告写入不计入求解预算。构造先保证可行解，时间上限按阶段和候选边界检查，不能保证硬实时。

本轮 PyVRP 控制重新运行全部 56 × 3，并额外补 R108 的 11 车上限三次；10 车记录保留。**171 条记录，165 条可行路线独立验证，6 次限时未找到**，后者不构成不可行证明。转换字段和矩阵逐例核对；种子、预算、版本、代码／输入哈希及每个上限的原始记录保留于 [fresh_pyvrp_runs.csv](m8/fresh_pyvrp_runs.csv) 和 [pyvrp_fresh](m8/pyvrp_fresh)。仍使用固定上限下的 `solve()`、零车辆固定成本，单独减车对照留到 M10。

[本轮默认逐实例对照](m8/delivery/vs_pyvrp_fresh.csv)有 39 个同车数实例，距离 gap 中位数 **28.8956%**、90 分位 **86.6042%**；其余 17 个实例自研车数更多，不计算距离 gap。C103 最好 10 车、1659.401（PyVRP 828.065），C104 最好 10 车、1561.894（PyVRP 824.776）；仍需要更深的固定车数搜索。

[历史 M7 比较](m8/delivery_vs_historical_m7.csv)有 **16 例车辆数退步、0 例改善**；同车数距离改善／退步／打平为 15/8/17。历史 M7 首次可行中位数为 0.0861445 秒，本轮同批冻结 M7 控制为 0.1750185 秒；最终 CLI 为 0.1237085 秒。Python、硬件描述和未改动的构造／减车文件哈希相同，已核查本轮实验进程，没有另一个持续 Python 求解批次。耗时变化的具体系统原因未证实，历史退步不能用本轮评估器的局部加速掩盖。当前同批 M7／M8 的 56 个最好车辆数则全部一致。

## 复现与文件

M7 冻结源码 SHA256：`bcdbfecac731779090e3a398702ae4d335968003d643f03bf90430cd1a9ec707`。

M8 源码 SHA256：`5d3f56a8df0d9ba91e368d7f3a64e9028fef5c9e1a9bdeff889848baf2b8ab27`。源码快照、1008 条主自研结果的验证信息、60 条吞吐路线、历史 186 条 PyVRP 记录和本轮 171 条记录的核验见 [verification.json](m8/verification.json)；归档文件哈希见 [archive_manifest.json](m8/archive_manifest.json)。原始结果位于忽略源码管理的 `runs/m8_*`。

从 `vrptw_solver` 目录运行，输出目录换成未使用的新名字：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=tests/.pytest-local
.venv/Scripts/python.exe benchmarks/m8_experiment.py run --out runs/m8_repeat
.venv/Scripts/python.exe benchmarks/m8_experiment.py analyse --out runs/m8_repeat
.venv/Scripts/python.exe -m vrptw batch data --seeds 0 1 2 --time-limit 0.5 --out runs/m8_default_repeat
.venv/Scripts/python.exe -m vrptw solve data/C103.txt --seed 0 --time-limit 0.5 --evaluation-mode full --out runs/m8_full_repeat
.venv/Scripts/python.exe -m vrptw solve data/C103.txt --seed 0 --time-limit 0.5 --num-neighbours 20 --out runs/m8_neighbour_repeat
```

吞吐脚本 `m8_rates.py` 接收冻结包路径和输出路径；归档脚本 `m8_collect.py` 读取已完成消融、CLI、吞吐和 PyVRP 目录后重验。当前阶段不增加算子或放宽可行性，下一阶段 M9 重点是把候选评估加速转化为更多有效固定车数移动，并重新执行同预算质量门槛。
