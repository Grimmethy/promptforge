"""Give a gallery image to an entry in another forge (CharacterForge) -- the PromptForge half of "linked images per entry" (2026-11-10).

Contract (shared with CharacterForge, do not rename):
  PROMPTFORGE_LINK_TARGETS   comma-separated name=url pairs; default characterforge=http://localhost:7433
  a target is used only through its GET /api/link-target descriptor
      {forge, label, entries_path, images_path ("/api/characters/{slug}/images"), roles, verdicts, categories?, max_per_role?}
  PromptForge POSTs the picture to <target><images_path> as multipart: image, role, tag, note, verdict, provenance (JSON copied from the gallery
  sidecar), source (JSON {forge, file}) and optionally replace=<image id>. The target COPIES the image; the gallery file is never touched.
  The gallery sidecar gains  "links": [{forge, slug, name, imageId, role, tag, note, verdict, ts}].

A repeat of the same target + role for the same gallery image is not sent again (the existing link is reported as "already"): a retry after a
partial failure cannot put a second copy on the target. Choosing a picture to replace (replace=<id>) always goes through and updates the link.

Nothing here starts a generation or touches the GPU.
"""

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_TARGETS = "characterforge=http://localhost:7433"
TIMEOUT = 6
_NORM = re.compile(r"[^a-z0-9]+")


class TargetUnreachable(Exception):
    """The target did not answer (down, refused, timed out, or not JSON)."""


def norm(text):
    return _NORM.sub("", str(text or "").lower())


def targets():
    """{name: base url} from PROMPTFORGE_LINK_TARGETS (malformed pairs are ignored), in the order given."""
    out = {}
    for pair in (os.environ.get("PROMPTFORGE_LINK_TARGETS") or DEFAULT_TARGETS).split(","):
        name, _, url = pair.partition("=")
        name, url = name.strip(), url.strip().rstrip("/")
        if name and url.startswith(("http://", "https://")):
            out[name] = url
    return out


def _open(req):
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8") or "null")
        except ValueError:
            body = None
        return e.code, body
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TargetUnreachable(str(getattr(e, "reason", e)))


def _get(url):
    status, body = _open(urllib.request.Request(url))
    if status != 200:
        raise TargetUnreachable("%s answered %s" % (url, status))
    return body


def descriptor(base):
    """The target's /api/link-target descriptor; TargetUnreachable when it is down or does not look like one."""
    d = _get(base + "/api/link-target")
    if not isinstance(d, dict) or not d.get("images_path") or not d.get("entries_path"):
        raise TargetUnreachable("%s has no usable link-target descriptor" % base)
    return d


def _images_url(base, desc, slug):
    return base + desc["images_path"].replace("{slug}", urllib.parse.quote(str(slug), safe=""))


def entries(base, desc):
    """The target's entries as [{slug, name, kind, group, faction, project, hasImage, thumb}]."""
    rows = _get(base + desc["entries_path"])
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not r.get("slug"):
            continue
        thumb = r.get("primaryImage") or ("/api/characters/%s/image?v=%s" % (urllib.parse.quote(r["slug"], safe=""), r["image"]) if r.get("image") else "")
        out.append({"slug": r["slug"], "name": r.get("name") or r["slug"], "kind": r.get("kind") or "character", "group": r.get("group") or "",
                    "faction": r.get("faction") or "", "project": r.get("project") or "", "hasImage": bool(thumb),
                    "thumb": base + thumb if thumb else ""})
    return out


def counts(base, desc, slug):
    """{role: n} of the pictures already linked to one entry on the target (the role selector shows n/10)."""
    body = _get(_images_url(base, desc, slug))
    out = {}
    for rec in (body.get("images") if isinstance(body, dict) else body) or []:
        if isinstance(rec, dict) and rec.get("role"):
            out[rec["role"]] = out.get(rec["role"], 0) + 1
    return out


def preselect(characters, rows):
    """Slugs of the entries a gallery image names for certain: its sidecar's own character names (not a guess from a file name),
    matched by name or slug, all of them when several. Anything else gives [] (a blank picker)."""
    wanted = {norm(c.get("name")) for c in characters or [] if isinstance(c, dict) and not c.get("guessed") and c.get("name")}
    return [r["slug"] for r in rows if wanted and (norm(r["name"]) in wanted or norm(r["slug"]) in wanted)]


