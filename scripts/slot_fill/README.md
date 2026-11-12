# slot_fill: fill the image slots of the library forges

A breadth-first batch engine. For round r = 1..10, for every entry, for every role that has fewer than r pictures, it makes one picture through PromptForge and links it to the
entry. Stopping at any moment leaves every entry balanced, and a re-run continues where it stopped (the counts come from the forge itself).

Run everything from the repo root with the repo's venv: `.venv/bin/python -m scripts.slot_fill <command> --forge characters|objects`.

| command | what it does |
|---|---|
| `run [--slugs a,b] [--round-from N] [--round-to M] [--limit N] [--dry-run]` | fills slots. It asks `forge-mode on` first and `forge-mode off` last when that command exists, takes the PromptForge GPU session, releases it at the end. `--dry-run` prints the plan and changes nothing. |
| `status` | slots filled and open per entry and role, and the disk used by the pictures the manifest names. |
| `report [--since ISO]` | writes `data/slot-fill-runs/<stamp>/summary.md` and one `<slug>.jpg` contact sheet per entry for the last run, and prints the summary path. |

ObjectForge has no linking yet: `--forge objects` only supports `run --dry-run` (it lists the wanted objects from `objectforge/lists/*.md`).

## Where things live

- State (outside the repo): `~/.local/share/slot-fill/<forge>.jsonl` (one row per picture, a `run-start` row per run) and `<forge>.pid` while a run is alive;
  the heartbeat is `~/.local/state/slot-fill/heartbeat.json`, rewritten after every picture.
- Banks: `data/slot-banks/characters.json` (8 roles x 10 variations), `defaults.json` (negative text, sizes, priority, target), `overrides.json` (per entry: `always_clothed`,
  `targets`, `skip_roles`, `skip`). They are plain JSON; edit them by hand. `proposed.json` (variations a model proposed) is never read: move what you want into the bank.
- Code: `engine.py` (the loop), `pf_client.py`, `headroom.py` (the light check before every picture), `adapters/` (one per forge), `banks.py`, `cli.py`, `report.py`.

## Rules

- The owner ranks and deletes. The engine never rejects, flags, holds back or deletes a picture: every picture that is not blank is linked with verdict `needs-work`.
- Three failures in a row stop a run (exit 1); no headroom for 30 minutes stops it (exit 2); a failed generate takes the GPU session once and retries that picture once.
- Never run it while the pipeline's local models hold the GPU: use `forge-mode` (or stop the pipeline, its drains and the P40 VM by hand) and say what you stopped.
- The operator's manual with the rules learned is `~/.claude/skills/fill-forge-slots/SKILL.md`.
