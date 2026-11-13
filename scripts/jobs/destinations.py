"""Deliver a finished picture to the job's destination: nothing, a folder (copy-dir) or a CharacterForge entry. The runner calls the function make_deliver()
returns after every ok picture; whatever it returns (a string) is recorded on that picture's result as 'delivered', and an exception is recorded as
'delivery_error' without failing the picture, which always stays in the PromptForge gallery too.

copy-dir files are named <job id>_<seed>.<ext>, so a client that filed a job knows exactly where each of its pictures lands.
"""
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

from scripts.slot_fill.adapters.characterforge import multipart

DEFAULT_CF = "http://localhost:7433"
_EXTS = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


def _ext(name):
    ext = os.path.splitext(str(name))[1].lstrip(".").lower()
    return ext if ext in _EXTS else "png"


def copy_name(job_id, seed, name):
    return "%s_%d.%s" % (job_id, seed, _ext(name))


def _default_post(url, body, content_type):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode()).get("error") or exc.code
        except (ValueError, OSError):
            msg = exc.code
        raise RuntimeError("CharacterForge refused the picture: %s" % msg)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError("CharacterForge not reachable at %s (%s)" % (url.split("/api/")[0], getattr(exc, "reason", exc)))


def _copy_dir(job, seed, gen, path):
    os.makedirs(path, exist_ok=True)
    final = os.path.join(path, copy_name(job["id"], seed, gen.name))
    tmp = os.path.join(path, ".%s.%s.tmp" % (os.path.basename(final), secrets.token_hex(4)))
    try:
        with open(tmp, "wb") as fh:
            fh.write(gen.data)
        os.replace(tmp, final)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return final


def make_deliver(cf_url=None, cf_post=None):
    post = cf_post or _default_post

    def deliver(job, seed, gen):
        dest = job.get("destination") or {"kind": "none"}
        kind = dest.get("kind")
        if kind == "none":
            return None
        if kind == "copy-dir":
            return _copy_dir(job, seed, gen, dest["path"])
        if kind == "characterforge":
            base = (cf_url or os.environ.get("CHARACTERFORGE_URL") or DEFAULT_CF).rstrip("/")
            prov = {"model": job["model"], "seed": seed, "steps": job["steps"], "cfg": job["cfg"], "prompt": job["positive"], "batch": "job-queue",
                    "job_id": job["id"], "requester": job["requester"]}
            fields = {"role": dest["role"], "tag": "job-%s-%d" % (job["id"], seed), "note": "job queue, requester %s" % job["requester"], "verdict": "needs-work",
                      "provenance": json.dumps(prov), "source": json.dumps({"forge": "job-queue", "job": job["id"]})}
            ext = _ext(gen.name)
            body, ctype = multipart(fields, os.path.basename(str(gen.name)), gen.data, _EXTS[ext])
            post(base + "/api/characters/%s/images" % urllib.parse.quote(dest["slug"], safe=""), body, ctype)
            return "characterforge:%s:%s" % (dest["slug"], dest["role"])
        raise ValueError("unknown destination kind %s" % kind)

    return deliver