def _multipart(fields, filename, data):
    boundary = "----pf" + uuid.uuid4().hex
    parts = []
    for k, v in fields:
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode("utf-8"))
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n" % (boundary, filename)).encode("utf-8"))
    parts.append(data)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    return "multipart/form-data; boundary=" + boundary, b"".join(parts)


def post_image(base, desc, slug, path, fields):
    """POST one picture to one entry; returns (status, json body). TargetUnreachable when the target does not answer."""
    with open(path, "rb") as fh:
        data = fh.read()
    ctype, body = _multipart(fields, os.path.basename(path), data)
    req = urllib.request.Request(_images_url(base, desc, slug), data=body, method="POST", headers={"Content-Type": ctype})
    return _open(req)


# ---- the sidecar's links ----

def read_sidecar(path):
    try:
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def find_link(meta, forge, slug, role):
    for link in meta.get("links") or []:
        if isinstance(link, dict) and link.get("forge") == forge and link.get("slug") == slug and link.get("role") == role:
            return link
    return None


def save_link(path, link):
    """Add link to the sidecar's links (an existing forge+slug+role entry is updated in place, never duplicated); atomic write."""
    meta = read_sidecar(path)
    links = [x for x in meta.get("links") or [] if isinstance(x, dict)]
    for i, x in enumerate(links):
        if (x.get("forge"), x.get("slug"), x.get("role")) == (link["forge"], link["slug"], link["role"]):
            links[i] = link
            break
    else:
        links.append(link)
    meta["links"] = links
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    os.replace(tmp, path)
    return link


def _fail(item, error, **extra):
    return {"slug": item["slug"], "role": item["role"], "ok": False, "error": error, **extra}


def assign(base, rel, image_path, sidecar_path, body):
    """Copy one gallery image to each requested {slug, role} on one target. Returns one result per target, in order:
    {slug, role, ok, imageId?, file?, already?, error?, status?, entries?}. Raises TargetUnreachable only when the descriptor cannot be read
    (nothing was sent); a target that fails part-way is reported in its own result and the others carry on. The gallery file is not modified."""
    desc = descriptor(base)
    forge = desc.get("forge") or "target"
    meta = read_sidecar(sidecar_path)
    provenance = {k: v for k, v in meta.items() if k != "links"}
    replace = body.get("replace") if isinstance(body.get("replace"), dict) else {}
    verdict = body.get("verdict") if body.get("verdict") in (desc.get("verdicts") or ["needs-work"]) else "needs-work"
    results = []
    for item in body.get("targets") or []:
        slug, role = str(item.get("slug") or ""), str(item.get("role") or "")
        item = {"slug": slug, "role": role}
        if not slug or not role:
            results.append(_fail(item, "slug and role are required"))
            continue
        old = find_link(meta, forge, slug, role)
        if old and not replace.get(slug):
            results.append({**item, "ok": True, "already": True, "imageId": old.get("imageId")})
            continue
        fields = [("role", role), ("tag", str(body.get("tag") or "")), ("note", str(body.get("note") or "")), ("verdict", verdict),
                  ("provenance", json.dumps(provenance)), ("source", json.dumps({"forge": "promptforge", "file": rel}))]
        if replace.get(slug):
            fields.append(("replace", str(replace[slug])))
        try:
            status, got = post_image(base, desc, slug, image_path, fields)
        except TargetUnreachable as e:
            results.append(_fail(item, "%s is not reachable: %s" % (desc.get("label") or forge, e), unreachable=True))
            continue
        got = got if isinstance(got, dict) else {}
        if status == 409:
            results.append(_fail(item, "role full", status=409, count=got.get("count"), entries=got.get("entries") or []))
        elif status != 201:
            results.append(_fail(item, got.get("error") or "the target answered %s" % status, status=status))
        else:
            save_link(sidecar_path, {"forge": forge, "slug": slug, "name": str(item_name(body, slug)), "imageId": got.get("id"), "role": role,
                                     "tag": str(body.get("tag") or ""), "note": str(body.get("note") or ""), "verdict": verdict, "ts": time.time()})
            meta = read_sidecar(sidecar_path)
            results.append({**item, "ok": True, "imageId": got.get("id"), "file": got.get("file")})
    return results


def item_name(body, slug):
    """Display name for a link: the one the client sent for that slug, else the slug."""
    names = body.get("names") if isinstance(body.get("names"), dict) else {}
    return names.get(slug) or slug
