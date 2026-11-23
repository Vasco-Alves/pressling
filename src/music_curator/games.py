#!/usr/bin/env python3
"""Game, film and TV music in your downloads: which game or film a song is from, who made the track, and how it would go in.

Curate never adds one of these by itself. It reads what the sources say and leaves a card on To check that says exactly what would
happen, for you (or your agent) to check and then pick:
- Who made the track comes only from a source that lists that very track: the soundtrack on Discogs (its length has to agree with
  your file's, or the card says it couldn't be compared), or the official upload ("Provided to YouTube by ...") whose album is the
  soundtrack.
- Wikipedia gives only the game's proper name, its year and its cover, and only from a page about a game, a film or a show whose
  title is the name your file gives. Its composers say nothing about one track: the page for Need for Speed II names three people,
  and none of them made "Menu".
- With nothing confirming who made it, the song can go in as "<Game> OST - <Track>", as the naming rules say.
- When two games go by that name and nothing says which, it asks instead of choosing.

Usage: pressling game "<file>" [--game NAME]     what Curate would put on To check for that file (it looks things up and changes nothing)
"""
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from . import config, folders, fpcompare, keys, ost, other_sources, parse, saving
from . import resolve as rv

GAME, FILM = "game", "film"
GENRE = {GAME: "Video Game Music", FILM: "Soundtrack"}
FOLDER = {GAME: "Game OST", FILM: "Movie & TV OST"}
INFOBOXES = {"video game": GAME, "film": FILM, "television": FILM}  # what a Wikipedia page's info box says it's about
SAID = "game: "  # how what you said a song is from is kept with it on the waiting list
NOTE = "game song"  # how the log's notes start for one, as for the songs added by hand before
SAME_LENGTH_S = ost.NEAR_S  # how far a track's length on a source may be from your file's for it to be the same
UNNAMED = ("Looks like game or film music, but its name doesn't say which game and which track. Use It's game music… to say which game "
           "it's from, or I know what this is… for its artist and title.")
WIKI = "https://en.wikipedia.org/w/api.php"
YOUTUBE_TIMEOUT_S = 90
IMAGE_MAX = 15 * 1024 * 1024
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ytdlp_helper.py")
QUALITY = re.compile(r"(?:HQ|HD|4K|1080p|720p|60 ?fps|High Quality|Official(?: Audio| Video)?|Audio|Full|Lyrics?)", re.I)
PLATFORM = re.compile(r"(?:PS[1-5]|PSX|PS ?One|PSP|PS ?Vita|PlayStation(?: [1-5])?|N64|Nintendo 64|NES|Famicom|SNES|Super Famicom|Super Nintendo|"
                      r"Game ?Boy(?: Advance| Colou?r)?|GBA|GBC|NDS|DS|3DS|Wii(?: ?U)?|Switch|GameCube|GCN|Xbox(?: 360| One| Series [XS])?|"
                      r"Dreamcast|(?:Sega )?Saturn|Genesis|Mega ?Drive|Master System|Amiga|C64|Commodore 64|MS-?DOS|DOS|PC|Windows|Arcade|"
                      r"Neo ?Geo|TurboGrafx(?:-16)?|PC ?Engine|Atari(?: 2600| ST)?|MSX|ZX Spectrum)", re.I)
ROMAN = re.compile(r"(?=[ivx])x{0,3}(?:ix|iv|v?i{0,3})")
_last = [0.0]
_youtube = []  # whether yt-dlp is there to ask, found out once


# ---------------- the file's name ----------------

def _game_part(text):
    """The game's side of a name without its OST words, or the "Original", "Official" or "Game" they leave at its end."""
    text = re.sub(r"[\(\[]\s*[\)\]]", " ", parse.OST_WORDS.sub(" ", text))
    text = re.sub(r"(?:\s+(?:original|official|video|game|music|complete|full))+\s*$", "", " " + text, flags=re.I)
    return " ".join(text.split()).strip(" -:|~,.")


def _track_part(text):
    """The track's side of a name: no OST words, no track number in front ("01.", "07 "), and no "from" or dash left where the game was."""
    text = " ".join(re.sub(r"[\(\[]\s*[\)\]]", " ", parse.OST_WORDS.sub(" ", text)).split())
    for _ in range(3):  # (only at the end: a track can start with "Of")
        text = re.sub(r"\s+(?:from|of)$", "", text, flags=re.I).strip(" -:|~,.")
    return re.sub(r"^(?:Track\s*\d{1,3}[.)]?|\d{1,3}[.)]|0\d)\s+(?=\S)", "", text, flags=re.I)


