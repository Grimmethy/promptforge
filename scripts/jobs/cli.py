"""The jobs command line: file, list and manage render jobs without the service running.

    python3 -m scripts.jobs add --file spec.json     (or --file - to read the spec from stdin; prints the new id)
    python3 -m scripts.jobs status [--requester NAME] [--state STATE] [--json]
    python3 -m scripts.jobs show ID
    python3 -m scripts.jobs hold ID | release ID | remove ID

It works on the same atomic job files as the HTTP API (PROMPTFORGE_JOBS_DIR respected). Exit codes: 0 ok, 2 the store refused (one 'error:' line per problem),
1 anything else (a missing file, JSON that does not parse).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "server"))
from core import jobs  # noqa: E402


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
    for name in ("show", "hold", "release", "remove"):
        p = sub.add_parser(name)
        p.add_argument("id")
    return ap


def main(argv=None, store=None, out=print, err=None, stdin=None):
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
