# M7 当前源码交付验收

M7 的实现与验收已完成。默认采用 `repair_order="due"`、`fleet_time_fraction=0.95`，即按截止时间优先修复，并把构造后剩余时间的 5% 留给 ILS。每目标车辆数 100 次试探、每候选 2 次局部移动、移除 3～8 个客户和 20 轮重启阈值保持原值。regret-2、窄时间窗排序和其他预算组合保留为可单独控制的实验选项。

本次明确从 `src` 加载的最终 CLI 批次为 `runs/m7_verified_delivery_seed012_0p5s/`：56 例 × 种子 0/1/2 × 0.5 秒，**168/168** 保存路线通过独立重算，全部五种输出文件齐全。**117/117** 次 R/RC 运行进入 ILS，解决了 M6 中这两类实例普遍没有距离搜索时间的问题。测试命令使用当前源码，结果为 **135 passed**。数值合同保持 `solomon_exact_1000_v1`。

## 最终成绩与局限

每实例取三个种子中按 `(车辆数, 距离整数)` 最好的可行结果。与历史 M6 比较：

| 指标 | 最终默认配置 |
| --- | ---: |
| 车辆数减少 / 增加 | 3 / 2 例 |
| 同车数距离改善 / 退步 / 打平 | 25 / 11 / 15 例 |
| 首次可行耗时中位数 / 最大值 | 0.086145 / 0.223139 秒 |
| 求解耗时中位数 / 最大值 | 0.500435 / 0.500839 秒 |
| ILS 运行数 / 总迭代数 | 168 / 227 |
| R 的 ILS 运行数 / 迭代数 | 69 / 77 |
| RC 的 ILS 运行数 / 迭代数 | 48 / 57 |

车辆改善为 R105：17→16、R108：11→10、RC207：4→3；退步为 RC108：13→14、RC201：4→5。未把车辆变化时的距离差纳入统计。逐例记录见 [confirmation_vs_m6.csv](m7_delivery/confirmation_vs_m6.csv)，其中 `label=delivery_default` 是本节结果。有限时间结果受机器状态影响，历史 M6 与当前结果的差值不等于某个单一机制的因果效果。

采用组合的交替控制复验在 `runs/m7_paired_balanced_seed012_0p5s/`，双方分别 168 次，保存路线再次通过 [balanced_verification.json](m7_delivery/balanced_verification.json) 核验。同一实例/种子的两个配置相邻执行并交替顺序，热身不纳入统计；56 个三种子最好目标中 **32 例改善、8 例退步、16 例打平**，其中车辆数 **5 例减少、2 例增加**，同车数距离 **27 例改善、6 例退步、16 例打平**。该复验使用显式配置及冻结的 M7 实验代码，默认值变化没有改变其实际参数。

与匹配车辆上限的 PyVRP 基线比较，最终默认批次有 50 例同车数，距离 gap 中位数 **28.1031%**、最近秩 90 分位 **74.4474%**。PyVRP 车辆更少的 6 例是 R105（16/15）、R109（14/12）、R112（12/10）、RC107（14/13）、RC108（14/13）、RC201（5/4）。新车辆上限补跑还发现 R105 在 16 车上限内能使用 15 车，因此不能把 6 例与 M6 原上限表的 3 例直接当作算法退步数量比较。未达到距离 gap 中位数低于 20% 的近期目标，后续 M8 继续改善候选筛选与评估速度。

## 消融与诊断

首轮 10 组消融各运行 168 次，配置显式冻结，逐次输出全部可行。部分实验与另一个 VS Code Codex 进程重叠，以下只作为探索数据；尤其局部搜索参数没有改变减车算法时出现的车辆波动，不能归因于该参数。

| 单项改动 | 比同期控制的车辆减少 / 增加 | 同车数距离改善 / 退步 / 打平 |
| --- | ---: | ---: |
| 每目标车数 10 次试探 | 3 / 0 | 6 / 2 / 45 |
| 留出 20% 剩余预算 | 0 / 8 | 27 / 3 / 18 |
| 每候选 8 次局部移动 | 2 / 3 | 0 / 7 / 44 |
| 移除 2～4 个客户 | 6 / 1 | 1 / 3 / 45 |
| 5 轮重启 | 1 / 1 | 1 / 2 / 51 |
| 截止时间优先修复 | 7 / 2 | 15 / 4 / 28 |
| 窄时间窗优先修复 | 7 / 7 | 13 / 9 / 20 |
| regret-2 修复 | 1 / 23 | 10 / 2 / 20 |
| 窄时间窗优先构造 | 5 / 4 | 7 / 12 / 28 |

