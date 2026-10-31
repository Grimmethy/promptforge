# Krea 2 Turbo in PromptForge

Added 2026-10-14. Krea 2 Turbo is a fast distilled text-to-image model (8 steps). In PromptForge it is an alternative **engine**: pick a model named
`krea2:<file>` and the job runs a different ComfyUI graph than the SDXL checkpoints.

## Files (ComfyUI `models/` folders)

| Folder | File | Size |
|---|---|---|
| `diffusion_models/` | `krea2_turbo_int8_convrot.safetensors` (also `krea2_turbo_fp8_scaled` / `_bf16` work) | 12.6 GB |
| `text_encoders/` | `qwen3vl_4b_fp8_scaled.safetensors` (override: `PROMPTFORGE_KREA2_TE`) | 4.9 GB |
| `vae/` | `qwen_image_vae.safetensors` (override: `PROMPTFORGE_KREA2_VAE`) | 0.24 GB |

All from <https://huggingface.co/Comfy-Org/Krea-2> (no login; the Civitai mirror needs one). The model list shows every `krea2_*.safetensors` that
ComfyUI has in `diffusion_models/` as `krea2:<file>`. The repo is tagged `krea-2-community-license`: read its terms before commercial use.

## What a Krea 2 job does

- Graph: `UNETLoader` + `CLIPLoader` (type `krea2`) + `VAELoader`, the positive encoded once, a zeroed copy as the negative, one `KSampler`
  (**8 steps, cfg 1.0, euler / simple** unless the request sets `steps` / `cfg` / `sampler` / `scheduler`), decode, save.
- No ControlNet: the openpose ControlNet is SDXL-only, so a pose skeleton is **dropped** and the job record carries a `warnings` entry.
- No regional prompting: a two-character job becomes one text prompt (base + character A + character B).
- Krea 2 reads natural language. The assembled prompt is the usual tag list; a caller may send `prompt_text` (a sentence prompt) to override it.

## LoRA Support

Krea 2 supports style LoRAs from the same repository. Each LoRA requires a trigger phrase.

### Request Shape

```json
{
  "model": "krea2:krea2_turbo_int8_convrot.safetensors",
  "prompt_text": "a pig artist painting in a studio",
  "loras": [
    {
      "name": "triple_exposure.safetensors",
      "weight": 1.0,
      "trigger": "TripleExposureRK"
    },
    {
      "name": "watercolor_bright_ink.safetensors",
      "weight": 1.0,
      "trigger": "bb_style watercolor"
    }
  ],
  "sampler": "euler",
  "scheduler": "simple",
  "steps": 8,
  "cfg": 1.0,
  "seed": 42,
  "width": 1168,
  "height": 1536
}
```

### Validation Rules

- **LoRA count**: Maximum 4 LoRAs per job
- **LoRA file**: Must exist in `models/loras/` directory (ComfyUI models folder)
- **Weight**: Must be between -2 and 2 (inclusive)
- **Trigger**: Optional; if provided, prefixed to prompt in LoRA order (comma-separated, deduplicated)
- **Dimensions**: Must be multiples of 16 (rounded down with warning if not)
- **Non-Krea-2 models**: LoRAs are ignored with a warning
