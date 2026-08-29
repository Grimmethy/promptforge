"""Vendored OpenPose skeleton drawing from Fannovel16/comfyui_controlnet_aux
(`src/custom_controlnet_aux/dwpose/`): the `decode_json_as_poses` + `draw_poses`
path plus the `util.draw_*pose` helpers and the `types` NamedTuples.

Trimmed to what's needed to turn an OpenPose keypoint frame (normalised 0..1
coords) into the exact skeleton PNG the xinsir OpenPose-SDXL ControlNet expects.
matplotlib's `hsv_to_rgb` is swapped for stdlib `colorsys`. numpy + opencv only.

Upstream is CMU-licensed for non-commercial use.
"""

import colorsys
import math
from typing import List, NamedTuple, Optional, Tuple, Union

import cv2
import numpy as np

eps = 0.01


class Keypoint(NamedTuple):
    x: float
    y: float
    score: float = 1.0
    id: int = -1


class BodyResult(NamedTuple):
    keypoints: List[Optional[Keypoint]]
    total_score: float = 0.0
    total_parts: int = 0


class PoseResult(NamedTuple):
    body: BodyResult
    left_hand: Optional[List[Keypoint]]
    right_hand: Optional[List[Keypoint]]
    face: Optional[List[Keypoint]]


def is_normalized(keypoints: List[Optional[Keypoint]]) -> bool:
    pn = [0 <= abs(k.x) <= 1 and 0 <= abs(k.y) <= 1 for k in keypoints if k is not None]
    return bool(pn) and all(pn)


def draw_bodypose(canvas: np.ndarray, keypoints: List[Optional[Keypoint]],
                  xinsr_stick_scaling: bool = False) -> np.ndarray:
    if not is_normalized(keypoints):
        H, W = 1.0, 1.0
    else:
        H, W, _ = canvas.shape

    CH, CW, _ = canvas.shape
    stickwidth = 4
    max_side = max(CW, CH)
    stick_scale = (1 if max_side < 500 else min(2 + (max_side // 1000), 7)) if xinsr_stick_scaling else 1

    limbSeq = [
        [2, 3], [2, 6], [3, 4], [4, 5],
        [6, 7], [7, 8], [2, 9], [9, 10],
        [10, 11], [2, 12], [12, 13], [13, 14],
        [2, 1], [1, 15], [15, 17], [1, 16], [16, 18],
    ]
    colors = [[255, 0, 0], [255, 85, 0], [255, 170, 0], [255, 255, 0], [170, 255, 0],
              [85, 255, 0], [0, 255, 0], [0, 255, 85], [0, 255, 170], [0, 255, 255],
              [0, 170, 255], [0, 85, 255], [0, 0, 255], [85, 0, 255], [170, 0, 255],
              [255, 0, 255], [255, 0, 170], [255, 0, 85]]

    for (k1_index, k2_index), color in zip(limbSeq, colors):
        kp1 = keypoints[k1_index - 1]
        kp2 = keypoints[k2_index - 1]
        if kp1 is None or kp2 is None:
            continue
        Y = np.array([kp1.x, kp2.x]) * float(W)
        X = np.array([kp1.y, kp2.y]) * float(H)
        mX, mY = np.mean(X), np.mean(Y)
        length = ((X[0] - X[1]) ** 2 + (Y[0] - Y[1]) ** 2) ** 0.5
        angle = math.degrees(math.atan2(X[0] - X[1], Y[0] - Y[1]))
        polygon = cv2.ellipse2Poly((int(mY), int(mX)),
                                   (int(length / 2), stickwidth * stick_scale),
                                   int(angle), 0, 360, 1)
        cv2.fillConvexPoly(canvas, polygon, [int(float(c) * 0.6) for c in color])

    for keypoint, color in zip(keypoints, colors):
        if keypoint is None:
            continue
        x = int(keypoint.x * W)
        y = int(keypoint.y * H)
        cv2.circle(canvas, (x, y), 4, color, thickness=-1)
    return canvas


def draw_handpose(canvas: np.ndarray, keypoints: Union[List[Keypoint], None]) -> np.ndarray:
    if not keypoints:
        return canvas
    if not is_normalized(keypoints):
        H, W = 1.0, 1.0
    else:
        H, W, _ = canvas.shape
    edges = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [0, 9],
             [9, 10], [10, 11], [11, 12], [0, 13], [13, 14], [14, 15], [15, 16],
             [0, 17], [17, 18], [18, 19], [19, 20]]
    for ie, (e1, e2) in enumerate(edges):
        k1, k2 = keypoints[e1], keypoints[e2]
        if k1 is None or k2 is None:
            continue
        x1, y1 = int(k1.x * W), int(k1.y * H)
        x2, y2 = int(k2.x * W), int(k2.y * H)
        if x1 > eps and y1 > eps and x2 > eps and y2 > eps:
            rgb = colorsys.hsv_to_rgb(ie / float(len(edges)), 1.0, 1.0)
            cv2.line(canvas, (x1, y1), (x2, y2), [c * 255 for c in rgb], thickness=2)
    for keypoint in keypoints:
        if keypoint is None:
            continue
        x, y = int(keypoint.x * W), int(keypoint.y * H)
        if x > eps and y > eps:
            cv2.circle(canvas, (x, y), 4, (0, 0, 255), thickness=-1)
    return canvas


def draw_facepose(canvas: np.ndarray, keypoints: Union[List[Keypoint], None]) -> np.ndarray:
    if not keypoints:
        return canvas
    if not is_normalized(keypoints):
        H, W = 1.0, 1.0
    else:
        H, W, _ = canvas.shape
    for keypoint in keypoints:
        if keypoint is None:
            continue
        x, y = int(keypoint.x * W), int(keypoint.y * H)
        if x > eps and y > eps:
            cv2.circle(canvas, (x, y), 3, (255, 255, 255), thickness=-1)
    return canvas


def draw_poses(poses: List[PoseResult], H, W, draw_body=True, draw_hand=True,
               draw_face=True, xinsr_stick_scaling=False) -> np.ndarray:
    canvas = np.zeros(shape=(H, W, 3), dtype=np.uint8)
    for pose in poses:
        if draw_body:
            canvas = draw_bodypose(canvas, pose.body.keypoints, xinsr_stick_scaling)
        if draw_hand:
            canvas = draw_handpose(canvas, pose.left_hand)
            canvas = draw_handpose(canvas, pose.right_hand)
        if draw_face:
            canvas = draw_facepose(canvas, pose.face)
    return canvas


def decode_json_as_poses(pose_json: dict) -> Tuple[List[PoseResult], list, int, int]:
    height = pose_json["canvas_height"]
    width = pose_json["canvas_width"]

    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    def decompress(numbers):
        if not numbers:
            return None
        assert len(numbers) % 3 == 0
        return [Keypoint(x, y) if c >= 1.0 else None for x, y, c in chunks(numbers, 3)]

    human = [
        PoseResult(
            body=BodyResult(keypoints=decompress(p.get("pose_keypoints_2d"))),
            left_hand=decompress(p.get("hand_left_keypoints_2d")),
            right_hand=decompress(p.get("hand_right_keypoints_2d")),
            face=decompress(p.get("face_keypoints_2d")),
        )
        for p in pose_json.get("people", [])
    ]
    return human, [], height, width
