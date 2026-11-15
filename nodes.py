"""The Character Helper node: joins the fields into one comma separated string."""

from __future__ import annotations

import json
import re

FIELD_ORDER = ["subject", "character", "hair", "eyes", "body", "outfit", "top", "bottom",
               "legwear", "accessories", "expression", "pose", "view", "background"]

_WEIGHTED = re.compile(r"^\((.+):(\s*-?\d*\.?\d+)\)$")
# Emoticon tags keep their underscore (^_^, >_<, o_o, ...).
_KEEP_UNDERSCORE = re.compile(r"^.{1,4}$|^[^a-z0-9]+_[^a-z0-9]+$")


def format_tag(tag, spaces=True, escape=True):
    tag = tag.strip()
    m = _WEIGHTED.match(tag)
    if m:  # "(tag:1.2)" - format the tag, keep the weight syntax
        return "(" + format_tag(m.group(1), spaces, escape) + ":" + m.group(2) + ")"
    if spaces and not _KEEP_UNDERSCORE.match(tag):
        tag = tag.replace("_", " ")
    if escape:
        tag = re.sub(r"(?<!\\)([()])", r"\\\1", tag)
    return tag


def parse_fields(value):
    """The widget value is JSON: {"fields": {"hair": ["aqua_hair", ...], ...}, ...}"""
    if isinstance(value, dict):
        data = value
    else:
        try:
            data = json.loads(value) if value else {}
        except (TypeError, ValueError):
            # not JSON: treat it as a plain comma separated prompt
            return {"extra": [t for t in str(value).split(",") if t.strip()]}
    fields = data.get("fields", data) if isinstance(data, dict) else {}
    out = {}
    for name, tags in fields.items():
        if isinstance(tags, str):
            tags = tags.split(",")
        if isinstance(tags, (list, tuple)):
            out[name] = [str(t).strip() for t in tags if str(t).strip()]
    return out


def join_fields(fields, spaces=True, escape=True):
    order = FIELD_ORDER + [f for f in fields if f not in FIELD_ORDER]
    seen, out = set(), []
    for name in order:
        for tag in fields.get(name, ()):
            key = tag.lower().replace(" ", "_")
            if key not in seen:
                seen.add(key)
                out.append(format_tag(tag, spaces, escape))
    return ", ".join(out)


class CharacterHelper:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "fields": ("CHARACTER_HELPER_FIELDS", {}),
                "underscores_to_spaces": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "aqua_hair -> aqua hair. Illustrious, NoobAI and Anima expect spaces.",
                }),
                "escape_parentheses": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "ganyu_(genshin_impact) -> ganyu \\(genshin impact\\), so the "
                               "brackets are not read as prompt weighting.",
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("tags",)
    FUNCTION = "build"
    CATEGORY = "utils/prompt"
    DESCRIPTION = ("Builds a comma separated tag prompt from fields (character, hair, eyes, clothes, "
                   "pose, ...). Pick a character to pre-fill its usual look, or roll the dice.")
    SEARCH_ALIASES = ["character", "danbooru", "tags", "prompt builder", "random prompt"]

    def build(self, fields, underscores_to_spaces=True, escape_parentheses=True):
        return (join_fields(parse_fields(fields), underscores_to_spaces, escape_parentheses),)


NODE_CLASS_MAPPINGS = {"CharacterHelper": CharacterHelper}
NODE_DISPLAY_NAME_MAPPINGS = {"CharacterHelper": "Character Helper"}