def _without(text, game):
    """The name with the game taken out, however its spaces and dots are written ("n.Gen Racing" comes out of "Jet Stream from NGEN Racing"):
    at either end before anywhere else, and without a year you added that the name hasn't got ("Doom 1993" comes out of "Doom OST - ...")."""
    for name in (game, re.sub(r"\s(?:19|20)\d\d$", "", game)):
        words = re.findall(r"\w+", name)
        if not words:
            continue
        pattern = r"\W*".join(re.escape(w) for w in words)
        for where in (rf"^\W*{pattern}(?!\w)", rf"(?<!\w){pattern}\W*$", rf"(?<!\w){pattern}(?!\w)"):
            out, found = re.subn(where, " ", text, count=1, flags=re.I)
            if found:
                return out
    return text


def split(name, said=""):
    """(game, track, year) from a file's name, or None. The game is the side with an OST word ("Need for Speed II Soundtrack - Main Menu",
    "Main Menu - Need for Speed II OST", "[OST] Chrono Trigger - Corridors of Time") or a note with one ("Main Menu (Need for Speed II OST)").
    Notes naming a platform or the quality go, and a year in a note or at the end of the game's name is kept as a hint ("Doom (1993) OST").
    With `said`, the game is the one you said and the track is the rest of the name ("Jet Stream from n.Gen Racing")."""
    year, in_note = None, ""

    def note(m):
        nonlocal year, in_note
        inner = " ".join(m.group(1).split())
        if re.fullmatch(r"(?:19|20)\d\d", inner) and parse.a_year(inner):
            year = year or parse.a_year(inner)
            return " "
        if not inner or PLATFORM.fullmatch(inner) or QUALITY.fullmatch(inner):
            return " "
        if not said and parse.OST_WORDS.search(inner) and _game_part(inner):  # "(Need for Speed II OST)": the game, in a note
            in_note = in_note or _game_part(inner)
            return " "
        return m.group(0)  # "(Main Theme)" is part of the track, and "[OST]" says which side the game is

    text = " ".join(re.sub(r"[\(\[]([^()\[\]]*)[\)\]]", note, parse.clean(name)).split())
    if said:
        game = " ".join(str(said).split())
        track = _track_part(_without(text, game))
    elif in_note:
        game, track = in_note, _track_part(text)
    else:
        sides = [s.strip() for s in re.split(r"\s+[-|~]\s+", text) if s.strip()]
        if len(sides) == 1 and ": " in text:  # "Crash Bandicoot 2 OST: Snow Go"
            left, right = text.split(": ", 1)
            if parse.OST_WORDS.search(left) or parse.OST_WORDS.search(right):
                sides = [left, right]
        sides = [s for s in sides if not re.fullmatch(r"(?:Track\s*)?\d{1,3}\.?", s, re.I)]  # "01": where it was on the album
        at = next((i for i, s in enumerate(sides) if parse.OST_WORDS.search(s)), None)
        if at is None:
            return None
        game, rest = _game_part(sides[at]), sides[:at] + sides[at + 1:]
        if not game and len(rest) > 1:  # "OST - Need for Speed II - Main Menu"
            game, rest = rest[0], rest[1:]
        track = _track_part(" - ".join(rest))
    m = re.search(r"\s((?:19|20)\d\d)$", game)
    if m and parse.a_year(m[1]):  # "Unreal Tournament 1999": a hint at which game. The name stays as you wrote it, for when no page is found
        year = year or parse.a_year(m[1])
    return (game, track, year) if game and track else None


# ---------------- Wikipedia: the game's name, year and cover ----------------

