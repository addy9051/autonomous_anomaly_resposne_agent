<#
.SYNOPSIS
  A/B experiment: baseline vs evidence_discipline synthesis prompt, on the dev suite and the holdout suite.

.DESCRIPTION
  Runs the agent solver -Runs times per (suite, variant), interleaved so server drift hits both variants equally,
  then prints a summary per configuration. Finished runs are skipped, so rerunning the script resumes it, and each
  run also checkpoints per scenario. Reports go to evals\reports\exp_<suite>_<variant>_run<N>.json.

  About 8 minutes per run on the L40S with -Concurrency 4. The default 3 runs x 2 suites x 2 variants is 12 runs.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File evals\rca_bench\run_experiment.ps1 -Runs 2
#>
param(
    [int]$Runs = 3,
    [int]$Concurrency = 4
)

$python = ".venv\Scripts\python.exe"
$suites = [ordered]@{
    dev     = "evals\rca_bench\suites\synthetic_v0.jsonl"
    holdout = "evals\rca_bench\suites\synthetic_v0_seed1.jsonl"
}
$variants = @("baseline", "evidence_discipline")

New-Item -ItemType Directory -Force evals\reports | Out-Null

foreach ($i in 1..$Runs) {
    foreach ($suite in $suites.Keys) {
        foreach ($variant in $variants) {
            $name = "exp_${suite}_${variant}_run$i"
            if (Test-Path "evals\reports\$name.json") {
                Write-Host "skip $name (already done)"
                continue
            }
            Write-Host "=== $name ==="
            & $python -m evals.rca_bench run --solver agent --warmup --concurrency $Concurrency --skip-runbook-search `
                --prompt-variant $variant --suite $suites[$suite] `
                --checkpoint "evals\reports\$name.jsonl" --out "evals\reports\$name.json"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "run $name failed with exit code $LASTEXITCODE; stopping. Rerun the script to resume."
                exit $LASTEXITCODE
            }
        }
    }
}

foreach ($suite in $suites.Keys) {
    foreach ($variant in $variants) {
        Write-Host ""
        Write-Host "##### $suite / $variant #####"
        $reports = Get-ChildItem "evals\reports\exp_${suite}_${variant}_run*.json" | ForEach-Object { $_.FullName }
        & $python -m evals.rca_bench summarize $reports
    }
}
