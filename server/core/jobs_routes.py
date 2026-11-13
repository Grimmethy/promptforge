"""HTTP routes of the render job queue (/api/jobs). Registered on the Flask app by server/app.py; the store is built per request so the jobs folder
(PROMPTFORGE_JOBS_DIR) is read at call time. Reads are open to anyone who can reach the service; every write must come from this machine.
"""
from flask import jsonify, request

from core import jobs

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


def register(app, store_factory=None):
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
        except jobs.JobError as exc:
            return jsonify({"error": str(exc), "errors": exc.errors}), 409
        return jsonify({"job": rec})

    for action in ("hold", "release", "remove"):
        app.add_url_rule("/api/jobs/<jid>/" + action, "jobs_" + action, (lambda a: lambda jid: transition(jid, a))(action), methods=["POST"])
