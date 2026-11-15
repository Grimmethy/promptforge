"""Assign a gallery image to link targets, and list what the targets offer.

Thin API over core/link_targets.py, which owns the wire contract, the sidecar
append and the idempotency. This module resolves paths, groups the requested
{slug, role} items by target, and reports one result per requested item, in
input order. It never writes the sidecar itself.
"""

import os

from core import link_targets

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))


def _gallery_dir():
    """Same resolution as server/app.py GALLERY_DIR, evaluated per call."""
    return os.path.abspath(os.path.expanduser(
        os.environ.get("PROMPTFORGE_GALLERY_DIR") or os.path.join(_REPO, "gallery")))


def _safe_path(rel):
    """Absolute path of gallery relpath `rel`, or None when it would sit outside the gallery."""
    root = _gallery_dir()
    p = os.path.abspath(os.path.join(root, rel))
    return p if p.startswith(root + os.sep) else None


def _sidecar_path(rel):
    """Sidecar path for a gallery relpath, same rule as server/app.py _sidecar_path_for."""
    d, base = os.path.split(rel)
    return os.path.join(_gallery_dir(), d, base.split("_")[0] + ".json")


def _error(item, target, error, **extra):
    return {"slug": str(item.get("slug") or ""), "role": str(item.get("role") or ""),
            "target": target, "ok": False, "error": error, **extra}


def assign_image(relpath, targets, tag, note, replace):
    """Copy one gallery image to each requested {slug, role} on its link target.

    relpath    gallery-relative path of the PNG
    targets    list of {"target": name, "slug", "role"}; "target" defaults to the first configured target
    tag, note strings sent with each copy
    replace    {slug: imageId} to replace an existing picture on that entry, or None

    Returns one result per requested item, in input order. Each is the delegate's
    per-item result ({slug, role, ok, ...}) plus "target". Targets are called one at a
    time; a target that cannot be reached fails only its own items.
    """
    items = [t if isinstance(t, dict) else {} for t in (targets or [])]
    configured = link_targets.targets()
    default = next(iter(configured), "")

    image_path = _safe_path(relpath or "")
    if not image_path or not os.path.isfile(image_path) or not image_path.endswith(".png"):
        return [_error(it, str(it.get("target") or default), "no such gallery image") for it in items]

    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(str(it.get("target") or default), []).append(i)

    shared = {"tag": str(tag or ""), "note": str(note or ""),
              "replace": replace if isinstance(replace, dict) else {}}
    results = [None] * len(items)
    for name, idxs in groups.items():
        base = configured.get(name)
        if not base:
            for i in idxs:
                results[i] = _error(items[i], name, "unknown link target %r" % name)
            continue
        body = dict(shared, targets=[{"slug": items[i].get("slug"), "role": items[i].get("role")} for i in idxs])
        try:
            got = link_targets.assign(base, relpath, image_path, _sidecar_path(relpath), body)
        except link_targets.TargetUnreachable as e:
            for i in idxs:
                results[i] = _error(items[i], name,
                                    "%s is not reachable (%s). The image was not touched." % (name, e),
                                    unreachable=True)
            continue
        except Exception as e:
            for i in idxs:
                results[i] = _error(items[i], name, "%s failed: %s" % (name, e or type(e).__name__))
            continue
        for k, i in enumerate(idxs):
            r = got[k] if k < len(got) and isinstance(got[k], dict) else {"ok": False, "error": "no result from %s" % name}
            results[i] = {**r, "target": name}
    return results


def list_link_targets():
    """Every configured link target with its entries. A target that fails (down, not a
    link target, bad answer) gets an error entry with entries: [] and never raises."""
    out = []
    for name, base in link_targets.targets().items():
        try:
            desc = link_targets.descriptor(base)
            rows = link_targets.entries(base, desc)
            out.append({"name": name, "url": base, "reachable": True, "error": None,
                        "label": desc.get("label") or name, "forge": desc.get("forge") or name,
                        "roles": desc.get("roles") or [], "verdicts": desc.get("verdicts") or [],
                        "entries": rows})
        except Exception as e:
            out.append({"name": name, "url": base, "reachable": False,
                        "error": "%s is not reachable (%s)." % (name, e or type(e).__name__),
                        "entries": []})
    return out