regret-2 缓存的是插入选择；改变路线后对所有剩余客户重新评估该路线，未改变路线的选择保持有效。30 个随机小实例的逐轮无缓存完整重算与其结果一致。虽然实现正确，它在此次短预算下增加了大量车辆，故不作为默认策略。

当前默认值的代表实例诊断共 12 次，另存 [representative_phases.csv](m7_delivery/representative_phases.csv)。C103/C104 的局部搜索平均仍耗时 0.391623/0.387781 秒，三种子分别接受 16/17 次移动。R101/RC101 的局部搜索平均有 0.013396/0.011944 秒，分别接受 2/1 次移动；修复仍耗时 0.390652/0.400185 秒。这表明预算保留已生效，修复和局部评估仍是下一阶段的热点。诊断有计数开销，其目标值不用于质量晋级。

## 文件、源码及测量来源

| 证据 | 文件 |
| --- | --- |
| 最终 168 次结果与完整配置 | [runs.csv](m7_delivery/delivery_default/runs.csv)、[summary.json](m7_delivery/delivery_default/summary.json) |
| 最终与匹配上限的 PyVRP 逐例比较 | [vs_pyvrp.csv](m7_delivery/delivery_default/vs_pyvrp.csv) |
| 10 组消融原始记录与比较 | [ablation_runs.csv](m7_delivery/ablation_runs.csv)、[ablation_comparison.csv](m7_delivery/ablation_comparison.csv) |
| 本会话两轮更激进组合的交替探索结果 | [confirmation_runs.csv](m7_delivery/confirmation_runs.csv)、[paired_summary.json](m7_delivery/paired_summary.json) |
| 自研 2,520 个保存结果的完整重验及环境统计 | [verification.json](m7_delivery/verification.json) |
| 全部 PyVRP 原始记录与统计 | [pyvrp_runs.csv](m7_delivery/pyvrp_runs.csv)、[metrics.json](m7_delivery/metrics.json) |
| 源码和文件哈希 | [archive_manifest.json](m7_delivery/archive_manifest.json) |

2,520 次重验包括首轮 1,680 次、本会话两轮交替探索共 672 次及最终默认 CLI 168 次。采用组合的另 336 次复验和代表实例 12 次诊断另行核验。PyVRP 保留 M6 的 168 条冻结记录，再补跑 R104/10、R105/16、R107/11、R108/10、R208/2、RC207/3 共 18 次：合计 186 次，其中 177 条可行保存路线全部重验，9 次未找到可行解；不作不可行性证明。

最终源码 SHA-256 为 `bcdbfecac731779090e3a398702ae4d335968003d643f03bf90430cd1a9ec707`，实验实现版为 `fb89464a08a8315316a993462b898339a1874a83f2825c9a1b6ff87072390d28`，两版完整包均保存在 `m7_delivery/source/<hash>/vrptw/`。最终 CLI 在北京时间 2026-10-02 13:56:35～13:58:35 运行，Python 3.12.4、Windows 11、16 个逻辑 CPU、单线程，完整环境与 UTC 时间见 JSON。

额外进程的启动来源已定位为 VS Code 的 Codex 扩展 `codex.exe app-server`，并非本会话的两名子代理；系统进程信息无法确定具体会话及重复启动原因。`runs/m7_delivery_seed012_0p5s/` 加载的是旧安装包（哈希 `e638d276…`，缺少 M7 配置字段），不能验收当前源码。本会话命名为 `confirmation_clean` 的探索轮也曾与该旧包批次重叠，不能仅凭目录名认定无并发。最终验收显式指定 `PYTHONPATH=src`，运行期间的进程检查只发现本次最终批次；额外数据保留，没有删除或终止归属未明的任务。

## 重跑当前源码

从 `vrptw_solver` 目录依次执行，不与其他求解批次重叠：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
& '.\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --basetemp=tests/.pytest-m7-replay
& '.\.venv\Scripts\python.exe' -m vrptw batch data --seeds 0 1 2 --time-limit 0.5 --out runs/m7_verified_replay
& '.\.venv\Scripts\python.exe' benchmarks/diagnose.py summarise --batch runs/m7_verified_replay --out runs/m7_verified_replay/analysis
& '.\.venv\Scripts\python.exe' benchmarks/m7_confirm.py --out runs/m7_balanced_replay --set repair_order=due --set fleet_time_fraction=0.95
```

未来主线代码改变后，可把 `PYTHONPATH` 指向上述最终哈希目录来复现该版 CLI。原始输入、M6 记录和外部参考解均保留。
