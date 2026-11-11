"""LoRA registry and stacks for the SDXL / Illustrious path. Pure: no Flask, no ComfyUI calls.

data/loras.json lists the LoRAs the owner has (file, base model, trigger words, default weight); validate_stack() turns what a job asks for
into the cleaned list build_graph chains as LoraLoader nodes. A LoRA whose base differs from the model's base gives a warning, not an error.
"""
import json
import os

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "loras.json")
DEFAULT_COMFY_MODELS = "/media/model-cache/github/TheAgent/app/tools/ComfyUI/models"
MAX_STACK = 6
WEIGHT_LIMIT = 2.0


def loras_dir():
    """The folder ComfyUI reads LoRA files from: PROMPTFORGE_LORAS_DIR, else <COMFYUI_MODELS_DIR or the default install>/loras."""
    return os.environ.get("PROMPTFORGE_LORAS_DIR") or os.path.join(os.environ.get("COMFYUI_MODELS_DIR") or DEFAULT_COMFY_MODELS, "loras")


def load_registry(path=None):
    """{'loras': [...], 'checkpoints': {...}} from the registry file; an absent file gives the empty registry."""
    try:
        with open(path or REGISTRY_PATH, encoding="utf-8") as fh:
            reg = json.load(fh)
    except FileNotFoundError:
        return {"loras": [], "checkpoints": {}}
    if not isinstance(reg, dict):
        raise ValueError("the LoRA registry must be a JSON object")
    reg.setdefault("loras", [])
    reg.setdefault("checkpoints", {})
    return reg


def by_file(registry, file):
    """The registry entry whose file is `file`, else None."""
    return next((e for e in registry.get("loras", []) if e.get("file") == file), None)


def by_id(registry, lora_id):
    return next((e for e in registry.get("loras", []) if e.get("id") == lora_id), None)


def model_base(registry, checkpoint):
    """The base ('illustrious', 'pony' ...) the registry lists for a checkpoint file, else None (unknown: no mismatch warning)."""
    return (registry.get("checkpoints") or {}).get(str(checkpoint or ""))


def validate_stack(stack, model_base=None, loras_dir_=None, registry=None):
    """(cleaned, warnings) for a requested LoRA stack: a list of {name | id, weight?, trigger?}.

    `name` is a LoRA file name, `id` a registry id; the registry fills in the file, the default weight and the triggers when absent.
    ValueError: not a list, more than MAX_STACK entries, an entry without a name or id, a weight that is not a number in [-2, 2].
    FileNotFoundError: the file is not in the LoRAs folder. A base different from `model_base` is a warning. Each cleaned entry is
    {name, weight, trigger} (trigger: the registry's triggers joined with ', ', or what the caller gave).
    """
    if not isinstance(stack, list):
        raise ValueError("loras must be a list")
    if len(stack) > MAX_STACK:
        raise ValueError("Maximum %d LoRAs allowed, got %d" % (MAX_STACK, len(stack)))
    reg = registry if registry is not None else load_registry()
    folder = loras_dir_ or loras_dir()
    cleaned, warnings = [], []
    for i, item in enumerate(stack):
        if not isinstance(item, dict):
            raise ValueError("LoRA #%d must be an object" % i)
        entry = by_id(reg, item["id"]) if item.get("id") else None
        if item.get("id") and entry is None:
            raise ValueError("LoRA #%d: no registry entry with id %r" % (i, item["id"]))
        file = item.get("name") or (entry or {}).get("file")
        if not file or not isinstance(file, str):
            raise ValueError("LoRA #%d needs a 'name' (file) or an 'id'" % i)
        entry = entry or by_file(reg, file)
        weight = item.get("weight", (entry or {}).get("default_weight", 1.0))
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not -WEIGHT_LIMIT <= weight <= WEIGHT_LIMIT:
            raise ValueError("LoRA '%s' weight %r out of bounds [-%g, %g]" % (file, weight, WEIGHT_LIMIT, WEIGHT_LIMIT))
        if not os.path.isfile(os.path.join(folder, file)):
            raise FileNotFoundError("LoRA file not found: %s (checked %s)" % (file, folder))
        trigger = item.get("trigger")
        if trigger is None and entry and entry.get("triggers"):
            trigger = ", ".join(entry["triggers"])
        if entry and model_base and entry.get("base") and entry["base"] != model_base:
            warnings.append("LoRA '%s' was made for %s but the model is %s" % (file, entry["base"], model_base))
        cleaned.append({"name": file, "weight": float(weight), "trigger": trigger or ""})
    return cleaned, warnings


def with_triggers(text, stack):
    """`text` with each LoRA's trigger words put in front once (a word already present, ignoring case, is not added again)."""
    have = (text or "").lower()
    add = []
    for lora in stack or []:
        for word in [w.strip() for w in str(lora.get("trigger") or "").split(",") if w.strip()]:
            if word.lower() not in have and word not in add:
                add.append(word)
    return ", ".join(add + ([text] if text else [])) if add else text
