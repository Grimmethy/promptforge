"""Civitai downloads through the owner's signed-in browser session (stdlib only). The browser work runs as a SUBPROCESS of a python that has
site_sessions and playwright (PROMPTFORGE_SESSION_PYTHON, default the books venv); PromptForge never types, reads or stores a password or a cookie.
`runner(argv, timeout) -> (returncode, stdout)` and `popen` are injectable so tests never open a browser."""
import json
import os
import socket
import subprocess

DEFAULT_PYTHON = "/media/model-cache/github/books/.venv/bin/python"
DEFAULT_PORT = 9341
_HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(_HERE, "..", "..", "scripts", "civitai_fetch.py")
SITES_FILE = os.path.join(_HERE, "..", "..", "data", "sites.toml")
STATUS_TIMEOUT = 90
TERM_GRACE = 20       # seconds a timed-out helper gets, after SIGTERM, to close its browser before it is killed
_login = [None]


class SessionError(Exception):
    """kind: signed_out | refused | timeout | unavailable | busy"""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def session_python():
    p = os.environ.get("PROMPTFORGE_SESSION_PYTHON") or DEFAULT_PYTHON
    return p if os.path.isfile(p) else None


def profile_dir():
    return os.environ.get("PROMPTFORGE_BROWSER_PROFILE") or os.path.join(os.path.expanduser("~"), ".config", "promptforge", "browser")


def port():
    try:
        return int(os.environ.get("PROMPTFORGE_BROWSER_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def headed():
    return bool(os.environ.get("PROMPTFORGE_BROWSER_HEADED"))


def port_busy():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port())) == 0


def _run(argv, timeout):
    """Run the helper. On a timeout it gets SIGTERM first (the helper then closes the browser it started) and SIGKILL only after TERM_GRACE seconds."""
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.terminate()
        try:
            p.communicate(timeout=TERM_GRACE)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
        raise
    return p.returncode, out


def _base(py):
    return [py, os.path.abspath(SCRIPT), "--profile", profile_dir(), "--port", str(port()), "--sites", os.path.abspath(SITES_FILE)] + (["--headed"] if headed() else [])


def _json_line(out):
    for line in reversed((out or "").strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def status(runner=None, timeout=STATUS_TIMEOUT):
    """{signed_in: bool|None, available: bool, error: str|None}. Never raises."""
    py = session_python()
    if not py:
        return {"signed_in": None, "available": False, "error": "session python not found"}
    if port_busy():
        return {"signed_in": None, "available": True, "error": "browser_busy"}
    try:
        rc, out = (runner or _run)(_base(py) + ["--status"], timeout)
    except subprocess.TimeoutExpired:
        return {"signed_in": None, "available": True, "error": "timeout"}
    except OSError as e:
        return {"signed_in": None, "available": False, "error": str(e)[:120]}
    data = _json_line(out)
    if not isinstance(data, dict) or "signed_in" not in data:
        return {"signed_in": None, "available": True, "error": "no answer from the browser helper"}
    return {"signed_in": bool(data["signed_in"]), "available": True, "error": None}


def download(url, dest, runner=None, timeout=3600):
    """Download `url` to `dest` through the saved session; returns the byte count or raises SessionError."""
    py = session_python()
    if not py:
        raise SessionError("unavailable", "session python not found")
    try:
        rc, out = (runner or _run)(_base(py) + ["--download", url, "--dest", dest, "--timeout", str(timeout)], timeout + 60)
    except subprocess.TimeoutExpired:
        raise SessionError("timeout", "the browser download took too long")
    except OSError as e:
        raise SessionError("unavailable", str(e)[:120])
    data = _json_line(out) or {}
    if data.get("ok") is True:
        return int(data.get("bytes") or 0)
    err = str(data.get("error") or "the browser helper failed")
    if err in ("signed_out", "refused", "timeout"):
        raise SessionError(err, err)
    if err == "browser_busy":
        raise SessionError("busy", "the Civitai browser window is open; close it first")
    raise SessionError("unavailable", err[:200])


def _login_argv(py):
    """Command for the sign-in window: the site-sessions console script next to the session python, else a -c import of its main()."""
    args = ["--profile-dir", profile_dir(), "--port", str(port()), "--sites", os.path.abspath(SITES_FILE), "login", "civitai"]
    script = os.path.join(os.path.dirname(py), "site-sessions")
    if os.path.isfile(script) and os.access(script, os.X_OK):
        return [script] + args
    return [py, "-c", "import sys; from site_sessions.cli import main; sys.exit(main())"] + args


def start_login(popen=None, wait=2.0):
    """Open a plain Brave window on the profile so the owner signs in by hand. Refuses a second window or a busy profile."""
    py = session_python()
    if not py:
        raise SessionError("unavailable", "session python not found")
    if (_login[0] is not None and _login[0].poll() is None) or port_busy():
        raise SessionError("busy", "a Civitai browser window is already open")
    argv = _login_argv(py)
    proc = (popen or subprocess.Popen)(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    _login[0] = proc
    try:
        # one bounded wait, not a poll loop: a child that dies at once is reported as unavailable (route 503)
        rc = proc.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        return  # still running: the sign-in window is open
    # the child exited within the wait: clear the handle so the next click is not refused as busy;
    # a clean exit (rc 0) is accepted without raising (judgement call: the window was closed at once)
    _login[0] = None
    if rc != 0:
        raise SessionError("unavailable", "the sign-in window did not start")
