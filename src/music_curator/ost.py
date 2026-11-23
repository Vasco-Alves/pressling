#!/usr/bin/env python3
"""Resolve game-soundtrack files ("<Game> OST - <Track>") against Discogs.

Usage: pressling ost <items.json> <out.json>
items.json: [{"key","src","game","title","artist"(optional),"mix"(optional)}]
Same rules as resolve.py (artist as credited, title as listed, album/year/genre rules); the folder is
always "Game OST". Status: ready | review | notfound | dup.
With loose (Curate's game cards), a track whose name is the one searched in fewer or more words ("Menu" for "Main Menu") counts too,
when it's the only such track on the soundtrack and its length agrees with the file's.
"""
import json
import os
import re
import subprocess
import sys

from . import discogs as d
from . import resolve as rv


def secs(p):
    out = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                         capture_output=True, text=True).stdout
    return round(float(out or 0), 1)


def game_ok(game, release_title):
    g = [t for t in rv.tokens(game) if t not in {"ost", "soundtrack", "psx", "n64", "video", "game", "original"}]
    rt = rv.tokens(release_title)
    return bool(g) and len(set(g) & rt) / len(g) >= 0.6


NEAR_S = 6  # how far a track's length may be from the file's when only the words of its name say it's this track
MAKERS = {"composed by", "created by", "written-by", "written by", "music by", "composer"}  # Discogs credits that say who made a track


def _makers(extras):
    """The different people credited with making a track (composed, created, written, music by; not the lyrics), in order."""
    names = []
    for e in extras or []:
        roles = [re.sub(r"\s*\[[^\]]*\]", "", part).strip().lower() for part in str(e.get("role") or "").split(",") if "lyric" not in part.lower()]
        if e.get("name") and any(role in MAKERS for role in roles) and e["name"] not in names:
            names.append(e["name"])
    return names


def _covers(tracks, pos):
    """Does a credit's list of tracks ("1, 3, 19") name this one? A range ("A1 to A3") isn't read, so it counts as no."""
    return bool(pos) and str(pos).strip() in [p.strip() for p in str(tracks or "").split(",")]


