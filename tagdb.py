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

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Fields a character preset is allowed to fill, and how picky it is:
# field -> (minimum % of the character's pictures, minimum share of the field's
# most common tag, maximum number of tags)
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

# Fields that describe who the character is.  "Randomize all" takes these from
# the character when one is set instead of rolling them.
IDENTITY_FIELDS = ("subject", "hair", "eyes", "body")

GARMENT_FIELDS = ("outfit", "top", "bottom")

RATING_MODES = {"sfw": (0, 1), "any": (0, 1, 2, 3), "nsfw": (2, 3)}

# The dice only use tags with at least this many posts: rarer ones are mostly
# unknown to image models.  (Autocomplete still offers everything.)
RANDOM_MIN_COUNT = 1000

_WEIGHT = re.compile(r"^\((.+):\s*-?\d*\.?\d+\)$")


def normalize(tag):
    """Turn whatever the user typed into a Danbooru-style tag name."""
    t = str(tag).strip()
    m = _WEIGHT.match(t)
    if m:
        t = m.group(1)
    t = t.replace("\\(", "(").replace("\\)", ")")
    return re.sub(r"\s+", "_", t.strip().lower())


def _match_rank(name, q, tokens):
    """0 = the name or one of its words starts with the query, 2 = contains it.

    "miku" should find hatsune_miku before the far rarer mikuma_(kancolle), so
    a match at the start of any word counts the same as one at the very start.
    """
    i = name.find(q)
    if i == 0 or (i > 0 and name[i - 1] in "_(-/:"):
        return 0
    if i > 0:
        return 2
    if len(tokens) > 1 and all(t in name for t in tokens):
        return 2
    return -1


