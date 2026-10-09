param(
    [string]$Out = "runs/four_solver_seed0_0p5_5s_repeat",
    [int[]]$Seeds = @(0),
    [double[]]$Budgets = @(0.5, 5),
    [string[]]$Instances = @(),
    [string[]]$Solvers = @("ours", "pyvrp", "ortools", "gurobi"),
    [string]$Python = "",
    [string]$PyvrpRoot = "",
    [string]$PyvrpPython = "",
    [string]$PyvrpSite = "",
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
$solverRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
if (-not $Python) { $Python = Join-Path $solverRoot ".venv-comparison/Scripts/python.exe" }
if (-not (Test-Path -LiteralPath $Python)) { throw "Python not found: $Python. See benchmarks/four_solver/README.md." }

Push-Location $solverRoot
try {
    $action = if ($VerifyOnly) { "verify" } else { "run" }
    $runArguments = @("benchmarks/four_solver/run.py", $action, "--out", $Out, "--python", $Python)
    if (-not $VerifyOnly) {
        $budgetText = @($Budgets | ForEach-Object { $_.ToString("G", [System.Globalization.CultureInfo]::InvariantCulture) })
        $runArguments += @("--seeds") + @($Seeds | ForEach-Object { $_.ToString() })
        $runArguments += @("--budgets") + $budgetText
        $runArguments += @("--solvers") + $Solvers
        if ($Instances.Count) { $runArguments += @("--instances") + $Instances }
        if ($PyvrpRoot) { $runArguments += @("--pyvrp-root", $PyvrpRoot) }
        if ($PyvrpPython) { $runArguments += @("--pyvrp-python", $PyvrpPython) }
        if ($PyvrpSite) { $runArguments += @("--pyvrp-site", $PyvrpSite) }
    }
    & $Python @runArguments
    if ($LASTEXITCODE -ne 0) { throw "Benchmark or verification failed (exit $LASTEXITCODE)." }
    & $Python benchmarks/four_solver/analyse.py --run-dir $Out
    if ($LASTEXITCODE -ne 0) { throw "Analysis failed (exit $LASTEXITCODE)." }
    & $Python benchmarks/four_solver_report.py --primary-run $Out --out "$Out/tables/report_zh.md"
    if ($LASTEXITCODE -ne 0) { throw "Chinese report generation failed (exit $LASTEXITCODE)." }
    Write-Host "Results: $Out/tables/report.md"
    Write-Host "Chinese tables: $Out/tables/report_zh.md"
    Write-Host "Per-instance table: $Out/tables/per_instance_wide.csv"
}
finally {
    Pop-Location
}
