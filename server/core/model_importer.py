"""Model importer core: a Civitai or Hugging Face link -> a plan (nothing downloaded) -> a verified file in the right ComfyUI folder, a LoRA
registry entry and a licence row. Stdlib only, no Flask; HTTP and downloading are injected so tests never touch the network.

Rules: only .safetensors files are accepted (never pickles); a size cap; a file is downloaded into models/.quarantine, its SHA256 (when the
site gives one) and its safetensors header are checked, and only then is it moved into place without ever overwriting an existing file.
A Civitai API key, if the owner created ~/.config/promptforge/civitai.json, is sent only to the site's own host, never to a download
redirect, and never appears in any returned value, log line or error text.

Command line (dry run by default):  python3 server/core/model_importer.py <link> [--type lora|checkpoint|embedding] [--yes]
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import struct
import sys
import tempfile
import threading
import http.client
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 6 * 1024 ** 3                    # loras, embeddings and anything that is not a checkpoint
CHECKPOINT_MAX_BYTES = 12 * 1024 ** 3       # a full SDXL / Illustrious checkpoint is about 6.9 GB (2026-11-13: every one was refused at 6 GiB)
FREE_SPACE_MARGIN = 2 * 1024 ** 3           # an import needs the file's size plus this much free on the models disk
CONNECT_TIMEOUT = 3.0   # seconds a single address gets before the next one is tried
FETCH_DEADLINE = 20.0   # seconds a metadata fetch may take in all
ALLOWED_HOSTS = ("civitai.com", "www.civitai.com", "civitai.red", "www.civitai.red", "huggingface.co")
TYPE_DIRS = {"lora": "loras", "locon": "loras", "dora": "loras", "checkpoint": "checkpoints", "textualinversion": "embeddings", "embedding": "embeddings"}
DEFAULT_MODELS_DIR = "/media/model-cache/github/TheAgent/app/tools/ComfyUI/models"
KEY_PATH = os.path.join(os.path.expanduser("~"), ".config", "promptforge", "civitai.json")
QUARANTINE_DIR = ".quarantine"
_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
REGISTRY_PATH = os.path.join(_DATA, "loras.json")
LICENSES_PATH = os.path.join(_DATA, "model-licenses.json")
_BASES = (("illustrious", "illustrious"), ("pony", "pony"), ("noob", "noobai"), ("anima", "anima"), ("krea", "krea2"), ("sdxl", "sdxl"))


class ImportError_(ValueError):
    """The link, the plan or the downloaded file cannot be imported (the message says why and never holds the API key)."""


# ----------------------------------------------------------------------------------------------- link

def max_bytes_for(model_type):
    """The size cap for a model of this type (a Civitai type string or our own: 'Checkpoint', 'checkpoint', 'LORA', ...): CHECKPOINT_MAX_BYTES for a
    checkpoint, MAX_BYTES for everything else."""
    return CHECKPOINT_MAX_BYTES if str(model_type or "").strip().lower() == "checkpoint" else MAX_BYTES


def parse_link(url):
    """{site: 'civitai', model_id, version_id|None} for a civitai.com / civitai.red /models/<id> link (optional ?modelVersionId=<n>), or
    {site: 'huggingface', repo, rev, path} for a huggingface.co /<owner>/<repo>/(blob|resolve)/<rev>/<path> file link. ImportError_ otherwise."""
    try:
        u = urllib.parse.urlparse(str(url).strip())
    except ValueError:
        raise ImportError_("not a link")
    host = (u.hostname or "").lower()
    if u.scheme != "https" or host not in ALLOWED_HOSTS:
        raise ImportError_("only https links to civitai.com, civitai.red or huggingface.co can be imported")
    if host.endswith("civitai.com") or host.endswith("civitai.red"):
        m = re.match(r"^/models/(\d+)(?:/|$)", u.path)
        if not m:
            raise ImportError_("a Civitai link looks like https://civitai.com/models/<number>")
        v = urllib.parse.parse_qs(u.query).get("modelVersionId", [None])[0]
        return {"site": "civitai", "model_id": int(m.group(1)), "version_id": int(v) if v and v.isdigit() else None}
    m = re.match(r"^/([^/]+)/([^/]+)/(?:blob|resolve)/([^/]+)/(.+)$", u.path)
    if not m:
        raise ImportError_("a Hugging Face link must point at one file: huggingface.co/<owner>/<repo>/blob/<revision>/<path>")
    return {"site": "huggingface", "repo": "%s/%s" % (m.group(1), m.group(2)), "rev": m.group(3), "path": urllib.parse.unquote(m.group(4))}


# ----------------------------------------------------------------------------------------------- http

class _NoAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects but drop the Authorization header when the target host differs from the original (a CDN must never see the key)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).hostname != urllib.parse.urlparse(req.full_url).hostname:
            new.headers = {k: v for k, v in new.headers.items() if k.lower() != "authorization"}
            new.unredirected_hdrs = {k: v for k, v in new.unredirected_hdrs.items() if k.lower() != "authorization"}
        return new


def _connect_ipv4_first(host, port, timeout, connect_timeout=None, source_address=None):
    """Like socket.create_connection, but IPv4 addresses are tried before IPv6 and each address gets only connect_timeout seconds."""
    per = CONNECT_TIMEOUT if connect_timeout is None else connect_timeout
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    infos.sort(key=lambda i: 0 if i[0] == socket.AF_INET else 1)
    err = None
    for family, kind, proto, _name, addr in infos:
        sock = None
        try:
            sock = socket.socket(family, kind, proto)
            sock.settimeout(min(per, timeout) if timeout else per)
            if source_address:
                sock.bind(source_address)
            sock.connect(addr)
            sock.settimeout(timeout)
            return sock
        except OSError as e:
            err = e
            if sock is not None:
                sock.close()
    raise err or OSError("no address found for %s" % host)


class _Ipv4FirstHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        self.sock = _connect_ipv4_first(self.host, self.port, self.timeout, source_address=self.source_address)
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._tunnel_host or self.host)


class _Ipv4FirstHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_Ipv4FirstHTTPSConnection, req)


class AuthRequired(ImportError_):
    """The site wants the owner signed in for this download (HTTP 401 or 403 from a Civitai host)."""


def _opener():
    return urllib.request.build_opener(_NoAuthOnRedirect(), _Ipv4FirstHTTPSHandler())


def _read_all(url, headers, timeout):
    req = urllib.request.Request(url, headers=dict(headers or {}, **{"User-Agent": "promptforge-importer/1"}))
    with _opener().open(req, timeout=timeout) as r:
        return r.read()


def default_fetcher(url, headers=None, deadline=None):
    """GET `url` and return the body bytes. IPv4 first; ImportError_ when the site has not answered after `deadline` seconds (default 20)."""
    limit = FETCH_DEADLINE if deadline is None else deadline
    box = {}

    def run():
        try:
            box["body"] = _read_all(url, headers, limit)
        except BaseException as e:  # handed back to the caller's thread
            box["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(limit)
    if t.is_alive():
        raise ImportError_("the site did not answer in %g s" % limit)
    if "err" in box:
        raise box["err"]
    return box["body"]


def default_head(url, headers=None, deadline=None):
    """HEAD `url` and return the HTTP status code (a 4xx/5xx comes back as its code, not an exception). Redirects are followed; the timeout is `deadline` seconds (default 20)."""
    limit = FETCH_DEADLINE if deadline is None else deadline
    req = urllib.request.Request(url, headers=dict(headers or {}, **{"User-Agent": "promptforge-importer/1"}), method="HEAD")
    try:
        with _opener().open(req, timeout=limit) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def default_downloader(url, dest, headers=None, max_bytes=MAX_BYTES, progress=None):
    """Stream `url` into the file `dest`; ImportError_ when the body grows past max_bytes. progress(bytes_so_far, total_or_None)."""
    req = urllib.request.Request(url, headers=dict(headers or {}, **{"User-Agent": "promptforge-importer/1"}))
    got = 0
    try:
        resp = _opener().open(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and "civitai" in (urllib.parse.urlparse(url).hostname or ""):
            raise AuthRequired("Civitai refused the download (401). It needs an API key: add or replace it in the Models panel.")
        raise
    with resp as r, open(dest, "wb") as out:
        total = int(r.headers.get("Content-Length") or 0) or None
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            got += len(chunk)
            if got > max_bytes:
                raise ImportError_("the download is larger than the %d byte limit" % max_bytes)
            out.write(chunk)
            if progress:
                progress(got, total)
    return got


def load_key(config_path=None):
    """The Civitai API key from ~/.config/promptforge/civitai.json {"api_key": "..."}, or None. The key is read from this file only."""
    try:
        with open(config_path or KEY_PATH, encoding="utf-8") as fh:
            k = json.load(fh).get("api_key")
    except (OSError, ValueError, AttributeError):
        return None
    return k.strip() if isinstance(k, str) and k.strip() else None


def save_key(api_key, config_path=None):
    """Store the Civitai API key in the key file (directory mode 0700, file mode 0600), written to a temp file and moved into place atomically.
    TypeError or ValueError for bad input. The key is never logged or included in an error message."""
    if not isinstance(api_key, str):
        raise TypeError("the API key must be a string")
    value = api_key.strip()
    if not value:
        raise ValueError("the API key is empty")
    path = config_path or KEY_PATH
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".civitai-", suffix=".tmp")
    try:
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, json.dumps({"api_key": value}).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def key_status(config_path=None):
    """True when a Civitai API key is stored. Returns a bool only, never the key."""
    return load_key(config_path) is not None


def delete_key(config_path=None):
    """Remove the stored Civitai API key. Succeeds when the key file is already absent."""
    try:
        os.remove(config_path or KEY_PATH)
    except FileNotFoundError:
        pass


def _scrub(text, key):
    return str(text).replace(key, "<key>") if key else str(text)


def _headers(key):
    return {"Authorization": "Bearer " + key} if key else {}


# ----------------------------------------------------------------------------------------------- plan

def _models_dir(models_dir):
    return models_dir or os.environ.get("COMFYUI_MODELS_DIR") or DEFAULT_MODELS_DIR


def map_base(text):
    """Our base name ('illustrious', 'pony', 'noobai', 'anima', 'krea2', 'sdxl') for a Civitai base-model string, else None."""
    low = str(text or "").lower()
    return next((b for k, b in _BASES if k in low), None)


def _flags(raw):
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(x) for x in raw] if isinstance(raw, list) else None


def licence_verdict(flags):
    """('conditional'|'no'|'unverified', text) from Civitai's allowCommercialUse flags: 'Image' (selling generated images) is the one that matters."""
    if flags is None:
        return "unverified", "Civitai lists no commercial-use permissions for this model"
    verdict = "conditional" if "Image" in flags else "no"
    return verdict, "Civitai creator permissions: %s" % (", ".join(flags) or "none")


