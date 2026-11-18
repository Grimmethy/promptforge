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

### AC-4 · Add scene_contribution accessor to themes.py
Strength: Strong
Split-Depth: 1
Files: themes.py

Problem:
The set of 'always-applied' theme keys (palette, lighting) is currently known only to callers, who must manually extract those specific keys from the result of themes.resolve and join them into a string. Adding a new always-applied key in the future requires finding and updating every call site that performs this extraction, because the canonical list is not encoded anywhere in the theme module itself.

Solution:
Add a public function `scene_contribution(resolved) -> str` (or a method on the resolved object if `resolve` returns a class/dataclass rather than a plain dict) that: (1) reads the canonical always-applied keys from the resolved theme, (2) filters out any that are None or empty, (3) returns the remaining values joined with a single space. Add a docstring stating this function is the single source of truth for which keys are always-applied and that adding a new always-applied key requires updating only this function. The function must return an empty string when the input is None or absent. Do not alter the return type or structure of `resolve` itself; this accessor is purely additive.

Benefits:
The 'which keys to apply' knowledge is encapsulated in one function. Adding a new always-applied key becomes a one-line change inside `scene_contribution`; no caller code changes are needed. Callers can pass the raw resolved theme object without knowing its internal key layout.

### AC-5 · Change assemble to obtain scene text via themes.scene_contribution
Strength: Strong
Split-Depth: 1
Files: server/core/assemble.py
Depends-On: AC-4

Problem:
The `assemble` function currently reads `spec.get("scene_tags")` and expects the caller to have already extracted the always-applied theme keys and joined them into a string. This forces every caller to know which keys are always-applied versus UI-suggestion-only, duplicating knowledge that belongs in the theme module. The docstring even documents the contract as 'scene_tags (already-resolved theme/setting/style/palette/lighting)', baking the join responsibility into the caller.

Solution:
Import the `scene_contribution` accessor from the themes module (candidate 0). In `assemble`, replace the direct read of `spec.get("scene_tags")` with a call to `themes.scene_contribution(spec.get("theme"))` (or the key name the callers actually use to pass the resolved theme — confirm by reading call sites). The accessor returns an empty string for None/absent input, preserving the existing null-handling where the scene chunk is simply omitted. Update the `assemble` docstring: replace 'scene_tags (already-resolved theme/setting/style/palette/lighting)' with 'theme (raw resolved theme object; assemble extracts the always-applied contribution internally)'. All other spec keys (pose_tags, position_tags, characters, quality, etc.) and the prompt-assembly logic remain untouched. Update any existing unit tests in the assemble test module that construct a `scene_tags` string by hand so they pass the raw resolved theme dict instead and assert the same final positive/negative output.

Benefits:
Callers pass the raw resolved theme object without knowing which keys are always-applied. The join logic and the canonical key list live in exactly one place (themes.scene_contribution). A future addition of a new always-applied key requires zero changes to assemble.py or its callers.

### AC-6 · Duplicated processing branch for custom and secondbrain sources in character_segment
Strength: Strong
Files: server/core/assemble.py

Problem:
In `character_segment`, the `"custom"` and `"secondbrain"` source branches execute an identical sequence: both call `data.normalize_kw` with the same keyword arguments and then `data.apply_weight` with the same weight, returning the result alongside an empty-string negative. The two branches differ only in the literal source string they match, yet the body is copy-pasted. Any future change to how these sources are normalized, weighted, or returned (for example, adding a second normalization pass or a conditional weight modifier) must be replicated in both places, creating a divergence risk that a single code path would eliminate.

Solution:
Replace the two separate `if src == "custom":` / `elif src == "secondbrain":` blocks with a single `if src in ("custom", "secondbrain"):` guard that executes the shared `normalize_kw` → `apply_weight` → return sequence exactly once. The surrounding logic (other source branches, the fallback) remains unchanged.

Benefits:
Removes the redundant branch, shrinks the function, and guarantees that custom and secondbrain sources stay in lockstep for any future modification. Reviewers and future contributors see one code path instead of two that must be kept in sync, reducing the chance of a silent behavioral split between the two source types.

### AC-7 · Public accessor for theme loading in `data`
Strength: Strong
Files: themes.py, server/core/data.py

Problem:
`themes.py` calls `data._load_yaml` and `data._load_overlay` directly inside `_all()`. These helpers are private — the leading underscore signals internal use — and every other module in this community (`assemble.py`, `randomizer.py`, `secondbrain.py`) uses `data`'s public surface (`data.get_entry`, `data.load_traits`, `data.DATA_DIR`, etc.). If `data` renames, reorganizes, or changes the signature of those loader helpers, `themes.py` breaks with no public contract to fall back on. The coupling is implicit and invisible to readers.

