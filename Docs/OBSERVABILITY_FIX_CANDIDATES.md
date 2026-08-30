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
