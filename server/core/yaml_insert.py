"""Comment-preserving, block-scoped add / update / delete of a single key in a YAML file.

`ruamel.yaml` is not a dependency and `yaml.safe_dump`-ing a whole file would drop
every comment and reflow every block scalar. So this does line-level text surgery:
every existing byte is kept, and only the target key's line(s) are spliced / replaced
/ removed at the right indent inside the right parent block. Rendered values come from
`yaml.safe_dump` on a one-key dict, so quoting/escaping is always correct.

After every edit the whole rebuilt text is re-parsed and the key is checked to resolve
(or, for a delete, to be gone) at the expected path; anything unexpected aborts the write.
"""

import os

import yaml

_INDENT = 2


def _indent_of(line):
    return len(line) - len(line.lstrip(" "))


def _is_blank(line):
    return line.strip() == ""


def _is_comment(line):
    return line.lstrip(" ").startswith("#")


def _key_of(line):
    """Return the mapping key a line defines, or None. Cheap heuristic: text before
    the first colon, when the line isn't blank/comment/indented scalar continuation."""
    if _is_blank(line) or _is_comment(line):
        return None
    stripped = line.strip()
    if ":" not in stripped:
        return None
    if stripped.startswith(("-", "?", "&", "*", "|", ">")):
        return None
    head = stripped.split(":", 1)[0].strip()
    # unquote a simple quoted key
    if len(head) >= 2 and head[0] in "\"'" and head[-1] == head[0]:
        head = head[1:-1]
    return head or None


def _block_end(lines, start, parent_indent):
    """First index >= start that dedents out of the parent's block (non-blank line
    at indent <= parent_indent). Blank lines never end a block."""
    for i in range(start, len(lines)):
        if _is_blank(lines[i]):
            continue
        if _indent_of(lines[i]) <= parent_indent:
            return i
    return len(lines)


def _render(new_key, value, remaining, indent):
    """YAML text for `new_key` (optionally wrapped in `remaining` nesting levels),
    indented to `indent` spaces. `value` is a str or a dict."""
    node = {new_key: value}
    for seg in reversed(remaining):
        node = {seg: node}
    dumped = yaml.safe_dump(node, sort_keys=False, allow_unicode=True,
                            width=10 ** 9, default_flow_style=False).rstrip("\n")
    pad = " " * indent
    return [pad + ln if ln else ln for ln in dumped.split("\n")]


def _resolve(doc, path):
    node = doc
    for seg in path:
        if not isinstance(node, dict) or seg not in node:
            return False, None
        node = node[seg]
    return True, node


