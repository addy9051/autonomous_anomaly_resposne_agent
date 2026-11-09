<#
.SYNOPSIS
  Confirms, on a fresh seed, that capping confidence when a narrow question disagrees reduces confident errors.

.DESCRIPTION
  Runs the agent live -Runs times on the seed-2 suite (never used to design the temporal rule or the cap) in two
  configurations: the plain baseline, and --attribution temporal_cap. The temporal rule needs no model calls, so the
  baseline runs are rescored offline to get the `temporal` configuration exactly. Then it prints one summary per
  configuration. The decision rule is written down in docs\pivot\06-RCA_BENCHMARK.md before the runs.

  With -AlsoKnown it also runs temporal_cap on the dev and holdout suites, to compare the live result with the
  offline estimate in docs\pivot\07-FIRST_AGENT_RUN_FINDINGS.md (it needs the earlier baseline runs for those suites).

  Finished runs are skipped, so rerunning the script resumes it. The cap adds one short model call per scenario.
  Run `python infra\modal\keep_warm.py off` afterwards; a warm GPU keeps billing.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File evals\rca_bench\run_cap_confirmation.ps1 -Runs 3 -Concurrency 12
#>
param(
    [int]$Runs = 3,
    [int]$Concurrency = 12,
    [switch]$AlsoKnown
)

$python = ".venv\Scripts\python.exe"
$fresh = "evals\rca_bench\suites\synthetic_v0_seed2.jsonl"
$known = [ordered]@{
    dev     = "evals\rca_bench\suites\synthetic_v0.jsonl"
    holdout = "evals\rca_bench\suites\synthetic_v0_seed1.jsonl"
}

New-Item -ItemType Directory -Force evals\reports | Out-Null

function Invoke-Run([string]$name, [string]$attribution, [string]$suite) {
    if (Test-Path "evals\reports\$name.json") {
        Write-Host "skip $name (already done)"
        return
    }
    Write-Host "=== $name ==="
    & $python -m evals.rca_bench run --solver agent --warmup --concurrency $Concurrency --skip-runbook-search `
        --attribution $attribution --suite $suite `
        --checkpoint "evals\reports\$name.jsonl" --out "evals\reports\$name.json"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "run $name failed with exit code $LASTEXITCODE; stopping. Rerun the script to resume."
        exit $LASTEXITCODE
    }
}

foreach ($i in 1..$Runs) {
    Invoke-Run "cap_seed2_baseline_run$i" "baseline" $fresh
    Invoke-Run "cap_seed2_temporal_cap_run$i" "temporal_cap" $fresh
    if ($AlsoKnown) {
        foreach ($suite in $known.Keys) {
            Invoke-Run "cap_${suite}_temporal_cap_run$i" "temporal_cap" $known[$suite]
        }
    }
}

# Offline and exact: the temporal rule applied to the baseline runs.
$baseline = Get-ChildItem "evals\reports\cap_seed2_baseline_run?.json" | ForEach-Object { $_.FullName }
& $python -m evals.rca_bench rescore $baseline | Out-Null

$configs = @(
    @{ label = "seed2 / baseline";               pattern = "cap_seed2_baseline_run?.json" },
    @{ label = "seed2 / temporal (offline)";     pattern = "cap_seed2_baseline_run?_temporal.json" },
    @{ label = "seed2 / temporal_cap (live)";    pattern = "cap_seed2_temporal_cap_run?.json" }
)
if ($AlsoKnown) {
    foreach ($suite in $known.Keys) {
        $configs += @{ label = "$suite / temporal_cap (live)"; pattern = "cap_${suite}_temporal_cap_run?.json" }
    }
}
foreach ($config in $configs) {
    Write-Host ""
    Write-Host "##### $($config.label) #####"
    $files = Get-ChildItem ("evals\reports\" + $config.pattern) -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
    if ($files) { & $python -m evals.rca_bench summarize $files }
}