def _gb_text(n_bytes):
    """Binary gigabytes (1024^3 bytes) with one decimal place, labelled GB: 1.5 GB."""
    return "%.1f GB" % (n_bytes / 1024 ** 3)


class OversizeError(ImportError_):
    """A file over its type's size cap. The plan turns it into a refusal it can show; everything else still raises."""


def _check_file(name, size, max_bytes, mtype=None):
    if not str(name).lower().endswith(".safetensors"):
        raise ImportError_("only .safetensors files are accepted (not %s)" % (os.path.splitext(str(name))[1] or "this file type"))
    if size and size > max_bytes:
        raise OversizeError("%s, over the %s limit for %ss" % (_gb_text(size), _gb_text(max_bytes), str(mtype or "model").lower()))


def fetch_plan(link, fetcher=None, key=None, models_dir=None, max_bytes=None, type_override=None, head=None):
    """The plan for `link` (a URL string or parse_link's dict) WITHOUT downloading the model: {site, model_id, version_id, name, type, base_model,
    base, trained_words, file: {name, size_bytes, sha256, url}, folder, destination, nsfw, allow_commercial_use, licence: {commercial_use, text,
    source_url}, warnings}. `fetcher(url, headers)` returns bytes (default: urllib). ImportError_ for a type we cannot place, a non-safetensors
    file, a file over max_bytes (default: max_bytes_for the model's type) or an unreadable answer."""
    spec = parse_link(link) if isinstance(link, str) else link
    fetch = fetcher or default_fetcher
    try:
        if spec["site"] == "civitai":
            plan = _plan_civitai(spec, fetch, key, max_bytes, type_override)
        else:
            plan = _plan_hf(spec, fetch, max_bytes, type_override)
    except ImportError_:
        raise
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
        raise ImportError_("could not read the model information: %s" % _scrub(e, key))
    if "error" in plan:
        return plan
    folder = TYPE_DIRS.get(str(plan["type"]).lower())
    if not folder:
        raise ImportError_("cannot place a %s model yet (loras, checkpoints and embeddings only)" % plan["type"])
    plan["folder"] = folder
    plan["destination"] = os.path.join(_models_dir(models_dir), folder, plan["file"]["name"])
    if os.path.exists(plan["destination"]):
        plan["warnings"].append("a file named %s is already installed and will not be overwritten" % plan["file"]["name"])
    if plan["base"] is None:
        plan["warnings"].append("the base model is unknown, so mismatches cannot be flagged")
    if key is None and plan["site"] == "civitai":
        try:
            if (head or default_head)(plan["file"]["url"], deadline=FETCH_DEADLINE) == 401:
                plan["warnings"].append("Civitai needs an API key to download this file: add one in the Models panel")
        except Exception:
            pass
    return plan


