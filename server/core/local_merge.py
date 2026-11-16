"""Seed-plus-local merge helper for JSON row files.

Reads a checked-in seed JSON array and an optional local JSON array and
returns their union. A local row replaces the seed row with the same key, in
place. Local rows with new keys are appended in file order. This module never
writes files.
"""

import json
import os


def _load_rows(path):
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise TypeError(
            f"{path}: expected a JSON array of rows, got {type(rows).__name__}"
        )
    return rows


def _key_of(row, key, source, index):
    if not isinstance(row, dict) or key not in row:
        raise KeyError(f"{source}: row {index} has no key {key!r}")
    return row[key]


def load_merged(seed_path, local_path, key):
    """Return seed rows merged with local rows keyed on ``key``.

    A missing local file returns the seed rows unchanged. A missing seed file
    raises. Corrupt files raise rather than being silently ignored.
    """
    seed = _load_rows(seed_path)
    if not os.path.exists(local_path):
        return list(seed)
    local = _load_rows(local_path)

    merged = list(seed)
    position = {}
    for i, row in enumerate(seed):
        position[_key_of(row, key, seed_path, i)] = i
    for i, row in enumerate(local):
        k = _key_of(row, key, local_path, i)
        if k in position:
            merged[position[k]] = row
        else:
            position[k] = len(merged)
            merged.append(row)
    return merged
