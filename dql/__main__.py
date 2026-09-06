"""CLI surface: python -m dql <cmd>."""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import paths, store

console = Console()

STAGES = [
    "generate", "induce", "label", "qa", "review",
    "improve", "evaluate", "report", "export",
]


def cmd_status(args: argparse.Namespace) -> int:
    paths.ensure_dirs()
    with store.session() as conn:
        tickets = store.ticket_count(conn)
        spend = store.total_cost(conn)
        cap = _cost_cap()

        state = Table(title="Pipeline state", title_justify="left", header_style="bold")
        state.add_column("Stage")
        state.add_column("Last successful run")
        state.add_column("When")
        state.add_column("Cost USD", justify="right")
        for stage in STAGES:
            row = store.latest_run(conn, stage)
            if row is None and stage == "review":
                # Run records for review were added after the first human pass,
                # so fall back to the adjudications themselves. They are the
                # evidence that the pass happened.
                adj = conn.execute(
                    "SELECT COUNT(*) n, MAX(ts) last FROM adjudications"
                ).fetchone()
                if adj["n"]:
                    state.add_row(stage, f"{adj['n']} adjudications on record",
                                  adj["last"] or "", "0.0000")
                    continue
            if row is None:
                state.add_row(stage, "[dim]not run[/dim]", "", "")
            else:
                state.add_row(
                    stage, row["run_id"], row["finished_at"] or "", f"{row['cost_usd']:.4f}"
                )

        facts = Table(show_header=False, box=None)
        facts.add_row("tickets in store", str(tickets))
        tax = store.get_artifact(conn, "taxonomy.yaml")
        if tax is None:
            facts.add_row("taxonomy", "[dim]not locked[/dim]")
        else:
            try:
                store.assert_taxonomy_locked(conn)
                facts.add_row("taxonomy", f"locked {tax['sha256'][:12]} [green]intact[/green]")
            except store.TaxonomyDrift:
                facts.add_row("taxonomy", f"locked {tax['sha256'][:12]} [red]DRIFTED[/red]")
        facts.add_row("adjudications", str(_count_lines(paths.ADJUDICATIONS)))
        pct = (spend / cap * 100) if cap else 0.0
        colour = "green" if pct < 60 else ("yellow" if pct < 90 else "red")
        facts.add_row(
            "cumulative spend",
            f"[{colour}]{spend:.4f} USD of {cap:.2f} cap ({pct:.1f} percent)[/{colour}]",
        )

        console.print(Panel(facts, title="data-quality-loop", title_align="left"))
        console.print(state)

        breakdown = store.cost_breakdown(conn)
        if breakdown:
            costs = Table(title="Spend by stage", title_justify="left", header_style="bold")
            costs.add_column("Stage")
            costs.add_column("Calls", justify="right")
            costs.add_column("Tokens in", justify="right")
            costs.add_column("Tokens out", justify="right")
            costs.add_column("USD", justify="right")
            for row in breakdown:
                costs.add_row(
                    row["stage"], str(row["calls"]), f"{row['tokens_in']:,}",
                    f"{row['tokens_out']:,}", f"{row['usd']:.4f}",
                )
            console.print(costs)
    return 0


def _cost_cap() -> float:
    from .llm import get_cost_cap

    return get_cost_cap()


