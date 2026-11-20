"""Theme presets — bundles of tag contributions (style / setting / palette /
lighting / negative). See data/themes.yaml."""

from . import data

_KEYS = ("style", "setting_bias", "palette", "lighting", "negative_add")


def _all():
    return data.load_themes()


def list_themes():
    keys = list(_all().keys())
    if "none" in keys:
        keys.remove("none")
        keys.insert(0, "none")
    return keys or ["none"]


def get_theme(name):
    t = _all().get(name) or {}
    return {k: str(t.get(k, "")) for k in _KEYS}


def resolve(name):
    """A theme -> {style, setting_bias, palette, lighting, negative_add}.

    `style` / `setting_bias` are UI suggestions; `palette` + `lighting` are the
    always-applied scene contribution; `negative_add` merges into the negative.
    """
    return get_theme(name or "none")
