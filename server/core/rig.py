"""A small named 3-D stick rig for the pose editor.

A rig is::

    {"joints": {"waist": [x, y, z], "chest": [...], "head": [...],
                "shoulder.L": [...], "elbow.L": [...], "hand.L": [...],
                "shoulder.R": ..., ...,
                "hip.L": ..., "knee.L": ..., "foot.L": ..., "hip.R": ...},
     "lengths": {"torso": .., "neck": .., "clavicle": .., "upperArm": ..,
                 "forearm": .., "hipWidth": .., "thigh": .., "shin": ..},
     "root": "waist"}

Coordinates are canvas fractions with the rig roughly centred on the origin
(so screen x = 0.5 + x). y increases downward (image convention). z is toward
the camera at azimuth 0. Bone lengths are shared L/R ("each body part").

The editor works in this space; ControlNet still wants COCO-18, so ``project``
renders a rig through an orbit camera into a COCO-18 person. The same math runs
client-side in web/editor.js — keep them in sync.
"""

import math

JOINTS = [
    "waist", "chest", "head",
    "shoulder.L", "elbow.L", "hand.L",
    "shoulder.R", "elbow.R", "hand.R",
    "hip.L", "knee.L", "foot.L",
    "hip.R", "knee.R", "foot.R",
]

# (parent, child, length-key). Walked from the root outward.
BONES = [
    ("waist", "chest", "torso"),
    ("chest", "head", "neck"),
    ("chest", "shoulder.L", "clavicle"), ("shoulder.L", "elbow.L", "upperArm"),
    ("elbow.L", "hand.L", "forearm"),
    ("chest", "shoulder.R", "clavicle"), ("shoulder.R", "elbow.R", "upperArm"),
    ("elbow.R", "hand.R", "forearm"),
    ("waist", "hip.L", "hipWidth"), ("hip.L", "knee.L", "thigh"),
    ("knee.L", "foot.L", "shin"),
    ("waist", "hip.R", "hipWidth"), ("hip.R", "knee.R", "thigh"),
    ("knee.R", "foot.R", "shin"),
]

LENGTH_KEYS = ["torso", "neck", "clavicle", "upperArm", "forearm",
               "hipWidth", "thigh", "shin"]

DEFAULT_LENGTHS = {"torso": 0.20, "neck": 0.16, "clavicle": 0.09, "upperArm": 0.15,
                   "forearm": 0.14, "hipWidth": 0.07, "thigh": 0.22, "shin": 0.22}

# Canonical forward depth (+z = toward camera) applied to a neutral rig and used
# to nudge a fully-planar loaded rig so the orbit camera can actually turn it
# instead of collapsing the limbs onto the spine. Keep mirrored with
# web/editor.js DEPTH_PRIOR / defaultRig.
DEPTH_PRIOR = {
    "waist": 0.0, "chest": 0.03, "head": 0.01,
    "shoulder.L": -0.01, "elbow.L": 0.05, "hand.L": 0.08,
    "shoulder.R": -0.01, "elbow.R": 0.05, "hand.R": 0.08,
    "hip.L": -0.01, "knee.L": 0.04, "foot.L": 0.0,
    "hip.R": -0.01, "knee.R": 0.04, "foot.R": 0.0,
}
_FLAT_Z_EPS = 0.005

# plausible range per body part (canvas fractions) — clamps rough lifts + edits
LENGTH_RANGE = {"torso": (0.13, 0.30), "neck": (0.09, 0.22), "clavicle": (0.05, 0.14),
                "upperArm": (0.09, 0.21), "forearm": (0.09, 0.21), "hipWidth": (0.04, 0.12),
                "thigh": (0.13, 0.30), "shin": (0.13, 0.30)}


def clamp_lengths(L):
    out = dict(DEFAULT_LENGTHS)
    for k, (lo, hi) in LENGTH_RANGE.items():
        v = L.get(k, DEFAULT_LENGTHS[k])
        out[k] = min(hi, max(lo, float(v)))
    return out

