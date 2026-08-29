"""OpenPose keypoint frame: load / save / transform / render.

A frame is ``{"people": [{"pose_keypoints_2d": [x,y,c ...18 triples],
"hand_left_keypoints_2d": [...]|None, ...}], "canvas_width": W, "canvas_height": H}``.
Coords normalised 0..1; missing point is [0,0,0]. Transforms ported verbatim from
ComfyUI-PromptForge; ``render`` returns PNG bytes via the vendored dwpose drawer.
"""

import copy
import io
import json
import math

import numpy as np
from PIL import Image

from .vendor import dwpose_draw

_LR_PAIRS = [(2, 5), (3, 6), (4, 7), (8, 11), (9, 12), (10, 13), (14, 15), (16, 17)]
_ARRAYS = ("pose_keypoints_2d", "hand_left_keypoints_2d",
           "hand_right_keypoints_2d", "face_keypoints_2d")


# --------------------------------------------------------------------------- io

def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def first_frame(kps):
    if isinstance(kps, list):
        return kps[0] if kps else {}
    return kps


# ------------------------------------------------------------------- transforms

def _triples(arr):
    return [(arr[i], arr[i + 1], arr[i + 2]) for i in range(0, len(arr), 3)]


def _flat(triples):
    out = []
    for x, y, c in triples:
        out += [x, y, c]
    return out


def _map_points(arr, fn):
    if not arr:
        return arr
    out = []
    for x, y, c in _triples(arr):
        if c > 0:
            nx, ny = fn(x, y)
            out += [nx, ny, c]
        else:
            out += [x, y, c]
    return out


def _apply(frame, fn, only_person=None):
    frame = copy.deepcopy(frame)
    for pi, person in enumerate(frame.get("people", [])):
        if only_person is not None and only_person >= 0 and pi != only_person:
            continue
        for key in _ARRAYS:
            person[key] = _map_points(person.get(key), fn)
    return frame


def translate(frame, dx, dy, only_person=None):
    return _apply(frame, lambda x, y: (x + dx, y + dy), only_person)


def scale_about_center(frame, s, only_person=None):
    return _apply(frame, lambda x, y: ((x - 0.5) * s + 0.5, (y - 0.5) * s + 0.5), only_person)


def rotate(frame, deg, aspect, only_person=None):
    if not deg:
        return copy.deepcopy(frame)
    r = math.radians(deg)
    cos, sin = math.cos(r), math.sin(r)

    def fn(x, y):
        px, py = (x - 0.5) * aspect, (y - 0.5)
        rx = px * cos - py * sin
        ry = px * sin + py * cos
        return (0.5 + rx / aspect, 0.5 + ry)

    return _apply(frame, fn, only_person)


def mirror(frame):
    frame = _apply(frame, lambda x, y: (1.0 - x, y))
    for person in frame.get("people", []):
        body = person.get("pose_keypoints_2d")
        if body and len(body) >= 54:
            t = _triples(body)
            for a, b in _LR_PAIRS:
                t[a], t[b] = t[b], t[a]
            person["pose_keypoints_2d"] = _flat(t)
        person["hand_left_keypoints_2d"], person["hand_right_keypoints_2d"] = (
            person.get("hand_right_keypoints_2d"), person.get("hand_left_keypoints_2d"))
    return frame


def nudge_person(frame, index, dx, dy, s):
    frame = scale_about_center(frame, s, only_person=index) if s != 1.0 else copy.deepcopy(frame)
    if dx or dy:
        frame = translate(frame, dx, dy, only_person=index)
    return frame


def transform(frame, *, mirror_h=False, scale=1.0, offset_x=0.0, offset_y=0.0,
              dutch_deg=0.0, canvas_w=1152, canvas_h=896):
    """mirror -> scale -> move -> rotate; stamps canvas dims."""
    f = copy.deepcopy(first_frame(frame))
    f.setdefault("canvas_width", canvas_w)
    f.setdefault("canvas_height", canvas_h)
    if mirror_h:
        f = mirror(f)
    if scale != 1.0:
        f = scale_about_center(f, scale)
    if offset_x or offset_y:
        f = translate(f, offset_x, offset_y)
    if dutch_deg:
        f = rotate(f, dutch_deg, aspect=float(canvas_w) / float(canvas_h))
    f["canvas_width"], f["canvas_height"] = canvas_w, canvas_h
    return f


# ------------------------------------------------------------------------ render

def render_png(frame, canvas_w, canvas_h, draw_body=True, draw_hands=False,
               draw_face=False, xinsr_scaling=True):
    """Return PNG bytes of the skeleton."""
    f = dict(first_frame(frame))
    f["canvas_width"], f["canvas_height"] = int(canvas_w), int(canvas_h)
    human, _animal, H, W = dwpose_draw.decode_json_as_poses(f)
    canvas = dwpose_draw.draw_poses(
        human, H, W, draw_body=draw_body, draw_hand=draw_hands,
        draw_face=draw_face, xinsr_stick_scaling=xinsr_scaling)
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(canvas)).save(buf, format="PNG")
    return buf.getvalue()
