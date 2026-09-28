# VRPTW 求解器实施状态

目标与阶段验收以 [`../PyVRP-main/VRPTW_SOLVER_PROJECT_PLAN.md`](../PyVRP-main/VRPTW_SOLVER_PROJECT_PLAN.md) 为准。本目录是独立项目，只实现标准 Solomon VRPTW。数学和数值合同固定在 [SPEC.md](SPEC.md)。

| 里程碑 | 状态 | 验收证据 |
| --- | --- | --- |
| M0 规则冻结 | 完成 | `SPEC.md` 固定七字段 TXT、千分位整数、时间窗、车辆优先目标及 JSON/SOL 格式；保存 `tests/fixtures/C101.sol` |
| M1 解析与验证 | 完成 | `src/vrptw/{problem,solomon,evaluate}.py`；56 个 TXT、手算边界、C101 参考路线的测试通过 |
| M2 构造解 | 完成 | `construct` 全位置可行插入；C101 自研 12 车、983.072；失败指出客户与已尝试车辆 |
| M3 局部搜索 | 完成 | relocate、swap、2-opt、2-opt*；C101 固定 12 车改进至 921.385；移动差值与完整验证一致 |
| M4 ILS 与减车 | 完成 | 固定种子/迭代路线与距离可重现；C101 自研 10 车、828.937，达到容量下界 |
| M5 批量与性能 | 完成 | 56 实例 × 3 种子，统一 0.5 秒预算；168/168 成功并再次验证；热点分析见报告 |

当前 `solve(instance, Config(...))` 依次运行构造、减车与 ILS；命令行提供 `solve / validate / batch`，成功运行写五种结果文件。C101 自研 10 车解通过独立验证，`tests/fixtures/C101.sol` 仍只是外部参考，不参与求解。M5 完整记录在 [benchmarks/M5_REPORT.md](benchmarks/M5_REPORT.md)，原始结果在忽略源码管理的 `runs/m5_delivery_seed012_0p5s/`。
