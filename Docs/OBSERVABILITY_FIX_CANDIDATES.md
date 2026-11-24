# Observability Fix Candidates

### AC-1 · Log-and-degrade instead of silent swallow in checkpoint-list helper
Strength: Strong
Files: server/app.py
Snippet:
```
        oi = _comfy_get("/object_info/CheckpointLoaderSimple")
        opts = oi["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        return list(opts)
    except Exception:
        return []


```

Problem:
The `except Exception: return []` block in the checkpoint-listing helper provides zero observability. A caller receiving `[]` cannot tell whether the user genuinely has no checkpoints installed or whether the ComfyUI backend is unreachable, misconfigured, or returning an unexpected schema. Because the exception is caught broadly (swallowing `KeyError`, `TypeError`, `ConnectionError`, and any programming bug in the dict-path traversal equally) and no log line, metric, or trace is emitted, a production incident where users report missing models leaves no server-side evidence. The exception is born and dies on that single `except` line; diagnosing it requires adding a `print` and redeploying.

Solution:
Replace the bare `except Exception: return []` with a structured warning log that captures the exception type, message, and the ComfyUI endpoint that was queried, then still return `[]` to preserve the graceful-degradation contract. Concretely: `except Exception as exc: logger.warning("Checkpoint list fetch failed for %s: %s (%s)", comfy_url, exc, type(exc).__name__); return []`. Optionally narrow the catch to `(requests.RequestException, KeyError, TypeError, ValueError)` so that truly unexpected errors (e.g. a `NameError` from a typo) still propagate and surface in a traceback rather than being silently absorbed.

Benefits:
Operators can now distinguish "user has zero checkpoints" from "backend is broken" by checking the server log or structured log stream during an incident. The warning line gives a concrete exception type and message, turning a silent 0-count into a one-line diagnostic. Narrowing the exception scope ensures genuine programming bugs (typos in the dict path, missing imports) are no longer masked and will appear in test runs or crash reports instead of being indistinguishable from a network blip. The graceful-degradation behavior for callers is unchanged—`[]` is still returned—so no downstream code needs modification.

### AC-2 · Replace silent `pass` in save-except with a structured warning log
Strength: Strong
Files: server/core/comfy.py
Snippet:
```
                        pass
            if saved:
                sidecar = {"id": job["id"], "seed": job["seed"],
                           "model": job["model"], "spec": job.get("spec"),
                           "prompt_used": job.get("prompt_used"),
                           "images": saved, "ts": time.time()}
                with open(os.path.join(outdir, f"{job['id'][:8]}.json"), "w") as fh:
```

Problem:
At line 220 the `except` handler for the job-artefact save path contains only `pass`, so any exception raised during the image or sidecar-JSON write (disk full, permission denied, transient I/O error, `JSON` serialisation failure on `job["spec"]`) is discarded with zero trace. The subsequent `if saved:` guard merely prevents a second crash on the sidecar write; it does not compensate for the missing observability. In a long-running ComfyUI server process there is no interactive terminal to surface a traceback, so the operator has no log line, metric, or user-visible error indicating that a particular job's outputs were silently lost.

Solution:
Replace the bare `pass` with a `logger.warning` call that captures the exception type and message plus the identifying context already in scope (job id, output directory, and the name of the artefact being written). For example: `logger.warning("Failed to save job artefact %s for job %s: %s", artifact_name, job_id, exc)` where `exc` is the bound exception from `except Exception as exc:`. Do not re-raise—the surrounding `if saved:` guard already handles the downstream path, and the save is best-effort—but do ensure the exception is logged at WARNING level so it appears in the server's default log stream without requiring DEBUG verbosity.

Benefits:
Operators and on-call engineers gain an immediate, greppable signal in the server log whenever a job's images or sidecar metadata fail to persist, turning an invisible data-loss event into a diagnosable one. The log line carries enough context (job id, target path, exception detail) to triage without attaching a debugger to a production process, and the WARNING level ensures it is visible under the default logging configuration rather than hidden behind a DEBUG flag.

### AC-3 · Subprocess probe swallows all exceptions and returns empty list with no diagnostic trail
Strength: Strong
Files: server/core/comfy_proc.py
Snippet:
```
            ],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except Exception:
        return []
    rows = []
    for line in out.strip().splitlines():
```

Problem:
The probe function wraps its `subprocess.run(...)` call in a bare `except Exception` (or equivalent catch-all) and unconditionally returns `[]`. Whether the child binary is missing (`FileNotFoundError`), segfaults, times out (`TimeoutExpired`), exits non-zero (`CalledProcessError`), or emits unparseable output, the caller receives the same empty list it would get for a legitimate "no rows" result. No log line, no metric increment, no structured error field is emitted, so a silent degradation of this probe is invisible in production until someone notices downstream data is missing and has to add temporary `print` statements to discover the root cause.

