"""The Character Helper node: joins the fields into one comma separated string."""

from __future__ import annotations

import json
import re

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
    """The widget's value, ``{"fields": {"hair": ["aqua_hair", ...], ...}, ...}`` as JSON, -> its fields."""
    if not value:
        return {}
    data = json.loads(value) if isinstance(value, str) else value
    return {name: [t.strip() for t in tags if t.strip()] for name, tags in data.get("fields", {}).items()}


def join_fields(fields, spaces=True, escape=True):
    # "1girl, solo" leads; the other fields follow in the order the node shows them
    order = sorted(fields, key=lambda name: name != "subject")
    seen, out = set(), []
    for name in order:
        for tag in fields[name]:
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
                   "pose, ...). Pick a character and fill in their usual look, or roll the dice.")
    SEARCH_ALIASES = ["character", "danbooru", "tags", "prompt builder", "random prompt"]

    def build(self, fields, underscores_to_spaces=True, escape_parentheses=True):
        return (join_fields(parse_fields(fields), underscores_to_spaces, escape_parentheses),)


NODE_CLASS_MAPPINGS = {"CharacterHelper": CharacterHelper}
NODE_DISPLAY_NAME_MAPPINGS = {"CharacterHelper": "Character Helper"}
