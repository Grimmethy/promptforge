#!/usr/bin/env python3
"""Download one Civitai file through the owner's signed-in browser session (password-free: the owner signed in by hand in a real Brave profile).

Run with a python that has the `site_sessions` and `playwright` packages (PromptForge's own venv has neither):
    civitai_fetch.py --profile DIR --port N --status
    civitai_fetch.py --profile DIR --port N --download URL --dest PATH [--timeout S] [--headed]
Prints ONE JSON line and exits 0 (ok) or 1. Never prints a name, a cookie or a password. fetch() takes the Sessions-like object so tests inject fakes."""
import argparse
import json
import os
import sys

HOME_URL = "https://civitai.com"
SESSION_JS = "fetch('/api/auth/session').then(r => r.json()).then(j => !!(j && j.user)).catch(() => false)"


def _page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()


def _signed_in(pg):
    try:
        pg.goto(HOME_URL, wait_until="domcontentloaded")
    except Exception:  # noqa: BLE001 - the evaluate below decides
        pass
    return bool(pg.evaluate(SESSION_JS))


def status(sessions, headless=True):
    with sessions.session(headless=headless) as ctx:
        return {"signed_in": _signed_in(_page(ctx))}


def fetch(sessions, url, dest, timeout=600, headless=True):
    """Save `url` to `dest` (never overwriting) using the saved session. Returns {ok: True, bytes} or {ok: False, error}."""
    if os.path.exists(dest):
        return {"ok": False, "error": "the destination file already exists"}
    try:
        with sessions.session(headless=headless) as ctx:
            pg = _page(ctx)
            if not _signed_in(pg):
                return {"ok": False, "error": "signed_out"}
            try:
                with pg.expect_download(timeout=timeout * 1000) as info:
                    try:
                        resp = pg.goto(url)          # a download aborts the navigation; that error is expected
                    except Exception:  # noqa: BLE001
                        resp = None
                    if resp is not None and getattr(resp, "status", 200) in (401, 403, 404):
                        return {"ok": False, "error": "refused"}
                info.value.save_as(dest)
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == "TimeoutError":
                    return {"ok": False, "error": "timeout"}
                raise
    except RuntimeError as e:
        return {"ok": False, "error": "browser_busy" if "busy" in str(e) else str(e)[:200]}
    if not os.path.isfile(dest):
        return {"ok": False, "error": "refused"}
    return {"ok": True, "bytes": os.path.getsize(dest)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--sites")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--download")
    ap.add_argument("--dest")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args(argv)
    from site_sessions.core import Sessions, load_sites
    sessions = Sessions(a.profile, a.port, load_sites(a.sites) if a.sites else None)
    try:
        if a.status:
            out = status(sessions, headless=not a.headed)
        elif a.download and a.dest:
            out = fetch(sessions, a.download, a.dest, a.timeout, headless=not a.headed)
        else:
            ap.error("give --status or --download URL --dest PATH")
    except RuntimeError as e:
        out = {"ok": False, "error": "browser_busy" if "busy" in str(e) else str(e)[:200]}
    print(json.dumps(out))
    return 0 if out.get("ok", True) is not False else 1


if __name__ == "__main__":
    sys.exit(main())