Solution:
Replace the catch-all silent return with a `logger.warning` (or `logger.error` for `FileNotFoundError`/`OSError` that indicate the binary is absent) that records the exception class, its message, the command string, the configured timeout, and — when available — the child's return code and a truncated stderr snippet. After logging, still return `[]` to preserve the existing caller contract, but attach a module-level counter (e.g. `probe_failure_total` via `prometheus_client` or the project's existing metrics helper) so the degradation is visible on dashboards. Do not rethrow; the "best-effort" semantics are intentional, but the failure must leave a trace.

Benefits:
Operators gain immediate visibility in structured logs and metrics whenever the probe degrades, can distinguish "genuinely empty result" from "probe is broken" without code inspection, and can correlate a spike in `probe_failure_total` with the exact exception and command that caused it — turning a silent, days-long debugging exercise into a one-line log search.

### AC-4 · Log the swallowed exception in Ollama /api/ps poll
Strength: Strong
Files: server/core/comfy_proc.py
Snippet:
```
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=5) as r:
            body = json.load(r)
    except Exception:
        return []
    models = body.get("models") if isinstance(body, dict) else None
    if not isinstance(models, list):
```

Problem:
The `try/except Exception: return []` block around the `urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=5)` call silently discards every distinct failure mode—`ConnectionRefusedError` (Ollama not running), `socket.timeout` (Ollama hung), `json.JSONDecodeError` (malformed or partial body), and `TypeError` (misconfigured `OLLAMA_URL`)—into an identical empty list. Because this lives in `server/core/`, it is almost certainly invoked from a request handler or a periodic status poll, so no user-facing traceback will surface the cause. An on-call engineer seeing "no models loaded" has zero log trail to tell whether the backend is down, returning garbage, or the URL constant is simply wrong.

Solution:
Replace the bare `except Exception:` with `except Exception as exc:` and, before the `return []`, emit a single structured log line at WARNING level that includes the exception type, its message, the target URL, and the timeout value, e.g. `logger.warning("Ollama /api/ps poll failed: %s: %s (url=%s, timeout=%ss)", type(exc).__name__, exc, OLLAMA_URL, 5)`. Keep the graceful-degradation contract intact—still return `[]` so callers are unaffected—but ensure the diagnostic signal is preserved in the application log (or structured-logging sink) for later correlation. No re-raise, no change to the return type, no new dependency.

Benefits:
Operators can now distinguish "Ollama process is not listening" from "Ollama is alive but returned a truncated JSON body" from "the OLLAMA_URL environment variable is set to a non-URL string" by reading a single log line, cutting mean-time-to-diagnose from an unbounded grep-and-guess session to a targeted log search. The change is a one-line addition inside the existing `except` clause, introduces no new code paths, and preserves the existing caller contract, so regression risk is negligible.

### AC-5 · Surface preview-render failures instead of swallowing them silently
Strength: Strong
Files: web/app.js
Snippet:
```
    $('#preview').textContent = pose + (p.positive
      ? `POSITIVE\n${p.positive}\n\nNEGATIVE\n${p.negative}`
      : `A (${r.n_chars} chars)\n${p.region_a}\n\nB\n${p.region_b}\n\nBASE\n${p.base}\n\nNEGATIVE\n${p.negative}`);
  } catch { }
}

function applyThemeHints() {
```

Problem:
The prompt-preview render step wraps its entire formatting-and-write path in a bare `catch {}` that discards the error object, the stack, and any context about which input shape triggered the failure. Because the rendered preview is the sole feedback channel the user sees while iterating on a prompt, a null `#preview` (panel not yet mounted), an unresolved async payload leaving `r` or `p` undefined, or a pathological `pose.toString` all collapse into a blank panel with zero diagnostic signal. The developer sees nothing in the console, the user sees an empty box, and the root cause is indistinguishable from "the feature simply hasn't loaded yet."

Solution:
Replace the empty `catch {}` with a handler that (1) logs a structured warning via `console.warn('[promptforge:preview] render failed', err)` so the error object, message, and stack are preserved in the browser console, (2) writes a short human-readable fallback into `#preview` (e.g. "Preview unavailable — see console for details") so the user knows the panel rendered *and* failed rather than simply being empty, and (3) re-throws the error only if the caller is expected to handle it (i.e. do not swallow it when the render is invoked from an event handler that has its own error boundary). No new dependency or global state is introduced; the change is confined to the existing `try/catch` block.

