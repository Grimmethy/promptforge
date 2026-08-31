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