def _oversize_refusal(name, mtype, base_model, file_name, size, error, licence):
    """The plan returned for a file over its size cap: the fields the page shows, the size in bytes and the refusal text (no folder, no destination)."""
    return {"name": name, "type": mtype, "base_model": base_model, "licence": licence,
            "file": {"name": os.path.basename(str(file_name)), "size": int(size)}, "error": error}


def _plan_civitai(spec, fetch, key, max_bytes, type_override):
    model = json.loads(fetch("https://civitai.com/api/v1/models/%d" % spec["model_id"], _headers(key)))
    if not isinstance(model, dict):
        raise ImportError_("unexpected answer from Civitai")
    versions = model.get("modelVersions") or []
    if spec.get("version_id"):
        versions = [v for v in versions if v.get("id") == spec["version_id"]]
    if not versions:
        raise ImportError_("no matching version of this model")
    ver = versions[0]
    files = [f for f in ver.get("files") or [] if str(f.get("name", "")).lower().endswith(".safetensors")]
    if not files:
        raise ImportError_("only .safetensors files are accepted and this version has none")
    f = next((x for x in files if x.get("primary")), files[0])
    size = int(round(float(f.get("sizeKB") or 0) * 1024))
    mtype = type_override or model.get("type")
    try:
        _check_file(f["name"], size, max_bytes if max_bytes is not None else max_bytes_for(mtype), mtype)
    except OversizeError as e:
        verdict, text = licence_verdict(_flags(model.get("allowCommercialUse")))
        return _oversize_refusal(model.get("name"), mtype, ver.get("baseModel"), f["name"], size, str(e),
                                 {"commercial_use": verdict, "text": text, "source_url": "https://civitai.com/models/%s" % model.get("id", spec["model_id"])})
    flags = _flags(model.get("allowCommercialUse"))
    verdict, text = licence_verdict(flags)
    return {"site": "civitai", "model_id": model.get("id", spec["model_id"]), "version_id": ver.get("id"),
            "name": "%s - %s" % (model.get("name"), ver.get("name")) if ver.get("name") else model.get("name"),
            "type": mtype, "base_model": ver.get("baseModel"), "base": map_base(ver.get("baseModel")),
            "trained_words": [str(w).strip() for w in ver.get("trainedWords") or [] if str(w).strip()],
            "file": {"name": os.path.basename(f["name"]), "size_bytes": size or None, "sha256": ((f.get("hashes") or {}).get("SHA256") or "").lower() or None,
                     "url": f.get("downloadUrl") or "https://civitai.com/api/download/models/%s" % ver.get("id")},
            "nsfw": bool(model.get("nsfw")), "allow_commercial_use": flags,
            "licence": {"commercial_use": verdict, "text": text, "source_url": "https://civitai.com/models/%s" % model.get("id", spec["model_id"])},
            "warnings": []}


