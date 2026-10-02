# 标准 Solomon VRPTW 合同（M0）

本项目只读取原始 Solomon `.txt`：实例名；`VEHICLE` 节中的最大车辆数与每车容量；`CUSTOMER` 节逐行七个整数 `ID X Y DEMAND READY DUE SERVICE`。节点 0 是唯一仓库，客户编号必须按 1..N 连续出现。需求、时间窗端点和服务时长非负，`READY <= DUE`，仓库需求与服务时长为 0。输入中的车辆数是上限。

## 数值与目标

- 对每条弧独立计算 `round(1000 * hypot(dx, dy))`。采用 Python `round` 的半偶舍入；行驶时间和距离共用这张整数矩阵。原始时间窗与服务时长乘 1000；容量和需求不缩放。内部距离、时间均为整数，报告时除以 1000。
- 路线只记录客户序列，仓库 0 隐含在首尾。每辆车从仓库 `ready` 出发，至多跑一趟。到达客户 `j` 后，`start_j = max(arrival_j, ready_j)`，`departure_j = start_j + service_j`。客户必须满足 `start_j <= due_j`；提前到达可等待。总需求不超过容量，回仓时间不超过仓库 `due`。
- 每个客户必须恰好访问一次。空路线可出现，但不计车辆数。只比较完整可行解，按 `(实际非空车辆数, 总距离整数值)` 字典序最小化；距离值相同允许任意路线顺序。`validate_solution` 从客户序列完整重算，任何自报的可行性或成本均不参与判断。
- 数值规则固定为 `solomon_exact_1000_v1`。改变比例或舍入方式需要新规则标识和重新生成的结果；不能混合比较。

## 结果格式合同

`python -m vrptw solve` 按下列合同写出结果，`python -m vrptw validate` 只取路线顺序重新裁决。`history.csv` 记录构造、减车及 ILS 中当前/候选/最佳解的车辆数、距离、可行性与耗时；`routes.png` 和 `convergence.png` 用于人工检查。

`solution.json` 使用 UTF-8 JSON，对外字段：

```json
{
  "schema_version": 1,
  "instance": "C101",
  "input_format": "solomon_txt",
  "routes": [[81, 78], [13, 17]],
  "vehicles": 2,
  "distance_ticks": 123456,
  "distance": 123.456,
  "feasible": true,
  "seed": 0,
  "threads": 1,
  "stop": {"reason": "max_iterations", "max_iterations": 1000, "time_limit_seconds": null},
  "runtime_seconds": 1.234,
  "first_feasible_seconds": 0.123,
  "numeric_rule": "solomon_exact_1000_v1",
  "input_sha256": "...",
  "code_sha256": "..."
}
```

示例仅说明结构，并非 C101 解。`routes` 不含仓库；`vehicles` 是非空路线数；`distance_ticks` 是精确的内部整数值，`distance` 等于它除以 1000；`feasible` 必须来自重新验证；`seed`、单线程数、首个可行解耗时、停止原因和总耗时记录实际运行。CLI 写入输入文件和求解器源码哈希。只有可行解才可输出为正式 `solution.json`。

M6 增加可选诊断字段，不改变数值规则或解格式版本：`config` 保存完整求解配置，`vehicle_limit` 保存原始输入上限，`fleet.milestones` 保存减车进展。开启诊断时，`diagnostics` 包含 `schema_version=1` 和逐阶段统计 `phases`，同一统计另写 `diagnostics.csv`；批量额外写 `batch_diagnostics.csv`。阶段自身耗时排除子阶段，各阶段之和等于 `runtime_seconds`；包含子阶段的耗时只用于观察父阶段。验证器仍只读取 `routes`。字段定义见 [README.md](README.md#M6-分阶段诊断)。

`routes.sol` 每条非空路线一行：`Route #1: 81 78 ...`，顺序从 1 开始；最后一行 `Cost: 828.937`，为总距离的千分位报告值。读取路线时，`Cost` 仅供人工参考，验证器必须忽略并重新计算。

## M0 基准

[`tests/fixtures/C101.sol`](tests/fixtures/C101.sol) 复制自 `PyVRP-main/results/solomon/C101.sol`，仅用作外部参考。该文件的 `Cost: 828937` 是千分位内部整数。独立解析、验证后的结果：100 位客户各访问一次、10 条非空路线、总距离 `828937`（`828.937`）、全部硬约束满足。C101 总需求 1810、容量 200，容量下界为 `ceil(1810/200)=10` 辆；此参考路线达到车辆数下界。它不是本项目搜索算法的产出，也不宣称距离全局最优。
