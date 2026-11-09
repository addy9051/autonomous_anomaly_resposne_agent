"""CLI: python -m evals.rca_bench {generate,validate,run,summarize} ..."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from evals.rca_bench.generator import generate_suite
from evals.rca_bench.metrics import SCORING_VERSION
from evals.rca_bench.prompt_variants import VARIANTS
from evals.rca_bench.runner import (
    DEFAULT_SUITE,
    append_checkpoint,
    build_report,
    load_checkpoint,
    load_suite,
    render_markdown,
    run_solver,
    save_suite,
    suite_sha256,
    summarize_reports,
)
from evals.rca_bench.solvers import SOLVERS

if TYPE_CHECKING:
    from evals.rca_bench.schema import Prediction, Scenario


def _cmd_generate(args: argparse.Namespace) -> int:
    scenarios = generate_suite(seed=args.seed, per_class=args.per_class)
    save_suite(scenarios, Path(args.out))
    print(f"wrote {len(scenarios)} scenarios to {args.out}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    scenarios = load_suite(Path(args.suite))
    answerable = sum(1 for s in scenarios if s.ground_truth.answerable)
    print(f"ok: {len(scenarios)} scenarios ({answerable} answerable, {len(scenarios) - answerable} unanswerable)")
    return 0


def _cmd_summarize(args: argparse.Namespace) -> int:
    print(summarize_reports([Path(p) for p in args.reports]))
    return 0


async def _warm_up(timeout_s: float) -> bool:
    """Wake a scale-to-zero LLM endpoint. Cold starts answer with redirects and can take ten minutes or more."""
    import httpx

    from shared.config import get_settings

    cfg = get_settings().llm
    url = cfg.base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    print(
        f"warming up the LLM endpoint (cold starts can take 10+ minutes; giving up after {timeout_s:.0f}s)...",
        flush=True,
    )

    async def request() -> int:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=timeout_s), follow_redirects=True) as client:
            return (await client.get(url, headers=headers)).status_code

    async def heartbeat() -> None:
        waited = 0
        while True:
            await asyncio.sleep(60)
            waited += 1
            print(f"  still warming up ({waited} min)...", flush=True)

    beat = asyncio.create_task(heartbeat())
    try:
        status = await asyncio.wait_for(request(), timeout=timeout_s)
    except TimeoutError:
        print(f"warm-up gave up after {timeout_s:.0f}s. Check the Modal function logs.", file=sys.stderr)
        return False
    except httpx.HTTPError as exc:
        print(f"warm-up failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    finally:
        beat.cancel()
    print(f"endpoint responded with HTTP {status}", flush=True)
    return status == 200


def _run_options(args: argparse.Namespace, solver_name: str) -> dict[str, object]:
    options: dict[str, object] = {"concurrency": args.concurrency, "timeout_s": args.timeout}
    if solver_name == "agent":
        from urllib.parse import urlparse

        from shared.config import get_settings

        llm = get_settings().llm
        options.update(
            skip_runbook_search=args.skip_runbook_search,
            prompt_variant=args.prompt_variant,
            diagnosis_model=llm.diagnosis_agent_model,
            llm_host=urlparse(llm.base_url).netloc,
        )
    return options


async def _run(args: argparse.Namespace) -> int:
    suite_path = Path(args.suite)
    scenarios = load_suite(suite_path)
    if args.limit:
        scenarios = scenarios[: args.limit]
    if args.checkpoint and len(args.solver) != 1:
        print("--checkpoint needs exactly one --solver", file=sys.stderr)
        return 2
    if args.concurrency < 1:
        print("--concurrency must be at least 1", file=sys.stderr)
        return 2
    if args.warmup and not await _warm_up(args.warmup_timeout):
        return 3
    # Predictions made under another scoring version or prompt variant are not reusable.
    sha = f"{suite_sha256(suite_path)}:scoring{SCORING_VERSION}:{args.prompt_variant}"
    checkpoint = Path(args.checkpoint) if args.checkpoint else None

    def progress(index: int, total: int, scenario: Scenario, prediction: Prediction, seconds: float) -> None:
        status = f"ERROR {prediction.error}" if prediction.error else "ok"
        print(f"[{index}/{total}] {scenario.scenario_id} {status} {seconds:.0f}s", flush=True)
        if checkpoint:
            append_checkpoint(checkpoint, sha, prediction)

    results = {}
    for name in args.solver:
        if name not in SOLVERS:
            print(f"unknown solver '{name}'. choose from: {', '.join(sorted(SOLVERS))}", file=sys.stderr)
            return 2
        completed = load_checkpoint(checkpoint, sha) if checkpoint else {}
        if completed:
            print(f"resuming: {len(completed)} scenarios already done", flush=True)
        agent_options = {"skip_runbook_search": args.skip_runbook_search, "prompt_variant": args.prompt_variant}
        solver = SOLVERS[name](**agent_options) if name == "agent" else SOLVERS[name]()
        predictions, metrics = await run_solver(
            solver,
            scenarios,
            completed=completed,
            on_prediction=progress,
            timeout_s=args.timeout or None,
            concurrency=args.concurrency,
        )
        results[name] = metrics
        if args.out:
            out = Path(args.out)
            target = out if len(args.solver) == 1 else out.with_name(f"{out.stem}_{name}{out.suffix}")
            target.parent.mkdir(parents=True, exist_ok=True)
            report = build_report(name, scenarios, predictions, metrics, suite_path, _run_options(args, name))
            target.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"report written to {target}", flush=True)
    print(render_markdown(results, suite_path, len(scenarios)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.rca_bench", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="regenerate the synthetic suite")
    gen.add_argument("--seed", type=int, default=0)
    gen.add_argument("--per-class", type=int, default=5)
    gen.add_argument("--out", default=str(DEFAULT_SUITE))
    gen.set_defaults(func=_cmd_generate)

    val = sub.add_parser("validate", help="load and validate a suite")
    val.add_argument("--suite", default=str(DEFAULT_SUITE))
    val.set_defaults(func=_cmd_validate)

    run = sub.add_parser("run", help="score one or more solvers")
    run.add_argument("--solver", action="append", required=True, help=f"one of: {', '.join(sorted(SOLVERS))}")
    run.add_argument("--suite", default=str(DEFAULT_SUITE))
    run.add_argument("--limit", type=int, default=0, help="score only the first N scenarios (smoke test)")
    run.add_argument("--out", help="write a JSON report (one file per solver when several are given)")
    run.add_argument("--checkpoint", help="JSONL file updated after every scenario; rerun the same command to resume")
    run.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help="seconds allowed per scenario before it counts as an error (0 = none)",
    )
    run.add_argument("--warmup", action="store_true", help="wake the LLM endpoint first (use with the agent solver)")
    run.add_argument(
        "--warmup-timeout", type=float, default=1200.0, help="seconds to wait for the endpoint before giving up"
    )
    run.add_argument("--concurrency", type=int, default=1, help="scenarios solved in parallel (default 1)")
    run.add_argument(
        "--skip-runbook-search",
        action="store_true",
        help="agent solver: fail runbook search immediately instead of trying an unavailable knowledge base",
    )
    run.add_argument(
        "--prompt-variant",
        default="baseline",
        choices=sorted(VARIANTS),
        help="agent solver: synthesis prompt variant for an A/B run (the production prompt is not changed)",
    )
    run.set_defaults(func=lambda a: asyncio.run(_run(a)))

    summary = sub.add_parser("summarize", help="summarise one or more JSON reports (several runs show mean and range)")
    summary.add_argument("reports", nargs="+")
    summary.set_defaults(func=_cmd_summarize)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
