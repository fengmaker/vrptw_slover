# M0～M12 测试

M12 当前源码 **348 项测试通过**，包含可选原生后端；本轮已在 Windows 构建扩展后执行全部测试。无扩展时原生专用测试跳过，Python／自动回退测试仍运行。完整验收见 [M12 报告](../benchmarks/M12_REPORT.md)。

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
- `test_m9_moves.py`：三种连续片段移动的同／跨路线字面结果、越界／重叠拒绝、固定车数与连接筛选；1350 个真实随机移动对照独立完整验证；first／best 的三评估模式接受序列一致、轮换组合一致、截止时间与超过两次接受。
- `test_m9_config.py`：无限／显式移动预算、算子及调度开关、诊断下固定工作量一致、CLI 配置落盘；空路线不能被固定车数移动激活。
- `test_m9_experiment.py`：旋转配置顺序、冻结 M8 与 M9、短预算路线与诊断记录保存／重验、混配置哈希拒绝及轮换消融合同。
- `test_m10_fleet.py`：750 组真实插入最优位置对照完整重算、后悔插入、内部不完整解覆盖守恒、R101／R109／R112 的多轮减车修复、1 tick 边界和取整距离移除回滚、目标统计与截止保护。
- `test_m10_experiment.py`：冻结 M9 控制、减车消融配置、旋转顺序、短预算路线保存／重验和篡改拒绝。
- `test_m10_baseline.py`：PyVRP 显式减车与距离搜索共享总预算、超时剩余预算、初始车辆上限、路线重验及重复种子拒绝。
- `test_m11_penalties.py`：容量／time warp 手算、避免重复累计传播迟到、回仓边界、完整覆盖与车辆上限、独立罚分窗口、整数倍率及参数验证。
- `test_m11_search.py`：固定种子迭代复现、不可行当前与可行公开 best、保留途中可行解、罚分更新后清空接受阈值、截止丢弃不完整修复、七算子汇总差分、固定车辆与空槽、每轮移动上限及显式覆盖、CLI 轨迹和摘要。
- `test_m11_experiment.py`：同预算配置、轮换顺序、冻结源码及输出、独立路线重验、源码／输入／结果／协议篡改拒绝、阶段计时与候选计数及诊断 CSV／JSON 一致。
- `test_m12_native.py`：2310 个真实随机移动对照完整 Python 重算；七算子正／反序、first／best、fixed／cyclic 的完整接受序列一致；边界、缓存更新、邻居筛选、截止、空槽、缺失扩展／不兼容配置／大整数回退、非法输入和极端索引。
- `test_m12_experiment.py`：独立 CLI 入口、源码／二进制冻结、交替次序、微型串行对照、逐次与批量诊断一致、源码／二进制／输入／结果／配置／预算篡改拒绝、失败批次拒绝，以及结果哈希被更新后仍由独立验证拒绝虚报。

M11 验收源码共 309 项测试通过。Windows 本机系统临时目录曾有权限故障，使用工作区内新的 `--basetemp=tests/.pytest-m12-<name>` 运行，避免覆盖已存在的临时目录。M11 完整消融、诊断与晋级结论见 [M11 报告](../benchmarks/M11_REPORT.md)。

M10 验收源码共 264 项测试通过；两轮各 840 条消融、63 条代表诊断和 168 条最终 CLI 路线共 1911 条另外独立验证。本轮 PyVRP 的 208 条固定上限可行路线、165 条显式减车可行路线也重验，两个协议分开报告。默认晋级和质量限制见 [M10 报告](../benchmarks/M10_REPORT.md)。M9 的 225 项测试及历史验收见 [M9 报告](../benchmarks/M9_REPORT.md)，M8 精确缓存证据见 [M8 报告](../benchmarks/M8_REPORT.md)，M7 见 [M7 交付报告](../benchmarks/M7_DELIVERY_REPORT.md)。

开启诊断时，`test_report_cli.py` 还检查诊断 CSV/JSON 与批量阶段输出。M6 完整实验另用 `benchmarks/diagnose.py` 从 336 个自研结果和 162 个 PyVRP 可行结果的保存路线再次独立重算，并核对配置、哈希和计数/计时一致性。验收见 [M6 报告](../benchmarks/M6_REPORT.md)。

`fixtures/C101.sol` 来自 `PyVRP-main/results/solomon/C101.sol`。测试故意不读取该文件自报的 `Cost`。

## 四方对比脚本检查

`test_four_solver_contract.py` 检查独立裁决与车辆优先成本；`test_four_solver_analysis.py` 检查缺失解、不同车数、种子选择、分位数和预算收益；Gurobi、OR-Tools 两组测试包含小实例穷举最优解与边界条件。Gurobi 另验证零需求/零行程子回路。

```powershell
.\.venv-comparison\Scripts\python.exe -m pytest tests/test_four_solver_contract.py tests/test_four_solver_analysis.py tests/test_four_solver_gurobi.py tests/test_four_solver_ortools.py -q -p no:cacheprovider --basetemp tests/.pytest-four-solver
```

本轮 13 项通过，另有 3 条 OR-Tools SWIG 弃用警告。Gurobi 求解测试需在持证账户运行；无有效许可证时对应求解测试跳过。实验路线重验使用 `benchmarks/four_solver/run.py verify --out <目录>`，完整命令见 [实验说明](../benchmarks/four_solver/README.md)。
