# Four-solver VRPTW comparison

The input contains 448 rows across 56 observed instances and budgets 0.5, 5 seconds. The report checks against 56 expected instances from the run manifest when available; without a manifest it uses the instances present in runs.csv. The standard full Solomon comparison contains 56 instances.

Per-instance summary values use the lexicographically best feasible result across observed attempts (one observed run per solver-instance-budget). A multi-run selection represents the effort of running each attempt, not single-run quality. Vehicle sums and means are populated only when every instance in the table has a feasible selected result.

## Primary protocol

- Start: cold start from the original Solomon input and frozen vehicle cap.
- Objective: lexicographic (vehicles, distance); our solver uses direct lexicographic selection and the reference adapters use their recorded dominating fixed vehicle cost.
- Warm start: no reference warm start.
- Timing: search budget excludes external model construction; preparation and search are reported separately; reported medians include preparation, search, solver runtime, validation, and total wall time where available.
- Validation: feasibility and objective values are taken from central validation recorded in runs.csv.
- Per-solver model and version details are summarized from each row's `metadata_json` in `summary.json`; the raw metadata remains in `raw_runs.csv`.

## Overall summary by budget

| Budget (s) | Solver | Feasible runs | Feasible instances | Vehicle sum* | Vehicle mean* | Vehicles better/equal/worse vs PyVRP | Same-K distance gap median (%) | Same-K p90 (%) | Median prep (s) | Median search (s) | Median total (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.5 | ours | 56/56 | 56/56 | 454 | 8.1071 | 28/24/4 (56 common) | 7.7841 | 45.4805 | 0.0001 | 0.5007 | 0.5021 |
| 0.5 | pyvrp | 56/56 | 56/56 | 490 | 8.75 | 0/0/0 (0 common) | — | — | 0.0012 | 0.5037 | 0.5074 |
| 0.5 | ortools | 56/56 | 56/56 | 463 | 8.2679 | 24/28/4 (56 common) | 1.5331 | 9.3252 | 0.0029 | 0.5007 | 0.5063 |
| 0.5 | gurobi | 27/56 | 27/56 | — | — | 1/1/25 (27 common) | 0 | 0 | 0.7454 | 0.503 | 1.2669 |
| 5 | ours | 56/56 | 56/56 | 443 | 7.9107 | 32/24/0 (56 common) | 0.0608 | 4.1299 | 0.0001 | 5.0007 | 5.0021 |
| 5 | pyvrp | 56/56 | 56/56 | 490 | 8.75 | 0/0/0 (0 common) | — | — | 0.0011 | 5.0038 | 5.0072 |
| 5 | ortools | 56/56 | 56/56 | 447 | 7.9821 | 33/23/0 (56 common) | 0 | 3.9338 | 0.0031 | 5.0006 | 5.0062 |
| 5 | gurobi | 54/56 | 54/56 | — | — | 5/3/46 (54 common) | 0 | 6.0525 | 0.7693 | 5.0056 | 5.7977 |

`*` Vehicle sum and mean are blank unless feasible coverage is complete. Pairwise vehicle comparisons use the common-feasible per-instance subset. Distance gaps compare only solutions with equal vehicle counts and are relative to PyVRP; positive means longer distance than PyVRP.
Runtime medians use all raw run rows for that solver and budget. `effective_seed_count` in `summary.csv` collapses repeated runs to one seed group when metadata explicitly says the backend does not support seeds.

## Instance-family summary

| Family | Budget (s) | Solver | Feasible instances | Vehicle sum* | Vehicle mean* | Vehicles better/equal/worse vs PyVRP | Same-K gap median (%) | Same-K p90 (%) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C | 0.5 | ours | 17/17 | 116 | 6.8235 | 6/9/2 (17 common) | 3.0615 | 13.6776 |
| C | 0.5 | pyvrp | 17/17 | 120 | 7.0588 | 0/0/0 (0 common) | — | — |
| C | 0.5 | ortools | 17/17 | 114 | 6.7059 | 6/11/0 (17 common) | 0 | 3.0615 |
| C | 0.5 | gurobi | 8/17 | — | — | 0/1/7 (8 common) | 0 | 0 |
| C | 5 | ours | 17/17 | 114 | 6.7059 | 6/11/0 (17 common) | 0 | 7.6019 |
| C | 5 | pyvrp | 17/17 | 120 | 7.0588 | 0/0/0 (0 common) | — | — |
| C | 5 | ortools | 17/17 | 114 | 6.7059 | 6/11/0 (17 common) | 0 | 0 |
| C | 5 | gurobi | 17/17 | 148 | 8.7059 | 2/3/12 (17 common) | 0 | 6.0525 |
| R | 0.5 | ours | 23/23 | 200 | 8.6957 | 13/8/2 (23 common) | 12.3581 | 53.7432 |
| R | 0.5 | pyvrp | 23/23 | 216 | 9.3913 | 0/0/0 (0 common) | — | — |
| R | 0.5 | ortools | 23/23 | 206 | 8.9565 | 9/11/3 (23 common) | 1.1962 | 9.0899 |
| R | 0.5 | gurobi | 11/23 | — | — | 1/0/10 (11 common) | — | — |
| R | 5 | ours | 23/23 | 195 | 8.4783 | 14/9/0 (23 common) | 0.683 | 3.7065 |
| R | 5 | pyvrp | 23/23 | 216 | 9.3913 | 0/0/0 (0 common) | — | — |
| R | 5 | ortools | 23/23 | 195 | 8.4783 | 15/8/0 (23 common) | -0.0351 | 7.4878 |
| R | 5 | gurobi | 22/23 | — | — | 2/0/20 (22 common) | — | — |
| RC | 0.5 | ours | 16/16 | 138 | 8.625 | 9/7/0 (16 common) | 9.8145 | 43.3688 |
| RC | 0.5 | pyvrp | 16/16 | 154 | 9.625 | 0/0/0 (0 common) | — | — |
| RC | 0.5 | ortools | 16/16 | 143 | 8.9375 | 9/6/1 (16 common) | 6.0449 | 9.817 |
| RC | 0.5 | gurobi | 8/16 | — | — | 0/0/8 (8 common) | — | — |
| RC | 5 | ours | 16/16 | 134 | 8.375 | 12/4/0 (16 common) | 0.9005 | 4.0661 |
| RC | 5 | pyvrp | 16/16 | 154 | 9.625 | 0/0/0 (0 common) | — | — |
| RC | 5 | ortools | 16/16 | 138 | 8.625 | 12/4/0 (16 common) | 0.8321 | 1.7792 |
| RC | 5 | gurobi | 15/16 | — | — | 1/0/14 (15 common) | — | — |

`*` Family vehicle sums and means require feasible coverage of every instance in that family.

## 0.5 s to 5 s improvement

Improvement is evaluated lexicographically: fewer vehicles takes precedence; distance is compared only when vehicle counts match. Distance improvement is `(distance at 0.5 s − distance at 5 s) / distance at 0.5 s`, so a positive value is shorter at 5 seconds.

| Solver | Fewer vehicles | Same K, shorter | Same K, longer | More vehicles | Feasible only at 5 s | Lost feasibility | No feasible either | Same-K distance improvement median (%) | p90 (%) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ours | 11 | 39 | 0 | 0 | 0 | 0 | 0 | 9.1467 | 26.1094 |
| pyvrp | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| ortools | 15 | 26 | 0 | 0 | 0 | 0 | 0 | 1.5746 | 8.2499 |
| gurobi | 25 | 0 | 0 | 0 | 27 | 0 | 2 | 0 | 0 |


## Seed-matched ours vs PyVRP runs

These rows compare the same instance, budget, and seed. Rows with an explicit metadata flag saying the backend does not support seeds are excluded from seed-matched comparisons.

| Budget (s) | Paired runs | Both feasible | Ours fewer K | Equal K | Ours more K | Same-K gap median (%) | Same-K p90 (%) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.5 | 56 | 56 | 28 | 24 | 4 | 7.7841 | 45.4805 |
| 5 | 56 | 56 | 32 | 24 | 0 | 0.0608 | 4.1299 |


Per-seed detail is in `paired_runs.csv`.

## Status counts

| Budget (s) | Solver | Runs | Feasible true | Feasible false | Feasibility unknown | Status counts |
| --- | --- | --- | --- | --- | --- | --- |
| 0.5 | ours | 56 | 56 | 0 | 0 | {"feasible": 56} |
| 0.5 | pyvrp | 56 | 56 | 0 | 0 | {"feasible": 56} |
| 0.5 | ortools | 56 | 56 | 0 | 0 | {"found": 56} |
| 0.5 | gurobi | 56 | 27 | 29 | 0 | {"optimal": 2, "time_limit": 54} |
| 5 | ours | 56 | 56 | 0 | 0 | {"feasible": 56} |
| 5 | pyvrp | 56 | 56 | 0 | 0 | {"feasible": 56} |
| 5 | ortools | 56 | 56 | 0 | 0 | {"found": 56} |
| 5 | gurobi | 56 | 54 | 2 | 0 | {"optimal": 3, "time_limit": 53} |


## Slowest selected feasible instances

Top ten by selected run total time for each solver and budget. Full rows, including preparation and search time, are in `tail_instances.csv`.

| Budget (s) | Solver | Rank | Instance | Total (s) | Prep (s) | Search (s) | Vehicles |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.5 | ours | 1 | R111 | 0.5029 | 5.4700001783203334e-05 | 0.5011155000029248 | 12 |
| 0.5 | ours | 2 | C207 | 0.5027 | 6.769999890821055e-05 | 0.5012952999968547 | 3 |
| 0.5 | ours | 3 | R102 | 0.5027 | 5.5300006351899356e-05 | 0.5010224000070593 | 18 |
| 0.5 | ours | 4 | R101 | 0.5027 | 6.44999963697046e-05 | 0.5011423000032664 | 21 |
| 0.5 | ours | 5 | RC202 | 0.5027 | 5.2100003813393414e-05 | 0.5010961000007228 | 4 |
| 0.5 | ours | 6 | C108 | 0.5026 | 5.419999797595665e-05 | 0.5011737999957404 | 11 |
| 0.5 | ours | 7 | R201 | 0.5025 | 7.109999569365755e-05 | 0.5008319999978994 | 4 |
| 0.5 | ours | 8 | RC107 | 0.5025 | 5.509999755304307e-05 | 0.5010268000041833 | 13 |
| 0.5 | ours | 9 | R203 | 0.5025 | 9.23000043258071e-05 | 0.5008700000034878 | 3 |
| 0.5 | ours | 10 | RC101 | 0.5025 | 8.209999941755086e-05 | 0.5009258999998565 | 16 |
| 0.5 | pyvrp | 1 | C101 | 0.5295 | 0.0010723000013967976 | 0.5009628999978304 | 10 |
| 0.5 | pyvrp | 2 | R203 | 0.5217 | 0.0012314999985392205 | 0.5183195999998134 | 4 |
| 0.5 | pyvrp | 3 | C103 | 0.5189 | 0.0012452000009943731 | 0.5156057000058354 | 11 |
| 0.5 | pyvrp | 4 | R204 | 0.5177 | 0.0017471999963163398 | 0.5137541999938549 | 3 |
| 0.5 | pyvrp | 5 | RC204 | 0.517 | 0.0015305999986594543 | 0.5133713999966858 | 3 |
| 0.5 | pyvrp | 6 | R207 | 0.5152 | 0.001273800000490155 | 0.5113864999948419 | 3 |
| 0.5 | pyvrp | 7 | RC202 | 0.5149 | 0.001226599997607991 | 0.5114395999989938 | 5 |
| 0.5 | pyvrp | 8 | R103 | 0.5141 | 0.0012523999976110645 | 0.5104042999955709 | 16 |
| 0.5 | pyvrp | 9 | RC103 | 0.5132 | 0.0012225000027683564 | 0.5097424999985378 | 16 |
| 0.5 | pyvrp | 10 | R209 | 0.5129 | 0.00105230000190204 | 0.5097211999964202 | 5 |
| 0.5 | ortools | 1 | C108 | 0.5185 | 0.003282099998614285 | 0.5127173999935621 | 10 |
| 0.5 | ortools | 2 | RC104 | 0.5167 | 0.003968400000303518 | 0.5111251999987871 | 11 |
| 0.5 | ortools | 3 | C101 | 0.5148 | 0.012343200003670063 | 0.5002246000003652 | 10 |
| 0.5 | ortools | 4 | C102 | 0.514 | 0.003738400002475828 | 0.5078129000030458 | 10 |
| 0.5 | ortools | 5 | C207 | 0.5127 | 0.0038505999982589856 | 0.506437800002459 | 3 |
| 0.5 | ortools | 6 | C109 | 0.5118 | 0.0029133999996702187 | 0.505433999998786 | 10 |
| 0.5 | ortools | 7 | R105 | 0.5093 | 0.0029169999979785644 | 0.5028687999947579 | 15 |
| 0.5 | ortools | 8 | R207 | 0.5091 | 0.003206100001989398 | 0.5034965000013472 | 3 |
| 0.5 | ortools | 9 | R104 | 0.5089 | 0.004896899998129811 | 0.5011742999995477 | 12 |
| 0.5 | ortools | 10 | C206 | 0.5086 | 0.0028338999982224777 | 0.5032813000070746 | 3 |
| 0.5 | gurobi | 1 | RC203 | 1.5306 | 0.9956932000059169 | 0.5062872000053176 | 25 |
| 0.5 | gurobi | 2 | RC204 | 1.5228 | 0.9840010999978404 | 0.5118596999964211 | 25 |
| 0.5 | gurobi | 3 | RC208 | 1.5184 | 0.9795997999972315 | 0.5095827999975882 | 25 |
| 0.5 | gurobi | 4 | R208 | 1.5086 | 0.9765425000005052 | 0.5042458999960218 | 25 |
| 0.5 | gurobi | 5 | C204 | 1.4876 | 0.9592287999985274 | 0.5014583000011044 | 25 |
| 0.5 | gurobi | 6 | R211 | 1.4683 | 0.9381371999988914 | 0.5029636999970535 | 25 |
| 0.5 | gurobi | 7 | R203 | 1.4471 | 0.9097079999992275 | 0.5082206000006408 | 25 |
| 0.5 | gurobi | 8 | R207 | 1.426 | 0.8937273999981699 | 0.5075544999999693 | 25 |
| 0.5 | gurobi | 9 | RC207 | 1.3925 | 0.8632976999942912 | 0.5037109999975655 | 25 |
| 0.5 | gurobi | 10 | C203 | 1.391 | 0.8631161999946926 | 0.5042482999997446 | 25 |
| 5 | ours | 1 | RC103 | 5.0031 | 5.3499999921768904e-05 | 5.001283199999307 | 12 |
| 5 | ours | 2 | R108 | 5.003 | 5.249999958323315e-05 | 5.001168200004031 | 10 |
| 5 | ours | 3 | RC208 | 5.0028 | 6.719999510096386e-05 | 5.000439600000391 | 3 |
| 5 | ours | 4 | RC108 | 5.0028 | 5.680000322172418e-05 | 5.001145700000052 | 12 |
| 5 | ours | 5 | C101 | 5.0026 | 6.140000186860561e-05 | 5.000958599994192 | 10 |
| 5 | ours | 6 | RC203 | 5.0026 | 7.530000584665686e-05 | 5.001072600003681 | 3 |
| 5 | ours | 7 | C206 | 5.0025 | 5.080000119050965e-05 | 5.00080010000238 | 3 |
| 5 | ours | 8 | R103 | 5.0024 | 5.0499998906161636e-05 | 5.00085869999748 | 14 |
| 5 | ours | 9 | C207 | 5.0023 | 5.709999823011458e-05 | 5.000756499997806 | 3 |
| 5 | ours | 10 | RC107 | 5.0023 | 6.22000006842427e-05 | 5.000868299997819 | 12 |
| 5 | pyvrp | 1 | R206 | 5.0242 | 0.0010505000027478673 | 5.020941599999787 | 4 |
| 5 | pyvrp | 2 | C202 | 5.0175 | 0.0010636000006343238 | 5.014057300002605 | 4 |
| 5 | pyvrp | 3 | R104 | 5.0157 | 0.001086700001906138 | 5.01256149999972 | 12 |
| 5 | pyvrp | 4 | R211 | 5.0151 | 0.001186900000902824 | 5.011575200005609 | 3 |
| 5 | pyvrp | 5 | R103 | 5.0149 | 0.0011641999954008497 | 5.0116749000007985 | 16 |
| 5 | pyvrp | 6 | R204 | 5.0148 | 0.0012552000043797307 | 5.01158569999825 | 3 |
| 5 | pyvrp | 7 | R210 | 5.0137 | 0.0011605000036070123 | 5.010415099997772 | 4 |
| 5 | pyvrp | 8 | C103 | 5.0124 | 0.0011960000047110952 | 5.0091720999989775 | 11 |
| 5 | pyvrp | 9 | R209 | 5.012 | 0.0012047999989590608 | 5.008631899996544 | 5 |
| 5 | pyvrp | 10 | R107 | 5.0119 | 0.0013325000036275014 | 5.008528099999239 | 12 |
| 5 | ortools | 1 | RC108 | 5.0137 | 0.002269299999170471 | 5.008985599997686 | 11 |
| 5 | ortools | 2 | RC105 | 5.0116 | 0.0024271000002045184 | 5.007477400002244 | 16 |
| 5 | ortools | 3 | C104 | 5.0091 | 0.005240699996647891 | 5.000752600004489 | 10 |
| 5 | ortools | 4 | C205 | 5.009 | 0.003653799998573959 | 5.00212730000203 | 3 |
| 5 | ortools | 5 | R108 | 5.0086 | 0.0038724999976693653 | 5.0029355000006035 | 10 |
| 5 | ortools | 6 | C108 | 5.0084 | 0.005220299994107336 | 5.000899099999515 | 10 |
| 5 | ortools | 7 | RC206 | 5.0077 | 0.003105200004938524 | 5.002748599996266 | 4 |
| 5 | ortools | 8 | C208 | 5.0075 | 0.004136799994739704 | 5.000562599998375 | 3 |
| 5 | ortools | 9 | R208 | 5.0073 | 0.0022320999996736646 | 5.001618599999347 | 2 |
| 5 | ortools | 10 | R202 | 5.0071 | 0.0034516000014264137 | 5.0012223000012455 | 4 |
| 5 | gurobi | 1 | RC208 | 6.0655 | 1.0284348000059254 | 5.005787999994936 | 8 |
| 5 | gurobi | 2 | R211 | 6.0335 | 0.9973747000040021 | 5.004317900005844 | 10 |
| 5 | gurobi | 3 | R204 | 6.0284 | 0.9794506000034744 | 5.017890700000862 | 7 |
| 5 | gurobi | 4 | R112 | 6.0006 | 0.9630293999944115 | 5.006982500002778 | 14 |
| 5 | gurobi | 5 | R208 | 5.9953 | 0.9641514999966603 | 5.004879899999651 | 3 |
| 5 | gurobi | 6 | R207 | 5.9923 | 0.9531633999940823 | 5.008840900001815 | 7 |
| 5 | gurobi | 7 | R103 | 5.9889 | 0.9194652999940445 | 5.0411110000059125 | 18 |
| 5 | gurobi | 8 | RC204 | 5.9859 | 0.9468829999968875 | 5.008009099998162 | 7 |
| 5 | gurobi | 9 | C104 | 5.9739 | 0.9343326000016532 | 5.008392800002184 | 12 |
| 5 | gurobi | 10 | RC207 | 5.9607 | 0.8301837999970303 | 5.100971299994853 | 11 |


## Gurobi bounds

Bound and gap fields recorded under Gurobi `metadata_json` are retained in `gurobi_bounds.csv` and `summary.json`. A missing bound means the runner did not record a matching metadata field.

| Budget (s) | Instance | Seed | Status | Metadata bounds/gap |
| --- | --- | --- | --- | --- |
| 0.5 | C101 | 0 | optimal | {"distance_upper_bound": 12022125, "mip_gap": 0.0, "mip_gap_target": 0.0, "objective_bound": 121050197.0, "objective_bound_continuous": 121050197.0, "objective_upper_bound": 312575275} |
| 0.5 | C102 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap_target": 0.0, "objective_bound": 96399275.0, "objective_bound_continuous": 96399275.0, "objective_upper_bound": 312575275} |
| 0.5 | C103 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap_target": 0.0, "objective_bound": 24087265.0, "objective_bound_continuous": 24087265.0, "objective_upper_bound": 312575275} |
| 0.5 | C104 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap_target": 0.0, "objective_bound": 24087265.0, "objective_bound_continuous": 24087265.0, "objective_upper_bound": 312575275} |
| 0.5 | C105 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap_target": 0.0, "objective_bound": 61708490.450954825, "objective_bound_continuous": 61708490.450954825, "objective_upper_bound": 312575275} |
| 0.5 | C106 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap_target": 0.0, "objective_bound": 36122317.0, "objective_bound_continuous": 36122317.0, "objective_upper_bound": 312575275} |
| 0.5 | C107 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 312575275} |
| 0.5 | C108 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap_target": 0.0, "objective_bound": 375336.09891774337, "objective_bound_continuous": 375336.09891774337, "objective_upper_bound": 312575275} |
| 0.5 | C109 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9988315160936312, "mip_gap_target": 0.0, "objective_bound": 339264.6645015481, "objective_bound_continuous": 339264.6645015481, "objective_upper_bound": 312575275} |
| 0.5 | C201 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.47755856933750057, "mip_gap_target": 0.0, "objective_bound": 25790686.411214318, "objective_bound_continuous": 25790686.411214318, "objective_upper_bound": 312575275} |
| 0.5 | C202 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9945712881961363, "mip_gap_target": 0.0, "objective_bound": 529915.4185199504, "objective_bound_continuous": 529915.4185199504, "objective_upper_bound": 312575275} |
| 0.5 | C203 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9983433585813947, "mip_gap_target": 0.0, "objective_bound": 505704.6100301164, "objective_bound_continuous": 505704.6100301164, "objective_upper_bound": 312575275} |
| 0.5 | C204 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 1.0, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 312575275} |
| 0.5 | C205 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap_target": 0.0, "objective_bound": 522350.1575016312, "objective_bound_continuous": 522350.1575016312, "objective_upper_bound": 312575275} |
| 0.5 | C206 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9976535139926955, "mip_gap_target": 0.0, "objective_bound": 511318.99659387046, "objective_bound_continuous": 511318.99659387046, "objective_upper_bound": 312575275} |
| 0.5 | C207 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9967588187347448, "mip_gap_target": 0.0, "objective_bound": 510564.69978228514, "objective_bound_continuous": 510564.69978228514, "objective_upper_bound": 312575275} |
| 0.5 | C208 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap_target": 0.0, "objective_bound": 504424.45078434807, "objective_bound_continuous": 504424.45078434807, "objective_upper_bound": 312575275} |
| 0.5 | R101 | 0 | optimal | {"distance_upper_bound": 11478875, "mip_gap": 0.0, "mip_gap_target": 0.0, "objective_bound": 219749442.0, "objective_bound_continuous": 219749442.0, "objective_upper_bound": 298450775} |
| 0.5 | R102 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 12408521.615701973, "objective_bound_continuous": 12408521.615701973, "objective_upper_bound": 298450775} |
| 0.5 | R103 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 9055.0, "objective_bound_continuous": 9055.0, "objective_upper_bound": 298450775} |
| 0.5 | R104 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 9055.0, "objective_bound_continuous": 9055.0, "objective_upper_bound": 298450775} |
| 0.5 | R105 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 23092376.0, "objective_bound_continuous": 23092376.0, "objective_upper_bound": 298450775} |
| 0.5 | R106 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 773549.397233008, "objective_bound_continuous": 773549.397233008, "objective_upper_bound": 298450775} |
| 0.5 | R107 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 298450775} |
| 0.5 | R108 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 298450775} |
| 0.5 | R109 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 736973.0514327736, "objective_bound_continuous": 736973.0514327736, "objective_upper_bound": 298450775} |
| 0.5 | R110 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 634651.9795138203, "objective_bound_continuous": 634651.9795138203, "objective_upper_bound": 298450775} |
| 0.5 | R111 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 632254.7665868489, "objective_bound_continuous": 632254.7665868489, "objective_upper_bound": 298450775} |
| 0.5 | R112 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 298450775} |
| 0.5 | R201 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9233090835185755, "mip_gap_target": 0.0, "objective_bound": 12510968.595578605, "objective_bound_continuous": 12510968.595578605, "objective_upper_bound": 298450775} |
| 0.5 | R202 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.997474795243418, "mip_gap_target": 0.0, "objective_bound": 733343.4280297881, "objective_bound_continuous": 733343.4280297881, "objective_upper_bound": 298450775} |
| 0.5 | R203 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9978228204889418, "mip_gap_target": 0.0, "objective_bound": 632937.7661808849, "objective_bound_continuous": 632937.7661808849, "objective_upper_bound": 298450775} |
| 0.5 | R204 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 298450775} |
| 0.5 | R205 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.997219873383942, "mip_gap_target": 0.0, "objective_bound": 744702.0244455824, "objective_bound_continuous": 744702.0244455824, "objective_upper_bound": 298450775} |
| 0.5 | R206 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9977594092963027, "mip_gap_target": 0.0, "objective_bound": 651373.111328649, "objective_bound_continuous": 651373.111328649, "objective_upper_bound": 298450775} |
| 0.5 | R207 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9979039066799844, "mip_gap_target": 0.0, "objective_bound": 609257.1709334506, "objective_bound_continuous": 609257.1709334506, "objective_upper_bound": 298450775} |
| 0.5 | R208 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 1.0, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 298450775} |
| 0.5 | R209 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9977472959120852, "mip_gap_target": 0.0, "objective_bound": 654841.051817488, "objective_bound_continuous": 654841.051817488, "objective_upper_bound": 298450775} |
| 0.5 | R210 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9977797523722556, "mip_gap_target": 0.0, "objective_bound": 645345.7767195264, "objective_bound_continuous": 645345.7767195264, "objective_upper_bound": 298450775} |
| 0.5 | R211 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 1.0, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 298450775} |
| 0.5 | RC101 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap_target": 0.0, "objective_bound": 63186945.202230364, "objective_bound_continuous": 63186945.202230364, "objective_upper_bound": 328942275} |
| 0.5 | RC102 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap_target": 0.0, "objective_bound": 711971.5938706892, "objective_bound_continuous": 711971.5938706892, "objective_upper_bound": 328942275} |
| 0.5 | RC103 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap_target": 0.0, "objective_bound": 581668.412172115, "objective_bound_continuous": 581668.412172115, "objective_upper_bound": 328942275} |
| 0.5 | RC104 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 328942275} |
| 0.5 | RC105 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap_target": 0.0, "objective_bound": 38838783.58642214, "objective_bound_continuous": 38838783.58642214, "objective_upper_bound": 328942275} |
| 0.5 | RC106 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 328942275} |
| 0.5 | RC107 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap_target": 0.0, "objective_bound": 567525.3391203767, "objective_bound_continuous": 567525.3391203767, "objective_upper_bound": 328942275} |
| 0.5 | RC108 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap_target": 0.0, "objective_bound": 540282.932154394, "objective_bound_continuous": 540282.932154394, "objective_upper_bound": 328942275} |
| 0.5 | RC201 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.993764931052914, "mip_gap_target": 0.0, "objective_bound": 1041556.1840471042, "objective_bound_continuous": 1041556.1840471042, "objective_upper_bound": 328942275} |
| 0.5 | RC202 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9978488050316434, "mip_gap_target": 0.0, "objective_bound": 662727.834562486, "objective_bound_continuous": 662727.834562486, "objective_upper_bound": 328942275} |
| 0.5 | RC203 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.998221839912238, "mip_gap_target": 0.0, "objective_bound": 570608.3696965239, "objective_bound_continuous": 570608.3696965239, "objective_upper_bound": 328942275} |
| 0.5 | RC204 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 1.0, "mip_gap_target": 0.0, "objective_bound": 0.0, "objective_bound_continuous": 0.0, "objective_upper_bound": 328942275} |
| 0.5 | RC205 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9966475339374261, "mip_gap_target": 0.0, "objective_bound": 769444.5974652546, "objective_bound_continuous": 769444.5974652546, "objective_upper_bound": 328942275} |
| 0.5 | RC206 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9973346598794276, "mip_gap_target": 0.0, "objective_bound": 716017.1245802813, "objective_bound_continuous": 716017.1245802813, "objective_upper_bound": 328942275} |
| 0.5 | RC207 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9981193807375356, "mip_gap_target": 0.0, "objective_bound": 603430.8668555778, "objective_bound_continuous": 603430.8668555778, "objective_upper_bound": 328942275} |
| 0.5 | RC208 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9983438451922657, "mip_gap_target": 0.0, "objective_bound": 530870.4651603681, "objective_bound_continuous": 530870.4651603681, "objective_upper_bound": 328942275} |
| 5 | C101 | 0 | optimal | {"distance_upper_bound": 12022125, "mip_gap": 0.0, "mip_gap_target": 0.0, "objective_bound": 121050197.0, "objective_bound_continuous": 121050197.0, "objective_upper_bound": 312575275} |
| 5 | C102 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.19933614796906243, "mip_gap_target": 0.0, "objective_bound": 96923871.0, "objective_bound_continuous": 96923871.0, "objective_upper_bound": 312575275} |
| 5 | C103 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.8303574046039299, "mip_gap_target": 0.0, "objective_bound": 24635383.194665395, "objective_bound_continuous": 24635383.194665395, "objective_upper_bound": 312575275} |
| 5 | C104 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.8315540870364595, "mip_gap_target": 0.0, "objective_bound": 24478501.95802173, "objective_bound_continuous": 24478501.95802173, "objective_upper_bound": 312575275} |
| 5 | C105 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.519963573929952, "mip_gap_target": 0.0, "objective_bound": 75943977.01231506, "objective_bound_continuous": 75943977.01231506, "objective_upper_bound": 312575275} |
| 5 | C106 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.7533802206964888, "mip_gap_target": 0.0, "objective_bound": 42074074.70175647, "objective_bound_continuous": 42074074.70175647, "objective_upper_bound": 312575275} |
| 5 | C107 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.7526355606976576, "mip_gap_target": 0.0, "objective_bound": 45123118.51422734, "objective_bound_continuous": 45123118.51422734, "objective_upper_bound": 312575275} |
| 5 | C108 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.995406442348155, "mip_gap_target": 0.0, "objective_bound": 780372.1410554571, "objective_bound_continuous": 780372.1410554571, "objective_upper_bound": 312575275} |
| 5 | C109 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9972042636493597, "mip_gap_target": 0.0, "objective_bound": 439845.1846701145, "objective_bound_continuous": 439845.1846701145, "objective_upper_bound": 312575275} |
| 5 | C201 | 0 | optimal | {"distance_upper_bound": 12022125, "mip_gap": 0.0, "mip_gap_target": 0.0, "objective_bound": 36657933.0, "objective_bound_continuous": 36657933.0, "objective_upper_bound": 312575275} |
| 5 | C202 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9791720011634727, "mip_gap_target": 0.0, "objective_bound": 763511.3858734982, "objective_bound_continuous": 763511.3858734982, "objective_upper_bound": 312575275} |
| 5 | C203 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9905400768261121, "mip_gap_target": 0.0, "objective_bound": 577731.1872782446, "objective_bound_continuous": 577731.1872782446, "objective_upper_bound": 312575275} |
| 5 | C204 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9937274253851375, "mip_gap_target": 0.0, "objective_bound": 535388.1920684024, "objective_bound_continuous": 535388.1920684024, "objective_upper_bound": 312575275} |
| 5 | C205 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9826776933543796, "mip_gap_target": 0.0, "objective_bound": 635725.2760444696, "objective_bound_continuous": 635725.2760444696, "objective_upper_bound": 312575275} |
| 5 | C206 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9905807713214831, "mip_gap_target": 0.0, "objective_bound": 573912.2187554162, "objective_bound_continuous": 573912.2187554162, "objective_upper_bound": 312575275} |
| 5 | C207 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9742867911636227, "mip_gap_target": 0.0, "objective_bound": 1266185.4888650696, "objective_bound_continuous": 1266185.4888650696, "objective_upper_bound": 312575275} |
| 5 | C208 | 0 | time_limit | {"distance_upper_bound": 12022125, "mip_gap": 0.9911008999093563, "mip_gap_target": 0.0, "objective_bound": 544342.0765501078, "objective_bound_continuous": 544342.0765501078, "objective_upper_bound": 312575275} |
| 5 | R101 | 0 | optimal | {"distance_upper_bound": 11478875, "mip_gap": 0.0, "mip_gap_target": 0.0, "objective_bound": 219749442.0, "objective_bound_continuous": 219749442.0, "objective_upper_bound": 298450775} |
| 5 | R102 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.7731243448447396, "mip_gap_target": 0.0, "objective_bound": 60310622.89632032, "objective_bound_continuous": 60310622.89632032, "objective_upper_bound": 298450775} |
| 5 | R103 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.8802037759780446, "mip_gap_target": 0.0, "objective_bound": 24928422.372305553, "objective_bound_continuous": 24928422.372305553, "objective_upper_bound": 298450775} |
| 5 | R104 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.8100621333109014, "mip_gap_target": 0.0, "objective_bound": 35139830.72391701, "objective_bound_continuous": 35139830.72391701, "objective_upper_bound": 298450775} |
| 5 | R105 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap_target": 0.0, "objective_bound": 78326715.29219413, "objective_bound_continuous": 78326715.29219413, "objective_upper_bound": 298450775} |
| 5 | R106 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.943858765622324, "mip_gap_target": 0.0, "objective_bound": 12353347.304165464, "objective_bound_continuous": 12353347.304165464, "objective_upper_bound": 298450775} |
| 5 | R107 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9968047307367931, "mip_gap_target": 0.0, "objective_bound": 667949.8324713035, "objective_bound_continuous": 667949.8324713035, "objective_upper_bound": 298450775} |
| 5 | R108 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9957545848216551, "mip_gap_target": 0.0, "objective_bound": 638989.0709302846, "objective_bound_continuous": 638989.0709302846, "objective_upper_bound": 298450775} |
| 5 | R109 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9958171289649417, "mip_gap_target": 0.0, "objective_bound": 872876.4203104376, "objective_bound_continuous": 872876.4203104376, "objective_upper_bound": 298450775} |
| 5 | R110 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9965360305892258, "mip_gap_target": 0.0, "objective_bound": 680786.5691076483, "objective_bound_continuous": 680786.5691076483, "objective_upper_bound": 298450775} |
| 5 | R111 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9968802400320546, "mip_gap_target": 0.0, "objective_bound": 687965.8991259317, "objective_bound_continuous": 687965.8991259317, "objective_upper_bound": 298450775} |
| 5 | R112 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9961899682359948, "mip_gap_target": 0.0, "objective_bound": 617296.0199797028, "objective_bound_continuous": 617296.0199797028, "objective_upper_bound": 298450775} |
| 5 | R201 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.844596112028578, "mip_gap_target": 0.0, "objective_bound": 12738997.65795149, "objective_bound_continuous": 12738997.65795149, "objective_upper_bound": 298450775} |
| 5 | R202 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9934798089648864, "mip_gap_target": 0.0, "objective_bound": 832545.5431165295, "objective_bound_continuous": 832545.5431165295, "objective_upper_bound": 298450775} |
| 5 | R203 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.994176802170147, "mip_gap_target": 0.0, "objective_bound": 676536.043400657, "objective_bound_continuous": 676536.043400657, "objective_upper_bound": 298450775} |
| 5 | R204 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9922769605915115, "mip_gap_target": 0.0, "objective_bound": 628918.8021099828, "objective_bound_continuous": 628918.8021099828, "objective_upper_bound": 298450775} |
| 5 | R205 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9894206747915338, "mip_gap_target": 0.0, "objective_bound": 863562.799942231, "objective_bound_continuous": 863562.799942231, "objective_upper_bound": 298450775} |
| 5 | R206 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9930195464791095, "mip_gap_target": 0.0, "objective_bound": 729144.2637490137, "objective_bound_continuous": 729144.2637490137, "objective_upper_bound": 298450775} |
| 5 | R207 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9920096460176612, "mip_gap_target": 0.0, "objective_bound": 651089.5596044225, "objective_bound_continuous": 651089.5596044225, "objective_upper_bound": 298450775} |
| 5 | R208 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.982330733403901, "mip_gap_target": 0.0, "objective_bound": 626474.8021099827, "objective_bound_continuous": 626474.8021099827, "objective_upper_bound": 298450775} |
| 5 | R209 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9948576795584056, "mip_gap_target": 0.0, "objective_bound": 715884.396667272, "objective_bound_continuous": 715884.396667272, "objective_upper_bound": 298450775} |
| 5 | R210 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9939483586839513, "mip_gap_target": 0.0, "objective_bound": 702919.3317069588, "objective_bound_continuous": 702919.3317069588, "objective_upper_bound": 298450775} |
| 5 | R211 | 0 | time_limit | {"distance_upper_bound": 11478875, "mip_gap": 0.9946099778407573, "mip_gap_target": 0.0, "objective_bound": 624408.6531937821, "objective_bound_continuous": 624408.6531937821, "objective_upper_bound": 298450775} |
| 5 | RC101 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.34203104726831335, "mip_gap_target": 0.0, "objective_bound": 134375305.60430804, "objective_bound_continuous": 134375305.60430804, "objective_upper_bound": 328942275} |
| 5 | RC102 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.937949206851413, "mip_gap_target": 0.0, "objective_bound": 14280408.006707545, "objective_bound_continuous": 14280408.006707545, "objective_upper_bound": 328942275} |
| 5 | RC103 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9092656713906406, "mip_gap_target": 0.0, "objective_bound": 19681844.94411467, "objective_bound_continuous": 19681844.94411467, "objective_upper_bound": 328942275} |
| 5 | RC104 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9967604766940392, "mip_gap_target": 0.0, "objective_bound": 620103.4090908127, "objective_bound_continuous": 620103.4090908127, "objective_upper_bound": 328942275} |
| 5 | RC105 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap_target": 0.0, "objective_bound": 61067066.682047665, "objective_bound_continuous": 61067066.682047665, "objective_upper_bound": 328942275} |
| 5 | RC106 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9961366914268869, "mip_gap_target": 0.0, "objective_bound": 887039.3266050266, "objective_bound_continuous": 887039.3266050266, "objective_upper_bound": 328942275} |
| 5 | RC107 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.996851882789347, "mip_gap_target": 0.0, "objective_bound": 642375.1395935925, "objective_bound_continuous": 642375.1395935925, "objective_upper_bound": 328942275} |
| 5 | RC108 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9973289007707233, "mip_gap_target": 0.0, "objective_bound": 578704.244273421, "objective_bound_continuous": 578704.244273421, "objective_upper_bound": 328942275} |
| 5 | RC201 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9852881051125956, "mip_gap_target": 0.0, "objective_bound": 1518704.716336714, "objective_bound_continuous": 1518704.716336714, "objective_upper_bound": 328942275} |
| 5 | RC202 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9951207943031252, "mip_gap_target": 0.0, "objective_bound": 813043.7430979852, "objective_bound_continuous": 813043.7430979852, "objective_upper_bound": 328942275} |
| 5 | RC203 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9916381433085548, "mip_gap_target": 0.0, "objective_bound": 646019.5633387091, "objective_bound_continuous": 646019.5633387091, "objective_upper_bound": 328942275} |
| 5 | RC204 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9935203116194781, "mip_gap_target": 0.0, "objective_bound": 582511.0649102873, "objective_bound_continuous": 582511.0649102873, "objective_upper_bound": 328942275} |
| 5 | RC205 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9924014769288826, "mip_gap_target": 0.0, "objective_bound": 971865.5987779384, "objective_bound_continuous": 971865.5987779384, "objective_upper_bound": 328942275} |
| 5 | RC206 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9917266216855894, "mip_gap_target": 0.0, "objective_bound": 849794.4193940528, "objective_bound_continuous": 849794.4193940528, "objective_upper_bound": 328942275} |
| 5 | RC207 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9951808129405326, "mip_gap_target": 0.0, "objective_bound": 678539.3356045269, "objective_bound_continuous": 678539.3356045269, "objective_upper_bound": 328942275} |
| 5 | RC208 | 0 | time_limit | {"distance_upper_bound": 12651625, "mip_gap": 0.9943915513233238, "mip_gap_target": 0.0, "objective_bound": 574061.3396192178, "objective_bound_continuous": 574061.3396192178, "objective_upper_bound": 328942275} |


## Per-instance results

`per_instance_wide.csv` contains one row per budget and instance with each solver's selected vehicle count, distance, and selected seed. Distance gaps versus PyVRP are populated only for equal-vehicle comparisons.

## Files

- `raw_runs.csv`: unchanged copy of the runner output.
- `summary.csv`, `group_summary.csv`, `status_counts.csv`: aggregate tables.
- `per_instance_wide.csv`, `improvement_0p5_to_5.csv`, `paired_runs.csv`: comparison detail.
- `tail_instances.csv`, `gurobi_bounds.csv`: runtime tail and recorded solver bounds.
- `summary.json`: machine-readable copy of all calculated tables and protocol notes.
