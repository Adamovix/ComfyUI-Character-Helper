"""Tag database: autocomplete, character presets and coherent random picks.

Pure Python + numpy, no ComfyUI imports, so it can be used and tested on its
own.  The data files in ``data/`` are produced by ``tools/build_db.py``.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import random
import re
import threading
from typing import NamedTuple

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# How picky a character preset is, per field:
# (minimum % of the character's pictures, minimum share of the field's most
# common tag, maximum number of tags)
PRESET_RULES = {
    "subject": (30, 0.0, 2),
    "hair": (28, 0.0, 5),
    "eyes": (28, 0.0, 2),
    "body": (30, 0.0, 4),
    "outfit": (15, 0.40, 3),
    "top": (10, 0.30, 8),
    "bottom": (10, 0.35, 4),
    "legwear": (10, 0.35, 4),
    "accessories": (10, 0.30, 6),
    "expression": (20, 0.0, 1),
    "pose": (15, 0.0, 2),
    "view": (15, 0.0, 1),
    "background": (15, 0.0, 2),
}
# Every character has hair and eyes: if nothing clears the bar above, lower it.
PRESET_FALLBACK_PERCENT = {"hair": 8, "eyes": 8}

# Fields that say who the character is.  "Randomize" takes these from the
# character, when one is set, instead of rolling them.
IDENTITY_FIELDS = ("subject", "hair", "eyes", "body")

GARMENT_FIELDS = ("outfit", "top", "bottom")

# Danbooru ratings, mildest first.  Picking one includes everything milder.
RATINGS = ("general", "sensitive", "questionable", "explicit")
DEFAULT_RATING = "sensitive"

# The dice only use tags with at least this many posts: rarer ones are mostly
# unknown to image models.  (Autocomplete still offers everything.)
RANDOM_MIN_COUNT = 1000

_WEIGHT = re.compile(r"^\((.+):\s*-?\d*\.?\d+\)$")


class Tag(NamedTuple):
    name: str
    field: int      # index into TagDB.field_ids
    count: int      # posts on Danbooru
    nsfw: float     # share of those posts rated questionable or explicit


class Character(NamedTuple):
    name: str
    count: int
    series: str     # "" when unknown
    aliases: list
    clean: int      # 1 when the statistics come from solo pictures of the usual look


def rating_level(rating):
    """0..3 for general..explicit; anything else counts as the default."""
    return RATINGS.index(rating if rating in RATINGS else DEFAULT_RATING)


def normalize(tag):
    """Turn whatever the user typed into a Danbooru-style tag name."""
    t = str(tag).strip()
    m = _WEIGHT.match(t)
    if m:
        t = m.group(1)
    t = t.replace("\\(", "(").replace("\\)", ")")
    return re.sub(r"\s+", "_", t.strip().lower())


def _match_rank(name, q, tokens):
    """0 = the name or one of its words starts with the query, 1 = contains it, -1 = no match.

    "miku" should find hatsune_miku before the far rarer mikuma_(kancolle), so
    a match at the start of any word counts the same as one at the very start.
    """
    i = name.find(q)
    if i == 0 or (i > 0 and name[i - 1] in "_(-/:"):
        return 0
    if i > 0:
        return 1
    if len(tokens) > 1 and all(t in name for t in tokens):
        return 1
    return -1


def _match(name, aliases, q, tokens):
    """(rank, alias that matched or None); rank -1 when nothing matches."""
    rank = _match_rank(name, q, tokens)
    if rank < 0:
        for alias in aliases:
            if alias.startswith(q):
                return 0, alias
    return rank, None


class TagDB:
    def __init__(self, data_dir=DATA_DIR):
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._loaded = False
        self._pool_ready = False
        self._char_weights = None

    # ------------------------------------------------------------------ load
    def _read_json(self, name):
        with gzip.open(os.path.join(self.data_dir, name), "rt", encoding="utf8") as f:
            return json.load(f)

    def load(self):
        """Tags and characters (about a second; the picture pool loads on first use)."""
        if self._loaded:
            return self
        with self._lock:
            if self._loaded:
                return self
            meta = self._read_json("tags.json.gz")
            self.fields = meta["fields"]                  # [{id, label, count, group}]
            self.field_ids = [f["id"] for f in self.fields]
            self.field_index = {f: i for i, f in enumerate(self.field_ids)}
            self.default_count = {f["id"]: f["count"] for f in self.fields}

            self.tags = [Tag(*t) for t in meta["tags"]]   # most common first
            self.tag_id = {t.name: i for i, t in enumerate(self.tags)}
            self.tag_field = np.array([t.field for t in self.tags], dtype=np.int16)
            self.by_field = {f: [] for f in self.field_ids}
            for i, t in enumerate(self.tags):
                self.by_field[self.field_ids[t.field]].append(i)
            self.aliases = {int(k): v for k, v in meta["aliases"].items()}
            # tag -> the more general tags it implies (white_shirt -> shirt)
            self.implies = {int(k): set(v) for k, v in meta["implies"].items()}
            self.soft = set(meta["soft"])                 # never part of a character preset
            self.colors = tuple(c + "_" for c in meta["colors"])
            # exclusive groups: one hair colour, one hair length, ...
            self.groups = {name: dict(tags=g["tags"], multi=set(g["multi"])) for name, g in meta["groups"].items()}
            self.group_of = {t: name for name, g in self.groups.items() for t in g["tags"]}
            self.series = meta["series"]                  # [name, count], most common first

            self.characters = [Character(*c) for c in self._read_json("characters.json.gz")]
            self.char_id = {c.name: i for i, c in enumerate(self.characters)}
            # per character: (tag, % of their pictures) pairs, most common first
            z = np.load(os.path.join(self.data_dir, "character_tags.npz"))
            self.c_indptr, self.c_tags, self.c_pct = z["indptr"], z["tags"], z["percent"]
            self._loaded = True
        return self

    def _load_pool(self):
        """The pool of real pictures the dice draw from, plus a tag -> pictures index."""
        self.load()
        if self._pool_ready:
            return
        with self._lock:
            if self._pool_ready:
                return
            z = np.load(os.path.join(self.data_dir, "posts.npz"))
            indptr = z["indptr"].astype(np.int64)
            tags = z["tags"].astype(np.int32)
            self.p_indptr, self.p_tags = indptr, tags     # picture p has tags[indptr[p]:indptr[p + 1]]
            self.p_rating = z["rating"]                   # 0..3, see RATINGS
            n = self.p_n = len(indptr) - 1
            post_of = np.repeat(np.arange(n, dtype=np.int32), np.diff(indptr))
            order = np.argsort(tags, kind="stable")
            self.inv_posts = post_of[order]               # pictures grouped by tag
            self.inv_ptr = np.searchsorted(tags[order], np.arange(len(self.tags) + 1))
            df = np.diff(self.inv_ptr).astype(np.float64)
            self.idf = np.log((n + 1.0) / (df + 1.0)).astype(np.float32)   # rare tags tell more
            # how many tags of each field a picture has
            self.p_field_count = np.zeros((len(self.field_ids), n), dtype=np.uint8)
            np.add.at(self.p_field_count, (self.tag_field[tags], post_of), 1)
            self._pool_ready = True

    # -------------------------------------------------------------- helpers
    def ids(self, tags):
        """Ids of the tags the database knows; the rest is skipped."""
        found = (self.tag_id.get(normalize(t)) for t in tags or ())
        return [i for i in found if i is not None]

    def _context_ids(self, context, skip=()):
        out = []
        for field, tags in (context or {}).items():
            if field not in skip:
                out.extend(self.ids(tags))
        return list(dict.fromkeys(out))

    def find_character(self, tags):
        """Index of the first known character among the given tags, or None."""
        self.load()
        for t in tags or ():
            i = self.char_id.get(normalize(t))
            if i is not None:
                return i
        return None

    def _char_pairs(self, ci):
        """[(tag id, percent of the character's pictures)], most common first."""
        a, b = int(self.c_indptr[ci]), int(self.c_indptr[ci + 1])
        return list(zip(self.c_tags[a:b].tolist(), self.c_pct[a:b].tolist()))

    def _char_field_tags(self, ci):
        """field id -> [(tag id, percent)] for a character, most common first."""
        out = {}
        for tid, pct in self._char_pairs(ci):
            out.setdefault(self.field_ids[self.tags[tid].field], []).append((tid, pct))
        return out

    def _is_person(self, ci):
        """Mostly drawn as 1girl / 1boy (not a creature, vehicle or object)."""
        people = set(self.groups["subject"]["tags"])
        return any(t in people and p >= 60 for t, p in self._char_pairs(ci))

    @staticmethod
    def _character_tags(c):
        return [c.name, c.series] if c.series else [c.name]

    def _subject_order(self, tid):
        """Sort key for the subject field: 1girl, solo, then anything more specific."""
        if self.group_of.get(tid) == "subject":
            return 0
        return 1 if self.tags[tid].name == "solo" else 2

    def _dedupe(self, tags):
        """Drop tags that another one already implies (shirt, next to white_shirt)."""
        implied = set()
        for t in tags:
            implied |= self.implies.get(t, set())
        return [t for t in tags if t not in implied]

    # --------------------------------------------------------- autocomplete
    def autocomplete(self, field, query, limit=12):
        """Tags matching what was typed: the edited field's own tags first, then by popularity."""
        self.load()
        q = normalize(query)
        if not q:
            return []
        tokens = [t for t in q.split("_") if t]
        if field == "character":
            return self._autocomplete_character(q, tokens, limit)

        fi = self.field_index.get(field, -1)
        found = []
        for i, t in enumerate(self.tags):
            rank, alias = _match(t.name, self.aliases.get(i, ()), q, tokens)
            if rank >= 0:
                found.append((t.field != fi, rank, -t.count, i, alias))
        found.sort()
        return [dict(tag=self.tags[i].name, count=self.tags[i].count, kind="tag",
                     field=self.field_ids[self.tags[i].field], alias=alias)
                for *_, i, alias in found[:limit]]

    def _autocomplete_character(self, q, tokens, limit):
        found = []
        for i, c in enumerate(self.characters):
            rank, alias = _match(c.name, c.aliases, q, tokens)
            if rank >= 0:
                found.append((rank, -c.count, i, alias))
        found.sort()
        out = [dict(tag=self.characters[i].name, count=self.characters[i].count, kind="character",
                    series=self.characters[i].series, alias=alias) for *_, i, alias in found[:limit]]
        if len(out) < limit:    # room left: a series tag is welcome in the character field too
            series = [(rank, -n, s) for s, n in self.series for rank in [_match_rank(s, q, tokens)] if rank >= 0]
            series.sort()
            out += [dict(tag=s, count=-n, kind="series") for _rank, n, s in series[:limit - len(out)]]
        return out

    def suggest(self, field, context, limit=14, rating=DEFAULT_RATING):
        """Suggestions for an empty input: what goes well with the other fields."""
        self.load()
        context = context or {}
        if field == "character":
            return [dict(tag=c.name, count=c.count, kind="character", series=c.series)
                    for c in self.characters[:limit]]

        out, seen = [], set(self.ids(context.get(field)))

        def add(tid, hint=""):
            if tid not in seen and len(out) < limit:
                seen.add(tid)
                out.append(dict(tag=self.tags[tid].name, count=self.tags[tid].count, kind="tag",
                                field=field, hint=hint))

        # 1. the character's own tags for this field
        ci = self.find_character(context.get("character"))
        if ci is not None:
            for tid, pct in self._char_field_tags(ci).get(field, ()):
                add(tid, f"{pct}%")
        # 2. what the pictures most similar to the other fields have here
        ctx = self._context_ids(context, skip=(field, "character"))
        if ctx and len(out) < limit:
            self._load_pool()
            scores, usable = self._score_posts(ctx, field, rating)
            if usable.any():
                top = self._top_posts(scores, usable, 300)
                counts = np.bincount(np.concatenate([self._post_tags(p) for p in top]),
                                     minlength=len(self.tags))
                counts[self.tag_field != self.field_index[field]] = 0
                for tid in np.argsort(-counts)[:limit * 2]:
                    if counts[tid] < 3:
                        break
                    add(int(tid))
        # 3. the field's most popular tags
        for tid in self.by_field.get(field, ()):
            if len(out) >= limit:
                break
            add(tid)
        return out

    # ------------------------------------------------------ character preset
    def _is_colour_variant(self, tag, parent):
        """white_shirt is a colour variant of shirt; sleeveless_shirt is not."""
        name, pname = self.tags[tag].name, self.tags[parent].name
        return name.endswith("_" + pname) and name[:-len(pname)] in self.colors

    def _select(self, field, items, min_percent=None):
        """Pick the tags of one field for a preset from [(tag id, percent)]."""
        min_pct, rel, max_tags = PRESET_RULES[field]
        if min_percent is not None:
            min_pct = min(min_pct, min_percent)
        pct = {t: p for t, p in items if t not in self.soft}
        cand = [t for t, p in pct.items() if p >= min_pct]

        # one tag per exclusive group (one hair colour, one hair length, ...)
        by_group = {}
        for t in cand:
            g = self.group_of.get(t)
            if g:
                by_group.setdefault(g, []).append(t)
        drop = set()
        for g, members in by_group.items():
            members.sort(key=lambda t: -pct[t])
            best = members[0]
            # prefer the more specific tag when it is nearly as common
            # (very_long_hair over long_hair)
            for t in members[1:]:
                if best in self.implies.get(t, ()) and pct[t] >= 0.5 * pct[best]:
                    best = t
            keep = [best]
            if self.groups[g]["multi"] & set(cand):     # e.g. two-tone hair: allow a second colour
                keep += [t for t in members if t != best
                         and best not in self.implies.get(t, ()) and t not in self.implies.get(best, ())
                         and pct[t] >= 0.4 * pct[best]][:1]
            drop |= set(members) - set(keep)
        cand = [t for t in cand if t not in drop]

        # specific vs generic (white_shirt vs shirt): the specific tag wins when
        # it covers a fair share of the generic one, otherwise it is noise
        drop = set()
        variants_of = {}
        for t in cand:
            for parent in self.implies.get(t, ()):
                if parent in pct and pct[t] < 0.2 * pct[parent]:
                    drop.add(t)
                elif self._is_colour_variant(t, parent):
                    variants_of.setdefault(parent, []).append(t)
        # colour variants of one garment: keep the main colour unless it is a close call
        for variants in variants_of.values():
            variants = sorted((t for t in variants if t not in drop), key=lambda t: -pct[t])
            drop |= {t for t in variants[1:] if pct[t] < 0.75 * pct[variants[0]]}
        cand = self._dedupe([t for t in cand if t not in drop])

        # what is left competes on how typical it is within the field
        if cand:
            top = max(pct[t] for t in cand)
            cand = [t for t in cand if pct[t] >= rel * top]
        cand.sort(key=lambda t: (self.group_of.get(t) != "subject", -pct[t]))
        return cand[:max_tags]

    def character_preset(self, name):
        """The usual look of a character, or None if the character is unknown.

        ``{"character": name, "series": ..., "count": posts, "fields": {field id: [tags]}}``
        """
        self.load()
        ci = self.char_id.get(normalize(name))
        if ci is None:
            return None
        c = self.characters[ci]
        per_field = self._char_field_tags(ci)
        fields = {"character": self._character_tags(c)}
        for field in self.field_ids:
            if field == "character":
                continue
            items = per_field.get(field, [])
            picked = self._select(field, items)
            if not picked and field in PRESET_FALLBACK_PERCENT:
                picked = self._select(field, items, PRESET_FALLBACK_PERCENT[field])
            if field == "subject":
                solo = self.tag_id["solo"]      # the statistics are about solo pictures
                picked = sorted(picked + [solo] * (solo not in picked), key=self._subject_order)
            fields[field] = [self.tags[t].name for t in picked]
        return dict(character=c.name, series=c.series, count=c.count, fields=fields)

    # ------------------------------------------------------------------ dice
    def _post_tags(self, p):
        return self.p_tags[self.p_indptr[p]:self.p_indptr[p + 1]]

    def _score_posts(self, ctx_ids, field, rating, want=1):
        """(how well each pool picture matches the context, which pictures may be used)."""
        scores = np.zeros(self.p_n, dtype=np.float32)
        for t in ctx_ids:
            scores[self.inv_posts[self.inv_ptr[t]:self.inv_ptr[t + 1]]] += self.idf[t]
        usable = self.p_rating <= rating_level(rating)
        if field is not None:
            have = self.p_field_count[self.field_index[field]]
            # pictures that can fill the field on their own keep the pick coherent
            full = usable & (have >= want)
            usable = full if full.sum() >= 200 else usable & (have > 0)
        return scores, usable

    def _top_posts(self, scores, usable, k):
        idx = np.flatnonzero(usable)
        if len(idx) <= k:
            return idx
        return idx[np.argpartition(-scores[idx], k)[:k]]

    def _draw_posts(self, ctx_ids, field, rating, rng, want=1, k=30):
        """Pool pictures in the order they should be tried: good matches first."""
        scores, usable = self._score_posts(ctx_ids, field, rating, want)
        if not usable.any():
            return []
        if ctx_ids:
            # the better a picture matches, the likelier it is drawn - but the
            # candidates stay numerous enough to keep results varied
            top = self._top_posts(scores, usable, 80)
            w = scores[top].astype(np.float64)
            w = np.exp((w - w.max()) / max(1e-6, 0.12 * max(float(w.max()), 1.0)))
            order = rng.choices([int(p) for p in top], weights=list(w), k=k)
        else:
            idx = np.flatnonzero(usable)
            order = [int(idx[rng.randrange(len(idx))]) for _ in range(k)]
        return list(dict.fromkeys(order))

    def _usable(self, tid, rating):
        tag = self.tags[tid]
        if tag.count < RANDOM_MIN_COUNT:
            return False
        # below "questionable", skip tags that mostly appear on questionable/explicit pictures
        return rating_level(rating) >= 2 or tag.nsfw < 0.5

    def _conflicts(self, tid, picked):
        """Would adding ``tid`` contradict or merely repeat what is picked?"""
        if tid in picked:
            return True
        g = self.group_of.get(tid)
        multi = self.groups[g]["multi"] & set(picked) if g else ()
        for t in picked:
            if g and not multi and self.group_of.get(t) == g:
                return True
            if tid in self.implies.get(t, ()) or t in self.implies.get(tid, ()):
                return True
        return False

    def _take(self, post, field, count, rating, rng, picked=()):
        """``picked`` plus tags of one field from one pool picture, up to ``count`` in total."""
        fi = self.field_index[field]
        tags = [int(t) for t in self._post_tags(post) if self.tag_field[t] == fi and self._usable(int(t), rating)]
        tags = self._dedupe(tags)
        rng.shuffle(tags)
        if field == "subject":
            tags.sort(key=self._subject_order)
        out = list(picked)
        for t in tags:
            if len(out) >= count:
                break
            if not self._conflicts(t, out):
                out.append(t)
        return out

    def _names(self, ids, field):
        """Tag names in the order they are shown: most common first."""
        key = self._subject_order if field == "subject" else (lambda t: -self.tags[t].count)
        return [self.tags[t].name for t in sorted(ids, key=key)]

    def random_character(self, rng=random):
        self.load()
        if self._char_weights is None:
            # well-known characters with clean statistics, weighted by popularity
            self._char_weights = [math.sqrt(c.count) if c.count >= 300 and c.clean and self._is_person(i) else 0.0
                                  for i, c in enumerate(self.characters)]
        return self._character_tags(rng.choices(self.characters, weights=self._char_weights, k=1)[0])

    def random_field(self, field, count, context, rating=DEFAULT_RATING, rng=random):
        """Up to ``count`` tags for one field that fit the rest of the prompt."""
        count = max(0, int(count))
        if count == 0:
            return []
        if field == "character":
            return self.random_character(rng)
        self._load_pool()
        ctx = self._context_ids(context, skip=(field, "character"))
        picked = []
        for post in self._draw_posts(ctx, field, rating, rng, want=count):
            picked = self._take(post, field, count, rating, rng, picked)
            if len(picked) >= count:
                break
        return self._names(picked, field)

    def randomize(self, fields, counts, context, rating=DEFAULT_RATING, seed=None):
        """Roll the given fields; ``context`` holds the current tags of all fields.

        One field is rolled on its own.  Several fields are taken from one real
        picture where possible, so that outfit, pose and scene belong together;
        gaps are filled from other pictures that match what has been chosen so
        far.  With a character set, their identity fields are not rolled but
        taken from the character.
        """
        self.load()
        rng = random.Random(seed) if seed is not None else random
        todo = [f for f in self.field_ids if f in fields]
        context = {f: list(tags) for f, tags in (context or {}).items() if f not in todo}
        counts = counts or {}
        out = {}

        def put(field, tags):
            out[field] = context[field] = tags

        def count_of(field):
            return max(0, int(counts.get(field, self.default_count[field])))

        if len(todo) == 1:
            put(todo[0], self.random_field(todo[0], count_of(todo[0]), context, rating, rng))
            return out

        if "character" in todo:
            put("character", self.random_character(rng) if count_of("character") > 0 else [])
            todo.remove("character")
        ci = self.find_character(context.get("character"))
        if ci is not None:
            preset = self.character_preset(self.characters[ci].name)["fields"]
            for f in [f for f in todo if f in IDENTITY_FIELDS]:
                put(f, preset[f])
                todo.remove(f)
        if not todo:
            return out

        self._load_pool()
        posts = self._draw_posts(self._context_ids(context, skip=("character",)), None, rating, rng, k=1)
        anchor = posts[0] if posts else None
        # a picture that is already dressed (a dress, or a shirt and a skirt)
        # should not get more clothes from elsewhere
        dressed = anchor is not None and sum(
            int(self.p_field_count[self.field_index[g]][anchor]) for g in GARMENT_FIELDS) >= 2
        for f in todo:
            n = count_of(f)
            picked = self._take(anchor, f, n, rating, rng) if anchor is not None and n else []
            if picked or n == 0 or (dressed and f in GARMENT_FIELDS):
                put(f, self._names(picked, f))
            else:   # the picture says nothing about this field: ask similar ones
                put(f, self.random_field(f, n, context, rating, rng))
        return out


DB = TagDB()