def _wiki(params, cache=""):
    """An answer from Wikipedia's API, kept in `cache` so a song looked up again costs nothing."""
    url = WIKI + "?" + urllib.parse.urlencode(dict(params, format="json", formatversion="2"))
    kept = os.path.join(cache, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json") if cache else ""
    if kept:
        try:
            with open(kept, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):  # nothing kept yet, or a file cut short
            pass
    wait = 0.5 - (time.time() - _last[0])  # gentle with a volunteer-run site
    if wait > 0:
        time.sleep(wait)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": other_sources.UA}), timeout=20) as r:
            data = json.load(r)
    finally:
        _last[0] = time.time()
    if not isinstance(data, dict):
        raise ValueError("an answer that isn't one")
    if kept and "error" not in data:
        os.makedirs(cache, exist_ok=True)
        saving.write(kept, lambda f: json.dump(data, f, ensure_ascii=False), encoding="utf-8")
    return data


def infobox(text):
    """(what it's about, {field: value}) from the first info box in a page's wikitext: ("video game", {"released": ..., ...}), or ("", {})."""
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    text = re.sub(r"<ref[^>/]*/>", "", text, flags=re.I)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)  # sources: their dates aren't the game's
    m = re.search(r"\{\{\s*Infobox[ _]+([^|}\n]*)", text, re.I)
    if not m:
        return "", {}
    depth, end, at = 0, len(text), m.start()
    while at < len(text) - 1:  # where it ends: each "{{" and "}}" counted once, "}}}}" as two
        pair = text[at:at + 2]
        if pair in ("{{", "}}"):
            depth += 1 if pair == "{{" else -1
            at += 2
            if depth == 0:
                end = at - 2
                break
        else:
            at += 1
    body, parts, piece, depth, at = text[m.start() + 2:end], [], [], 0, 0
    while at < len(body):
        pair = body[at:at + 2]
        if pair in ("{{", "[["):
            depth, at = depth + 1, at + 2
            piece.append(pair)
        elif pair in ("}}", "]]"):
            depth, at = max(depth - 1, 0), at + 2
            piece.append(pair)
        elif body[at] == "|" and depth == 0:
            parts.append("".join(piece))
            piece, at = [], at + 1
        else:
            piece.append(body[at])
            at += 1
    parts.append("".join(piece))
    fields = {}
    for part in parts[1:]:
        key, eq, value = part.partition("=")
        if eq:
            fields[" ".join(key.split()).lower().replace(" ", "_")] = value.strip()
    return " ".join(m.group(1).split()).lower(), fields


def first_year(value):
    """The earliest year a field gives, in its dates and templates ("{{Video game release|NA|March 31, 1997|EU|1997}}"), or None."""
    years = [parse.a_year(y) for y in re.findall(r"(?<!\d)((?:19|20)\d\d)(?!\d)", value or "")]
    years = [y for y in years if y]
    return min(years) if years else None