Solution:
Add a public accessor in `data` (e.g. `data.load_themes()`) that wraps the existing loader helpers, and have `themes._all()` delegate to it. This makes the contract visible and lets `data` evolve its internals without breaking `themes`.

Benefits:
The dependency between `themes` and `data` becomes an explicit, documented contract. `data` can refactor its internal loaders without ripple failures, and readers can discover the theme-loading path through the public API rather than by tracing private helpers.

### AC-8 · Schema and validation for `assemble()` spec
Strength: Strong
Files: server/core/assemble.py

Problem:
`assemble()` accepts a dict with a long list of recognized keys (`characters[]`, `scene_tags`, `pose_tags`, `position_tags`, `quality`, `quality_text`, `manual_prefix`, `manual_suffix`, `negative`, `group_scene`, `char_segments`) documented only in the docstring. Unknown keys are silently dropped with no validation, no schema object, and no error path for malformed input. A caller who misspells `scene_tags` as `scen_tags` gets a silently empty scene — no warning, no error, just a wrong prompt. Every new caller has to read the docstring and the code to learn the schema; there is no single source of truth.

Solution:
Define a small schema or TypedDict for the spec, validate at the entry point, and raise on unknown keys or missing required fields. This turns the implicit contract into an explicit one.

Benefits:
Callers get immediate, clear feedback on malformed input instead of silent wrong output. The schema becomes the single source of truth for the spec shape, reducing onboarding friction and preventing regressions when the spec evolves.

### AC-9 · Collapse `resolve` and `get_theme` in `themes`
Strength: Strong
Files: themes.py

Problem:
`resolve(name)` does exactly `get_theme(name or "none")` — it adds no logic, no validation, no documentation beyond what `get_theme` already has, and no behavioral difference other than the default. Both functions return the same `{style, setting_bias, palette, lighting, negative_add}` dict. Callers have to learn two names for the same operation, and the docstring of `resolve` restates `get_theme`'s behavior without adding information. If the default should ever change (e.g. to `"none"` vs `None`), the duplication makes it easy to drift.

Solution:
Collapse to a single public function — either keep `get_theme` with the default baked in, or keep `resolve` and deprecate `get_theme`. Pick one name and remove the other.

Benefits:
Callers learn one name for one operation. The default behavior is documented in one place, eliminating the risk of the two functions drifting apart. Code review and maintenance become simpler since there is no longer a hidden alias to track.

### AC-10 · AC-7a: Add public `data.load_themes()` accessor in `server/core/data.py`
Strength: Strong
Split-Depth: 1
Files: server/core/data.py

Problem:
The theme loading logic in `themes._all()` directly calls private helpers `data._load_yaml` and `data._load_overlay`, creating tight coupling to internal implementation details and duplicating the NSFW overlay merge pattern. Other modules in the codebase use `data`'s public surface without issues, so theme loading should follow the same convention.

Solution:
Add a new public function `load_themes()` to `server/core/data.py` that encapsulates the current theme loading behavior:

1. Load the base themes YAML from `os.path.join(DATA_DIR, 'themes.yaml')` using the existing `_load_yaml` helper.
2. Load the NSFW overlay from the relative path `'themes'` using the existing `_load_overlay` helper (which handles the NSFW toggle and file-absence cases).
3. Merge the overlay onto the base using the existing `_deep_merge` helper.
4. Return the merged dict.

The function should be placed after the existing public functions in `data.py`, follow the naming convention (no leading underscore), and include a docstring documenting its purpose and return type.

Benefits:
Encapsulates theme loading logic in a single public API, removes coupling to private helpers (`_load_yaml`, `_load_overlay`), makes the NSFW overlay behavior explicit and reusable, and follows the established pattern of public accessors in `data.py` (e.g., `data.get_entry`, `data.load_traits`).

### AC-11 · AC-7b: Create `server/core/themes.py` with `_all()` using the new public accessor
Strength: Strong
Split-Depth: 1
Files: server/core/themes.py
Depends-On: AC-10

Problem:
The `themes` module does not exist yet but is needed to provide theme loading functionality. The module should expose a `_all()` function that returns all themes by loading them from the data directory with NSFW overlay support, using the public API from `data.py` rather than calling private helpers directly.

Solution:
Create `server/core/themes.py` with the following:

1. Import the `data` module from the same package: `from . import data`
2. Define a `_all()` function that:
   - Calls `data.load_themes()` to get the merged themes dict
   - Returns the result unchanged
3. Add a docstring explaining that `_all()` returns all themes (including NSFW overlays when enabled)

The function signature and behavior should match what `themes._all()` would have done in the original implementation, but now delegates to the public `data.load_themes()` accessor.

Benefits:
Provides the `themes` module with a clean public API, uses the public accessor from `data.py` instead of calling private helpers directly, maintains the same external behavior as the original `themes._all()`, and properly separates concerns between data loading (handled by `data.py`) and theme loading (handled by `themes.py`).