Benefits:
Developers can immediately see *which* input shape broke the formatter and *where* it threw, turning a silent blank-panel bug into a one-line console hit. Users get a visible "something went wrong" cue instead of mistaking a failure for an empty result, which eliminates the most common support ticket ("the preview just shows nothing"). Because the fix is scoped to the single `catch` block, it carries no regression risk to the happy-path rendering logic and can be verified with a unit test that stubs `$('#preview')` to `null` and asserts the warning is emitted.

### AC-6 · Silent catch in best-effort state gatherer loses all diagnostic signal
Strength: Strong
Files: web/app.js
Snippet:
```
      rigs: editor ? editor.getRigs() : null,
      libFrame: lastLibFrame,
    });
  } catch { }
}
function formValues() {
  const o = {};
```

Problem:
The `catch` block around the state-gathering code (assembling `rigs` via `editor.getRigs()` and `libFrame`, then passing them to a downstream call) contains no statement at all. If `editor.getRigs()` throws an internal invariant error, or the downstream call rejects synchronously, the exception is swallowed with zero trace: no `console.warn`, no structured log, no counter. A developer diagnosing "why is the rigs panel blank?" or "why did the frame snapshot come back `null`?" has no signal in the browser console, DevTools network tab, or any other surface to distinguish "editor was legitimately absent" from "editor was present but `getRigs()` blew up." The guard `editor ? … : null` already covers the absent-editor path, so any throw reaching the `catch` is an unexpected runtime failure that is now permanently invisible.

