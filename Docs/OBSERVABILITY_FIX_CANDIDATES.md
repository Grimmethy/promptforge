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