def near_title(listed, wanted):
    """Is one name the other in fewer words? "Menu" and "Main Menu" are; "Menu" and "Main Theme" aren't. Worth something only with lengths that agree."""
    a, b = (rv.tokens(re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", t or "")) for t in (listed, wanted))
    return bool(a and b) and (a <= b or b <= a)


def resolve(it, history, library_keys, loose=False):
    out = {"key": it["key"], "src": it["src"], "file": os.path.basename(it["src"]), "secs": secs(it["src"]),
           "status": "notfound", "reasons": [], "notes": []}
    title, game, artist = it["title"], it.get("game", ""), it.get("artist", "")
    st = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", title).strip()
    tries = []
    if artist:
        tries.append(dict(artist=artist, track=st))
    if game:
        tries += [dict(q=f"{game} {st}"), dict(release_title=game, track=st)]
        if loose:  # the soundtrack by the game's name alone, for a track listed under another name ("Menu" for "Main Menu")
            tries += [dict(release_title=game), dict(q=f"{game} soundtrack")]
    results = []
    for t in tries:
        res = d.search(**t)
        good = [r for r in res if (artist and rv.artist_ok(artist, rv.result_artist(r))) or
                (game and game_ok(game, r.get("title", "")))]
        if good:
            results = good
            break
    if not results:
        out["reasons"].append("not found on Discogs")
        return out
    results.sort(key=rv.year_of)
    want = d.base_title(st)
    found, looked, listed = None, [], []
    weigh = loose and out["secs"]  # Curate's game cards: every record that lists the track is weighed by length, not just the oldest
    for r in results[:6]:
        rel = d.get(f"/releases/{r['id']}")
        if not rel:
            continue
        c = d.cond_release(rel)
        looked.append((r, c))
        hits = [t for t in c["tracklist"] if t.get("type") in (None, "track") and (rv.same_title(t["title"], st) if want else d.base_title(t["title"]) == want)]  # a title Discogs gives in two alphabets matches by either half
        if it.get("mix"):
            hits = [t for t in hits if rv.mix_matches(it["mix"], t["title"])] or []
        if hits and weigh:
            listed.append((r, c, hits))
        elif hits:
            found = (r, c, hits)
            break
    other_length = None
    if listed:  # the one at the file's length; else one with no length, unless another pressing of that record gives the track one; else another version, said as such
        timed = lambda x: [rv.secs(t.get("dur")) for t in x[2] if rv.secs(t.get("dur"))]  # noqa: E731
        same = [x for x in listed if any(abs(s - out["secs"]) <= NEAR_S for s in timed(x))]
        untimed = [x for x in listed if not timed(x) and not any(y is not x and y[0].get("master_id") and y[0].get("master_id") == x[0].get("master_id") and timed(y) for y in listed)]
        found = (same or untimed or [None])[0]
        other_length = None if found else next((x for x in listed if timed(x)), listed[0])  # another version: said with a pressing that gives its length
    if not found and weigh and not it.get("mix"):  # "Menu" for "Main Menu": the one track whose name is this one in fewer or more words, at the file's length
        for r, c in looked:
            near = [t for t in c["tracklist"] if t.get("type") in (None, "track") and near_title(t["title"], st) and rv.secs(t.get("dur")) and abs(rv.secs(t["dur"]) - out["secs"]) <= NEAR_S]
            if len(near) == 1:
                found = (r, c, near)
                out["notes"].append(f'listed on Discogs as "{near[0]["title"]}"')
                break
    found = found or other_length  # only another version of it: still worth saying who's credited, and how far the length is off
    if not found:
        out["reasons"].append("soundtrack found on Discogs, but not this track" + (f" ({it['mix']})" if it.get("mix") else ""))
        out["candidates"] = [r["title"] for r in results[:4]]
        unsure = [(c, t) for _, c in looked for t in c["tracklist"] if loose and t.get("type") in (None, "track") and near_title(t["title"], st) and not rv.secs(t.get("dur"))]
        if len({rv.norm(t["title"]) for _, t in unsure}) == 1:  # one near name with no length to tell (on however many pressings): worth a mention for a person to weigh, never a credit
            c, t = unsure[0]
            makers = _makers(t.get("extra"))
            out["maybe"] = dict(title=t["title"], release_title=c["title"], artist=rv.credit(t["artists"] or ([{"name": makers[0], "anv": ""}] if len(makers) == 1 else c["artists"])))
        return out
    r, rel, hits = found
    track = hits[0]
    if len({t["title"] for t in hits}) > 1:
        out["status"] = "review"
        out["reasons"].append("several versions: " + "; ".join(f"{t['pos']} {t['title']} [{t['dur'] or '?'}]" for t in hits))
    makers = _makers(track.get("extra"))
    arts = track["artists"] or ([{"name": makers[0], "anv": ""}] if len(makers) == 1 else rel["artists"])  # the track's own composer says more than the record's artist
    who = rv.credit(arts)
    if rv.norm(who) == "various" or not who:  # a various-artists soundtrack may still credit one person with all of its music
        whole = _makers([e for e in rel.get("extra") or [] if not e.get("tracks") or _covers(e["tracks"], track.get("pos"))])
        if len(whole) == 1:
            who = rv.credit([{"name": whole[0], "anv": ""}])
    if rv.norm(who) == "various" or not who:
        out["status"] = "review"
        out["reasons"].append("no artist credited for this track on Discogs")
    t_title, who = rv.clean_title(track["title"], track.get("pos"), who)
    same_master = [x for x in results if x.get("master_id") and x.get("master_id") == r.get("master_id")] or [r]
    years = [rv.year_of(x) for x in same_master if rv.year_of(x) < 9999]
    album, album_why = rv.album_for(rel)
    genre, options, toss, gwhy = rv.pick_genre(same_master, rel, who, history)
    out.update({"release": rel["id"], "master": rel.get("master_id"), "release_title": rel["title"],
                "discogs": (f"https://www.discogs.com/master/{rel['master_id']}" if rel.get("master_id")
                            else f"https://www.discogs.com/release/{rel['id']}"),
                "artist": who, "title": t_title, "album": album, "album_why": album_why,
                "year": min(years) if years else rel.get("year"), "genre": genre, "genre_options": options,
                "toss_up": toss, "genre_why": gwhy, "folder": "Game OST", "how": "track found on the soundtrack"})
    if track.get("dur") and rv.secs(track["dur"]):
        diff = round(out["secs"] - rv.secs(track["dur"]))
        out["dur_diff"] = diff
        if abs(diff) > 25:
            out["status"] = "review"
            out["reasons"].append(f"file is {abs(diff)}s {'longer' if diff > 0 else 'shorter'} than the soundtrack version ({track['dur']})")
    imgs = sorted(rel.get("images") or [], key=lambda i: 0 if i.get("type") == "primary" else 1)
    out["cover_url"] = imgs[0]["uri"] if imgs else None
    key = rv.norm(f"{who} - {t_title}")
    if key in library_keys:
        out["status"] = "dup"
        out["reasons"].append("already in library: " + library_keys[key])
    if out["status"] == "notfound":
        out["status"] = "ready"
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    items_path, out_path, cache = argv[0:3]
    d.CACHE_DIR = cache
    library_keys = {}
    for lib in argv[3:]:
        for dp, _, fns in os.walk(lib):
            for fn in fns:
                if fn.lower().endswith(".mp3"):
                    library_keys.setdefault(rv.norm(os.path.splitext(fn)[0]), os.path.join(dp, fn))
    results, history = [], {}
    for it in json.load(open(items_path, encoding="utf-8")):
        try:
            r = resolve(it, history, library_keys)
        except Exception as ex:  # noqa: BLE001
            r = {"key": it["key"], "src": it["src"], "file": os.path.basename(it["src"]), "status": "review",
                 "reasons": [f"lookup error: {ex}"], "notes": []}
        results.append(r)
        print(f"[{it['key']}] {r['status']:<8} {r['file'][:70]}", flush=True)
    json.dump(results, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