Solution:
Inside the existing `catch (err)` block, add a single `console.warn('[state-gather] best-effort snapshot failed:', err)` (or the project's equivalent lightweight logging helper if one exists). Do not rethrow, do not add a telemetry beacon, and do not change the surrounding control flow — the page must still continue rendering. The log should include the error object so the stack trace is preserved in the console, and the prefix tag makes it greppable. This is the minimal change: one line in the catch body, no new imports, no new dependency.

Benefits:
Any future regression in `editor.getRigs()` or the downstream call becomes immediately visible in the browser console during local development and in any log-forwarding setup the team already has, turning an invisible silent failure into a one-line diagnostic. The cost is a single `console.warn` that fires only on the exceptional path, so the happy path is unchanged and there is no performance or noise impact. It also preserves the intended "best-effort, don't crash the UI" behavior while giving the next engineer a concrete starting point instead of a blank stare at a `null` value.

### AC-7 · Silent catch in loadGallery swallows load failures
Strength: Strong
Files: web/app.js
Snippet:
```
async function loadGallery(reset = true) {
  if (_galLoading) return;
  const g = $('#gallery');
  if (reset) {
    _galOffset = 0; _galTotal = 0;
    g.innerHTML = '';
    g.append(..._skelTiles(12));   // placeholders while page 1 loads
```

Problem:
`loadGallery` is an async function that resets gallery state, clears the DOM, appends skeleton tiles, and then (past the visible truncation) fetches and renders real tiles. The scanner confirmed an empty `catch` block at line 754 inside this function. Because the catch body contains no statement at all, any rejection from the fetch, a DOM error during tile insertion, or an exception in the pagination logic is absorbed silently: no `console.error` or `console.warn` call is made, no rethrow propagates to a caller, and the user is left staring at the skeleton placeholders (or a half-rendered grid) with zero diagnostic trail. In a project whose only logging primitive is `console.error`/`console.warn`, an empty catch is a complete observability dead-end.

Solution:
Replace the empty catch body with a single `console.error('loadGallery failed:', err);` call that captures the rejection reason, followed by `throw err;` so that any upstream `.catch` handler, `await`-ing caller, or top-level unhandled-rejection hook still receives the failure. This uses only the `console.error` primitive already present in the codebase, adds no dependency, and keeps the function's async contract intact (callers that `await loadGallery()` will see the rejection rather than a silent resolve).

Benefits:
Developers and operators now see a timestamped, context-labelled error in the browser console or Node stderr the moment a gallery load fails, eliminating the "blank gallery, no clue" debugging cycle. Rethrowing preserves the promise chain so any retry logic, error-boundary, or user-facing toast that keys off a rejected promise will actually fire. The fix is a two-line change scoped to one catch block, with no API or dependency surface added.

### AC-8 · Silent swallow of `pf.models()` failures in job runner
Strength: Strong
Files: scripts/jobs/runner.py
Snippet:
```
            return 1
        try:
            available = set(pf.models() or [])
        except Exception:  # noqa: BLE001
            available = set()
        beat()
        fails, last_requester = 0, None
```

Problem:
The `except Exception:` block on the `pf.models()` call swallows all errors without logging or signaling. If model listing fails due to initialization issues, network problems, or API errors, the code silently continues with an empty model set. This is inconsistent with the preceding `pf.gpu_acquire()` error handling (lines 163-169) which logs failures and returns early. Operators have no visibility into why jobs might be failing or behaving unexpectedly when model availability is critical.

Solution:
Add logging and return early if model listing fails, consistent with the gpu_acquire block pattern. Use the existing `out()` function for output and append to the `reasons` list if available, then return 1 to signal failure. This ensures operators are notified of model listing failures and jobs don't proceed with an empty model set.

Benefits:
Operators will be immediately aware when model listing fails, enabling faster diagnosis and resolution. Jobs won't silently proceed with no available models, preventing downstream failures that are difficult to trace back to the root cause. The error handling is now consistent across both GPU acquisition and model listing operations.

### AC-9 · Silent swallow in timing data collection lacks observability
Strength: Strong
Files: scripts/jobs/runner.py
Snippet:
```
        if secs > 0:
            try:
                job_plan.append_timing(timings_path, {"model": rec["model"], "width": rec["width"], "height": rec["height"], "steps": rec["steps"], "seconds": secs})
            except (ValueError, OSError):
                pass
        if deliver is not None:
            try:
```

Problem:
The `except (ValueError, OSError): pass` at line 266 silently swallows errors from `job_plan.append_timing()` with no logging, re-raise, or fallback. When the timings file becomes unwritable due to permission issues, disk exhaustion, or path invalidity, the code continues reporting success while losing all timing metadata. The adjacent `deliver()` block (lines 270-275) uses `# noqa: BLE001` to acknowledge its intentional swallow, but the timing block lacks this deliberation — suggesting the `pass` may have been added without the same consideration for observability.

Solution:
Replace the bare `pass` with a log statement using the project's existing `out()` primitive (visible at line 257), providing enough context to identify the problematic seed and error. This preserves the non-fatal behavior — the picture was already generated and the row recorded — while making timing write failures visible for debugging.

Benefits:
Systematic failures in timing data collection become detectable instead of silent. When timing data goes missing from `timings_path`, operators will have a log entry indicating which seed failed and why, rather than starting from zero context. The fix uses only the `out()` primitive already present in the file, requiring no new dependencies.

### AC-10 · False Positive: Slot-fill error captured in manifest, not swallowed
Strength: Strong
Snippet:
```
                        rec = adapter.link(slug, role, tag, rnd, spec, gen.job, gen.name, gen.data)
                        row = {"slug": slug, "role": role, "round": rnd, "tag": tag, "status": "linked", "file": (rec or {}).get("file"), "gallery": gen.name}
                        fails = 0
                    except (SlotFillError, OSError, ValueError) as exc:
                        row = {"slug": slug, "role": role, "round": rnd, "status": "error", "error": str(exc)[:200]}
                        fails += 1
                        st["errors"] += 1
```

The `except` block at line 115 does not log or re-raise, but the caught exception is not being discarded: `str(exc)[:200]` is stored in `row["error"]`, the row's `status` is set to `"error"`, and the row is appended to the manifest via `_append(manifest, row)` at line 120. The error context therefore travels with the failure record and is visible to any downstream consumer of the manifest (dashboard, report, alerting). The scanner's heuristic correctly identified the absence of `logging.`, `raise`, or `print`, but the project's established pattern is manifest-based error capture rather than log-based, so this is a deliberate design choice, not a swallowed error.

### AC-11 · Surface gallery files missing from the disk-size total
Strength: Strong
Files: scripts/slot_fill/report.py
Snippet:
```
    for r in linked:
        try:
            disk += os.path.getsize(os.path.join(gallery_dir, r.get("gallery") or ""))
        except OSError:
            pass
    by_slug = {}
    for r in linked:
```

Problem:
The disk-size loop in the report builder calls `os.path.getsize` on each linked row's gallery file and, on `OSError`, executes a bare `pass`. A missing or unreadable gallery file contributes 0 bytes to `disk`, and nothing is recorded. The report then shows a disk figure that undercounts with no indication that any files were skipped. The `missing` list is initialized alongside `sheets`, but in the code shown it is never populated from the `miss` value returned by `contact_sheet`, so the only other signal for these files is not visibly connected to the report. A reader of the report cannot tell that the total is incomplete.

Solution:
In the `OSError` handler, log the skipped gallery path and the run's slug and role using the stdlib `logging` module, for example `logging.warning("slot_fill: cannot stat gallery %s for %s/%s: %s", path, slug, role, exc)`, and bind the exception with `as exc`. Keep the loop otherwise unchanged. Separately, append the `miss` entries returned by `contact_sheet` to `missing` so the missing-file information is actually carried forward. Do not add any metrics or new dependencies.

Benefits:
Gallery files that are absent or unreadable become visible in the logs with enough context to identify them, rather than silently reducing the reported disk total. Someone reading the report or the logs can tell when the size figure is incomplete and which run caused it.