class TagDB:
    def __init__(self, data_dir=DATA_DIR):
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._loaded = False
        self._pool_ready = False

    # ------------------------------------------------------------------ load
    def _read(self, name):
        with gzip.open(os.path.join(self.data_dir, name), "rt", encoding="utf8") as f:
            return json.load(f)

    def load(self):
        if self._loaded:
            return self
        with self._lock:
            if self._loaded:
                return self
            meta = self._read("tags.json.gz")
            self.fields = meta["fields"]
            self.field_ids = [f["id"] for f in self.fields]
            self.field_index = {f: i for i, f in enumerate(self.field_ids)}
            self.tags = meta["tags"]                      # [name, field index, count, nsfw share]
            self.tag_id = {t[0]: i for i, t in enumerate(self.tags)}
            self.tag_field = np.array([t[1] for t in self.tags], dtype=np.int16)
            self.implies = {int(k): set(v) for k, v in meta["implies"].items()}
            self.aliases = {int(k): v for k, v in meta["aliases"].items()}
            self.series = meta["series"]                  # [name, count]
            self.series_set = {s[0] for s in self.series}
            self.soft = set(meta.get("soft", ()))        # too generic for a character preset
            self.colors = tuple(c + "_" for c in meta.get("colors", ()))
            self.groups = meta["groups"]
            self.group_of = {}
            for gname, g in self.groups.items():
                g["multi"] = set(g["multi"])
                for t in g["tags"]:
                    self.group_of[t] = gname
            self.by_field = {f: [] for f in self.field_ids}
            for i, t in enumerate(self.tags):             # tags are sorted by count already
                self.by_field[self.field_ids[t[1]]].append(i)
            self.characters = self._read("characters.json.gz")  # [name, count, series, aliases, clean]
            z = np.load(os.path.join(self.data_dir, "character_tags.npz"))
            self.c_indptr, self.c_tags, self.c_pct = z["indptr"], z["tags"], z["percent"]
            self.char_id = {c[0]: i for i, c in enumerate(self.characters)}
            self.built = meta.get("built", "")
            self._loaded = True
        return self

    def _load_pool(self):
        self.load()
        if self._pool_ready:
            return
        with self._lock:
            if self._pool_ready:
                return
            z = np.load(os.path.join(self.data_dir, "posts.npz"))
            indptr = z["indptr"].astype(np.int64)
            tags = z["tags"].astype(np.int32)
            self.p_indptr, self.p_tags, self.p_rating = indptr, tags, z["rating"]
            n = len(indptr) - 1
            self.p_n = n
            post_of = np.repeat(np.arange(n, dtype=np.int32), np.diff(indptr))
            order = np.argsort(tags, kind="stable")
            self.inv_posts = post_of[order]               # posts grouped by tag
            self.inv_ptr = np.searchsorted(tags[order], np.arange(len(self.tags) + 1))
            df = np.diff(self.inv_ptr).astype(np.float64)
            self.idf = np.log((n + 1.0) / (df + 1.0)).astype(np.float32)
            # how many tags of each field a post has
            self.p_field_count = np.zeros((len(self.field_ids), n), dtype=np.uint8)
            np.add.at(self.p_field_count, (self.tag_field[tags], post_of), 1)
            self._pool_ready = True

    # -------------------------------------------------------------- helpers
    def ids(self, tags):
        out = []
        for t in tags or ():
            i = self.tag_id.get(normalize(t))
            if i is not None:
                out.append(i)
        return out

    def _context_ids(self, context, skip=()):
        out = []
        for field, tags in (context or {}).items():
            if field in skip:
                continue
            out.extend(self.ids(tags))
        return list(dict.fromkeys(out))

    def find_character(self, tags):
        """First known character among the given tags, as an index (or None)."""
        self.load()
        for t in tags or ():
            i = self.char_id.get(normalize(t))
            if i is not None:
                return i
        return None

    def _is_person(self, ci):
        """Mostly drawn as 1girl / 1boy (not a creature, vehicle or object)."""
        people = set(self.groups["subject"]["tags"])
        return any(t in people and p >= 60 for t, p in self._char_pairs(ci))

    def _char_pairs(self, ci):
        """[(tag id, percent of the character's pictures)], most common first."""
        a, b = int(self.c_indptr[ci]), int(self.c_indptr[ci + 1])
        return list(zip(self.c_tags[a:b].tolist(), self.c_pct[a:b].tolist()))

    def _char_field_tags(self, ci):
        """field id -> [(tag id, percent)] for a character, most common first."""
        out = {}
        for tid, pct in self._char_pairs(ci):
            out.setdefault(self.field_ids[self.tags[tid][1]], []).append((tid, pct))
        return out

    # --------------------------------------------------------- autocomplete
    def autocomplete(self, field, query, limit=12):
        self.load()
        q = normalize(query)
        if not q:
            return []
        tokens = [t for t in q.split("_") if t]
        found = []
        if field == "character":
            for i, c in enumerate(self.characters):
                r = _match_rank(c[0], q, tokens)
                alias = None
                if r < 0:
                    for a in c[3]:
                        if a.startswith(q):
                            r, alias = 0, a
                            break
                if r >= 0:
                    found.append((r, -c[1], i, alias))
            found.sort()
            out = [dict(tag=self.characters[i][0], count=self.characters[i][1], kind="character",
                        series=self.characters[i][2], alias=alias) for _r, _n, i, alias in found[:limit]]
            if len(out) < limit:
                extra = [(r, -n, s) for s, n in self.series for r in [_match_rank(s, q, tokens)] if r >= 0]
                extra.sort()
                out += [dict(tag=s, count=-n, kind="series") for _r, n, s in extra[:limit - len(out)]]
            return out

        fi = self.field_index.get(field, -1)
        for i, t in enumerate(self.tags):
            r = _match_rank(t[0], q, tokens)
            alias = None
            if r < 0:
                for a in self.aliases.get(i, ()):
                    if a.startswith(q):
                        r, alias = 0, a
                        break
            if r >= 0:
                # tags of the field being edited come first, then everything else
                found.append((0 if t[1] == fi else 1, r, -t[2], i, alias))
        found.sort()
        return [dict(tag=self.tags[i][0], count=self.tags[i][2], kind="tag",
                     field=self.field_ids[self.tags[i][1]], alias=alias)
                for _o, _r, _n, i, alias in found[:limit]]

    def suggest(self, field, context, limit=14, rating="sfw"):
        """Suggestions for an empty input: what goes well with the other fields."""
        self.load()
        context = context or {}
        have = set(self.ids(context.get(field)))
        out, seen = [], set(have)

        def add(tid, hint):
            if tid not in seen and len(out) < limit:
                seen.add(tid)
                out.append(dict(tag=self.tags[tid][0], count=self.tags[tid][2], kind="tag",
                                field=field, hint=hint))

        if field == "character":
            return [dict(tag=c[0], count=c[1], kind="character", series=c[2]) for c in self.characters[:limit]]

        ci = self.find_character(context.get("character"))
        if ci is not None:
            for tid, pct in self._char_field_tags(ci).get(field, ()):
                add(tid, f"{pct}%")
        ctx = self._context_ids(context, skip=(field, "character"))
        if ctx and len(out) < limit:
            self._load_pool()
            scores, mask = self._score_posts(ctx, field, rating)
            if mask.any():
                top = self._top_posts(scores, mask, 300)
                counts = np.bincount(np.concatenate([self._post_tags(p) for p in top]),
                                     minlength=len(self.tags))
                fi = self.field_index[field]
                counts[self.tag_field != fi] = 0
                for tid in np.argsort(-counts)[:limit * 2]:
                    if counts[tid] < 3:
                        break
                    add(int(tid), "")
        for tid in self.by_field.get(field, ()):
            if len(out) >= limit:
                break
            add(tid, "")
        return out

    # ------------------------------------------------------ character preset
    def _dedupe(self, picked):
        """Drop tags that another picked tag already implies (shirt < white_shirt)."""
        implied = set()
        for t in picked:
            implied |= self.implies.get(t, set())
        return [t for t in picked if t not in implied]

    def _is_colour_variant(self, tag, parent):
        """white_shirt is a colour variant of shirt; sleeveless_shirt is not."""
        name, pname = self.tags[tag][0], self.tags[parent][0]
        return name.endswith("_" + pname) and name[:-len(pname)] in self.colors

    def _select(self, field, items, strict=True):
        """Pick the tags of one field for a preset from [(tag id, percent)]."""
        min_pct, rel, max_tags = PRESET_RULES[field]
        if not strict:
            min_pct = min(min_pct, 8)
        items = [(t, p) for t, p in items if t not in self.soft]
        if not items:
            return []
        pct = dict(items)
        cand = [t for t, p in items if p >= min_pct]

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
            if self.groups[g]["multi"] & set(cand):
                keep += [t for t in members if t != best
                         and best not in self.implies.get(t, ()) and t not in self.implies.get(best, ())
                         and pct[t] >= 0.4 * pct[best]][:1]
            drop |= set(members) - set(keep)
        cand = [t for t in cand if t not in drop]

        # specific vs generic (white_shirt vs shirt): the specific tag wins when
        # it covers a fair share of the generic one, otherwise it is noise
        drop = set()
        kids = {}
        for t in cand:
            for parent in self.implies.get(t, ()):
                if parent in pct and pct[t] < 0.2 * pct[parent]:
                    drop.add(t)
                elif self._is_colour_variant(t, parent):
                    kids.setdefault(parent, []).append(t)
        # colour variants of one garment: keep the main colour unless it is a close call
        for parent, variants in kids.items():
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
        """The usual look of a character: {"character": [...], "hair": [...], ...}"""
        self.load()
        ci = self.char_id.get(normalize(name))
        if ci is None:
            return None
        c = self.characters[ci]
        per_field = self._char_field_tags(ci)
        fields = {"character": [c[0]] + ([c[2]] if c[2] else [])}
        for field in self.field_ids:
            if field == "character":
                continue
            picked = self._select(field, per_field.get(field, []))
            if not picked and field in ("hair", "eyes"):
                picked = self._select(field, per_field.get(field, []), strict=False)
            fields[field] = [self.tags[t][0] for t in picked]
        if "solo" not in fields["subject"]:
            fields["subject"].append("solo")
        fields["subject"].sort(key=lambda t: t == "solo")   # 1girl, solo
        return dict(character=c[0], series=c[2], count=c[1], fields=fields)

    # ---------------------------------------------------------------- random
    def _post_tags(self, p):
        return self.p_tags[self.p_indptr[p]:self.p_indptr[p + 1]]

    def _score_posts(self, ctx_ids, field, rating, want=1):
        """How well each pool post matches the context + which posts are usable."""
        scores = np.zeros(self.p_n, dtype=np.float32)
        for t in ctx_ids:
            scores[self.inv_posts[self.inv_ptr[t]:self.inv_ptr[t + 1]]] += self.idf[t]
        mask = np.isin(self.p_rating, RATING_MODES.get(rating, RATING_MODES["sfw"]))
        if field is not None:
            have = self.p_field_count[self.field_index[field]]
            # posts that can fill the field on their own keep the pick coherent
            full = mask & (have >= want)
            mask = full if full.sum() >= 200 else mask & (have > 0)
        return scores, mask

    def _top_posts(self, scores, mask, k):
        idx = np.flatnonzero(mask)
        if len(idx) <= k:
            return idx
        part = np.argpartition(-scores[idx], k)[:k]
        return idx[part]

    def _draw_posts(self, ctx_ids, field, rating, rng, want=1, k=30):
        """Pool posts in the order they should be tried: good matches first."""
        scores, mask = self._score_posts(ctx_ids, field, rating, want)
        if not mask.any():
            return []
        if ctx_ids:
            # the better a post matches, the likelier it is drawn - but the
            # candidates stay numerous enough to keep results varied
            top = self._top_posts(scores, mask, 80)
            w = scores[top].astype(np.float64)
            w = np.exp((w - w.max()) / max(1e-6, 0.12 * max(float(w.max()), 1.0)))
            order = rng.choices([int(p) for p in top], weights=list(w), k=k)
        else:
            idx = np.flatnonzero(mask)
            order = [int(idx[rng.randrange(len(idx))]) for _ in range(k)]
        return list(dict.fromkeys(order))

    def _usable(self, tid, rating):
        t = self.tags[tid]
        if t[2] < RANDOM_MIN_COUNT:          # too rare for image models to know
            return False
        return rating != "sfw" or t[3] < 0.5

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
        """Up to ``count`` tags of one field from one pool post."""
        fi = self.field_index[field]
        tags = [int(t) for t in self._post_tags(post) if self.tag_field[t] == fi and self._usable(int(t), rating)]
        tags = self._dedupe(tags)
        rng.shuffle(tags)
        if field == "subject":   # "1girl, solo" before anything more specific
            tags.sort(key=lambda t: (self.group_of.get(t) != "subject", self.tags[t][0] != "solo"))
        out = list(picked)
        for t in tags:
            if len(out) >= count:
                break
            if not self._conflicts(t, out):
                out.append(t)
        return out

    def _names(self, ids, field):
        if field == "subject":
            ids = sorted(ids, key=lambda t: (self.group_of.get(t) != "subject", self.tags[t][0] == "solo"))
        else:
            ids = sorted(ids, key=lambda t: -self.tags[t][2])
        return [self.tags[t][0] for t in ids]

    def random_field(self, field, count, context, rating="sfw", rng=None):
        """``count`` tags for one field that fit the rest of the prompt."""
        rng = rng or random
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

    def random_character(self, rng=None):
        rng = rng or random
        self.load()
        if not hasattr(self, "_char_weights"):
            # well-known characters with clean statistics, weighted by popularity
            self._char_weights = [math.sqrt(c[1]) if c[1] >= 300 and c[4] and self._is_person(i) else 0.0
                                  for i, c in enumerate(self.characters)]
        c = rng.choices(self.characters, weights=self._char_weights, k=1)[0]
        return [c[0]] + ([c[2]] if c[2] else [])

    def randomize(self, fields, counts, context, rating="sfw", seed=None):
        """Fill several fields at once.  ``context`` holds the fields to keep.

        All fields are taken from one real picture where possible, so that the
        outfit, pose and scene belong together; gaps are filled from other
        pictures that match what has been chosen so far.
        """
        self.load()
        rng = random.Random(seed) if seed is not None else random
        context = {k: list(v) for k, v in (context or {}).items()}
        counts = counts or {}
        out = {}
        todo = [f for f in self.field_ids if f in fields]
        for f in todo:
            context.pop(f, None)
        if not todo:
            return out

        def put(field, tags):
            out[field] = tags
            context[field] = tags

        def count_of(field):
            default = next(x["count"] for x in self.fields if x["id"] == field)
            return max(0, int(counts.get(field, default)))

        if "character" in todo:
            put("character", self.random_character(rng) if count_of("character") > 0 else [])
        rest = [f for f in todo if f != "character"]
        if len(todo) == 1:
            for f in rest:
                put(f, self.random_field(f, count_of(f), context, rating, rng))
            return out

        ci = self.find_character(context.get("character"))
        if ci is not None:
            preset = self.character_preset(self.characters[ci][0])["fields"]
            for f in [f for f in rest if f in IDENTITY_FIELDS]:
                put(f, preset[f])
                rest.remove(f)
        if not rest:
            return out

        self._load_pool()
        anchor = None
        posts = self._draw_posts(self._context_ids(context, skip=("character",)), None, rating, rng, k=1)
        if posts:
            anchor = posts[0]
        # a picture that is already dressed (a dress, or a shirt and a skirt)
        # should not get more clothes from elsewhere
        dressed = anchor is not None and sum(
            int(self.p_field_count[self.field_index[g]][anchor]) for g in GARMENT_FIELDS) >= 2
        for f in rest:
            n = count_of(f)
            picked = self._take(anchor, f, n, rating, rng) if anchor is not None and n else []
            if picked or n == 0 or (dressed and f in GARMENT_FIELDS):
                put(f, self._names(picked, f))
            else:   # the picture says nothing about this field: ask similar ones
                put(f, self.random_field(f, n, context, rating, rng))
        return out


DB = TagDB()
