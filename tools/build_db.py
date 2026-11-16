#!/usr/bin/env python3
"""Build the tag database shipped in ``data/``.

You do NOT need to run this to use the node - the built database is committed
to the repository. Run it only if you want to refresh the data.

    pip install pyarrow numpy
    python tools/build_db.py --work /some/scratch/dir            # everything
    python tools/build_db.py --work /some/scratch/dir --stage build   # re-run last step only

Stages
------
download  Fetches the source datasets from Hugging Face (about 4.2 GB).
scan      One pass over 11.7M Danbooru posts: tag counts, per-character tag
          statistics and a pool of well-tagged solo posts.  (~10 min, ~8 GB RAM)
build     Sorts tags into fields and writes the files in ``data/``.

Sources (see README.md for licences)
------------------------------------
* nyanko-devs/danbooru2026          post metadata (tags, rating, score)
* Mexes/danbooru-tag-taxonomy       tag -> slot classification, aliases
* itterative/danbooru_wikis_full    Danbooru wiki tag groups, implications
* Jio7/danbooru-tags-classified     second opinion for tags the taxonomy skips
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import pickle
import random
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from field_rules import (  # noqa: E402
    ALT_LOOK_TAGS,
    COLORS,
    EXCLUSIVE_GROUPS,
    FIELDS,
    NOT_IDENTITY,
    POOL_EXCLUDE_TAGS,
    classify_tag,
)

HF = "https://huggingface.co/datasets/{repo}/resolve/main/{path}"
SOURCES = {
    "posts.parquet": ("nyanko-devs/danbooru2026", "metadata/posts-snapshot.parquet"),
    "taxonomy.parquet": ("Mexes/danbooru-tag-taxonomy", "tags.parquet"),
    "tag_groups.parquet": ("itterative/danbooru_wikis_full", "tag_groups.parquet"),
    "tag_implications.parquet": ("itterative/danbooru_wikis_full", "tag_implications.parquet"),
}
JIO7_CATEGORIES = ("attire", "feature", "action", "setting", "expression", "object")
SOURCES.update({f"jio7_{c}.csv": ("Jio7/danbooru-tags-classified", f"{c}.csv") for c in JIO7_CATEGORIES})

# ---------------------------------------------------------------- tuning knobs
MIN_GENERAL_COUNT = 150     # general tags rarer than this are dropped
MIN_CHARACTER_COUNT = 40    # characters with fewer posts are dropped
MIN_SERIES_COUNT = 40
CANON_MIN_POSTS = 25        # below this, fall back to "any post" statistics
CHAR_TAGS_PER_FIELD = 14    # how many candidate tags are stored per field
CHAR_MIN_FREQ = 0.08        # ...and their minimum frequency
POOL_MIN_SCORE = 25
POOL_MIN_GENERAL_TAGS = 22
POOL_SIZE = {"g": 60000, "s": 60000, "q": 20000, "e": 20000}

COUNT_TAGS = ("1girl", "1boy", "1other")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------- download
def stage_download(work):
    os.makedirs(work, exist_ok=True)
    for name, (repo, path) in SOURCES.items():
        dest = os.path.join(work, name)
        if os.path.exists(dest):
            log("have", name)
            continue
        url = HF.format(repo=repo, path=path)
        log("downloading", url)
        tmp = dest + ".part"
        with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 22)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, dest)


# --------------------------------------------------------------- implications
def load_implications(work):
    import pyarrow.parquet as pq
    imp = pq.read_table(os.path.join(work, "tag_implications.parquet")).to_pydict()
    implies = collections.defaultdict(set)
    for a, b, st in zip(imp["antecedent_name"], imp["consequent_name"], imp["status"]):
        if st == "active":
            implies[a].add(b)
    return implies


def transitive(implies):
    """tag -> every tag it implies, directly or indirectly."""
    out = {}

    def walk(t, seen):
        for u in implies.get(t, ()):
            if u not in seen:
                seen.add(u)
                walk(u, seen)
        return seen

    for t in list(implies):
        out[t] = walk(t, set())
    return out


# ----------------------------------------------------------------------- scan
def base_name(character_tag):
    """``artoria_pendragon_(swimsuit_archer)_(fate)`` -> ``artoria_pendragon``.

    Variants of one character share this prefix, which lets us recognise posts
    that show a single person even though they carry several character tags.
    """
    return character_tag.split("_(", 1)[0]


_EMPTY = frozenset()
_COLS = ["id", "score", "rating", "is_deleted", "tag_string_general",
         "tag_string_character", "tag_string_copyright"]


def _count_chunk(args):
    path, groups = args
    import pyarrow.parquet as pq
    f = pq.ParquetFile(path)
    gen, gen_nsfw, cha, cop = (collections.Counter() for _ in range(4))
    for g in groups:
        d = f.read_row_group(g, columns=_COLS).to_pydict()
        for deleted, rating, tg, tc, tp in zip(d["is_deleted"], d["rating"], d["tag_string_general"],
                                               d["tag_string_character"], d["tag_string_copyright"]):
            if deleted:
                continue
            tags = tg.split(" ") if tg else []
            gen.update(tags)
            if rating in ("q", "e"):
                gen_nsfw.update(tags)
            if tc:
                cha.update(tc.split(" "))
            if tp:
                cop.update(tp.split(" "))
    return gen, gen_nsfw, cha, cop


def _stats_chunk(args):
    path, groups, gen_ids, char_ids, char_implies = args
    import pyarrow.parquet as pq
    f = pq.ParquetFile(path)
    canon, anyp = {}, {}        # char id -> [n_posts, Counter(tag id)]
    series = {}                 # char id -> Counter(copyright tag)
    pool = []
    alt = ALT_LOOK_TAGS
    excl = POOL_EXCLUDE_TAGS
    for g in groups:
        d = f.read_row_group(g, columns=_COLS).to_pydict()
        for pid, score, rating, deleted, tg, tc, tp in zip(
                d["id"], d["score"], d["rating"], d["is_deleted"], d["tag_string_general"],
                d["tag_string_character"], d["tag_string_copyright"]):
            if deleted or not tg:
                continue
            tags = tg.split(" ")
            tagset = set(tags)
            solo = "solo" in tagset
            plain = not (tagset & excl)
            ids = None
            if tc:
                names = tc.split(" ")
                chars = [char_ids[c] for c in names if c in char_ids]
                if chars:
                    ids = [gen_ids[t] for t in tags if t in gen_ids]
                    # saber_(fate) implies artoria_pendragon_(fate): still one person
                    implied = set()
                    for c in names:
                        implied |= char_implies.get(c, _EMPTY)
                    one_person = len({base_name(c) for c in names if c not in implied}) == 1
                    is_canon = (solo and one_person and plain and rating in ("g", "s")
                                and not (tagset & alt))
                    for c in chars:
                        e = anyp.get(c)
                        if e is None:
                            e = anyp[c] = [0, collections.Counter()]
                        e[0] += 1
                        e[1].update(ids)
                        if is_canon:
                            e = canon.get(c)
                            if e is None:
                                e = canon[c] = [0, collections.Counter()]
                            e[0] += 1
                            e[1].update(ids)
                        if tp:
                            s = series.get(c)
                            if s is None:
                                s = series[c] = collections.Counter()
                            s.update(tp.split(" "))
            if (solo and plain and score is not None and score >= POOL_MIN_SCORE
                    and len(tags) >= POOL_MIN_GENERAL_TAGS
                    and sum(t in tagset for t in COUNT_TAGS) == 1):
                if ids is None:
                    ids = [gen_ids[t] for t in tags if t in gen_ids]
                pool.append((pid, score, rating, ids))
    return canon, anyp, series, pool


def stage_scan(work, jobs):
    import multiprocessing as mp
    import pyarrow.parquet as pq

    path = os.path.join(work, "posts.parquet")
    n_groups = pq.ParquetFile(path).metadata.num_row_groups
    chunks = [list(range(i, min(i + 3, n_groups))) for i in range(0, n_groups, 3)]

    log("pass 1/2: tag counts")
    gen, gen_nsfw, cha, cop = (collections.Counter() for _ in range(4))
    with mp.Pool(jobs) as pool:
        for i, r in enumerate(pool.imap_unordered(_count_chunk, [(path, c) for c in chunks])):
            for total, part in zip((gen, gen_nsfw, cha, cop), r):
                total.update(part)
            if i % 10 == 0:
                log(f"  {i + 1}/{len(chunks)}")

    gen_names = [t for t, n in gen.most_common() if n >= MIN_GENERAL_COUNT]
    char_names = [t for t, n in cha.most_common() if n >= MIN_CHARACTER_COUNT]
    gen_ids = {t: i for i, t in enumerate(gen_names)}
    char_ids = {t: i for i, t in enumerate(char_names)}
    log(f"  {len(gen_names)} general tags, {len(char_names)} characters")

    implied = transitive(load_implications(work))
    char_implies = {c: frozenset(u for u in implied.get(c, ()) if u in cha) for c in cha if c in implied}

    log("pass 2/2: character statistics + post pool")
    canon, anyp, series, pool_posts = {}, {}, {}, []

    def merge(total, part):
        for c, (n, cnt) in part.items():
            e = total.get(c)
            if e is None:
                total[c] = [n, cnt]
            else:
                e[0] += n
                e[1].update(cnt)

    with mp.Pool(jobs) as pool:
        work_items = [(path, c, gen_ids, char_ids, char_implies) for c in chunks]
        for i, (r_canon, r_any, r_series, r_pool) in enumerate(pool.imap_unordered(_stats_chunk, work_items)):
            merge(canon, r_canon)
            merge(anyp, r_any)
            for c, cnt in r_series.items():
                if c in series:
                    series[c].update(cnt)
                else:
                    series[c] = cnt
            pool_posts.extend(r_pool)
            if i % 10 == 0:
                log(f"  {i + 1}/{len(chunks)}  pool={len(pool_posts)}")

    out = dict(gen_names=gen_names, gen_count=[gen[t] for t in gen_names],
               gen_nsfw=[gen_nsfw[t] for t in gen_names],
               char_names=char_names, char_count=[cha[t] for t in char_names],
               series_count={t: n for t, n in cop.items() if n >= MIN_SERIES_COUNT},
               canon=canon, anyp=anyp, series=series, pool=pool_posts)
    with open(os.path.join(work, "scan.pkl"), "wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    log("scan done")


# ---------------------------------------------------------------------- build
def load_side_tables(work):
    import pyarrow.parquet as pq
    tax = pq.read_table(os.path.join(work, "taxonomy.parquet")).to_pydict()
    slots, aliases, renamed = {}, {}, {}
    for tag, cat, slot, al in zip(tax["tag"], tax["danbooru_category"], tax["slot"], tax["aliases"]):
        if cat == "general":
            slots[tag] = slot
        al = [a for a in (al or ()) if a]
        aliases[(cat, tag)] = al
        for a in al:
            renamed[(cat, a)] = tag
    # The taxonomy keeps the name a tag had when it was first listed, the post
    # dump has today's name.  Look renamed tags up under their old name and
    # keep that old name as an alias, so typing either one finds the tag.
    for (cat, new), old in renamed.items():
        if (cat, new) in aliases:
            continue
        aliases[(cat, new)] = [old] + [a for a in aliases[(cat, old)] if a != new]
        if cat == "general" and old in slots:
            slots[new] = slots[old]
    grp = pq.read_table(os.path.join(work, "tag_groups.parquet")).to_pydict()
    groups = collections.defaultdict(set)
    for g, t in zip(grp["tag_group"], grp["tag"]):
        groups[t].add(g.split(":", 1)[1])
    implies = load_implications(work)
    jio7 = {}
    for cat in JIO7_CATEGORIES:
        with open(os.path.join(work, f"jio7_{cat}.csv"), encoding="utf8") as f:
            for line in f:
                jio7.setdefault(line.rsplit(",", 1)[0].strip().strip('"'), cat)
    return slots, aliases, groups, implies, jio7


def stage_build(work, out_dir):
    import numpy as np

    log("loading scan")
    with open(os.path.join(work, "scan.pkl"), "rb") as f:
        scan = pickle.load(f)
    slots, aliases, groups, implies, jio7 = load_side_tables(work)
    implied = transitive(implies)

    gen_names, gen_count, gen_nsfw = scan["gen_names"], scan["gen_count"], scan["gen_nsfw"]
    field_index = {field[0]: i for i, field in enumerate(FIELDS)}

    # ---- general tags -> fields ------------------------------------------
    # A tag's id is its position in `tags`; the ids used by the scan are
    # translated through `remap`.  Each entry: [name, field, posts, share of
    # those posts rated questionable or explicit].
    tags, remap, report = [], {}, collections.defaultdict(list)
    for old, (name, count, nsfw) in enumerate(zip(gen_names, gen_count, gen_nsfw)):
        field = classify_tag(name, slots.get(name), groups.get(name, ()), jio7.get(name))
        report[field or "-"].append(name)
        if field is None:
            continue
        remap[old] = len(tags)
        tags.append([name, field_index[field], count, round(nsfw / count, 2)])
    tag_id = {t[0]: i for i, t in enumerate(tags)}
    log(f"{len(tags)} of {len(gen_names)} general tags assigned to fields")
    with open(os.path.join(work, "field_report.txt"), "w", encoding="utf8") as f:
        for field, names in report.items():
            f.write(f"== {field} ({len(names)})\n" + " ".join(names) + "\n\n")

    # implications between kept tags (white_shirt -> shirt): used to drop the
    # redundant general tag when the specific one is present.
    imp_out = {}
    for t, i in tag_id.items():
        parents = sorted(tag_id[u] for u in implied.get(t, ()) if u in tag_id)
        if parents:
            imp_out[i] = parents

    groups_out = {}
    for gname, g in EXCLUSIVE_GROUPS.items():
        groups_out[gname] = dict(tags=[tag_id[m] for m in g["tags"] if m in tag_id],
                                 multi=[tag_id[m] for m in g["multi"] if m in tag_id])

    general_aliases = {}
    for t, i in tag_id.items():
        al = [a for a in aliases.get(("general", t), ()) if a != t and not a.startswith("/")]
        if al:
            general_aliases[i] = al[:6]

    # ---- series -----------------------------------------------------------
    series_count = scan["series_count"]
    # a series tag that is implied by another (fate/grand_order -> fate_(series))
    series_parents = {s: {p for p in implied.get(s, ()) if p in series_count} for s in series_count}

    def pick_series(counter, n_posts):
        """Most specific copyright that covers most of the character's posts."""
        best = [s for s, n in counter.most_common(8) if s in series_count and n >= 0.5 * n_posts]
        if not best:
            best = [s for s, _n in counter.most_common(1) if s in series_count]
        if not best:
            return None
        parents = set().union(*(series_parents[s] for s in best))
        specific = [s for s in best if s not in parents]
        return (specific or best)[0]

    # ---- characters ---------------------------------------------------------
    log("characters")
    characters, char_ptr, char_tags, char_pct = [], [0], [], []
    n_canon = 0
    for cid, (name, count) in enumerate(zip(scan["char_names"], scan["char_count"])):
        entry = scan["canon"].get(cid)
        clean = entry is not None and entry[0] >= CANON_MIN_POSTS
        if clean:
            n_canon += 1
        else:
            entry = scan["anyp"].get(cid)
        if entry is None:
            continue
        n, counter = entry
        per_field = collections.defaultdict(list)
        for old, c in counter.items():
            new = remap.get(old)
            if new is None:
                continue
            freq = c / n
            if freq >= CHAR_MIN_FREQ:
                per_field[tags[new][1]].append((freq, new))
        flat = []
        for items in per_field.values():
            items.sort(reverse=True)
            flat.extend([new, round(freq * 100)] for freq, new in items[:CHAR_TAGS_PER_FIELD])
        flat.sort(key=lambda x: -x[1])
        ser = scan["series"].get(cid)
        series_name = pick_series(ser, scan["anyp"][cid][0]) if ser and cid in scan["anyp"] else None
        al = [a for a in aliases.get(("character", name), ()) if a != name][:4]
        characters.append([name, count, series_name or "", al, int(clean)])
        char_tags.extend(x[0] for x in flat)
        char_pct.extend(x[1] for x in flat)
        char_ptr.append(len(char_tags))
    log(f"{len(characters)} characters ({n_canon} with clean solo statistics)")

    # ---- post pool ------------------------------------------------------------
    log("post pool")
    rng = random.Random(1234)
    by_rating = collections.defaultdict(list)
    for pid, score, rating, ids in scan["pool"]:
        by_rating[rating].append((score, pid, ids))
    indptr, flat_ids, ratings = [0], [], []
    for r_index, rating in enumerate("gsqe"):
        posts = by_rating[rating]
        # weight towards well-liked posts without letting the very top dominate
        posts.sort(key=lambda p: -(p[0] ** 0.5) * rng.random())
        for score, pid, ids in posts[:POOL_SIZE[rating]]:
            new = sorted({remap[i] for i in ids if i in remap})
            if len(new) < 12:
                continue
            flat_ids.extend(new)
            indptr.append(len(flat_ids))
            ratings.append(r_index)
    log(f"{len(ratings)} pool posts, {len(flat_ids)} tags")

    # ---- write ------------------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    meta = dict(
        built=time.strftime("%Y-%m-%d"),
        fields=[dict(id=i, label=label, count=count, group=group) for i, label, count, group in FIELDS],
        tags=tags,
        implies=imp_out,
        groups=groups_out,
        soft=sorted(tag_id[t] for t in NOT_IDENTITY if t in tag_id),
        colors=COLORS,
        aliases=general_aliases,
        series=sorted(([s, n] for s, n in series_count.items()), key=lambda x: -x[1]),
    )

    def dump(name, obj):
        p = os.path.join(out_dir, name)
        with gzip.GzipFile(p, "wb", compresslevel=9, mtime=0) as f:
            f.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf8"))
        log(f"wrote {name}: {os.path.getsize(p) / 1e6:.2f} MB")

    dump("tags.json.gz", meta)
    dump("characters.json.gz", characters)
    p = os.path.join(out_dir, "character_tags.npz")
    np.savez_compressed(p, indptr=np.asarray(char_ptr, dtype=np.uint32),
                        tags=np.asarray(char_tags, dtype=np.uint16),
                        percent=np.asarray(char_pct, dtype=np.uint8))
    log(f"wrote character_tags.npz: {os.path.getsize(p) / 1e6:.2f} MB")
    p = os.path.join(out_dir, "posts.npz")
    np.savez_compressed(p, indptr=np.asarray(indptr, dtype=np.uint32),
                        tags=np.asarray(flat_ids, dtype=np.uint16),
                        rating=np.asarray(ratings, dtype=np.uint8))
    log(f"wrote posts.npz: {os.path.getsize(p) / 1e6:.2f} MB")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True, help="scratch directory for downloads and intermediate files")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "data"))
    ap.add_argument("--stage", choices=["download", "scan", "build", "all"], default="all")
    ap.add_argument("--jobs", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    a = ap.parse_args()
    if a.stage in ("download", "all"):
        stage_download(a.work)
    if a.stage in ("scan", "all"):
        stage_scan(a.work, a.jobs)
    if a.stage in ("build", "all"):
        stage_build(a.work, a.out)


if __name__ == "__main__":
    main()