def _plan_hf(spec, fetch, max_bytes, type_override):
    if not type_override:
        raise ImportError_("a Hugging Face file has no type information: say whether it is a lora, a checkpoint or an embedding")
    cap = max_bytes if max_bytes is not None else max_bytes_for(type_override)
    _check_file(spec["path"], 0, cap)
    tree = json.loads(fetch("https://huggingface.co/api/models/%s/tree/%s?recursive=true" % (spec["repo"], urllib.parse.quote(spec["rev"], safe="")), {}))
    if not isinstance(tree, list):
        raise ImportError_("unexpected answer from Hugging Face")
    entry = next((t for t in tree if t.get("path") == spec["path"]), None)
    if entry is None:
        raise ImportError_("that file is not in the repository")
    size = int(entry.get("size") or 0)
    try:
        _check_file(spec["path"], size, cap, type_override)
    except OversizeError as e:
        return _oversize_refusal(os.path.splitext(os.path.basename(spec["path"]))[0], type_override, None, spec["path"], size, str(e),
                                 {"commercial_use": "unverified", "text": "Hugging Face file: licence not read, check the repository",
                                  "source_url": "https://huggingface.co/%s" % spec["repo"]})
    name = os.path.basename(spec["path"])
    return {"site": "huggingface", "model_id": spec["repo"], "version_id": spec["rev"], "name": os.path.splitext(name)[0], "type": type_override,
            "base_model": None, "base": None, "trained_words": [],
            "file": {"name": name, "size_bytes": size or None, "sha256": ((entry.get("lfs") or {}).get("oid") or "").lower() or None,
                     "url": "https://huggingface.co/%s/resolve/%s/%s" % (spec["repo"], spec["rev"], urllib.parse.quote(spec["path"]))},
            "nsfw": False, "allow_commercial_use": None,
            "licence": {"commercial_use": "unverified", "text": "Hugging Face file: licence not read, check the repository",
                        "source_url": "https://huggingface.co/%s" % spec["repo"]}, "warnings": []}


