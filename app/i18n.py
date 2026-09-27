import json
from pathlib import Path

CATALOG_PATH = Path(__file__).parent / "static" / "i18n.json"
DEFAULT_LANG = "ca"

with open(CATALOG_PATH, encoding="utf-8") as _f:
    CATALOG: dict[str, dict[str, str]] = json.load(_f)

SUPPORTED_LANGS = tuple(CATALOG)


class _KeepMissing(dict):
    # A placeholder with no value stays visible ("{reference}") instead of raising -
    # a half-filled message is more useful to a user than a crashed export.
    def __missing__(self, key):
        return "{" + key + "}"


def normalize_lang(lang: str | None) -> str:
    return lang if lang in CATALOG else DEFAULT_LANG


def translate(lang: str | None, key: str, **params) -> str:
    """Look up `key` in the shared catalog (`static/i18n.json`, also used by the browser UI),
    falling back to Catalan and then to the key itself, and fill `{name}` placeholders."""
    text = CATALOG[normalize_lang(lang)].get(key) or CATALOG[DEFAULT_LANG].get(key) or key
    return text.format_map(_KeepMissing({k: "" if v is None else v for k, v in params.items()}))


def labels_for(key: str) -> set[str]:
    """Every language's text for `key` - used to read back files written in any language."""
    return {texts[key] for texts in CATALOG.values() if key in texts}