def _bare_title(title):
    """A page's title without what tells it apart from others: "Doom (1993 video game)" is Doom, "Interstellar (film)" Interstellar."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", title or "").strip()


def _numbers(text):
    return {w for w in keys.words(text) if w.isdigit() or ROMAN.fullmatch(w)}


def _same_name(asked, title, year=None):
    """Is this page's title the name the file gives? Spelled the same once spaces and dots go ("NGEN Racing" is n.Gen Racing), or mostly the
    same words with the same numbers: Need for Speed II isn't The Need for Speed, and Halo 3 isn't Halo 2. A year is one the file may leave out."""
    bare = _bare_title(title)
    if rv.norm(asked) and rv.norm(asked) == rv.norm(bare):
        return True
    if _numbers(asked) - {str(year)} != _numbers(bare) - {str(year)}:
        return False
    mine, theirs = rv.tokens(asked) - {str(year)}, rv.tokens(bare) - {str(year)}
    both = len(mine & theirs)
    return bool(mine and theirs) and both / len(mine) >= 0.6 and both / len(theirs) >= 0.6


def _page(page):
    """What a page says of a game, a film or a show: {title, page, url, kind, year, image}. None for any other page (a series, a person, a song)."""
    if not isinstance(page, dict) or page.get("missing") or page.get("invalid"):
        return None
    revisions = page.get("revisions") if isinstance(page.get("revisions"), list) else []
    first = revisions[0] if revisions and isinstance(revisions[0], dict) else {}
    text = ((first.get("slots") or {}).get("main") or {}).get("content") or ""
    kind, fields = infobox(text if isinstance(text, str) else "")
    what = INFOBOXES.get(kind)
    if not what:
        return None
    released = next((fields[k] for k in ("released", "release", "release_date", "first_aired", "airdate") if fields.get(k)), "")
    image = str((page.get("original") or {}).get("source") or "").split("?")[0]
    url = str(page.get("fullurl") or "")
    return dict(title=_bare_title(str(page.get("title") or "")), page=str(page.get("title") or ""), kind=what, year=first_year(released),
                url=url if url.startswith("https://en.wikipedia.org/wiki/") else "",
                image=image if image.startswith("https://upload.wikimedia.org/") else "")


def _choose(fits, names, year):
    """The page among those that fit: the one with the name itself first, the one from the year the file gives if it gives one.
    With no year and more than one game going by that name, all of them, for the card to ask which."""
    if not fits:
        return None
    exact = [p for p in fits if p.get("direct")] + [p for p in fits if not p.get("direct") and any(rv.norm(n) == rv.norm(p["title"]) for n in names)]
    ranked = exact + [p for p in fits if p not in exact]
    if year:
        near = [p for p in ranked if p["year"] and abs(p["year"] - year) <= 1]
        return near[0] if near else None
    same = [p for p in ranked if rv.norm(p["title"]) == rv.norm(ranked[0]["title"])]
    if len({p["year"] for p in same}) > 1:
        return dict(ranked[0], others=same[1:])
    return ranked[0]


def wikipedia(game, year=None, cache=""):
    """The page for the game (or film, or show) the file names, or None when no page fits. A page fits when its info box says it's about a
    game, a film or a show and its title is the name the file gives; with more than one going by that name and no year to tell them apart,
    the answer holds the others too, under "others"."""
    names = [game]
    m = re.search(r"\s((?:19|20)\d\d)$", game)
    if m:  # "Unreal Tournament 1999" may be "Unreal Tournament", from 1999
        names.append(game[: m.start()].strip())
    fits = []
    for page in _pages(names, cache).values():  # the name itself, or a page it leads to ("Need for Speed IV" is Need for Speed: High Stakes)
        found = _page(page)
        if found and all(f["page"] != found["page"] for f in fits):
            fits.append(dict(found, direct=True))
    seen = {f["page"] for f in fits}
    for query in [game, f"{names[-1]} video game"] + ([f"{names[-1]} {year} video game"] if year else []):  # a search too, for other games of that name
        answer = _wiki(dict(action="query", list="search", srsearch=query, srlimit="8", srprop=""), cache)
        hits = (answer.get("query") or {}).get("search") if isinstance(answer.get("query"), dict) else None
        titles = [x["title"] for x in hits or [] if isinstance(x, dict) and isinstance(x.get("title"), str)]
        titles = [t for t in titles if t not in seen and any(_same_name(n, t, year) for n in names)][:6]
        if titles:
            seen.update(titles)
            fits += [x for x in (_page(p) for p in _pages(titles, cache).values()) if x and all(f["page"] != x["page"] for f in fits)]
        chosen = _choose(fits, names, year)
        if chosen:
            return chosen
    return None


def _pages(titles, cache=""):
    """{title asked for: its page}, in one question. A redirect gives the page it leads to; a title with no page gives None."""
    answer = _wiki(dict(action="query", prop="revisions|pageimages|info", rvprop="content", rvslots="main", rvsection="0", piprop="original",
                        pilicense="any", inprop="url", titles="|".join(titles), redirects="1"), cache)
    query = answer.get("query") if isinstance(answer.get("query"), dict) else {}
    leads = {s["from"]: s["to"] for s in (query.get("normalized") or []) + (query.get("redirects") or [])
             if isinstance(s, dict) and isinstance(s.get("from"), str) and isinstance(s.get("to"), str)}
    pages = {p.get("title"): p for p in query.get("pages") or [] if isinstance(p, dict)}
    out = {}
    for title in titles:
        final = title
        for _ in range(3):  # "Need for speed IV" is normalized, then redirected
            final = leads.get(final, final)
        out[title] = pages.get(final)
    return out


def image(url):
    """A cover from Wikipedia's image server, as bytes ("" when it isn't one)."""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme != "https" or parts.hostname != "upload.wikimedia.org":
        return b""
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": other_sources.UA}), timeout=30) as r:
        data = r.read(IMAGE_MAX + 1)
    return b"" if len(data) > IMAGE_MAX else data  # (cut short, it would be a broken picture)


# ---------------- the official upload ----------------

def _can_ask_youtube():
    if not _youtube:
        try:
            ok = subprocess.run([config.ytdlp_python(), "-I", "-c", "import yt_dlp"], capture_output=True, timeout=30, creationflags=folders.NO_WINDOW).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        _youtube.append(ok)
    return _youtube[0]


def _yt(args, cache="", stopping=None):
    """What yt-dlp says (metadata only: nothing is downloaded), kept in `cache`; None when it couldn't be had. Stop and a time limit end it."""
    kept = os.path.join(cache, hashlib.sha1(json.dumps(args).encode("utf-8")).hexdigest() + ".json") if cache else ""
    if kept:
        try:
            with open(kept, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    try:
        out = fpcompare.run_or_give_up([config.ytdlp_python(), "-I", HELPER, *args], YOUTUBE_TIMEOUT_S, stopping, text=True, encoding="utf-8", errors="replace")[0]
        data = json.loads(out.strip().splitlines()[-1])
    except (fpcompare.GaveUp, OSError, ValueError, IndexError):
        return None
    if isinstance(data, dict) and data.get("error"):
        return None
    if kept:
        os.makedirs(cache, exist_ok=True)
        saving.write(kept, lambda f: json.dump(data, f, ensure_ascii=False), encoding="utf-8")
    return data


def _source_video(src):
    """The YouTube video a download came from, when its Comment tag says (many downloaders keep the link there)."""
    try:
        comment = str(parse.probe(src)[0].get("comment") or "")
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    m = re.search(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|shorts/))([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", comment)
    return m[1] if m else ""


def _on_album(game, album):
    return bool(rv.norm(game)) and (rv.norm(game) in rv.norm(album) or ost.game_ok(game, album))


def provided(info, names, track, secs):
    """The official upload's credit when this video is one ("Provided to YouTube by ...", on an auto-made "- Topic" channel) of this track,
    on an album named after the game, at your file's length: {artist, title, album, link, year}. Otherwise None."""
    if not isinstance(info, dict) or not str(info.get("channel") or "").endswith(" - Topic") or not re.fullmatch(r"[A-Za-z0-9_-]{11}", str(info.get("id") or "")):
        return None
    desc = str(info.get("description") or "")
    if "Provided to YouTube by" not in desc:
        return None
    blocks = [b.strip() for b in desc.replace("\r", "").split("\n\n") if b.strip()]
    line = next((b for b in blocks if " · " in b), "")
    parts = [p.strip() for p in line.split(" · ") if p.strip()]
    if len(parts) < 2:
        return None
    after = blocks.index(line) + 1
    album = blocks[after] if after < len(blocks) and not blocks[after].startswith(("℗", "Released on")) else ""
    length = info.get("duration") if isinstance(info.get("duration"), (int, float)) else None
    if not any(_on_album(n, album) or _on_album(n, parts[0]) for n in names):  # its album is the soundtrack, or its own title names the game ("Jet Stream from n.Gen Racing")
        return None
    if secs and length and abs(length - secs) > SAME_LENGTH_S:  # another version of it
        return None
    if not (rv.same_title(parts[0], track) or (secs and length and ost.near_title(parts[0], track))):
        return None
    years = [parse.a_year(y) for y in re.findall(r"(?:℗\s*|Released on:\s*)((?:19|20)\d\d)", desc)]
    return dict(artist=rv.unify_joiners(config.rule("artist_joiner").join(parts[1:])), title=parts[0], album=album,
                link=f"https://www.youtube.com/watch?v={info['id']}", year=min([y for y in years if y], default=None))


def official_upload(src, names, track, secs, cache="", stopping=None):
    """The official upload of this track, if yt-dlp is installed to ask: the video the file came from first (its link is in the Comment tag),
    then a search. Only what the videos say about themselves is read."""
    if not _can_ask_youtube():
        return None
    came_from = _source_video(src)
    if came_from:
        hit = provided(_yt(["info", came_from], cache, stopping), names, track, secs)
        if hit:
            return hit
    found = _yt(["search", f"{names[-1]} {track}", "8"], cache, stopping)
    tried = 0
    for v in found if isinstance(found, list) else []:
        if tried >= 3 or (stopping and stopping.is_set()):
            break
        if not isinstance(v, dict) or not str(v.get("channel") or "").endswith(" - Topic") or v.get("id") == came_from:
            continue
        if secs and isinstance(v.get("duration"), (int, float)) and abs(v["duration"] - secs) > SAME_LENGTH_S:
            continue
        tried += 1
        hit = provided(_yt(["info", str(v.get("id"))], cache, stopping), names, track, secs)
        if hit:
            return hit
    return None


# ---------------- the card ----------------

def _span(seconds):
    return f"{seconds} seconds" if seconds < 90 else f"{seconds // 60}:{seconds % 60:02d}"


def _a_year(value):
    return parse.a_year(str(value)) if re.fullmatch(r"(?:19|20)\d\d", str(value or "")) else None


def propose(game, track, year, disc, wiki, upload, wiki_down=False):
    """The card for To check: the ways it can go in (each saying why), where it goes, and the reason the page shows. Nothing here decides.
    `disc` is what Discogs says of the track (ost.resolve), `wiki` the game's page, `upload` the official upload; any can be None.
    `wiki_down`: Wikipedia couldn't be reached, which says nothing about whether it has a page."""
    disc = disc or {}
    if wiki and wiki.get("others"):
        group = [wiki, *wiki["others"]]
        years = sorted({p["year"] for p in group if p["year"]})
        what = "game" if all(p["kind"] == GAME for p in group) else "game or film"
        return dict(status="notfound", options=[], reasons=[],
                    why=f"More than one {what} is called {wiki['title']} ({', '.join(map(str, years))}), and the file doesn't say which. "
                        f"Use It's game music… to say which, with its year: {wiki['title']} {years[-1]}, say.")
    kind = (wiki or {}).get("kind") or GAME
    name = (wiki or {}).get("title") or game
    if disc.get("status") == "dup":
        where = next((r.split(": ", 1)[1] for r in disc.get("reasons") or [] if r.startswith("already in library: ")), "")
        return dict(status="dup", options=[], reasons=disc.get("reasons") or [], why=f"Already in your library: {os.path.basename(where) or 'a song with this name'}")
    options, said, confirmed = [], [], []

    def offer(artist, title, note, why):
        same = next((o for o in options if rv.norm(o["artist"]) == rv.norm(artist) and rv.norm(o["title"]) == rv.norm(title)), None)
        if same:  # two sources say the same: one way in, with both reasons
            same["note"], same["why"] = f"{same['note']} and {note}", f"{same['why']}; {why}"
        else:
            options.append(dict(id=f"game|{artist}|{title}", pos="", artist=artist, title=title, dur="", gap=None, note=note, why=why))

    if disc.get("release"):
        listed, credited, gap = disc.get("title") or track, disc.get("artist") or "", disc.get("dur_diff")
        on = f'Discogs lists "{listed}" on {disc.get("release_title") or "the soundtrack"}'
        if not credited or rv.norm(credited) == "various":
            said.append(f"{on}, but credits nobody for it.")
        elif gap is not None and abs(gap) > SAME_LENGTH_S:
            said.append(f"{on}, credited to {credited}, but yours is {_span(abs(gap))} {'longer' if gap > 0 else 'shorter'}, so it may be another version.")
        else:
            said.append(f"{on}, credited to {credited}, {'and its length matches yours' if gap is not None else 'but gives no length to compare with yours'}.")
            offer(credited, listed, "credited on Discogs", f'"{listed}" on {disc.get("release_title")} ({disc.get("discogs")}), credited to {credited}'
                  + ("" if gap is not None else ", no length to compare"))
            confirmed.append("Discogs")
        several = next((r for r in disc.get("reasons") or [] if r.startswith("several versions: ")), "")
        if several:
            said.append(f"The soundtrack lists more than one track by that name ({several.split(': ', 1)[1]}).")
    elif disc.get("maybe"):  # a near name with no length to tell by: for you to weigh, never a way in
        maybe = disc["maybe"]
        said.append(f'Discogs lists "{maybe["title"]}" by {maybe["artist"] or "nobody in particular"} on {maybe["release_title"]}, which may be this track, '
                    "but gives no length to compare with yours. If it is, type it in with I know what this is.")
    elif disc.get("candidates"):
        said.append(f"Discogs has {disc['candidates'][0]}, but not this track on it.")
    if upload:
        said.append(f'The official upload credits {upload["artist"]} for "{upload["title"]}", on {upload["album"]}.')
        offer(upload["artist"], upload["title"], "in the official upload", f'the official upload ({upload["link"]}) on {upload["album"]}, credited to {upload["artist"]}')
        confirmed.append("the official upload")
    if not confirmed:
        said.append("Nothing confirms who made this track.")
    offer(f"{name} OST", track, "your rule when nothing confirms who made it",
          f"named after the {'game' if kind == GAME else 'film or show'}, since nothing confirmed who made it")
    year = (wiki or {}).get("year") or year or _a_year(disc.get("year")) or ""
    if not wiki and not wiki_down:
        said.insert(0, "Wikipedia has no page that fits, so its year and cover come from the file or the soundtrack, where they say.")
    head = f"{'Game music' if kind == GAME else 'Film or TV music'} from {name}" + (f" ({year})" if year else "") + "."
    folder = f"{FOLDER[kind]}/{name}"
    cover = disc.get("cover_url") if disc.get("release") and disc.get("cover_url") else (wiki or {}).get("image") or ""
    link = disc.get("discogs") if "Discogs" in confirmed else (wiki or {}).get("url") or (upload or {}).get("link") or ""
    return dict(status="review", options=options, reasons=[], genre=GENRE[kind], album=name, year=year, folder=folder, cover_url=cover or "",
                discogs=link or "", release_title=disc.get("release_title") or (wiki or {}).get("page") or "", labels="", game=name, kind=kind,
                wiki=(wiki or {}).get("url") or "", why=" ".join([head, *said, f"Pick how it goes in: into {folder}, as {GENRE[kind]}."]))


def soundtrack(item, names, track, history, library_keys):
    """What Discogs says of this track on the game's soundtrack, by each name the game goes by: ost.resolve, which also takes "Menu" for
    "Main Menu" when it's the only such track and its length agrees with the file's."""
    found = {}
    for name in names:
        found = ost.resolve(dict(key=item["key"], src=item["src"], game=name, title=track), history, library_keys, loose=True)
        if found.get("release"):
            break
    return found


def look_up(item, history, library_keys, cache, said="", stopping=None):
    """Everything Curate finds about a game or film song, as a card for To check (see propose). A source that can't be reached is left out,
    and the card says to look again; Discogs failing is for the caller, as with any song."""
    named = split(os.path.splitext(item["file"])[0], said)
    if not named:
        why = (f"You said it's from {said}, but the rest of its name doesn't say which track it is. Use I know what this is… to give its artist "
               "and title.") if said else UNNAMED
        return dict(status="notfound", options=[], reasons=[], why=why)
    game, track, year = named
    missed = []
    try:
        wiki = wikipedia(game, year, os.path.join(cache, "wikipedia"))
    except (OSError, ValueError, http.client.HTTPException):
        wiki = None
        missed.append("Wikipedia")
    names = [game] + ([wiki["title"]] if wiki and not wiki.get("others") and rv.norm(wiki["title"]) != rv.norm(game) else [])
    disc = soundtrack(item, names, track, history, library_keys)
    if wiki and wiki.get("others") and _a_year(disc.get("year")):  # two games go by that name: the soundtrack's year may say which
        near = [p for p in [wiki, *wiki["others"]] if p["year"] and abs(p["year"] - _a_year(disc.get("year"))) <= 1]
        if len(near) == 1:
            wiki = {k: v for k, v in near[0].items() if k != "others"}
    upload = official_upload(item["src"], names, track, item.get("secs"), os.path.join(cache, "youtube"), stopping)
    found = propose(game, track, year, disc, wiki, upload, wiki_down="Wikipedia" in missed)
    if missed:
        found["retry"] = True
        found["why"] += f" {' and '.join(missed)} couldn't be reached, so it'll be looked up again next time."
    return found


def main(argv=None):
    """`pressling game FILE [--game NAME]`: the card Curate would leave for this file, as JSON."""
    import argparse
    p = argparse.ArgumentParser(prog="pressling game")
    p.add_argument("file")
    p.add_argument("--game", default="", help="the game, film or show it's from, if its name doesn't say")
    p.add_argument("--library", action="append", default=[], help="your library, to say if it's already there")
    p.add_argument("--cache", default=os.path.join(os.path.expanduser("~"), ".cache", "pressling"))
    args = p.parse_args(argv)
    from . import discogs
    discogs.CACHE_DIR = os.path.join(args.cache, "discogs")
    item = dict(key="1", src=args.file, file=os.path.basename(args.file), secs=parse.probe(args.file)[1])
    library_keys = rv.load_library_keys(args.library) if args.library else {}
    found = look_up(item, {}, library_keys, args.cache, args.game)
    json.dump(found, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main()
