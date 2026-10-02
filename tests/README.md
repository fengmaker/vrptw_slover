# M0～M8 测试

在项目根目录运行：

```powershell
python -m pytest -q -p no:cacheprovider --basetemp=tests/.pytest-local
```

- `test_problem.py`：不可变输入、编号和字段约束、千分位舍入矩阵。
- `test_solomon.py`：全部 56 个原始 TXT、C101 字段、坏行与转换格式拒绝。
- `test_evaluate.py`：等待、截止边界、超载、回仓、覆盖、空路线车辆计数，以及 C101 外部路线重新验证。
- `test_construct.py`：C101 自研初始可行解、确定性、无法插入时的明确原因。
- `test_moves.py`：四种邻域、客户守恒、真实 C101 移动差分、不可行移动拒绝和局部最优停止。
- `test_solver.py`：C101 自研 10 车、容量下界和零迭代行为。
- `test_ils_fleet.py`：固定种子/迭代复现、零秒停止、参数验证。
- `test_report_cli.py`：五个输出文件、篡改自报成本后的独立验证、批量单例失败继续。
- `test_diagnostics.py`：嵌套计时、候选与完整路线重算、拒绝/容量预筛选、接受移动数、开关下固定工作量一致性及零预算阶段。
- `test_benchmark_compare.py`：冻结车辆上限、不同时间预算拒绝、M5 侧文件兼容和新车辆上限补跑要求。
- `test_m7_search.py`：紧迫客户修复、内部截止检查丢弃未完成修复、减车时间份额、容量下界跳过减车；regret-2 的缓存选择与 30 个随机小实例的逐轮完整重算一致，诊断开关不改变固定工作量结果。
- `test_m7_experiment.py`：消融配置冻结、路线保存后重验、实例/种子覆盖和 M6 预算核对、最近秩 90 分位与车辆优先比较。
- `test_m7_confirm.py`：相邻运行的配置顺序交替、首次可行耗时配对及三种子最好目标统计。
- `test_neighbourhood.py`：整数相关性、稳定排序、对称邻居、等待／时间窗排名与四种算子的连接筛选。
- `test_m8_evaluation.py`：2400 个真实随机移动及随机紧窗实例的精确差分；前缀／后缀载重、距离、回仓时间和违规原因与完整重算一致；缓存更新、非空接受序列、完整邻域、诊断计数和非法参数。

M8 当前源码共 171 项测试通过；840 条主消融、168 条最终 CLI 和 60 条吞吐实验路线另外独立验证。精确缓存、默认选择与质量限制见 [M8 报告](../benchmarks/M8_REPORT.md)。M7 历史验收与退步记录见 [M7 交付报告](../benchmarks/M7_DELIVERY_REPORT.md)，补充消融见 [M7 报告](../benchmarks/M7_REPORT.md)。

开启诊断时，`test_report_cli.py` 还检查诊断 CSV/JSON 与批量阶段输出。M6 完整实验另用 `benchmarks/diagnose.py` 从 336 个自研结果和 162 个 PyVRP 可行结果的保存路线再次独立重算，并核对配置、哈希和计数/计时一致性。验收见 [M6 报告](../benchmarks/M6_REPORT.md)。

`fixtures/C101.sol` 来自 `PyVRP-main/results/solomon/C101.sol`。测试故意不读取该文件自报的 `Cost`。