def _count_lines(path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def cmd_generate(args: argparse.Namespace) -> int:
    from .generate import run

    return run(seed=args.seed, count=args.count)


def cmd_induce(args: argparse.Namespace) -> int:
    from .induce import realign, run

    if getattr(args, "realign", False):
        return realign(fresh=args.fresh)
    return run(fresh=args.fresh, relock=args.relock)


def cmd_label(args: argparse.Namespace) -> int:
    from .label import run

    return run(source=args.source, fresh=args.fresh)


def cmd_qa(args: argparse.Namespace) -> int:
    from .qa import run

    return run()


def cmd_review(args: argparse.Namespace) -> int:
    from .review import run

    return run(reviewer=args.reviewer, limit=args.limit)


def cmd_improve(args: argparse.Namespace) -> int:
    from .improve import run

    return run()


def cmd_evaluate(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(paths.ROOT))
    from eval.harness import run

    return run(version=args.version)


def cmd_report(args: argparse.Namespace) -> int:
    from .report import run

    return run(email=args.email)


def cmd_export(args: argparse.Namespace) -> int:
    from .export import run

    return run()


def cmd_run_all(args: argparse.Namespace) -> int:
    """Everything except review, which needs a human."""
    steps = [
        ("generate", lambda: cmd_generate(argparse.Namespace(seed=args.seed, count=600))),
        ("induce", lambda: cmd_induce(argparse.Namespace(fresh=args.fresh, relock=False))),
        ("label", lambda: cmd_label(argparse.Namespace(source="both", fresh=args.fresh))),
        ("qa", lambda: cmd_qa(argparse.Namespace())),
    ]
    for name, fn in steps:
        console.rule(f"[bold]{name}")
        code = fn()
        if code != 0:
            console.print(f"[red]{name} failed with exit code {code}. Stopping.[/red]")
            return code

    console.rule("[bold yellow]human review required")
    console.print(
        Panel(
            "QA has routed a queue of items that need a human decision.\n\n"
            "  [bold]python -m dql review[/bold]\n\n"
            "Then continue with:\n\n"
            "  python -m dql improve\n"
            "  python -m dql evaluate --version both\n"
            "  python -m dql report\n"
            "  python -m dql export",
            title="run-all stops here on purpose",
            title_align="left",
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dql",
        description="data-quality-loop: taxonomy induction, labeling, quality "
                    "measurement, human review, and a measured improvement cycle.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="synthesize the corpus and seal ground truth")
    p.add_argument("--seed", type=int, default=20260904)
    p.add_argument("--count", type=int, default=600)
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("induce", help="induce and lock the taxonomy")
    p.add_argument("--fresh", action="store_true", help="bypass the LLM cache")
    p.add_argument("--relock", action="store_true", help="deliberately re-lock the taxonomy")
    p.add_argument("--realign", action="store_true",
                   help="regenerate only the vendor alignment, leaving the locked "
                        "taxonomy and all existing labels untouched")
    p.set_defaults(func=cmd_induce)

    p = sub.add_parser("label", help="label the corpus")
    p.add_argument("--source", choices=["llm", "heuristic", "both"], default="both")
    p.add_argument("--fresh", action="store_true", help="bypass the LLM cache")
    p.set_defaults(func=cmd_label)

    p = sub.add_parser("qa", help="run the quality framework and route to humans")
    p.set_defaults(func=cmd_qa)

    p = sub.add_parser("review", help="human review queue")
    p.add_argument("--reviewer", default="mohamed")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("improve", help="apply adjudications, produce dataset v2")
    p.set_defaults(func=cmd_improve)

    p = sub.add_parser("evaluate", help="score against sealed ground truth")
    p.add_argument("--version", choices=["v1", "v2", "both"], default="both")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("report", help="write REPORT.md")
    p.add_argument("--email", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("export", help="write fine-tune-ready splits and the datasheet")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("run-all", help="every stage except review")
    p.add_argument("--seed", type=int, default=20260904)
    p.add_argument("--fresh", action="store_true")
    p.set_defaults(func=cmd_run_all)

    p = sub.add_parser("status", help="pipeline state and cumulative spend")
    p.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except store.TaxonomyDrift as exc:
        console.print(Panel(str(exc), title="[red]taxonomy lock violated", title_align="left"))
        return 2
    except Exception as exc:  # noqa: BLE001
        from .llm import CostCapExceeded, MissingAPIKey

        if isinstance(exc, (CostCapExceeded, MissingAPIKey)):
            console.print(Panel(str(exc), title="[red]stopped", title_align="left"))
            return 3
        raise


if __name__ == "__main__":
    raise SystemExit(main())
