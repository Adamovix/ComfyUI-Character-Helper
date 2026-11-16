"""Run with:  python -m pytest tests"""

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nodes import format_tag, join_fields, parse_fields  # noqa: E402
from tagdb import DB, RATINGS, normalize, rating_level  # noqa: E402

DB.load()
ALL_BUT_CHARACTER = [f for f in DB.field_ids if f != "character"]


def test_format_tag():
    assert format_tag("aqua_hair") == "aqua hair"
    assert format_tag("aqua_hair", spaces=False) == "aqua_hair"
    assert format_tag("ganyu_(genshin_impact)") == r"ganyu \(genshin impact\)"
    assert format_tag("ganyu_(genshin_impact)", escape=False) == "ganyu (genshin impact)"
    assert format_tag("^_^") == "^_^"                       # emoticons keep their underscore
    assert format_tag("(aqua_hair:1.2)") == "(aqua hair:1.2)"  # weights survive
    assert format_tag(r"ganyu \(genshin impact\)") == r"ganyu \(genshin impact\)"


def test_join_fields():
    value = json.dumps({"fields": {"character": ["hatsune_miku"], "subject": ["1girl"],
                                   "hair": ["aqua_hair", " twintails ", ""], "eyes": ["Aqua Hair"]}})
    # subject leads, then the fields in the order given; blanks and repeats are dropped
    assert join_fields(parse_fields(value)) == "1girl, hatsune miku, aqua hair, twintails"
    assert join_fields(parse_fields(value), spaces=False) == "1girl, hatsune_miku, aqua_hair, twintails"
    assert join_fields(parse_fields("")) == ""
    assert join_fields(parse_fields({"fields": {"hair": ["a"]}})) == "a"


def test_normalize():
    assert normalize(" Aqua Hair ") == "aqua_hair"
    assert normalize(r"ganyu \(genshin impact\)") == "ganyu_(genshin_impact)"
    assert normalize("(long hair:1.3)") == "long_hair"


def test_autocomplete():
    assert DB.autocomplete("character", "hatsune")[0]["tag"] == "hatsune_miku"
    assert DB.autocomplete("character", "miku")[0]["tag"] == "hatsune_miku"      # a later word matches too
    assert DB.autocomplete("character", "Hatsune Miku")[0]["series"] == "vocaloid"
    assert DB.autocomplete("legwear", "stockings")[0] == dict(
        DB.autocomplete("legwear", "thighhighs")[0], alias="stockings")          # found through an alias
    top = DB.autocomplete("top", "leather")
    assert top[0]["tag"] == "leather_jacket" and top[0]["field"] == "top"        # own field first
    assert DB.autocomplete("hair", "") == []


def test_character_preset():
    fields = DB.character_preset("hatsune_miku")["fields"]
    assert list(fields) == DB.field_ids
    assert fields["character"] == ["hatsune_miku", "vocaloid"]
    assert fields["subject"] == ["1girl", "solo"]
    assert {"twintails", "aqua_hair", "very_long_hair"} <= set(fields["hair"])
    assert "long_hair" not in fields["hair"]                # implied by very_long_hair
    assert fields["eyes"] == ["aqua_eyes"]
    assert "detached_sleeves" in fields["top"] and "shirt" not in fields["top"]
    assert DB.character_preset("no_such_character_tag") is None


def test_randomize():
    miku = {"character": ["hatsune_miku", "vocaloid"]}
    a = DB.randomize(ALL_BUT_CHARACTER, {}, miku, seed=7)
    assert a == DB.randomize(ALL_BUT_CHARACTER, {}, miku, seed=7)     # same seed, same result
    assert "aqua_hair" in a["hair"]                         # identity comes from the character
    assert "character" not in a                             # only the requested fields come back

    original = DB.randomize(DB.field_ids, {"character": 0}, {}, seed=3)
    assert original["character"] == [] and original["subject"][0] in ("1girl", "1boy", "1other")

    # one field on its own: the character dice does not touch anything else
    assert list(DB.randomize(["character"], {}, {"hair": ["red_hair"]}, seed=1)) == ["character"]


def test_random_field():
    hair = DB.random_field("hair", 3, {}, rng=random.Random(1))
    assert 1 <= len(hair) <= 3
    ids = DB.ids(hair)
    colours = set(DB.groups["hair_color"]["tags"])
    assert sum(i in colours for i in ids) <= 1 or DB.groups["hair_color"]["multi"] & set(ids)
    assert DB.random_field("hair", 0, {}) == []


def test_ratings_are_cumulative():
    assert [rating_level(r) for r in RATINGS] == [0, 1, 2, 3]
    assert rating_level(None) == rating_level("nonsense") == 1          # unknown -> the default
    DB._load_pool()
    sizes = [int(DB._score_posts([], None, r)[1].sum()) for r in RATINGS]
    assert sizes == sorted(sizes) and len(set(sizes)) == 4          # each rating adds pictures
    assert sizes[-1] == DB.p_n                                       # explicit = everything


def test_suggest():
    tags = [s["tag"] for s in DB.suggest("pose", {"outfit": ["swimsuit"], "background": ["beach"]})]
    assert tags and len(tags) == len(set(tags))
    top = DB.suggest("top", DB.character_preset("hatsune_miku")["fields"])
    assert top[0]["hint"].endswith("%")                      # the character's own tags come first


def test_example_workflow_matches_the_widget_format():
    with open(os.path.join(ROOT, "example_workflows", "character_helper.json"), encoding="utf8") as f:
        workflow = json.load(f)
    node = next(n for n in workflow["nodes"] if n["type"] == "CharacterHelper")
    state = json.loads(node["widgets_values"][0])
    assert list(state["fields"]) == DB.field_ids and state["counts"] == DB.default_count
    assert state["rating"] in RATINGS
    assert join_fields(parse_fields(node["widgets_values"][0])).startswith("1girl, solo, hijiri byakuren, touhou")
