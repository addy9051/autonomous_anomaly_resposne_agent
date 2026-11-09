<#
.SYNOPSIS
  Live test of per-candidate judging with log-probability confidence, aimed at abstention.

.DESCRIPTION
  1. Probes the endpoint once (and wakes it): the judge modes are meaningless if the server returns no
     log-probabilities, so the script stops if the probe fails.
  2. Runs the agent live -Runs times per suite and mode. Modes: judge_temporal (does not ask about changes the
     temporal rule rules out) and judge (asks about every change; needs no onset timestamps).
  3. Prints one summary per configuration, with the comparison runs that already exist when they are present.

  The thresholds and the decision rule are fixed in docs\pivot\06-RCA_BENCHMARK.md before any run. If you change a
  threshold after seeing results, the suites you saw are no longer a fair test; use synthetic_v0_seed3.jsonl, which
  is reserved and has not been run.

  Finished runs are skipped, so rerunning the script resumes it. Each run also checkpoints per scenario.
  A run costs about the same as a baseline run plus a few short calls per scenario.
  Run `python infra\modal\keep_warm.py off` afterwards; a warm GPU keeps billing.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File evals\rca_bench\run_judge_experiment.ps1 -Runs 3 -Concurrency 12
  powershell -ExecutionPolicy Bypass -File evals\rca_bench\run_judge_experiment.ps1 -Runs 1 -Modes judge -Suites seed2
#>
param(
    [int]$Runs = 3,
    [int]$Concurrency = 12,
    [string[]]$Modes = @("judge_temporal", "judge"),
    [string[]]$Suites = @("seed2", "holdout")
)

$python = ".venv\Scripts\python.exe"
$suitePaths = @{
    dev      = "evals\rca_bench\suites\synthetic_v0.jsonl"
    holdout  = "evals\rca_bench\suites\synthetic_v0_seed1.jsonl"
    seed2    = "evals\rca_bench\suites\synthetic_v0_seed2.jsonl"
    seed3    = "evals\rca_bench\suites\synthetic_v0_seed3.jsonl"
}

New-Item -ItemType Directory -Force evals\reports | Out-Null

& $python -m evals.rca_bench probe-judge --warmup --suite $suitePaths[$Suites[0]]
if ($LASTEXITCODE -ne 0) {
    Write-Host "probe failed (exit code $LASTEXITCODE); not running the experiment."
    exit $LASTEXITCODE
}

foreach ($i in 1..$Runs) {
    foreach ($suite in $Suites) {
        foreach ($mode in $Modes) {
            $name = "judge_${suite}_${mode}_run$i"
            if (Test-Path "evals\reports\$name.json") {
                Write-Host "skip $name (already done)"
                continue
            }
            Write-Host "=== $name ==="
            & $python -m evals.rca_bench run --solver agent --concurrency $Concurrency --skip-runbook-search `
                --attribution $mode --suite $suitePaths[$suite] `
                --checkpoint "evals\reports\$name.jsonl" --out "evals\reports\$name.json"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "run $name failed with exit code $LASTEXITCODE; stopping. Rerun the script to resume."
                exit $LASTEXITCODE
            }
        }
    }
}

# A run whose verdicts fell back to the written word did not measure log-probability confidence.
$fallbacks = Select-String -Path "evals\reports\judge_*_run?.json" -Pattern "no-logprobs" -List -ErrorAction SilentlyContinue
if ($fallbacks) {
    Write-Host ""
    Write-Host "WARNING: these runs contain verdicts without log-probabilities and do not count:"
    $fallbacks | ForEach-Object { Write-Host "  $($_.Path)" }
}

$comparison = @{
    seed2   = @(
        @{ label = "baseline";                    pattern = "cap_seed2_baseline_run?.json" },
        @{ label = "temporal (offline)";          pattern = "cap_seed2_baseline_run?_temporal.json" },
        @{ label = "temporal_cap (live)";         pattern = "cap_seed2_temporal_cap_run?.json" }
    )
    holdout = @(
        @{ label = "baseline";                    pattern = "exp_holdout_baseline_run?.json" },
        @{ label = "temporal (offline)";          pattern = "exp_holdout_baseline_run?_temporal.json" }
    )
}
foreach ($suite in $Suites) {
    $configs = @()
    foreach ($mode in $Modes) {
        $configs += @{ label = "$mode (live)"; pattern = "judge_${suite}_${mode}_run?.json" }
    }
    if ($comparison.ContainsKey($suite)) { $configs += $comparison[$suite] }
    foreach ($config in $configs) {
        Write-Host ""
        Write-Host "##### $suite / $($config.label) #####"
        $files = Get-ChildItem ("evals\reports\" + $config.pattern) -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
        if ($files) { & $python -m evals.rca_bench summarize $files } else { Write-Host "(no reports found)" }
    }
}