# ----------------------------------------------------------------------------------------------- verify

def check_safetensors(path):
    """True when `path` is a plausible safetensors file: an 8 byte little-endian header length, a JSON object header, and every tensor's data
    offsets inside the file. ImportError_ otherwise (a pickle or a truncated download fails here)."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        raw = fh.read(8)
        if len(raw) < 8:
            raise ImportError_("not a safetensors file: too short")
        n = struct.unpack("<Q", raw)[0]
        if n == 0 or n > min(100 * 1024 * 1024, size - 8):
            raise ImportError_("not a safetensors file: bad header length")
        try:
            header = json.loads(fh.read(n))
        except ValueError:
            raise ImportError_("not a safetensors file: the header is not JSON")
    if not isinstance(header, dict):
        raise ImportError_("not a safetensors file: the header is not an object")
    end = size - 8 - n
    for name, t in header.items():
        if name == "__metadata__":
            continue
        offs = t.get("data_offsets") if isinstance(t, dict) else None
        if not (isinstance(offs, list) and len(offs) == 2 and all(isinstance(x, int) for x in offs) and 0 <= offs[0] <= offs[1] <= end):
            raise ImportError_("not a safetensors file: tensor %s points outside the file" % name)
    return True


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------------------------- write

def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = "%s.%s.tmp" % (path, secrets.token_hex(4))
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:48] or "lora"


def register(plan, registry_path=None):
    """Add or update the LoRA registry entry (or the checkpoint's base) for the imported file; returns the registry id or None."""
    path = registry_path or REGISTRY_PATH
    reg = _read_json(path, {"loras": [], "checkpoints": {}})
    reg.setdefault("loras", [])
    reg.setdefault("checkpoints", {})
    name = plan["file"]["name"]
    rid = None
    if plan["folder"] == "loras":
        entry = next((e for e in reg["loras"] if e.get("file") == name), None)
        today = datetime.date.today().isoformat()
        fields = {"name": plan["name"], "file": name, "base": plan["base"], "triggers": list(plan["trained_words"]),
                  "source_url": plan["licence"]["source_url"], "notes": "Imported %s from %s." % (today, plan["site"])}
        if entry:
            entry.update(fields)
            rid = entry["id"]
        else:
            taken, rid, k = {e.get("id") for e in reg["loras"]}, _slug(plan["name"]), 1
            base_id = rid
            while rid in taken:
                k += 1
                rid = "%s_%d" % (base_id, k)
            reg["loras"].append(dict({"id": rid, "default_weight": 0.8, "clip_skip": None}, **fields))
    elif plan["folder"] == "checkpoints" and plan["base"]:
        reg["checkpoints"][name] = plan["base"]
    _write_json(path, reg)
    return rid


def record_licence(plan, licenses_path=None):
    """Add or update the model-licenses.json row keyed by the file name."""
    path = licenses_path or LICENSES_PATH
    lic = _read_json(path, {})
    lic[plan["file"]["name"]] = {"source_url": plan["licence"]["source_url"], "licence": plan["licence"]["text"],
                                 "commercial_use": plan["licence"]["commercial_use"],
                                 "note": "Imported by the model importer; flags are the uploader's, not legal advice.",
                                 "checked": datetime.date.today().isoformat()}
    _write_json(path, lic)


def _check_free_space(models_dir, size):
    """ImportError_ when the disk holding models_dir (or its nearest existing parent) has less than `size` + FREE_SPACE_MARGIN bytes free."""
    probe = os.path.abspath(models_dir)
    while probe and not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    free = shutil.disk_usage(probe).free
    need = int(size) + FREE_SPACE_MARGIN
    if free < need:
        raise ImportError_("not enough free disk space: %.1f GB needed (the file plus %.0f GB spare), %.1f GB free" % (need / 1e9, FREE_SPACE_MARGIN / 1024 ** 3, free / 1e9))


def execute_plan(plan, models_dir=None, downloader=None, registry_path=None, licenses_path=None, key=None, progress=None, max_bytes=None):
    """Download `plan`'s file into models/.quarantine, verify it (size cap, SHA256 when the plan has one, safetensors header), move it into the
    destination folder WITHOUT overwriting anything, then write the registry entry and the licence row. The quarantine folder is always removed.
    Returns {file, destination, registry_id, licence}. ImportError_ on any failure, with nothing left behind in quarantine or destination."""
    base = _models_dir(models_dir)
    folder = TYPE_DIRS.get(str(plan["type"]).lower())
    if not folder:
        raise ImportError_("cannot place a %s model yet" % plan["type"])
    if max_bytes is None:
        max_bytes = max_bytes_for(plan["type"])
    _check_file(plan["file"]["name"], plan["file"].get("size_bytes") or 0, max_bytes)
    name = os.path.basename(plan["file"]["name"])
    dest = os.path.join(base, folder, name)
    if os.path.exists(dest):
        raise ImportError_("%s is already installed; it was not overwritten" % name)
    _check_free_space(base, plan["file"].get("size_bytes") or 0)
    quarantine = os.path.join(base, QUARANTINE_DIR, secrets.token_hex(8))
    os.makedirs(quarantine)
    tmp = os.path.join(quarantine, name)
    try:
        try:
            (downloader or default_downloader)(plan["file"]["url"], tmp, _headers(key) if _same_site(plan["file"]["url"]) else {}, max_bytes, progress)
        except ImportError_:
            raise
        except (urllib.error.URLError, OSError) as e:
            raise ImportError_("the download failed: %s" % _scrub(e, key))
        if os.path.getsize(tmp) > max_bytes:
            raise ImportError_("the downloaded file is over the size limit")
        want = plan["file"].get("sha256")
        if want and _sha256(tmp) != want.lower():
            raise ImportError_("the downloaded file does not match its published SHA256; it was discarded")
        check_safetensors(tmp)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            os.link(tmp, dest)                      # fails (never overwrites) when the destination appeared meanwhile
        except FileExistsError:
            raise ImportError_("%s is already installed; it was not overwritten" % name)
    finally:
        _rmtree(quarantine)
        try:
            os.rmdir(os.path.join(base, QUARANTINE_DIR))
        except OSError:
            pass
    rid = register(plan, registry_path)
    record_licence(plan, licenses_path)
    return {"file": name, "destination": dest, "registry_id": rid, "licence": plan["licence"]["commercial_use"]}


def _same_site(url):
    h = (urllib.parse.urlparse(url).hostname or "").lower()
    return h in ALLOWED_HOSTS


def _rmtree(path):
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            os.unlink(os.path.join(root, f))
        for d in dirs:
            os.rmdir(os.path.join(root, d))
    if os.path.isdir(path):
        os.rmdir(path)


# ----------------------------------------------------------------------------------------------- command line

def main(argv=None, fetcher=None, downloader=None, out=None, key=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description="Import a model from a Civitai or Hugging Face link (dry run unless --yes).")
    ap.add_argument("link")
    ap.add_argument("--type", dest="type_", choices=("lora", "checkpoint", "embedding"))
    ap.add_argument("--yes", action="store_true", help="really download and install it")
    ap.add_argument("--models-dir")
    a = ap.parse_args(argv)
    key = key if key is not None else load_key()
    try:
        plan = fetch_plan(a.link, fetcher, key, a.models_dir, type_override=a.type_)
        print("%s (%s, base %s)" % (plan["name"], plan["type"], plan["base_model"] or "unknown"), file=out)
        print("  file: %s, %s MB" % (plan["file"]["name"], round((plan["file"]["size_bytes"] or 0) / 1048576, 1)), file=out)
        print("  trigger words: %s" % (", ".join(plan["trained_words"]) or "none"), file=out)
        print("  licence: %s (%s)" % (plan["licence"]["commercial_use"], plan["licence"]["text"]), file=out)
        print("  destination: %s" % plan["destination"], file=out)
        for w in plan["warnings"]:
            print("  warning: %s" % w, file=out)
        if not a.yes:
            print("dry run: nothing was downloaded (add --yes to install)", file=out)
            return 0
        res = execute_plan(plan, a.models_dir, downloader, key=key)
        print("installed %s (registry id %s)" % (res["destination"], res["registry_id"]), file=out)
        return 0
    except ImportError_ as e:
        print("error: %s" % _scrub(e, key), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
