# Reference projects for PromptForge

Scouted on GitHub (2026-10-13) as prior art for this project (shot/prompt builder for ComfyUI). Read them for ideas, data models, pitfalls and
code worth adapting; check each licence before copying anything.

These are also the leads Agent Manager's **Scouted Repos** pipeline works on **when this repo is the active project**: it
only picks up leads whose "Relevant to" tag is exactly `promptforge` (this repo's directory name) in the shared index
`UsefulProjectIndex/INDEX.md`. To add one, add a table row and a matching `### org/repo` note there, tagged with that name:

    | [org/repo](https://github.com/org/repo) | github | what it is | promptforge -- why it matters here | lead |

## References

### [adieyal/sd-dynamic-prompts](https://github.com/adieyal/sd-dynamic-prompts)

Wildcard and template language for generating prompt variations (Stable Diffusion web UI extension).

**Why it matters here:** The standard for combinatorial/random prompts: its wildcard and template syntax is the model for PromptForge's randomizer and presets.

### [twri/sdxl_prompt_styler](https://github.com/twri/sdxl_prompt_styler)

SDXL prompt styler node: a JSON library of named styles that wrap a prompt.

**Why it matters here:** Same idea as PromptForge's styles.yaml (medium-specific tags and negatives per style); worth comparing the style schema.

### [mirabarukaso/character_select_stand_alone_app](https://github.com/mirabarukaso/character_select_stand_alone_app)

Stand-alone app that selects a character, builds the prompt and sends it to ComfyUI/WebUI via API.

**Why it matters here:** Closest cousin to PromptForge plus CharacterForge: character picker, prompt assembly and ComfyUI API in one app. Also relevant to: characterforge.

### [receyuki/comfyui-prompt-reader-node](https://github.com/receyuki/comfyui-prompt-reader-node)

Reads and writes generation metadata (prompts, settings) across tools for ComfyUI.

**Why it matters here:** Embedding the prompt and settings in the image file itself: an option beyond the .md sidecars our gallery uses.

### [DominikDoom/a1111-sd-webui-tagcomplete](https://github.com/DominikDoom/a1111-sd-webui-tagcomplete)

Booru-style tag autocompletion for prompt boxes.

**Why it matters here:** Tag vocabulary and autocomplete for PromptForge's prompt and keyword fields (our models are booru-tag trained).
