"""The jobs command line: file, list and manage render jobs without the service running.

    python3 -m scripts.jobs add --file spec.json     (or --file - to read the spec from stdin; prints the new id)
    python3 -m scripts.jobs status [--requester NAME] [--state STATE] [--json]
    python3 -m scripts.jobs show ID
    python3 -m scripts.jobs hold ID | release ID | remove ID
    python3 -m scripts.jobs run [--limit N] [--requester NAME] [--priority urgent|normal|bulk] [--dry-run]   (render the queue; see runner.py)
    python3 -m scripts.jobs estimate [--requester NAME]
    python3 -m scripts.jobs results ID

It works on the same atomic job files as the HTTP API (PROMPTFORGE_JOBS_DIR respected). Exit codes: 0 ok, 2 the store refused (one 'error:' line per problem),
1 anything else (a missing file, JSON that does not parse).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "server"))
from core import job_plan, jobs  # noqa: E402

from . import destinations, runner  # noqa: E402


def _counts_line(records):
    counts = {s: 0 for s in jobs.STATES}
    for r in records:
        if r.get("state") in counts:
            counts[r["state"]] += 1
    return " | ".join("%s %d" % (s, counts[s]) for s in jobs.STATES), counts


def _job_line(r):
    return "%s  %s  %s  %d seeds  %s  %s" % (r.get("id"), r.get("state"), r.get("priority"), len(r.get("seeds") or []), r.get("model"), r.get("requester"))


def build_parser():
    ap = argparse.ArgumentParser(prog="python3 -m scripts.jobs", description="Render job queue.")
    sub = ap.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="file a job from a JSON spec")
    add.add_argument("--file", required=True, help="path to the spec, or - for stdin")
    add.add_argument("--json", action="store_true", help="print the whole record")
    st = sub.add_parser("status", help="counts and one line per job")
    st.add_argument("--requester")
    st.add_argument("--state", choices=jobs.STATES)
    st.add_argument("--json", action="store_true")
    rn = sub.add_parser("run", help="render the queued jobs")
    rn.add_argument("--limit", type=int, default=0, help="stop after this many pictures")
    rn.add_argument("--requester")
    rn.add_argument("--priority", choices=jobs.PRIORITIES)
    rn.add_argument("--dry-run", action="store_true", help="print the planned order and the estimate, change nothing")
    es = sub.add_parser("estimate", help="how long the queued jobs will take")
    es.add_argument("--requester")
    for name in ("show", "results", "hold", "release", "remove"):
        p = sub.add_parser(name)
        p.add_argument("id")
    return ap


def main(argv=None, store=None, out=print, err=None, stdin=None, run_fn=None):
    err = err or (lambda msg: print(msg, file=sys.stderr))
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0
    store = store or jobs.JobStore()
    try:
        if args.command == "add":
            raw = (stdin or sys.stdin).read() if args.file == "-" else open(args.file, encoding="utf-8").read()
            rec = store.add(json.loads(raw))
            out(json.dumps(rec, indent=2, ensure_ascii=False) if args.json else rec["id"])
        elif args.command == "status":
            everything = store.list()
            shown = store.list(state=args.state, requester=args.requester)
            line, counts = _counts_line(everything)
            if args.json:
                out(json.dumps({"jobs": shown, "counts": counts}, indent=2, ensure_ascii=False))
            else:
                out(line)
                for r in shown:
                    out(_job_line(r))
        elif args.command == "run":
            return (run_fn or runner.run)(store, runner.JobPF(), limit=args.limit, requester=args.requester, priority=args.priority, dry_run=args.dry_run, out=out,
                                            deliver=destinations.make_deliver())
        elif args.command == "estimate":
            queued = store.list(state="queued", requester=args.requester)
            if not queued:
                out("queue is empty")
            else:
                est = job_plan.estimate(queued, job_plan.load_timings(os.path.join(store.root, "timings.jsonl")))
                out("queued %d jobs, %d pictures, about %s%s" % (len(queued), est["pictures"], runner.format_seconds(est["total_seconds"]), " (rough)" if est["rough"] else ""))
        elif args.command == "results":
            rec = store.get(args.id)
            if rec is None:
                raise jobs.JobError("no job %s" % args.id)
            by_seed = {r.get("seed"): r for r in rec.get("results") or []}
            ok = 0
            for seed in rec["seeds"]:
                r = by_seed.get(seed)
                if r is None:
                    out("%d  pending" % seed)
                elif r.get("status") == "ok":
                    ok += 1
                    out("%d  ok  %ss  %s  %s" % (seed, r.get("seconds"), r.get("gallery"), r.get("delivered") or "-"))
                else:
                    out("%d  error  %s" % (seed, r.get("error")))
            out("%d of %d seeds ok" % (ok, len(rec["seeds"])))
        elif args.command == "show":
            rec = store.get(args.id)
            if rec is None:
                raise jobs.JobError("no job %s" % args.id)
            out(json.dumps(rec, indent=2, ensure_ascii=False))
        else:
            out(getattr(store, args.command)(args.id)["state"])
    except jobs.JobError as exc:
        for e in exc.errors:
            err("error: %s" % e)
        return 2
    except (OSError, ValueError) as exc:
        err("error: %s" % exc)
        return 1
    return 0
