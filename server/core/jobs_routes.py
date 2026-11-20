"""HTTP routes of the render job queue (/api/jobs). Registered on the Flask app by server/app.py; the store is built per request so the jobs folder
(PROMPTFORGE_JOBS_DIR) is read at call time. Reads are open to anyone who can reach the service; every write must come from this machine.
"""
import datetime
import os

from flask import jsonify, request

from core import comfy_proc, job_plan, job_run, jobs

_PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP")
_LOCAL = ("127.0.0.1", "::1")


def loopback_only(req):
    """True when the request came straight from this machine: a loopback address and no proxy header (the tailnet gateway adds one)."""
    return req.remote_addr in _LOCAL and not any(req.headers.get(h) for h in _PROXY_HEADERS)


def _counts(records):
    out = {s: 0 for s in jobs.STATES}
    for r in records:
        if r.get("state") in out:
            out[r["state"]] += 1
    return out


def register(app, store_factory=None, spawn=None, kill=None):
    make = store_factory or (lambda: jobs.JobStore())

    def refuse_remote():
        return jsonify({"error": "jobs can only be filed from this machine"}), 403

    @app.route("/api/jobs", methods=["POST"])
    def jobs_add():
        if not loopback_only(request):
            return refuse_remote()
        spec = request.get_json(force=True, silent=True)
        if not isinstance(spec, dict):
            return jsonify({"error": "the body must be a JSON object", "errors": ["the body must be a JSON object"]}), 400
        try:
            rec = make().add(spec)
        except jobs.JobError as exc:
            return jsonify({"error": str(exc), "errors": exc.errors}), 400
        return jsonify({"job": rec}), 201

    @app.route("/api/jobs", methods=["GET"])
    def jobs_list():
        store = make()
        state = request.args.get("state") or None
        if state is not None and state not in jobs.STATES:
            return jsonify({"error": "unknown state %s" % state}), 400
        records = store.list(state=state, requester=request.args.get("requester") or None)
        return jsonify({"jobs": records, "counts": _counts(store.list())})

    @app.route("/api/jobs/summary", methods=["GET"])
    def jobs_summary():
        records = make().list()
        by_requester, by_priority = {}, {}
        for r in records:
            who = by_requester.setdefault(r.get("requester") or "", {s: 0 for s in jobs.STATES})
            if r.get("state") in who:
                who[r["state"]] += 1
            by_priority[r.get("priority")] = by_priority.get(r.get("priority"), 0) + 1
        return jsonify({"counts": _counts(records), "by_requester": by_requester, "by_priority": by_priority})

    @app.route("/api/jobs/<jid>", methods=["GET"])
    def jobs_get(jid):
        rec = make().get(jid)
        if rec is None:
            return jsonify({"error": "no job %s" % jid}), 404
        return jsonify({"job": rec})

    def transition(jid, action):
        if not loopback_only(request):
            return refuse_remote()
        store = make()
        if store.get(jid) is None:
            return jsonify({"error": "no job %s" % jid}), 404
        try:
            rec = getattr(store, action)(jid)
        except jobs.JobStateError as exc:
            return jsonify({"error": str(exc), "errors": exc.errors}), 409
        except jobs.JobError as exc:
            return jsonify({"error": str(exc), "errors": exc.errors}), 404
        return jsonify({"job": rec})

    @app.route("/api/jobs/estimate", methods=["GET"])
    def jobs_estimate():
        store = make()
        queued = store.list(state="queued", requester=request.args.get("requester") or None)
        est = job_plan.estimate(queued, job_plan.load_timings(os.path.join(store.root, "timings.jsonl")))
        return jsonify({"queued": len(queued), "pictures": est["pictures"], "total_seconds": est["total_seconds"], "rough": est["rough"],
                        "per_job": est["per_job"], "order": [j["id"] for j in job_plan.order_jobs(queued)]})

    @app.route("/api/jobs/<jid>/edit", methods=["POST"])
    def jobs_edit(jid):
        if not loopback_only(request):
            return refuse_remote()
        patch = request.get_json(force=True, silent=True)
        if not isinstance(patch, dict):
            return jsonify({"error": "the body must be a JSON object", "errors": ["the body must be a JSON object"]}), 400
        store = make()
        if store.get(jid) is None:
            return jsonify({"error": "no job %s" % jid}), 404
        try:
            rec = store.update(jid, patch)
        except jobs.JobStateError as exc:
            return jsonify({"error": str(exc), "errors": exc.errors}), 409
        except jobs.JobError as exc:
            return jsonify({"error": str(exc), "errors": exc.errors}), 400
        return jsonify({"job": rec})

    @app.route("/api/jobs/window", methods=["GET"])
    def jobs_window_get():
        return jsonify({"window": make().window(), "now": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})

    @app.route("/api/jobs/window", methods=["POST"])
    def jobs_window_set():
        if not loopback_only(request):
            return refuse_remote()
        body = request.get_json(force=True, silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "the body must be a JSON object", "errors": ["the body must be a JSON object"]}), 400
        try:
            return jsonify({"window": make().set_window(body.get("start"), body.get("end"))})
        except jobs.JobError as exc:
            return jsonify({"error": str(exc), "errors": exc.errors}), 400

    @app.route("/api/jobs/window", methods=["DELETE"])
    def jobs_window_clear():
        if not loopback_only(request):
            return refuse_remote()
        make().clear_window()
        return jsonify({"window": None})

    @app.route("/api/jobs/run", methods=["GET"])
    def jobs_run_status():
        return jsonify(job_run.status(make()))

    @app.route("/api/jobs/run", methods=["POST"])
    def jobs_run_start():
        if not loopback_only(request):
            return refuse_remote()
        body = request.get_json(force=True, silent=True)
        if body is not None and not isinstance(body, dict):
            return jsonify({"error": "the body must be a JSON object"}), 400
        # Gate on ComfyUI readiness: if it's down, try to start it once.
        if not comfy_proc.comfy_up():
            try:
                comfy_proc.ensure_comfyui_ready()
            except comfy_proc.ComfyUnavailable as exc:
                reason = exc.reason or str(exc)
                return jsonify({"error": "ComfyUI is down and could not be started: %s" % reason,
                                "log_tail": exc.log_tail if exc.log_tail else None}), 409
        try:
            return jsonify(job_run.start(make(), (body or {}).get("hours"), spawn=spawn)), 202
        except job_run.RunError as exc:
            return jsonify({"error": str(exc)}), exc.status

    @app.route("/api/jobs/run/stop", methods=["POST"])
    def jobs_run_stop():
        if not loopback_only(request):
            return refuse_remote()
        try:
            return jsonify(job_run.stop(make(), **({"kill": kill} if kill else {})))
        except job_run.RunError as exc:
            return jsonify({"error": str(exc)}), exc.status

    for action in ("hold", "release", "remove"):
        app.add_url_rule("/api/jobs/<jid>/" + action, "jobs_" + action, (lambda a: lambda jid: transition(jid, a))(action), methods=["POST"])
