"""Select isolated rechecks into a new M8 dataset, preserving both originals."""

import argparse
import json
from pathlib import Path
import shutil

from m8_experiment import _read_csv, _sha256, _write_json, _write_csv, analyse_experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--replacement", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    assert not args.out.exists(), "output must be new"
    original = json.loads((args.original / "experiment.json").read_text(encoding="utf-8"))
    replacement = json.loads((args.replacement / "experiment.json").read_text(encoding="utf-8"))
    assert original["status"] == replacement["status"] == "completed"
    assert original["variants"].keys() == replacement["variants"].keys()
    assert set(replacement["instances"]) <= set(original["instances"])
    assert replacement["seeds"] == original["seeds"], "recheck must cover the same seeds"
    expected = {(name, str(seed)) for name in replacement["instances"] for seed in original["seeds"]}
    shutil.copytree(args.original, args.out)
    for label in original["variants"]:
        assert original["variants"][label]["config"] == replacement["variants"][label]["config"]
        assert original["variants"][label]["code_sha256"] == replacement["variants"][label]["code_sha256"]
        old_rows = _read_csv(args.original / label / "batch_summary.csv")
        new_rows = _read_csv(args.replacement / label / "batch_summary.csv")
        changes = {(row["instance"], row["seed"]): row for row in new_rows}
        assert len(changes) == len(new_rows) and set(changes) == expected
        old_keys = {(row["instance"], row["seed"]) for row in old_rows}
        assert expected <= old_keys
        for row in new_rows:
            assert row["status"] == "ok" and row["feasible"] == "True"
            run = Path(label) / row["instance"] / f"seed-{row['seed']}"
            for file in (args.replacement / run).iterdir():
                if file.is_file():
                    shutil.copyfile(file, args.out / run / file.name)
        selected = [changes.get((row["instance"], row["seed"]), row) for row in old_rows]
        _write_csv(args.out / label / "batch_summary.csv", selected, tuple(old_rows[0]))
        metadata = json.loads((args.out / label / "batch_summary.json").read_text(encoding="utf-8"))
        metadata["finished_at_utc"] = replacement["finished_at_utc"]
        metadata["isolated_recheck_instances"] = replacement["instances"]
        _write_json(args.out / label / "batch_summary.json", metadata)
        original["variants"][label]["batch_summary_sha256"] = _sha256(args.out / label / "batch_summary.csv")
    replaced = {(case["instance"], case["seed"]): case for case in replacement["order"]["cases"]}
    for case in original["order"]["cases"]:
        key = (case["instance"], case["seed"])
        if key in replaced:
            new = replaced[key]
            case["original_results"] = case["results"]
            case["results"], case["variants"] = new["results"], new["variants"]
            case["isolated_recheck_case_index"] = new["case_index"]
    original["finished_at_utc"] = replacement["finished_at_utc"]
    original["isolated_recheck"] = dict(original=str(args.original.resolve()),
                                       replacement=str(args.replacement.resolve()),
                                       instances=replacement["instances"], measured_runs=original["total_runs"] + replacement["total_runs"],
                                       reason="brief added boundary pytest overlapped tail; conservatively recheck R211 and RC101")
    _write_json(args.out / "experiment.json", original)
    shutil.copyfile(args.original / "experiment.json", args.out / "original_experiment.json")
    shutil.copyfile(args.replacement / "experiment.json", args.out / "recheck_experiment.json")
    analyse_experiment(argparse.Namespace(out=args.out, data=Path(original["data_directory"])))
    print(f"selected {len(replaced)} rechecked cases into {args.out}; original datasets preserved")


if __name__ == "__main__":
    main()
