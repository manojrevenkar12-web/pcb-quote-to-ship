"""q2s: run the evaluation, build the report, or run the daily pipeline on a simulated month."""

from __future__ import annotations

import argparse
import json


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="q2s")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("evaluate", help="all stacks on all seeds, machine study, scenarios -> reports/")
    ev.add_argument("--workers", type=int, default=2)
    ev.add_argument("--quick", action="store_true", help="one seed, today vs system only")
    sub.add_parser("report", help="reports/REPORT.md and reports/dashboard.html from reports/results.json")
    pl = sub.add_parser("pipeline", help="simulate days into the landing zone, then dbt build and the Dagster job")
    pl.add_argument("--days", type=int, default=45)
    args = ap.parse_args(argv)
    if args.cmd == "evaluate":
        from q2s.evaluation.run import evaluate

        res = evaluate(args.workers, args.quick)
        print(json.dumps(res["comparisons"].get("system", {}), indent=2))
    elif args.cmd == "report":
        from q2s.evaluation.report import build

        build()
    elif args.cmd == "pipeline":
        from q2s.evaluation.pipeline_demo import run_pipeline

        print(json.dumps(run_pipeline(args.days), indent=2, default=str))


if __name__ == "__main__":
    main()