def _read_lines(file_path):
    with open(file_path, "r", encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # drop the artefact of a trailing newline; re-added on write
    return lines


def _commit(file_path, lines, expect_path, want_val, *, deleted=False):
    """Re-parse the rebuilt file, verify the key landed (or is gone), atomic-write."""
    out = "\n".join(lines).rstrip("\n") + "\n"
    try:
        doc = yaml.safe_load(out)
    except yaml.YAMLError as exc:
        raise ValueError(f"edit would break {os.path.basename(file_path)}: {exc}")
    ok, got = _resolve(doc, expect_path)
    if deleted:
        if ok:
            raise ValueError("delete did not take; aborted")
    else:
        if not ok:
            raise ValueError("edit did not land where expected; aborted")
        if isinstance(want_val, dict):
            if got != want_val and not (want_val == {} and got is None):
                raise ValueError("edit did not land where expected; aborted")
        elif str(got or "") != str(want_val or ""):
            raise ValueError("edit did not land where expected; aborted")
    tmp = file_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(out)
    os.replace(tmp, file_path)
    return file_path


def _walk_parent(lines, parent_path):
    """Descend `parent_path` as far as it exists. Returns
    (parent_indent, scan_start, scan_end, matched) — the deepest existing parent's
    indent and the [scan_start, scan_end) line window of its child block."""
    parent_indent = -_INDENT           # the virtual root; top-level keys sit at 0
    scan_start, scan_end = 0, len(lines)
    matched = 0
    for seg in parent_path:
        want = parent_indent + _INDENT
        found = None
        for i in range(scan_start, scan_end):
            if _is_blank(lines[i]) or _is_comment(lines[i]):
                continue
            if _indent_of(lines[i]) == want and _key_of(lines[i]) == seg:
                found = i
                break
        if found is None:
            break
        parent_indent = want
        scan_start = found + 1
        scan_end = _block_end(lines, scan_start, parent_indent)
        matched += 1
    return parent_indent, scan_start, scan_end, matched


def _span_of_key(lines, scan_start, scan_end, indent, key):
    """Line range [i, j) of `key` at `indent` plus everything indented under it
    (folded scalars, {pos,neg} children), trailing blank lines trimmed. None if absent."""
    for i in range(scan_start, scan_end):
        if _is_blank(lines[i]) or _is_comment(lines[i]):
            continue
        if _indent_of(lines[i]) == indent and _key_of(lines[i]) == key:
            j = i + 1
            while j < scan_end:
                if _is_blank(lines[j]):
                    j += 1
                    continue
                if _indent_of(lines[j]) <= indent:
                    break
                j += 1
            while j - 1 > i and _is_blank(lines[j - 1]):
                j -= 1
            return i, j
    return None


def _validate_names(new_key, parent_path):
    new_key = str(new_key).strip()
    if not new_key or "\n" in new_key or new_key.startswith("#"):
        raise ValueError("invalid entry name")
    if any(not str(p).strip() for p in parent_path):
        raise ValueError("invalid parent path")
    return new_key


def insert_entry(file_path, parent_path, new_key, value):
    """Splice `new_key: value` into `file_path` under `parent_path` (a list of
    ancestor mapping keys, e.g. ["entries"] or ["female", "cute", "lolita"]).

    Missing tail segments of `parent_path` are created as nested maps. `value` is a
    plain string (leaf) or a dict (theme block / empty branch `{}`). Returns the
    file path. Raises ValueError on a duplicate key or if the result won't parse.
    """
    new_key = _validate_names(new_key, parent_path)
    lines = _read_lines(file_path)
    parent_indent, scan_start, scan_end, matched = _walk_parent(lines, parent_path)

    remaining = list(parent_path[matched:])
    child_indent = parent_indent + _INDENT
    dup_name = remaining[0] if remaining else new_key

    for i in range(scan_start, scan_end):
        if _is_blank(lines[i]) or _is_comment(lines[i]):
            continue
        if _indent_of(lines[i]) == child_indent and _key_of(lines[i]) == dup_name:
            raise ValueError(f"'{dup_name}' already exists")

    idx = scan_end
    while idx - 1 >= scan_start and _is_blank(lines[idx - 1]):
        idx -= 1

    new_lines = _render(new_key, value, remaining, child_indent)
    rebuilt = lines[:idx] + new_lines + lines[idx:]
    want_val = value if isinstance(value, dict) else str(value or "")
    return _commit(file_path, rebuilt, list(parent_path) + [new_key], want_val)


def update_entry(file_path, parent_path, key, value):
    """Replace an existing `key`'s value in place under `parent_path`, keeping every
    other byte (comments included). `value` is a str or a dict. Raises if `parent_path`
    or `key` is not found, or the result won't parse."""
    key = _validate_names(key, parent_path)
    lines = _read_lines(file_path)
    parent_indent, scan_start, scan_end, matched = _walk_parent(lines, parent_path)
    if matched != len(parent_path):
        raise ValueError("parent path not found")
    child_indent = parent_indent + _INDENT
    span = _span_of_key(lines, scan_start, scan_end, child_indent, key)
    if span is None:
        raise ValueError(f"'{key}' not found")
    i, j = span
    new_lines = _render(key, value, [], child_indent)
    rebuilt = lines[:i] + new_lines + lines[j:]
    want_val = value if isinstance(value, dict) else str(value or "")
    return _commit(file_path, rebuilt, list(parent_path) + [key], want_val)


def delete_entry(file_path, parent_path, key):
    """Remove `key` (and everything indented under it) from `file_path` under
    `parent_path`, keeping every other byte. Raises if not found or the result won't parse."""
    key = _validate_names(key, parent_path)
    lines = _read_lines(file_path)
    parent_indent, scan_start, scan_end, matched = _walk_parent(lines, parent_path)
    if matched != len(parent_path):
        raise ValueError("parent path not found")
    child_indent = parent_indent + _INDENT
    span = _span_of_key(lines, scan_start, scan_end, child_indent, key)
    if span is None:
        raise ValueError(f"'{key}' not found")
    i, j = span
    rebuilt = lines[:i] + lines[j:]
    # collapse a blank line left doubled by the removal
    if 0 < i < len(rebuilt) and _is_blank(rebuilt[i - 1]) and _is_blank(rebuilt[i]):
        del rebuilt[i]
    return _commit(file_path, rebuilt, list(parent_path) + [key], None, deleted=True)
