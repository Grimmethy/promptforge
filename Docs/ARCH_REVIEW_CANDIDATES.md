# Architecture Review Candidates

### AC-1 · Theme resolution leaks "which keys to apply" to the caller
Strength: Strong
Files: themes.py, server/core/assemble.py

Problem:
`themes.py`'s `resolve` function returns a dictionary whose keys are split into two semantic groups: `style` and `setting_bias` are documented as "UI suggestions," while `palette` and `lighting` are the "always-applied scene contribution." However, `assemble.py`'s `assemble` function expects `spec.get("scene_tags")` to already be a fully-resolved string containing theme/setting/style/palette/lighting. This means an unseen caller must know the internal key taxonomy of `themes.py`, selectively extract `palette` and `lighting`, discard `style` and `setting_bias`, and concatenate the result into a single string before handing it to `assemble`. The decision of *what* constitutes the scene contribution is split across two modules, breaking locality: a change to which keys are "always-applied" requires editing both `themes.py` and the caller, with no type or interface contract to catch the mismatch.

Solution:
Add a small public method on the theme resolution result (or a dedicated helper in `themes.py`) such as `scene_contribution()` that returns the pre-joined string of the always-applied keys. `assemble.py` then calls that single accessor instead of expecting a pre-assembled `scene_tags` string from the caller. The caller's responsibility shrinks to "resolve the theme and pass the object through," and the knowledge of which keys are scene-level stays entirely inside `themes.py`.

Benefits:
The theme module becomes self-contained: adding, removing, or renaming an always-applied key requires touching only `themes.py`. The caller no longer needs to understand the internal key split, reducing the chance of silently dropping a new always-applied key or accidentally including a UI-suggestion key in the prompt. Testing `assemble` becomes straightforward because it no longer depends on the caller having performed the correct extraction.

### AC-2 · Duplicated custom/secondbrain branch in character_segment
Strength: Strong
Files: server/core/assemble.py

Problem:
In `assemble.py`, the `character_segment` function contains two branches—`src == "custom"` and `src == "secondbrain"`—that execute byte-for-byte identical logic: both call `data.normalize_kw(custom, outfit_kw, expr_kw)` and then `data.apply_weight(seg, weight)`. The only difference is the source label, which is not used in the processing at all. This duplication means any future change to how a text-based character source is normalized or weighted must be made in two places, and a regression in one branch will not be caught by the other.

Solution:
Collapse the two branches into a single condition: `if src in ("custom", "secondbrain"):`. The processing body is unchanged. If the two sources ever need to diverge, the split can be reintroduced at that point with a clear comment explaining the difference.

Benefits:
Eliminates a maintenance trap where a fix applied to one branch is forgotten in the other. Makes the assembler's intent explicit: "secondbrain" is architecturally a text source, identical to "custom," and the UI distinction carries no processing weight. Reduces the function's line count and cognitive load for the next reader.

### AC-3 · themes.py reaches into data's private loaders
Strength: Worth exploring
Files: themes.py, data

Problem:
`themes.py` imports the `data` module and, in addition to its public accessors, calls the private file-loading helpers `data._load_yaml` and `data._load_overlay` directly. By contrast, `randomizer.py` uses only the public API (`data.load_traits()`, `data.load_outfit_pools()`) and does not reach past the module boundary. The issue is isolated to `themes.py`: it depends on `data`'s internal implementation details for YAML/overlay parsing, which means unit-testing `themes.py` in isolation requires mocking `data`'s private loading machinery, and any refactor of `data`'s file-parsing internals can silently break `themes.py` without a type or interface contract to catch the mismatch.

Solution:
Add one or two public accessors in `data` (e.g., `data.load_theme_yaml(path)` and `data.load_overlay(path)`) that wrap the existing `_load_yaml` / `_load_overlay` calls and return already-parsed, typed structures. `themes.py` then calls those public accessors instead of the underscore-prefixed helpers. No new protocol or interface layer is needed; the change is a thin public wrapper around code that already exists inside `data`.

Benefits:
`themes.py` can be tested by injecting a stub for the public accessor, without mocking private methods or file I/O internals. A change to the YAML schema or overlay format is contained to `data`'s loader functions, and `themes.py` sees a stable public signature. The dependency graph gains one explicit public entry point where previously there was an implicit private one, making the contract visible to readers and linters.
