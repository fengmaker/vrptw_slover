"""Build compact Chinese tables from the independently validated experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))
from four_solver.analyse import analyze, load_runs

LABELS = {"ours": "自研 M12", "pyvrp": "PyVRP", "ortools": "OR-Tools", "gurobi": "Gurobi MILP"}


def number(value, digits=3):
    return "—" if value is None or value == "" else f"{float(value):.{digits}f}"


def table(headers, rows):
    clean = lambda value: str(value).replace("|", "\\|")
    return "\n".join(["| " + " | ".join(headers) + " |",
                      "| " + " | ".join("---" for _ in headers) + " |",
                      *["| " + " | ".join(clean(v) for v in row) + " |" for row in rows]]) + "\n"


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    rank = (len(values) - 1) * fraction
    lo = int(rank)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (rank - lo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-run", type=Path, required=True)
    parser.add_argument("--distance-run", type=Path)
    parser.add_argument("--diagnostic-run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--table-dir", type=Path, help="link target for CSV tables; default: primary-run/tables")
    args = parser.parse_args()
    manifest = json.loads((args.primary_run / "manifest.json").read_text(encoding="utf-8"))
    protocol = manifest["protocol"]
    rows = load_runs(args.primary_run / "runs.csv")
    analysis = analyze(rows, protocol["instances"], protocol["budgets"])
    lines = ["# M12 与 PyVRP、OR-Tools、Gurobi 的短预算对比", "",
             f"{len(protocol['instances'])} 个 Solomon 100 客户实例；种子 {protocol['seeds']}；预算 {protocol['budgets']} 秒。"
             "每个实例独立冷启动，四方采用原始车辆上限和相同整数矩阵，按车辆数、距离依次优化。", "",
             "距离单位为原坐标单位；内部成本仍是整数 ticks。每条完整路线均经独立验证。"
             f"每例每档运行 {len(protocol['seeds'])} 个种子；多种子时逐实例取车辆优先的最好可行解，"
             "相应搜索努力为种子数乘单次预算。成绩是本机这一轮观察。", "",
             "主表 PyVRP 使用支配距离的车辆固定成本，Gurobi 使用本项目实现的 MILP；"
             + ("后面的固定车辆上限距离表用于观察 PyVRP 的路线搜索能力。" if args.distance_run else
                "可另跑固定车辆上限距离对照来观察 PyVRP 的路线搜索能力。")
             + "模型和配置对结果的影响应随表一起解读。", "",
             "## 四方主表", ""]
    summary = []
    for row in analysis["summary"]:
        summary.append([row["budget_seconds"], LABELS[row["solver"]],
                        f"{row['feasible_instance_count']}/{row['instance_count']}",
                        row["vehicle_sum"] if row["vehicle_sum"] is not None else "—",
                        f"{row['vehicle_better_vs_pyvrp']}/{row['vehicle_equal_vs_pyvrp']}/{row['vehicle_worse_vs_pyvrp']}" if row["solver"] != "pyvrp" else "基准",
                        row["same_vehicle_distance_gap_count"] if row["solver"] != "pyvrp" else "—",
                        number(row["same_vehicle_distance_gap_median_percent"]),
                        number(row["same_vehicle_distance_gap_p90_percent"])])
    lines += [table(["预算/s", "求解器", "可行实例", "总车辆*", "对 PyVRP 车数少/同/多", "同车数 n", "距离 gap 中位数/%", "P90/%"], summary),
              "`*` 只有全部实例可行才填总车辆；距离 gap 为 `(本方距离/PyVRP距离−1)×100%`，正数表示更长。车数不同不计算距离 gap。", "",
              "## 自研相对各后端", ""]
    best = {}
    for row in rows:
        if str(row.get("feasible", "")).lower() != "true":
            continue
        identity = (row["solver"], row["instance"], float(row["budget_seconds"]))
        quality = (int(row["vehicles"]), int(row["distance_ticks"]))
        if identity not in best or quality < best[identity]:
            best[identity] = quality
    pair_rows = []
    tails = []
    for budget in sorted(protocol["budgets"]):
        for other in ("pyvrp", "ortools", "gurobi"):
            pairs = [(name, best[("ours", name, budget)], best[(other, name, budget)])
                     for name in protocol["instances"] if ("ours", name, budget) in best and (other, name, budget) in best]
            gaps = [100 * (own[1] / ref[1] - 1) for _, own, ref in pairs if own[0] == ref[0] and ref[1]]
            pair_rows.append([budget, LABELS[other], len(pairs),
                              sum(own[0] < ref[0] for _, own, ref in pairs),
                              sum(own[0] == ref[0] for _, own, ref in pairs),
                              sum(own[0] > ref[0] for _, own, ref in pairs),
                              number(statistics.median(gaps) if gaps else None), number(percentile(gaps, .9))])
            for name, own, ref in pairs:
                if own[0] == ref[0] and ref[1]:
                    tails.append({"budget_seconds": budget, "reference": other, "instance": name,
                                  "vehicles": own[0], "ours_distance": own[1] / 1000,
                                  "reference_distance": ref[1] / 1000,
                                  "distance_gap_percent": 100 * (own[1] / ref[1] - 1)})
    lines += [table(["预算/s", "对照", "共同可行 n", "自研少车", "同车", "自研多车", "同车 gap 中位数/%", "P90/%"], pair_rows),
              "共同可行集合随对照而变，Gurobi 缺失解不能记成自研质量胜出。", "", "## 增加到 5 秒的收益", ""]
    lines.append(table(["求解器", "减车", "同车更短", "同车更长", "同车同距", "车更多", "仅5s可行", "仅0.5s可行", "两档均无解"],
                       [[LABELS[r["solver"]], r["fewer_vehicles_at_5s"], r["same_vehicles_shorter_distance_at_5s"],
                         r["same_vehicles_longer_distance_at_5s"], r["same_lexicographic_result"], r["more_vehicles_at_5s"], r["feasible_only_at_5s"],
                         r["lost_feasibility_at_5s"], r["no_feasible_solution_at_either_budget"]]
                        for r in analysis["improvement_summary"]]))
    lines += ["", "## 实际耗时", "",
              "预算约束求解调用，外部建模单列。端到端为适配器完整调用加独立验证，排除读入、导入和报告写入。", "",
              table(["预算/s", "求解器", "建模中位/s", "搜索中位/s", "端到端中位/s"],
                    [[r["budget_seconds"], LABELS[r["solver"]], number(r["median_preparation_seconds"], 4),
                      number(r["median_search_seconds"], 4), number(r["median_total_seconds"], 4)] for r in analysis["summary"]])]
    if args.distance_run:
        supplement = json.loads((args.distance_run / "manifest.json").read_text(encoding="utf-8"))
        primary_hash = hashlib.sha256((args.primary_run / "runs.csv").read_bytes()).hexdigest()
        if supplement["cap_source_sha256"] != primary_hash:
            raise ValueError("distance supplement belongs to a different primary run")
        expected_caps = {(r["instance"], float(r["budget_seconds"]), int(r["seed"]), int(r["vehicles"]), r["input_sha256"])
                         for r in rows if r["solver"] == "ours" and str(r["feasible"]).lower() == "true"}
        for name, budget, seed, cap, input_hash in supplement["cases"]:
            if (name, float(budget), int(seed), int(cap), input_hash) not in expected_caps:
                raise ValueError("distance supplement case/cap does not match primary data")
        with (args.distance_run / "summary.csv").open(encoding="utf-8-sig", newline="") as stream:
            distance_rows = list(csv.DictReader(stream))
        with (args.distance_run / "comparison.csv").open(encoding="utf-8-sig", newline="") as stream:
            distance_cases = list(csv.DictReader(stream))
        lines += ["", "## 补充：固定自研车辆上限后的 PyVRP 距离对照", "",
                  "每例只把主实验自研找到的车辆数作为 PyVRP 上限，固定成本为 0，冷启动，不传路线。"
                  "这是给定车队后的距离诊断，与主表的原始上限车辆优先任务分开。", "",
                  table(["预算/s", "类别", "PyVRP可行/运行", "PyVRP更少车", "同车 n", "自研距离 gap 中位数/%", "P90/%"],
                        [[r["budget_seconds"], r["family"], f"{r['feasible']}/{r['runs']}", r["pyvrp_fewer_vehicles"], r["same_vehicles"],
                          number(r["distance_gap_median_percent"]), number(r["distance_gap_p90_percent"])] for r in distance_rows])]
        lines += ["同车数集合在两档预算中分别选择，样本数不同；预算收益另看前面的逐实例配对表。", "",
                  "### 5 秒距离长尾", "",
                  table(["实例", "车数", "自研距离", "PyVRP距离", "自研 gap/%"],
                        [[r["instance"], r["ours_vehicles"], number(int(r["ours_distance_ticks"]) / 1000),
                          number(r["distance"]), number(r["distance_gap_percent"])]
                         for r in sorted([r for r in distance_cases if float(r["budget_seconds"]) == 5 and r["distance_gap_percent"]],
                                         key=lambda r: -float(r["distance_gap_percent"]))[:6]]),
                  "### 补充搜索找到更少车辆的实例", "",
                  table(["预算/s", "实例", "自研车数", "PyVRP车数"],
                        [[r["budget_seconds"], r["instance"], r["ours_vehicles"], r["vehicles"]]
                         for r in distance_cases if r["vehicle_gap_ours_minus_pyvrp"] and float(r["vehicle_gap_ours_minus_pyvrp"]) > 0])]
    if args.diagnostic_run:
        diagnostic = json.loads((args.diagnostic_run / "manifest.json").read_text(encoding="utf-8"))
        if diagnostic["code_sha256"] != protocol["solver_code_sha256"]:
            raise ValueError("diagnostic solver source differs from primary run")
        with (args.diagnostic_run / "phases.csv").open(encoding="utf-8-sig", newline="") as stream:
            phase_rows = list(csv.DictReader(stream))
        groups = {}
        for row in phase_rows:
            groups.setdefault((row["instance"], float(row["budget_seconds"])), {})[row["phase"]] = row
        lines += ["", "## 自研阶段诊断（另跑，不参与质量排名）", "",
                  "下表为诊断模式的独占阶段占比；子阶段已经从父阶段扣除，各阶段可相加。计数与计时会有插桩开销。", "",
                  table(["实例", "预算/s", "构造/%", "减车控制/%", "修复/%", "局部搜索/%", "验证/%", "扰动/%", "ILS控制/%", "其他/%", "ILS轮数"],
                        [[name, budget, *[number(float(phases.get(p, {}).get("runtime_share_percent", 0)), 1)
                                         for p in ("construction", "fleet_reduction", "repair", "local_search", "validation", "perturbation", "ils_control", "other")],
                          next(iter(phases.values()))["iterations"]] for (name, budget), phases in sorted(groups.items())])]
        repair_shares = [float(p["repair"]["runtime_share_percent"]) for (name, _), p in groups.items()
                         if name in ("R101", "RC101")]
        local_shares = [float(p["local_search"]["runtime_share_percent"]) for (name, _), p in groups.items()
                        if name in ("C103", "C104")]
        lines += ["", "## 差距与后续优先级", ""]
        if repair_shares:
            lines += [f"R101/RC101 的修复占比为 {min(repair_shares):.1f}%–{max(repair_shares):.1f}%。"
                      "这部分仍在 Python；短预算中构造、减车和修复挤占原生局部搜索时间。优先优化或迁移修复热点，并单独验证减车成功率。", ""]
        if local_shares:
            lines += [f"C103/C104 的局部搜索占比为 {min(local_shares):.1f}%–{max(local_shares):.1f}%。"
                      "5 秒长尾仍保留较大距离差，提示还需对扰动、重启与路线重组做消融，增加时间并未消除所有局部最优。"
                      "阶段占比来自插桩诊断，以上属于结合质量结果的推断。", ""]
    pyvrp_improvement = next((r for r in analysis["improvement_summary"] if r["solver"] == "pyvrp"), None)
    if pyvrp_improvement:
        lines += [f"主表 PyVRP 采用严格车辆成本的这组参数，5 秒时有 {pyvrp_improvement['same_lexicographic_result']} 例与 0.5 秒完全相同。"
                  "这一配置的停滞需另用罚分校准或官方减车流程调查；固定上限距离表更适合定位自研路线质量差距。", ""]
    gurobi_5 = [json.loads(r.get("metadata_json") or "{}") for r in rows
                if r["solver"] == "gurobi" and float(r["budget_seconds"]) == 5 and str(r["feasible"]).lower() == "true"]
    if gurobi_5:
        mip_gaps = [r["mip_gap"] for r in gurobi_5 if "mip_gap" in r]
        nodes = [r["node_count"] for r in gurobi_5 if "node_count" in r]
        lines += [f"Gurobi 5 秒可行记录的 MIP gap 中位数为 {100 * statistics.median(mip_gaps):.2f}%，"
                  f"节点数中位数为 {statistics.median(nodes):g}。这份基础 MTZ 模型的松弛下界较弱，短预算主要停留在根节点。"
                  "它未配置额外车数/容量有效不等式或热启动；结果反映本模型和参数，不能泛化为 Gurobi 的 VRPTW 能力上限。", ""]
    tables = args.table_dir or args.primary_run / "tables"
    versions = {solver: set() for solver in ("pyvrp", "ortools", "gurobi")}
    for row in rows:
        metadata = json.loads(row.get("metadata_json") or "{}")
        solver = row["solver"]
        if solver in versions:
            value = metadata.get({"pyvrp": "version", "ortools": "ortools_version", "gurobi": "gurobi_version"}[solver])
            if value is not None:
                versions[solver].add(".".join(map(str, value)) if isinstance(value, list) else str(value))
    version_text = {k: "/".join(sorted(v)) or "未记录" for k, v in versions.items()}
    relative = lambda path: Path(os.path.relpath(path.resolve(), args.out.parent.resolve())).as_posix()
    lines += ["", "## 逐实例表与复现", "",
              f"[完整协议和运行命令]({relative(ROOT / 'benchmarks/four_solver/README.md')})；"
              f"[四方逐实例 CSV]({relative(tables / 'per_instance_wide.csv')})；"
              f"[分组 CSV]({relative(tables / 'group_summary.csv')})；"
              f"[原始逐次记录]({relative(tables / 'raw_runs.csv')})。", "",
              f"PyVRP {version_text['pyvrp']} 为本地 ILS；OR-Tools {version_text['ortools']} 为 RoutingModel 插入 + GLS；"
              f"Gurobi {version_text['gurobi']} 为本项目二下标弧 MILP。"
              "主表 PyVRP 使用严格车辆固定成本，没有调用其独立 `minimise_fleet()`，不能将该配置的成绩泛化为库的最佳水平。", "",
              "Gurobi 的原始浮点目标、整数路线目标和两者差值均保留；公开成本以独立验证为准。"
              "其 bound/MIP gap 是车辆固定成本加距离的标量目标 gap，不能读成距离 gap；optimal 状态采用 Gurobi 的数值容差。", ""]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    tail_path = args.out.with_name("four_solver_quality_tails.csv")
    tails.sort(key=lambda r: (r["budget_seconds"], r["reference"], -r["distance_gap_percent"]))
    with tail_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, list(tails[0]) if tails else ["instance"])
        writer.writeheader()
        writer.writerows(tails)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
