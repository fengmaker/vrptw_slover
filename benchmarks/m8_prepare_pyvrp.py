"""Preserve M7 PyVRP records and list any missing M8 comparison caps."""

import csv
import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
old = ROOT / "runs/m7_pyvrp_seed012_0p5s"
out = ROOT / "runs/m8_pyvrp_seed012_0p5s"
assert not out.exists(), "M8 baseline destination must be new"
shutil.copytree(old, out)
with (out / "0p5s_runs.csv").open(encoding="utf-8", newline="") as stream:
    rows = list(csv.DictReader(stream))
existing = {(row["instance"], int(row["vehicle_cap"]), int(row["seed"])) for row in rows}
fixed = json.loads((ROOT / "benchmarks/pyvrp/caps.json").read_text(encoding="utf-8"))
batches = [ROOT / "runs/m8_ablation_corrected_seed012_0p5s" / label for label in
           ("baseline", "cached", "incremental", "n20", "n40")]
batches.append(ROOT / "runs/m8_verified_delivery_seed012_0p5s")
needed = set()
for batch in batches:
    with (batch / "batch_summary.csv").open(encoding="utf-8", newline="") as stream:
        own = list(csv.DictReader(stream))
    best = {}
    for row in own:
        assert row["status"] == "ok"
        name = row["instance"]
        best[name] = min(best.get(name, 10**9), int(row["vehicles"]))
    for name, count in best.items():
        cap = min(count, fixed[name]["vehicle_cap"])
        if any((name, cap, seed) not in existing for seed in (0, 1, 2)):
            needed.add((name, cap))
rounds = []
while needed:
    selected = {}
    for name, cap in sorted(needed):
        if name not in selected:
            selected[name] = dict(vehicle_cap=cap, source="M8 three-seed best vehicle count")
    rounds.append(selected)
    needed -= {(name, entry["vehicle_cap"]) for name, entry in selected.items()}
for i, caps in enumerate(rounds, 1):
    (out / f"supplement_caps_{i}.json").write_text(json.dumps(caps, indent=2) + "\n", encoding="utf-8")
origin = dict(source=str(old.resolve()), original_rows=len(rows),
              original_csv_sha256=hashlib.sha256((old / "0p5s_runs.csv").read_bytes()).hexdigest(),
              supplement_cap_rounds=rounds)
(out / "m8_baseline_origin.json").write_text(json.dumps(origin, indent=2) + "\n", encoding="utf-8")
print(json.dumps(origin, indent=2))