# COCO-18 index -> rig joint (14..17 eyes/ears are left empty)
COCO_FROM_RIG = {
    0: "head", 1: "chest",
    2: "shoulder.R", 3: "elbow.R", 4: "hand.R",
    5: "shoulder.L", 6: "elbow.L", 7: "hand.L",
    8: "hip.R", 9: "knee.R", 10: "foot.R",
    11: "hip.L", 12: "knee.L", 13: "foot.L",
}


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _norm(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def default_rig(cx=0.0):
    L = dict(DEFAULT_LENGTHS)
    Z = DEPTH_PRIOR
    j = {
        "waist": [cx, 0.10, Z["waist"]],
        "chest": [cx, 0.10 - L["torso"], Z["chest"]],
        "head": [cx, 0.10 - L["torso"] - L["neck"], Z["head"]],
        "shoulder.L": [cx + L["clavicle"], 0.10 - L["torso"] + 0.01, Z["shoulder.L"]],
        "elbow.L": [cx + L["clavicle"], 0.10 - L["torso"] + L["upperArm"], Z["elbow.L"]],
        "hand.L": [cx + L["clavicle"], 0.10 - L["torso"] + L["upperArm"] + L["forearm"], Z["hand.L"]],
        "shoulder.R": [cx - L["clavicle"], 0.10 - L["torso"] + 0.01, Z["shoulder.R"]],
        "elbow.R": [cx - L["clavicle"], 0.10 - L["torso"] + L["upperArm"], Z["elbow.R"]],
        "hand.R": [cx - L["clavicle"], 0.10 - L["torso"] + L["upperArm"] + L["forearm"], Z["hand.R"]],
        "hip.L": [cx + L["hipWidth"], 0.10, Z["hip.L"]],
        "knee.L": [cx + L["hipWidth"], 0.10 + L["thigh"], Z["knee.L"]],
        "foot.L": [cx + L["hipWidth"], 0.10 + L["thigh"] + L["shin"], Z["foot.L"]],
        "hip.R": [cx - L["hipWidth"], 0.10, Z["hip.R"]],
        "knee.R": [cx - L["hipWidth"], 0.10 + L["thigh"], Z["knee.R"]],
        "foot.R": [cx - L["hipWidth"], 0.10 + L["thigh"] + L["shin"], Z["foot.R"]],
    }
    return {"joints": j, "lengths": L, "root": "waist"}


def _nudge_if_flat(j):
    """A rig with ~no depth can't be turned by the orbit camera without the
    limbs collapsing onto the spine. Give a genuinely planar rig the canonical
    depth prior. Idempotent: a rig that already has depth is returned unchanged."""
    zs = [p[2] for p in j.values()]
    if zs and (max(zs) - min(zs)) < _FLAT_Z_EPS:
        for name, p in j.items():
            p[2] = p[2] + DEPTH_PRIOR.get(name, 0.0)
    return j


def normalize(rig):
    """Coerce a loaded rig into the canonical shape (fill gaps from defaults)."""
    if not isinstance(rig, dict):
        return default_rig()
    base = default_rig()
    j = dict(base["joints"])
    for name, p in (rig.get("joints") or {}).items():
        if name in j and isinstance(p, (list, tuple)) and len(p) >= 2:
            j[name] = [float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0]
    L = dict(base["lengths"])
    for k, v in (rig.get("lengths") or {}).items():
        if k in L:
            L[k] = float(v)
    _nudge_if_flat(j)
    return {"joints": j, "lengths": clamp_lengths(L), "root": rig.get("root", "waist")}


def constrain(rig):
    """Snap every child joint to its rest length from its parent, walking from
    the root outward and preserving each bone's current direction."""
    j = rig["joints"]
    L = rig["lengths"]
    for parent, child, key in BONES:
        p, c = j.get(parent), j.get(child)
        if not p or not c:
            continue
        d = _sub(c, p)
        n = _norm(d) or 1e-6
        rest = L.get(key, 0.1)
        j[child] = [p[0] + d[0] / n * rest, p[1] + d[1] / n * rest, p[2] + d[2] / n * rest]
    return rig


def lengths_from_joints(j):
    def d(a, b, fb):
        if a in j and b in j:
            return _norm(_sub(j[a], j[b]))
        return fb

    def avg(pairs, fb):
        vals = [d(a, b, None) for a, b in pairs]
        vals = [v for v in vals if v]
        return sum(vals) / len(vals) if vals else fb

    D = DEFAULT_LENGTHS
    return clamp_lengths({
        "torso": d("waist", "chest", D["torso"]),
        "neck": d("chest", "head", D["neck"]),
        "clavicle": avg([("chest", "shoulder.L"), ("chest", "shoulder.R")], D["clavicle"]),
        "upperArm": avg([("shoulder.L", "elbow.L"), ("shoulder.R", "elbow.R")], D["upperArm"]),
        "forearm": avg([("elbow.L", "hand.L"), ("elbow.R", "hand.R")], D["forearm"]),
        "hipWidth": avg([("waist", "hip.L"), ("waist", "hip.R")], D["hipWidth"]),
        "thigh": avg([("hip.L", "knee.L"), ("hip.R", "knee.R")], D["thigh"]),
        "shin": avg([("knee.L", "foot.L"), ("knee.R", "foot.R")], D["shin"]),
    })


# ------------------------------------------------------------------ projection

def _roty(p, a):
    c, s = math.cos(a), math.sin(a)
    return [p[0] * c + p[2] * s, p[1], -p[0] * s + p[2] * c]


def _rotx(p, a):
    c, s = math.cos(a), math.sin(a)
    return [p[0], p[1] * c - p[2] * s, p[1] * s + p[2] * c]


# orbit camera distance for the perspective divide — smaller = stronger
# foreshortening, which is what distinguishes front from back and tells
# ControlNet which end of an axial pose is nearest.
CAM_DIST = 1.35


def project_person(rig, azimuth=0.0, elevation=0.0):
    """rig -> a COCO-18 person dict (perspective orbit camera). Drops the nose
    when the camera is behind the head; blanks the skeleton only if the
    projection genuinely collapses to a point/line."""
    j = rig["joints"]
    rot = {name: _rotx(_roty(p, -azimuth), -elevation) for name, p in j.items()}
    scr = {}
    for name, q in rot.items():
        f = CAM_DIST / max(0.35, CAM_DIST - q[2])
        scr[name] = (min(1.0, max(0.0, 0.5 + q[0] * f)),
                     min(1.0, max(0.0, 0.5 + q[1] * f)))
    xs = [s[0] for s in scr.values()]
    ys = [s[1] for s in scr.values()]
    # Rigs carry real depth now, so a side/back view is a legitimate projection —
    # only blank when everything has collapsed onto a point or a line.
    degenerate = (max(xs) - min(xs) < 0.03) or (max(ys) - min(ys) < 0.03)
    hide_nose = ("head" in rot and "chest" in rot
                 and rot["head"][2] < rot["chest"][2] - 0.05)
    flat = []
    for i in range(18):
        name = COCO_FROM_RIG.get(i)
        if name and name in scr and not degenerate and not (i == 0 and hide_nose):
            flat += [scr[name][0], scr[name][1], 1.0]
        else:
            flat += [0.0, 0.0, 0.0]
    return {"pose_keypoints_2d": flat, "face_keypoints_2d": None,
            "hand_left_keypoints_2d": None, "hand_right_keypoints_2d": None}


def project_frame(rig_or_rigs, azimuth=0.0, elevation=0.0, canvas_w=1152, canvas_h=896):
    rigs = rig_or_rigs if isinstance(rig_or_rigs, list) else [rig_or_rigs]
    people = [project_person(normalize(r), azimuth, elevation) for r in rigs]
    return {"people": people, "canvas_width": canvas_w, "canvas_height": canvas_h}


# ------------------------------------------------------ lift legacy COCO-18

def coco18_to_rig(flat):
    """Best-effort lift of a flat COCO-18 array into a rig. If the figure is
    near-flat in x (a profile capture) the x spread is read as depth so the
    result is a real 3-D side-facing rig."""
    tri = [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, min(len(flat), 54), 3)]
    present = {COCO_FROM_RIG[i]: (tri[i][0], tri[i][1])
               for i in COCO_FROM_RIG if i < len(tri) and tri[i][2] > 0}
    xs = [p[0] for p in present.values()]
    profile = bool(xs) and (max(xs) - min(xs) < 0.12)
    xmean = sum(xs) / len(xs) if xs else 0.5

    j = {}
    for name, (x, y) in present.items():
        if profile:
            j[name] = [0.0, y - 0.5, (x - xmean) * 3.0]
        else:
            j[name] = [x - 0.5, y - 0.5, 0.0]

    if "hip.L" in j and "hip.R" in j:
        j["waist"] = [(j["hip.L"][k] + j["hip.R"][k]) / 2 for k in range(3)]
    elif "chest" in j:
        j["waist"] = [j["chest"][0], j["chest"][1] + DEFAULT_LENGTHS["torso"], j["chest"][2]]
    else:
        j["waist"] = [0.0, 0.1, 0.0]

    base = default_rig()["joints"]
    for name in JOINTS:
        j.setdefault(name, list(base[name]))

    rig = {"joints": j, "lengths": lengths_from_joints(j), "root": "waist"}
    return constrain(rig)


def to_rigs(doc):
    """Accept a stored pose doc in any shape -> list of rigs."""
    if isinstance(doc, dict) and doc.get("rigs"):
        return [normalize(r) for r in doc["rigs"]]
    if isinstance(doc, dict) and doc.get("rig"):
        return [normalize(doc["rig"])]
    # legacy: a bare COCO-18 frame / person
    people = doc.get("people") if isinstance(doc, dict) else None
    if people:
        return [coco18_to_rig(p.get("pose_keypoints_2d") or []) for p in people]
    if isinstance(doc, dict) and doc.get("pose_keypoints_2d"):
        return [coco18_to_rig(doc["pose_keypoints_2d"])]
    return [default_rig()]
