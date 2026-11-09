<#
.SYNOPSIS
  Live test of the decomposed change-attribution decision, plus the deterministic temporal rule.

.DESCRIPTION
  Runs the agent with --attribution decompose -Runs times on the dev and holdout suites (the baseline runs already
  exist from run_experiment.ps1). The temporal rule needs no model calls, so it is applied offline with `rescore`:
  to the baseline runs (the `temporal` mode, exact) and to the decompose runs (`decompose_temporal`, approximate:
  see evals\rca_bench\rescore.py). Then it prints a summary per configuration.

  Finished runs are skipped, so rerunning the script resumes it. Each run also checkpoints per scenario.
  The decomposed decision adds one short model call per scenario, so a run takes about as long as a baseline run.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File evals\rca_bench\run_attribution_experiment.ps1 -Runs 3 -Concurrency 12
#>
param(
    [int]$Runs = 3,
    [int]$Concurrency = 12
)

$python = ".venv\Scripts\python.exe"
$suites = [ordered]@{
    dev     = "evals\rca_bench\suites\synthetic_v0.jsonl"
    holdout = "evals\rca_bench\suites\synthetic_v0_seed1.jsonl"
}

New-Item -ItemType Directory -Force evals\reports | Out-Null

foreach ($i in 1..$Runs) {
    foreach ($suite in $suites.Keys) {
        $name = "att_${suite}_decompose_run$i"
        if (Test-Path "evals\reports\$name.json") {
            Write-Host "skip $name (already done)"
            continue
        }
        Write-Host "=== $name ==="
        & $python -m evals.rca_bench run --solver agent --warmup --concurrency $Concurrency --skip-runbook-search `
            --attribution decompose --suite $suites[$suite] `
            --checkpoint "evals\reports\$name.jsonl" --out "evals\reports\$name.json"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "run $name failed with exit code $LASTEXITCODE; stopping. Rerun the script to resume."
            exit $LASTEXITCODE
        }
    }
}

# Offline: the temporal rule on the existing baseline runs and on the new decompose runs.
$baseline = Get-ChildItem "evals\reports\exp_*_baseline_run?.json" | ForEach-Object { $_.FullName }
$decompose = Get-ChildItem "evals\reports\att_*_decompose_run?.json" | ForEach-Object { $_.FullName }
& $python -m evals.rca_bench rescore ($baseline + $decompose) | Out-Null

$configs = @(
    @{ label = "baseline";            pattern = "exp_{0}_baseline_run?.json" },
    @{ label = "temporal (offline)";  pattern = "exp_{0}_baseline_run?_temporal.json" },
    @{ label = "decompose";           pattern = "att_{0}_decompose_run?.json" },
    @{ label = "decompose + temporal (offline)"; pattern = "att_{0}_decompose_run?_temporal.json" }
)
foreach ($suite in $suites.Keys) {
    foreach ($config in $configs) {
        Write-Host ""
        Write-Host "##### $suite / $($config.label) #####"
        $files = Get-ChildItem ("evals\reports\" + ($config.pattern -f $suite)) | ForEach-Object { $_.FullName }
        & $python -m evals.rca_bench summarize $files
    }
}
