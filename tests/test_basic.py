"""Run with:  python -m pytest tests"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nodes import format_tag, join_fields, parse_fields  # noqa: E402
from tagdb import DB, normalize, rating_level  # noqa: E402

DB.load()


def test_format_tag():
    assert format_tag("aqua_hair") == "aqua hair"
    assert format_tag("aqua_hair", spaces=False) == "aqua_hair"
    assert format_tag("ganyu_(genshin_impact)") == r"ganyu \(genshin impact\)"
    assert format_tag("ganyu_(genshin_impact)", escape=False) == "ganyu (genshin impact)"
    assert format_tag("^_^") == "^_^"                       # emoticons keep their underscore
    assert format_tag("(aqua_hair:1.2)") == "(aqua hair:1.2)"  # weights survive
    assert format_tag(r"ganyu \(genshin impact\)") == r"ganyu \(genshin impact\)"


def test_join_fields():
    value = '{"fields": {"hair": ["aqua_hair", "twintails"], "character": ["hatsune_miku"], "subject": ["1girl"]}}'
    assert join_fields(parse_fields(value)) == "1girl, hatsune miku, aqua hair, twintails"
    assert join_fields(parse_fields("")) == ""
    assert join_fields(parse_fields({"hair": "a, b", "eyes": ["a"]}), spaces=False) == "a, b"
    assert join_fields(parse_fields("plain, text")) == "plain, text"


def test_normalize():
    assert normalize(" Aqua Hair ") == "aqua_hair"
    assert normalize(r"ganyu \(genshin impact\)") == "ganyu_(genshin_impact)"
    assert normalize("(long hair:1.3)") == "long_hair"


def test_autocomplete():
    assert DB.autocomplete("character", "hatsune")[0]["tag"] == "hatsune_miku"
    assert DB.autocomplete("character", "Hatsune Miku")[0]["series"] == "vocaloid"
    top = DB.autocomplete("top", "leather")
    assert top[0]["tag"] == "leather_jacket" and top[0]["field"] == "top"
    assert DB.autocomplete("hair", "") == []


def test_character_preset():
    fields = DB.character_preset("hatsune_miku")["fields"]
    assert fields["character"] == ["hatsune_miku", "vocaloid"]
    assert fields["subject"] == ["1girl", "solo"]
    assert {"twintails", "aqua_hair", "very_long_hair"} <= set(fields["hair"])
    assert "long_hair" not in fields["hair"]                # implied by very_long_hair
    assert fields["eyes"] == ["aqua_eyes"]
    assert "detached_sleeves" in fields["top"] and "shirt" not in fields["top"]
    assert DB.character_preset("no_such_character_tag") is None


def test_random():
    ctx = {"character": ["hatsune_miku", "vocaloid"]}
    a = DB.randomize([f for f in DB.field_ids if f != "character"], {}, ctx, "sensitive", seed=7)
    b = DB.randomize([f for f in DB.field_ids if f != "character"], {}, ctx, "sensitive", seed=7)
    assert a == b                                           # same seed, same result
    assert "aqua_hair" in a["hair"]                         # identity comes from the character
    assert "character" not in a

    hair = DB.random_field("hair", 3, {}, "sensitive", random.Random(1))
    assert 1 <= len(hair) <= 3
    colours = set(DB.groups["hair_color"]["tags"])
    multi = DB.groups["hair_color"]["multi"]
    ids = DB.ids(hair)
    assert sum(i in colours for i in ids) <= 1 or any(i in multi for i in ids)

    full = DB.randomize(DB.field_ids, {"character": 0}, {}, "sensitive", seed=3)
    assert full["character"] == [] and full["subject"]


def test_suggest():
    tags = [s["tag"] for s in DB.suggest("pose", {"outfit": ["swimsuit"], "background": ["beach"]})]
    assert tags and len(tags) == len(set(tags))


def test_ratings_are_cumulative():
    assert [rating_level(r) for r in ("general", "sensitive", "questionable", "explicit")] == [0, 1, 2, 3]
    assert rating_level("sfw") == 1 and rating_level("nsfw") == 3     # values saved by older versions
    assert rating_level(None) == rating_level("nonsense") == 1
    sizes = [int(DB._score_posts([], None, r)[1].sum()) for r in ("general", "sensitive", "questionable", "explicit")]
    assert sizes == sorted(sizes) and len(set(sizes)) == 4          # each rating adds pictures
    assert sizes[-1] == DB.p_n                                       # explicit = everything
