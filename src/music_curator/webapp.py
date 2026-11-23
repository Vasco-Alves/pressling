"""`pressling serve`: a small local web app to explore the library, follow the pipeline and check songs by ear.

Standard library only. It listens on 127.0.0.1, and with `--lan` on your home network too, behind a private link. Browsing only reads your music: your answers go to decisions.json (next to the log),
which `pressling apply` and your agent act on later. The one thing that adds to your library is the Curate button (see curate.py),
and the one outside service it talks to is Discogs. Looks right, on Double-check, writes to the log straight away, the way `pressling verified`
does: it changes no music.
"""
import collections
import copy
import csv
import datetime
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
import re
import secrets
import socket
import socketserver
import sys
import threading
import time
import unicodedata
import urllib.parse
import webbrowser

from . import config, folders, library, saving, tagwriter

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"), "/glass.css": ("glass.css", "text/css; charset=utf-8"), "/hifi.css": ("hifi.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/effects.js": ("effects.js", "text/javascript; charset=utf-8"), "/packs.js": ("packs.js", "text/javascript; charset=utf-8"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; media-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
LAN_COOKIE = "curator_lan"
PC_ONLY = {"/api/better/swap", "/api/settings/better", "/api/settings", "/api/settings/compare", "/api/settings/extras", "/api/settings/check", "/api/settings/pick", "/api/spotify/check", "/api/spotify/move", "/api/spotify/stream", "/api/spotify/send", "/api/export/copy", "/api/export/playlists"}  # choosing folders, writing files elsewhere and signing in to Spotify all happen on the computer
ACTIONS = {"keep", "genre", "identify", "drop", "recheck", "better", "cover", "flag", "add", "added", "remove", "pick", "stream", "wrong", "game"}
ANSWERS_CHANGED = "Another program changed your answers at that very moment, so this wasn't saved. Try again."  # `pressling apply`, say, taking away the ones it carried out
ANSWERS_BUSY = "Your answers file couldn't be opened just now, so nothing was saved. Try again in a moment."  # saving anyway could put back answers that were taken out of it since it was last read
WORDS = dict(keep="keep as it is", genre="change the genre", identify="identify it", drop="not worth keeping",
             recheck="look at it again", better="find a better copy", cover="find a cover", flag="flagged", add="add it to a playlist", added="added to a playlist", remove="take it out of a Spotify playlist",
             pick="use the version you picked", stream="it's on Spotify", wrong="take it out and look again")
LOW_KBPS = 128  # below this a song is worth a better copy
CLOSE_GAP_S = 6  # an Auto-matched song this far from the record's length is worth a closer listen (Curate lets in up to 20 s)
SMALL_COVER_PX = 500  # a cover smaller than this on its short side is worth a look
GOOD_KBPS = 192  # below this, worth knowing while you listen
LENGTH_SAID = re.compile(r"(.*?),\s*(no length on the record|length matches the record|length within (\d+) s of the record)$")  # how Curate writes the length it went by
DISCARD_S = 2  # how long a refused request's leftover body gets read before the connection closes
BODY_TIMEOUT_S = 15  # how long to wait for a request body that was promised
EXPORT_BODY_MAX = 1 << 20  # room for about 70,000 songs
IMPORT_BODY_MAX = 8 << 20  # a playlist file can list a whole library, with the full path of every song
ENTRY = re.compile(r"^\s*[-*]\s+\*\*(.+?)\*\*(?:\s*\([^)]*\))?\s*:\s*(.+?)\s*$")


def norm(p):
    return os.path.normcase(os.path.normpath(p))


def text_key(s):
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^\w]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def is_date(text):
    try:
        datetime.datetime.fromisoformat(text)
        return True
    except (TypeError, ValueError):
        return False


def parse_range(header, size):
    """(first, last) byte of a Range header, None without one, 'bad' if it can't be satisfied."""
    m = re.fullmatch(r"bytes=(\d{0,18})-(\d{0,18})", (header or "").strip())
    if not m or not (m[1] or m[2]):
        return None
    if m[1]:
        first, last = int(m[1]), min(int(m[2]), size - 1) if m[2] else size - 1
    else:
        first, last = max(0, size - int(m[2])), size - 1
    return "bad" if first > last or first >= size else (first, last)


def genre_options(text, current=""):
    """'Techno / Acid / Hardcore' -> a clean list of the styles to choose from (only names, never a sentence)."""
    opts = []
    for part in text.split("/"):
        part = re.sub(r"^the album lists\s+", "", part.strip(), flags=re.I).split(",")[0].strip()
        if part and len(part) <= 30 and len(part.split()) <= 3 and part not in opts:
            opts.append(part)
    if current and current not in opts:
        opts.insert(0, current)
    return opts if len(opts) >= 2 else []


def first_clause(notes, needle):
    """The part of a ';'-separated note that mentions needle."""
    for part in notes.split(";"):
        if needle in part.lower():
            return part.strip()
    return notes.strip()


def trust_of(row):
    if row is None:
        return "unknown"
    notes, link = row.get("notes", "").lower(), row.get("discogs", "").lower()
    if "unverified" in notes or "placeholder" in notes or "original not found" in notes:
        return "unverified"
    if "matched automatically" in notes or "picked by you" in notes:
        return "auto"
    if "discogs.com" in link or "musicbrainz.org" in link:
        return "verified"
    return "source" if link else "unknown"


def checks_for(song, row, reason, flagged):
    """Why a person should look at this song (empty: nothing to decide)."""
    checks = []
    if song["area"] != "library":
        if song["area"] == "review" or reason:
            checks.append(dict(kind="waiting", why=reason or "Not verified yet, so it isn't in your library."))
        return checks
    notes = (row or {}).get("notes", "")
    low = notes.lower()
    m = re.search(r"genre toss-up:\s*([^;]+)", notes, re.I)
    if m:
        options = genre_options(m.group(1), song["genre"])
        why = "Discogs lists " + ", ".join(options) + ". Which one fits?" if options else m.group(1).strip()
        checks.append(dict(kind="genre", why=why, options=options))
    elif re.search(r"genre (is )?a guess", low):
        checks.append(dict(kind="genre", why="The genre is a guess. Does it sound right?", options=[]))
    if "unverified" in low or "placeholder" in low or "original not found" in low:
        needle = "unverified" if "unverified" in low else "placeholder" if "placeholder" in low else "original not found"
        checks.append(dict(kind="unverified", why=first_clause(notes, needle)))
    if row and row.get("status", "").startswith("check"):
        checks.append(dict(kind="problem", why=row["status"].removeprefix("check:").strip()))
    if song["kbps"] and song["kbps"] < LOW_KBPS:
        checks.append(dict(kind="quality", why=f"Only {song['kbps']} kbps. A better copy might exist."))
    if not song["cover"]:
        checks.append(dict(kind="cover", why="No cover yet."))
    if flagged:
        checks.append(dict(kind="flag", why=flagged))
    return checks


def seconds(text):
    """'6:32' -> 392, '1:02:03' -> 3723. None if it isn't a length."""
    parts = str(text or "").strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total or None


def bare_title(title):
    """A title without what's in brackets: the versions of one song share it."""
    return text_key(re.sub(r"[(\[][^)\]]*[)\]]", "", title or ""))


def _no_featured(title):
    return text_key(re.sub(r"[(\[]\s*(?:feat\.?|ft\.?|featuring)\s[^)\]]*[)\]]", "", title or "", flags=re.I))


def double_check(song, row, record, tracks, px, original):
    """What to look at in a song Curate added with nobody double-checking: how it got in, anything worth a closer listen, and the versions on its record.
    record: what's known about the record (discogs_index), tracks: its tracklist, px: the cover's size, original: whether the downloaded file is still there."""
    notes = [p.strip() for p in (row or {}).get("notes", "").split(";") if p.strip()]
    said = next((p[len(tagwriter.AUTO_NOTE):].lstrip(" :") for p in notes if p.startswith(tagwriter.AUTO_NOTE)), None)
    closer = []
    if said is None:
        why = "You picked the version on To check, and Curate added it."
        closer.append("You picked the version yourself")
    else:
        why = f"Curate added it by itself: {said}." if said else "Curate added it by itself."
        m = LENGTH_SAID.match(said)
        how, length = (m[1], m[2]) if m else (said, "")
        if how.startswith("length matches"):
            closer.append("Chosen from several versions by its length")
        elif how.startswith("untitled track"):
            closer.append("An untitled side, chosen by your file's name")
        elif re.match(r"\d+ tracks on the record share", how):
            closer.append(f"{how.split()[0]} tracks on the record share its title")
        if m and m[3] and int(m[3]) > CLOSE_GAP_S:
            closer.append(f"{m[3]} s off the record's length")
        elif length == "no length on the record":
            closer.append("The record lists no length to compare")
    if any(p.lower().startswith("genre toss-up") for p in notes):
        closer.append("Its genre was a toss-up")
    if song["kbps"] and song["kbps"] < GOOD_KBPS:
        closer.append(f"Only {song['kbps']} kbps")
    if not song["cover"]:
        closer.append("No cover")
    elif px and min(px) < SMALL_COVER_PX:
        closer.append(f"Small cover, {px[0]} × {px[1]}")
    if any(c["kind"] == "better" for c in song["checks"]):
        closer.append("A better copy may exist")
    title, versions = bare_title(song["title"]), []
    for pos, name, dur in tracks or []:
        if bare_title(name) == title and len(versions) < 12:
            length = seconds(dur)
            versions.append(dict(pos=pos, title=name, dur=dur, gap=round(song["secs"] - length) if length and song["secs"] else None, this=_no_featured(name) == _no_featured(song["title"])))
    year = record.get("year") if record else None
    disc = dict(title=record["title"], label=record["label"], year=year if type(year) is int and year < 9999 else "") if record and record.get("title") else None  # a hand-made answer can say anything
    return dict(kind="auto", why=why, closer=closer, disc=disc, versions=versions, px=px, original=original)


def release_format(j):
    """'Vinyl, 12", EP' from a Discogs release. The speed says nothing anyone browsing would want."""
    first = (j.get("formats") or [{}])[0]
    return ", ".join(x for x in [first.get("name") or "", *[d for d in first.get("descriptions") or [] if "RPM" not in d]] if x)


def discogs_index(cache_dir):
    """Label, country, format and track count per release and per master, from the saved Discogs answers (never the network)."""
    releases, masters = {}, {}
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return releases, masters
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(cache_dir, name), encoding="utf-8") as f:
                j = json.load(f)
        except (OSError, ValueError):
            continue
        try:
            if not isinstance(j, dict) or not j.get("labels") or "id" not in j:
                continue
            label = re.sub(r"\s*\(\d+\)$", "", j["labels"][0].get("name") or "").strip()
            info = dict(label="" if label.lower().startswith("not on label") else label, country=j.get("country") or "", year=j.get("year") or 9999,
                        format=release_format(j), tracks=sum(1 for t in j.get("tracklist") or [] if t.get("type_", "track") == "track"),
                        title=str(j.get("title") or ""), file=name)  # the tracklist itself is read only when a song needs it
            releases[j["id"]] = info
            mid = j.get("master_id")
            if mid and (mid not in masters or info["year"] < masters[mid]["year"]):  # the earliest pressing
                masters[mid] = info
        except (AttributeError, TypeError, KeyError, IndexError):  # one odd saved answer is simply skipped
            continue
    return releases, masters


EXTRAS = ("curator", "spotify")


def extras_state(collection):
    """Which extras are on. The app is a music player; the Curator (looking up downloads, To check, Progress) and Spotify are extras.
    A choice made in Settings stands. Until there is one, the Curator is on when there's a downloads folder or a log to go on, and
    Spotify when it's been set up or checked before."""
    saved = config.cfg().get("extras")
    saved = saved if isinstance(saved, dict) else {}
    auto = dict(curator=bool(collection.paths.get("inbox") or collection.log_rows), spotify=bool(config.get("spotify", "client_id") or collection.saved_lists))
    return {k: saved[k] if isinstance(saved.get(k), bool) else auto[k] for k in EXTRAS}


def save_extras(body):
    """Turn extras on or off: {"curator": true, "spotify": false}, either or both. Returns an error, or None once it's saved."""
    changes = {k: body[k] for k in EXTRAS if k in body}
    if not changes or set(body) - set(EXTRAS) or any(not isinstance(v, bool) for v in changes.values()):
        return "bad request"
    try:
        config.save_table(config.target_path(), "extras", changes)
        config.load(config.target_path())
    except (OSError, ValueError) as ex:
        return f"Couldn't save that: {ex}"
    return None


class Collection:
    """Everything the page shows, rebuilt cheaply: files are re-read only when they change."""

    def __init__(self, paths):
        self.paths = paths
        self.lock = threading.RLock()
        self.parsed = {}
        self.songs = {}
        self.log_sig, self.log_rows, self.log_latest, self.log_moved, self.log_first = None, [], {}, {}, {}
        self.log_done, self.log_back = set(), {}
        self.list_sig, self.list_reasons = None, {}
        self.labels = None
        self.decisions, self.decisions_sig, self.decisions_broken = {}, None, False
        self.ideas_sig, self.ideas, self.ideas_names = None, {}, []
        self.curated_sig, self.curated = None, {}
        self.hits_sig, self.hits = None, {}
        self.better_sig, self.better = None, {}
        self.plays, self.plays_sig, self.plays_broken = {}, None, False
        self.saved_lists, self.saved_lists_sig = {}, None
        self.levels, self.levels_sig = {}, None
        self.waves, self.waves_sig = {}, None
        self.sound_edges, self.sound_edges_sig = {}, None
        self.own, self.own_sig = {}, None
        self.styles, self.styles_sig = {}, None
        self.cover_px, self.tracklists = {}, {}  # only for the songs on Double-check
        self._read_decisions()

    def _watch(self, name, path, keep):
        """Keep self.<name> in step with a JSON file, reading it only when it has changed. `keep` picks out what's wanted from the object in it, or returns None if that's the wrong shape.
        With no file there's nothing. One that can't be read just now leaves what was last read, and False is returned: what's in memory may then be less than the file holds."""
        try:
            st = os.stat(path)
        except (FileNotFoundError, NotADirectoryError, TypeError, ValueError):
            setattr(self, name, {})
            setattr(self, f"{name}_sig", None)
            return True
        except OSError:
            return False
        sig = (st.st_mtime_ns, st.st_size)
        if sig == getattr(self, f"{name}_sig"):
            return True
        try:
            with open(path, encoding="utf-8-sig") as f:
                saved = json.load(f)
            kept = keep(saved) if isinstance(saved, dict) else None
        except OSError:
            return False
        except (ValueError, KeyError, TypeError, AttributeError):  # damaged: nothing in it can be used, and the next save makes it whole again
            return True
        if kept is not None:
            setattr(self, name, kept)
            setattr(self, f"{name}_sig", sig)
        return True

    def _read_levels(self):
        """How loud each song was measured to be."""
        return self._watch("levels", levels_path(self.paths), lambda saved: {k: v for k, v in saved.items() if type(v) in (int, float)})

    def _read_waves(self):
        """The shape of each song that has been played."""
        return self._watch("waves", waves_path(self.paths), lambda saved: {k: v for k, v in saved.items() if isinstance(v, list) and all(type(x) is int and 0 <= x <= 100 for x in v)})

    def _read_edges(self):
        """Where the sound of each song that has been played starts and ends."""
        return self._watch("sound_edges", edges_path(self.paths), lambda saved: {k: v for k, v in saved.items() if isinstance(v, list) and len(v) == 2 and all(type(x) in (int, float) and 0 <= x < 1e6 for x in v) and v[0] < v[1]})

    def _read_saved_lists(self):
        """Your Spotify playlists as they were at the last Check Spotify."""
        from . import spotify  # here because it needs this module loaded first
        self._watch("saved_lists", spotify.cache_path(self.paths), lambda saved: saved)

    def _read_own(self):
        """The playlists you made here. `own_playlists` won't save over a file that can't be read."""
        from . import own_playlists  # here because it needs this module loaded first
        self._watch("own", own_lists_path(self.paths), own_playlists.well_formed)

    def _read_plays(self):
        """How often each song was played. Same care as the answers: a file that can't be read is never saved over.
        False if the file is there but couldn't be opened just now: what's in memory may then be behind it, or nothing at all."""
        path = plays_path(self.paths)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            self.plays, self.plays_sig, self.plays_broken = {}, None, False
            return True
        except (OSError, TypeError, ValueError):
            return False
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self.plays_sig:
            return True
        try:
            with open(path, encoding="utf-8-sig") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("not a list of songs")
        except OSError:
            return False
        except ValueError:
            self.plays_broken = True
            return True
        counted = lambda v: isinstance(v, dict) and type(v.get("plays")) is int and 0 < v["plays"] < 1_000_000
        self.plays, self.plays_sig, self.plays_broken = {k: v for k, v in loaded.items() if counted(v)}, sig, False
        return True

    def _read_styles(self):
        """Every style Discogs lists for each record (`genres.py`)."""
        self._watch("styles", styles_path(self.paths), lambda saved: {k: v for k, v in saved.items() if isinstance(v, dict) and isinstance(v.get("styles"), list)})

    def _read_hits(self):
        """What the last Check Spotify found for the songs still waiting."""
        self._watch("hits", spotify_hits_path(self.paths), lambda hits: {k: v["hit"] for k, v in hits.items() if isinstance(v, dict) and isinstance(v.get("hit"), dict)})

    def _read_better(self):
        """Links an agent left to possibly better copies (`pressling maybe-better`). Each one becomes a link on the page."""
        self._watch("better", better_links_path(self.paths), lambda links: {k: v for k, v in links.items() if isinstance(v, dict) and is_web_link(v.get("url")) and isinstance(v.get("why"), str)})

    def _read_curated(self):
        """What the last Curate run couldn't settle, and why."""
        self._watch("curated", curated_path(self.paths), lambda saved: {k: v for k, v in saved["songs"].items() if isinstance(v, dict) and isinstance(v.get("why"), str)})

    def _read_ideas(self):
        """The playlist ideas from `pressling suggest`, if any."""
        def keep(saved):
            songs, names = saved["songs"], saved["playlists"]
            if not isinstance(songs, dict) or not isinstance(names, list):
                return None
            self.ideas_names = [n for n in names if isinstance(n, str)]
            return {k: v for k, v in songs.items() if isinstance(v, dict) and isinstance(v.get("why"), str) and isinstance(v.get("fits"), list) and isinstance(v.get("also", []), list)}
        self._watch("ideas", suggestions_path(self.paths), keep)
        if self.ideas_sig is None:
            self.ideas_names = []

    def _read_decisions(self):
        """The answers can change behind the page's back: `pressling apply` takes away the ones it has carried out. False if the file is there but couldn't be looked at just now, so what's in memory may be out of date."""
        try:
            st = os.stat(self.paths["decisions"])
        except FileNotFoundError:
            self.decisions, self.decisions_sig, self.decisions_broken = {}, None, False
            return True
        except (OSError, TypeError, ValueError):
            return False
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self.decisions_sig:
            return True
        try:
            with open(self.paths["decisions"], encoding="utf-8-sig") as f:  # some editors add a byte-order mark
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("not a list of answers")
        except OSError:  # keep what we have and try on the next load
            return False
        except ValueError:  # hand-edited into something unreadable: saving over it would lose every answer in it
            self.decisions_broken = True
            return True
        self.decisions, self.decisions_sig, self.decisions_broken = {k: v for k, v in loaded.items() if isinstance(v, dict)}, sig, False
        return True

    def _read_log(self):
        path = self.paths["log"]
        try:
            st = os.stat(path)
        except (OSError, TypeError, ValueError):
            self.log_sig, self.log_rows, self.log_latest, self.log_moved, self.log_first = None, [], {}, {}, {}
            self.log_done, self.log_back = set(), {}
            return
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self.log_sig:
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
                rows = list(csv.DictReader(f, restval=""))
        except (OSError, ValueError, csv.Error):  # open in Excel right now, say: keep what we have and try on the next load
            self.log_sig = None
            return
        root, latest, moved, first = norm(self.paths["library"]) + os.sep, {}, {}, {}
        for r in rows:
            status = r.get("status") or ""
            d = norm(r["dest"]) if r.get("dest") and not status.startswith(("skipped", "error")) else ""  # a row that didn't write the file says nothing about the file there
            if d:
                latest[d] = r
                if not d.startswith(root):
                    moved.setdefault(os.path.basename(d), []).append(r)
            src = norm(r["src"]) if r.get("src") else ""
            if status == "ok" and is_date(r.get("date", "")):  # when the song first arrived, whatever changed later: a better copy comes from somewhere else, so its name counts too
                for known_as in filter(None, (src, log_name(r))):
                    first.setdefault(known_as, r["date"])
        self.log_sig, self.log_rows, self.log_latest, self.log_moved, self.log_first = sig, rows, latest, moved, first
        self.log_done, self.log_back = tagwriter.written_sources(rows, key=lambda src: norm(src) if src else "")  # one taken back out because the match was wrong is waiting again

    def _first_seen(self, row, otherwise=""):
        if not row:
            return otherwise
        return min(filter(None, (self.log_first.get(norm(row["src"])) if row.get("src") else "", self.log_first.get(log_name(row)))), default=otherwise)

    def _log_row(self, path, rel):
        """The song's log entry. After the whole library has moved, its old paths in the log still end in the same folders and name."""
        row = self.log_latest.get(norm(path))
        if row:
            return row
        wanted = os.sep + norm(rel)
        return next((r for r in reversed(self.log_moved.get(os.path.basename(wanted), [])) if norm(r["dest"]).endswith(wanted)), None)

    def _read_review_list(self):
        path = self.paths["review_list"]
        try:
            st = os.stat(path)
        except (OSError, TypeError, ValueError):
            self.list_sig, self.list_reasons = None, {}
            return
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self.list_sig:
            return
        reasons = {}
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                for line in f:
                    m = ENTRY.match(line)
                    if m:
                        reasons[text_key(m.group(1))] = m.group(2)
        except (OSError, ValueError):
            self.list_sig = None
            return
        self.list_sig, self.list_reasons = sig, reasons

    def _reason(self, stem, artist, title):
        keys = [text_key(stem), text_key(f"{artist} - {title}")]
        for k in keys:
            if k in self.list_reasons:
                return self.list_reasons[k]
        for k in keys:
            if len(k) >= 8:
                for known, why in self.list_reasons.items():
                    if len(known) >= 8 and (known in k or k in known):
                        return why
        return ""

    def _roots(self):
        yield "library", self.paths["library"], True
        if self.paths["review"]:
            yield "review", self.paths["review"], True
        for folder in self.paths["inbox"]:
            yield "inbox", folder, False

    @staticmethod
    def _audio_files(root, recursive):
        if recursive:
            for dp, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in files:
                    if not name.startswith(".") and os.path.splitext(name)[1].lower() in library.AUDIO:
                        yield os.path.join(dp, name)
        elif os.path.isdir(root):
            with os.scandir(root) as entries:
                for entry in entries:
                    try:
                        is_file = entry.is_file()
                    except OSError:
                        continue
                    if is_file and not entry.name.startswith(".") and os.path.splitext(entry.name)[1].lower() in library.AUDIO:
                        yield entry.path

    def refresh(self):
        with self.lock:
            self._read_decisions()
            self._read_ideas()
            self._read_curated()
            self._read_hits()
            self._read_better()
            self._read_plays()
            self._read_saved_lists()
            self._read_own()
            self._read_levels()
            self._read_styles()
            self._read_log()
            self._read_review_list()
            if self.labels is None:
                self.labels = discogs_index(os.path.join(self.paths["cache"], "discogs"))
            songs, seen = {}, set()
            for area, root, recursive in self._roots():
                try:
                    paths = list(self._audio_files(root, recursive))
                except (OSError, ValueError):  # a folder that can't be listed right now
                    continue
                for path in paths:
                    try:
                        n = norm(path)
                        if n in seen:
                            continue
                        seen.add(n)
                        st = os.stat(path)
                        sig = (st.st_mtime_ns, st.st_size)
                        known = self.parsed.get(n)
                        if not known or known[0] != sig:
                            known = self.parsed[n] = (sig, library.read_song(path))
                        song = self._song(area, root, path, known[1], st)
                    except (OSError, ValueError, AttributeError, TypeError, KeyError):  # one bad file never hides the rest of the library
                        continue
                    songs[song["id"]] = song
            self.songs = songs

    def new_downloads(self):
        """Songs in your downloads folders that Curate hasn't looked at yet, for the page to mention: {count, names (the first few)}.
        Only those folders are read (not the library), and the log and Curate's notes only when they've changed."""
        with self.lock:
            self._read_log()
            self._read_curated()
            found = []
            for folder in self.paths["inbox"] or []:
                where = os.path.basename(os.path.normpath(folder))
                for path in self._audio_files(folder, False):
                    name = os.path.basename(path)
                    if norm(path) not in self.log_done and f"inbox/{where}/{name}" not in self.curated:
                        found.append(name)
        return dict(count=len(found), names=sorted(found, key=str.lower)[:5])

    def _song(self, area, root, path, info, st):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        where = os.path.basename(os.path.normpath(root))
        key = f"{area}/{rel}" if area != "inbox" else f"inbox/{where}/{rel}"
        row = self._log_row(path, rel) if area == "library" else None
        link = (row or {}).get("discogs") or (info["comment"] if info["comment"].startswith("http") else "")
        song = dict(id=hashlib.sha1(norm(path).encode("utf-8", "surrogatepass")).hexdigest()[:12], key=key, area=area, where=where,
                    folder=os.path.dirname(rel), file=os.path.basename(path), artist=info["artist"], title=info["title"],
                    album=info["album"], year=info["year"], genre=info["genre"], secs=info["secs"], kbps=info["kbps"],
                    bytes=st.st_size, cover=info["cover"], v=str(st.st_mtime_ns), link=link, done=area == "inbox" and norm(path) in self.log_done,
                    added=self._first_seen(row, (row or {}).get("date", "")), was=os.path.basename((row or {}).get("src", "")),
                    notes=(row or {}).get("notes", ""), trust=trust_of(row), label="", country="", format="", tracks=0, also=[], wide=False, _path=path, _rel=rel, _tagless=info.get("tagless", False))
        heard = self.plays.get(play_key(song))
        song.update(plays=heard["plays"] if heard else 0, played=str(heard.get("last") or "") if heard else "", lufs=self.levels.get(level_key(song)))
        m, record = re.search(r"discogs\.com/(release|master)/(\d{1,12})(?!\d)", link), None
        if m and self.labels:
            record = self.labels[0 if m[1] == "release" else 1].get(int(m[2]))
            if record:
                song.update(label=record["label"], country=record["country"], format=record["format"], tracks=record["tracks"])
        styles = self.styles.get(record_of(link))
        if styles:  # the file keeps its one genre: these are for finding it under the others
            song.update(also=[x for x in styles["styles"] if isinstance(x, str) and x and x != song["genre"]], wide=bool(styles.get("wide")))
        stem = os.path.splitext(song["file"])[0]
        held = self.curated.get(key) if area == "inbox" and not song["done"] else None
        listed = self._reason(stem, song["artist"], song["title"]) if area != "library" else ""
        reason = (held["why"] or "Not verified yet, so it isn't in your library.") if held and (held.get("said") or not listed) else listed  # what someone found out says more than Curate's names and lengths, unless Curate is answering what you told it; a song held back always gets a line on the page, even if the reason was left empty
        back = self.log_back.get(norm(path)) if area == "inbox" and not song["done"] and not held else None
        if back is not None and not reason:  # taken out of the library and not looked up again yet
            reason = f"You said Curate's match wasn't right{f': {back}' if back else ''}. Press Curate to look it up again."
        flag = self.decisions.get(f"flag:{key}")
        song["checks"] = checks_for(song, row, reason, (flag or {}).get("note") or ("Flagged by you" if flag else ""))
        song["looked"] = bool(held) and not held.get("retry")  # one held back by a network blip is worth another go
        if held:
            waiting = next(c for c in song["checks"] if c["kind"] == "waiting")
            waiting["options"] = [dict({k: o.get(k) for k in ("id", "pos", "artist", "title", "dur", "gap")}, **{k: str(o[k]) for k in ("note", "why") if o.get(k)})
                                  for o in held.get("options") or [] if isinstance(o, dict)]  # a game song's ways in each say why
            record = held.get("record") if isinstance(held.get("record"), dict) else {}
            if str(record.get("discogs", "")).startswith("https://www.discogs.com/"):  # it becomes a link on the page
                waiting["record"] = dict(discogs=record["discogs"], title=str(record.get("release_title", "")), labels=str(record.get("labels", "")))
            if record.get("game") and record.get("folder"):  # a game or film song: where it would go, and its page
                wiki = str(record.get("wiki") or "")
                waiting["game"] = dict(name=str(record["game"]), year=str(record.get("year") or ""), folder=str(record["folder"]), genre=str(record.get("genre") or ""),
                                       kind=str(record.get("kind") or "game"), wiki=wiki if wiki.startswith("https://en.wikipedia.org/wiki/") else "")
            heard = held.get("audio") if isinstance(held.get("audio"), dict) else {}
            match = heard.get("match") if isinstance(heard.get("match"), dict) else None
            if heard.get("size") == st.st_size and match and re.fullmatch(r"https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}", str(match.get("url", ""))):  # it becomes a link on the page
                waiting["audio"] = dict(strong=True, url=match["url"], option=str(match.get("option") or ""))
            elif heard.get("size") == st.st_size and "match" not in heard and type(heard.get("tried")) is int and heard["tried"] > 0:
                waiting["audio"] = dict(strong=False, tried=heard["tried"])
        hit = self.hits.get(key) if area != "library" and not song["done"] else None
        if hit and re.fullmatch(r"[A-Za-z0-9]{10,40}", str(hit.get("id", ""))):
            song["checks"].append(dict(kind="spotify", why=spotify_words(hit), link=f"https://open.spotify.com/track/{hit['id']}"))
        found = self.better.get(key)
        if found and found.get("secs") == song["secs"]:  # other audio by now means the copy was swapped in, so the link has done its job (new tags don't change the length)
            song["checks"] = [c for c in song["checks"] if c["kind"] != "quality"]  # with a copy to look at, "a better one might exist" says nothing new
            song["checks"].append(dict(kind="better", why=found["why"], link=found["url"], checked=bool(found.get("checked"))))
        if key in self.ideas:
            song["checks"].append(dict(kind="playlist", why=self.ideas[key]["why"], fits=self.ideas[key]["fits"], also=self.ideas[key].get("also", [])))
        if song["trust"] == "auto":
            song["checks"].append(double_check(song, row, record, self._tracks_of(record), self._cover_size(path, st) if song["cover"] else None, self._original(row)))
        return song

    def _tracks_of(self, record):
        """[(position, title, length)] on a record, from its saved Discogs answer."""
        if not record:
            return []
        if record["file"] not in self.tracklists:
            try:
                with open(os.path.join(self.paths["cache"], "discogs", record["file"]), encoding="utf-8") as f:
                    j = json.load(f)
                self.tracklists[record["file"]] = [(str(t.get("position") or ""), str(t.get("title") or ""), str(t.get("duration") or ""))
                                                   for t in j.get("tracklist") or [] if isinstance(t, dict) and t.get("type_", "track") == "track"]
            except (OSError, ValueError, AttributeError, TypeError):
                self.tracklists[record["file"]] = []
        return self.tracklists[record["file"]]

    def _cover_size(self, path, st):
        """[width, height] of a song's cover, read again only when the file changes."""
        n, sig = norm(path), (st.st_mtime_ns, st.st_size)
        known = self.cover_px.get(n)
        if not known or known[0] != sig:
            found = library.read_cover(path)
            known = self.cover_px[n] = (sig, library.image_size(found[0]) if found else None)
        return list(known[1]) if known[1] else None

    def _original(self, row):
        """Whether the downloaded file a library song was made from is still in one of your downloads folders."""
        src = (row or {}).get("src") or ""
        return bool(src) and any(norm(os.path.dirname(src)) == norm(d) for d in self.paths["inbox"]) and os.path.isfile(src)

    def _rows_of(self, path, rel):
        """Every log row about the file at path, oldest first. After the whole library has moved, its old paths still end in the same folders and name."""
        rows = [r for r in self.log_rows if r.get("dest") and not (r.get("status") or "").startswith(("skipped", "error"))]
        mine = [r for r in rows if norm(r["dest"]) == norm(path)]
        wanted = os.sep + norm(rel)
        return mine or [r for r in rows if norm(r["dest"]).endswith(wanted)]

    def checked(self, song_id, undo=False):
        """Looks right, from the page: an Auto-matched song you checked is written down as verified, the way `pressling verified` does it.
        With undo, the notes from before come back. Returns what the page shows for the song now, or {error}."""
        from . import apply, curate  # here because they need this module loaded first
        with self.lock:
            song = self.songs.get(str(song_id))
            if not song or song["area"] != "library" or not os.path.exists(song["_path"]):
                return dict(error="That song isn't in your library any more. Refresh the page.")
            try:
                with curate._OneAtATime(os.path.join(os.path.dirname(self.paths["log"]), "curate.lock"), wait=1):  # Curate and the command line write to the log too. A command that only adds a row is done in a moment
                    self._read_log()  # read with the lock held, so the new row is made from what the log says now and not from what it said when the page loaded
                    rows = self._rows_of(song["_path"], song["_rel"])
                    row = curate.unchecked_row(rows) if undo else curate.checked_row(rows[-1] if rows else None, curate.CHECKED_ON_THE_PAGE)
                    if not row:
                        return dict(error="That was changed somewhere else meanwhile. Refresh the page." if undo else "It isn't Auto-matched any more. Refresh the page.")
                    handle, writer = apply._open_log(self.paths["log"])
                    try:
                        writer.writerow(row)
                    finally:
                        handle.close()
            except curate.Stop:
                return dict(error="Curate is running. Try again when it's finished.")
            except (OSError, SystemExit):
                return dict(error="The log can't be written just now (is it open in another program?), so nothing changed.")
            self._read_log()
            latest = self._log_row(song["_path"], song["_rel"])
            song.update(trust=trust_of(latest), notes=(latest or {}).get("notes", ""))
            return dict(trust=song["trust"], notes=song["notes"])

    def progress(self):
        rows = self.log_rows
        last = {}
        for r in rows:
            if r.get("src"):
                last[norm(r["src"])] = r
        kinds = collections.Counter()
        for r in last.values():
            s = r.get("status", "")
            kinds["Written" if s == "ok" else "Removed later" if s.startswith("removed") else "Skipped" if s.startswith("skipped")
                  else "Needs a look" if s.startswith("check") else "Failed" if s.startswith("error") else "Other"] += 1
        here = {norm(s["_path"]): s for s in self.songs.values() if s["area"] == "library"}
        moved = {}  # old log path -> song, for a library that has moved
        for key, s in here.items():
            if key not in self.log_latest:
                wanted = os.sep + norm(s["_rel"])
                for r in self.log_moved.get(os.path.basename(wanted), []):
                    if norm(r["dest"]).endswith(wanted):
                        moved[norm(r["dest"])] = key
        first = {}
        for r in rows:
            d, when = norm(r["dest"]) if r.get("dest") else "", r.get("date", "")
            key = d if d in here else moved.get(d)
            if r.get("status") == "ok" and key and key not in first and is_date(when):
                first[key] = self._first_seen(r, when)
        stamps = sorted(first.values())
        timeline, unit = [], "day"
        if stamps:
            try:
                span = (datetime.datetime.fromisoformat(stamps[-1]) - datetime.datetime.fromisoformat(stamps[0])).days
            except TypeError:  # a mix of dates with and without a time zone
                span = 0
            unit, cut = ("hour", 13) if span <= 2 else ("day", 10) if span <= 120 else ("month", 7)
            buckets = collections.Counter(s[:cut] for s in stamps)
            total = 0
            for b in sorted(buckets):
                total += buckets[b]
                timeline.append(dict(t=b, added=buckets[b], total=total))
        recent = [dict(when=r.get("date", "") if is_date(r.get("date", "")) else "", status=r.get("status", ""), was=os.path.basename(r.get("src") or ""),
                       now=os.path.basename(r.get("dest") or ""), artist=r.get("artist", ""), title=r.get("title", ""))
                  for r in rows[-40:][::-1]]
        return dict(outcomes=kinds.most_common(), timeline=timeline, unit=unit, recent=recent, rows=len(rows))

    def snapshot(self):
        with self.lock:
            self.refresh()
            songs = [{k: v for k, v in s.items() if not k.startswith("_")} for s in self.songs.values()]
            from . import own_playlists, spotify, swap  # here because they need this module loaded first
            return dict(songs=songs, decisions=self.decisions, progress=self.progress(),
                        meta=dict(library=os.path.basename(os.path.normpath(self.paths["library"])), spotify=spotify.status(self.paths),
                                  has_log=bool(self.log_rows), has_review=bool(self.paths["review"]), playlists=self.ideas_names, my_playlists=spotify.playable(self.songs.values(), self.saved_lists), own_playlists=own_playlists.listed(self), extras=extras_state(self),
                                  decisions_file=os.path.basename(self.paths["decisions"]), fetch=swap.setting()[0]))

    def decide(self, body):
        """Save (or undo, with action null) one decision. Returns an error message, or None."""
        for _ in range(3):  # another program may have saved the answers at that very moment: then it's done again on what that one saved
            problem = self._decide(body)
            if problem != ANSWERS_CHANGED:
                break
        return problem

    def _decide(self, body):
        with self.lock:
            if not self._read_decisions():
                return ANSWERS_BUSY
            if self.decisions_broken:
                return f"Your answers file ({os.path.basename(self.paths['decisions'])}) can't be read, so nothing was saved. Ask your AI app to repair it."
            song = self.songs.get(str(body.get("id")))
            kind, action = str(body.get("kind") or ""), body.get("action")
            if not song:
                return "unknown song"
            if not os.path.exists(song["_path"]):
                return "This song has moved since the page was loaded. Refresh the page and answer again."
            if not re.fullmatch(r"[a-z]{1,20}", kind):
                return "bad kind"
            key = f"{kind}:{song['key']}"
            answers = dict(self.decisions)  # a copy: the answers in memory change only once the change is in the file
            if action is None:
                answers.pop(key, None)
            elif not isinstance(action, str) or action not in ACTIONS:
                return "bad action"
            else:
                answers[key] = dict(
                    kind=kind, action=action, song=f"{song['artist']} - {song['title']}".strip(" -"), file=song["key"],
                    genre=str(body.get("genre") or "")[:60], note=str(body.get("note") or "")[:1000],
                    at=datetime.datetime.now().isoformat(timespec="seconds"))
            return self._save_decisions(answers)

    def _save_decisions(self, answers):
        """Write these answers to the file, and only then take them as the ones in memory: a save that fails leaves the page showing what the file really says. Returns an error message, or None."""
        path = self.paths["decisions"]
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            saving.write(path, lambda f: json.dump(answers, f, ensure_ascii=False, indent=1), read_as=self.decisions_sig, encoding="utf-8", errors="replace")
        except saving.Changed:
            return ANSWERS_CHANGED
        except OSError as ex:
            return f"Couldn't save that: {ex.strerror or ex}"
        self.decisions = answers
        return None

    def update_answers(self, change):
        """Change several answers in one step, on the latest version of the list. Returns an error message, or None.
        `change` may be run more than once, each time on a fresh copy of the list: if another program saved the answers meanwhile, it's done again on what that one saved."""
        for _ in range(3):
            problem = self._update_answers(change)
            if problem != ANSWERS_CHANGED:
                break
        return problem

    def _update_answers(self, change):
        with self.lock:
            if not self._read_decisions():
                return ANSWERS_BUSY
            if self.decisions_broken:
                return f"Your answers file ({os.path.basename(self.paths['decisions'])}) can't be read, so nothing was saved."
            answers = copy.deepcopy(self.decisions)  # a copy all the way down: `change` alters answers in place
            change(answers)
            return self._save_decisions(answers)

    def played(self, song_id):
        """Count one listen. Returns what the page shows for that song now, or an error."""
        with self.lock:
            if not self._read_plays():  # saving what's in memory could wipe counts the file has and this run never got to read
                return dict(error="Your play counts couldn't be opened just now, so this play wasn't counted.")
            if self.plays_broken:
                return dict(error=f"Your play counts ({os.path.basename(plays_path(self.paths))}) can't be read, so this play wasn't counted.")
            song = self.songs.get(str(song_id))
            if not song:
                return dict(error="unknown song")
            key, now = play_key(song), datetime.datetime.now().isoformat(timespec="seconds")
            was = self.plays.get(key) or {}
            entry = dict(song=f"{song['artist']} - {song['title']}".strip(" -") or song["file"], plays=was.get("plays", 0) + 1, first=was.get("first") or now, last=now)
            path = plays_path(self.paths)
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                saving.write(path, lambda f: json.dump({**self.plays, key: entry}, f, ensure_ascii=False, indent=1), encoding="utf-8", errors="replace")
            except OSError as ex:
                return dict(error=f"Couldn't save that play: {ex}")
            self.plays[key] = entry
            song.update(plays=entry["plays"], played=now)
            return dict(plays=entry["plays"], played=now)

    def _measured(self, song_id, word, kept, read, path_of, measure):
        """Something measured from a song's audio: from the file it's kept in, or measured now and added to it. Returns {word: value}, with None if it can't be told, or {error}."""
        with self.lock:
            read()
            song = self.songs.get(str(song_id))
            if not song:
                return dict(error="unknown song")
            key, path = level_key(song), song["_path"]
            if key in getattr(self, kept):
                return {word: getattr(self, kept)[key]}
        value = measure(path)  # outside the lock: the rest of the app doesn't wait for this
        if value is None:
            return {word: None}
        with self.lock:
            if read() is False:  # the file is there and couldn't be opened: saving what's in memory could cut it down to this one song. It's measured again another time
                return {word: value}
            getattr(self, kept)[key] = value
            try:
                os.makedirs(os.path.dirname(path_of(self.paths)) or ".", exist_ok=True)
                saving.write(path_of(self.paths), lambda f: json.dump(getattr(self, kept), f, ensure_ascii=False), encoding="utf-8", errors="replace")
            except OSError:  # only costs measuring it again next time
                pass
        return {word: value}

    def level(self, song_id):
        """How loud a song is, measuring it the first time it's asked for."""
        from . import loudness
        return self._measured(song_id, "lufs", "levels", self._read_levels, levels_path, loudness.measure)

    def wave(self, song_id):
        """The shape of a song, measuring it the first time it's asked for."""
        from . import waveform
        return self._measured(song_id, "wave", "waves", self._read_waves, waves_path, waveform.measure)

    def edges(self, song_id):
        """Where the sound of a song starts and ends, measuring it the first time it's asked for."""
        from . import waveform
        return self._measured(song_id, "edges", "sound_edges", self._read_edges, edges_path, waveform.edges)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "curator"
    sys_version = ""

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionError, TimeoutError):  # browsers drop audio requests all the time when seeking
            self.close_connection = True

    def _reply(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if code >= 400:  # an unread request body must never be parsed as a second request
            self.send_header("Connection", "close")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        if code >= 400 and self.command == "POST" and not self.body_read:
            self._discard_body()

    def _discard_body(self):
        """Read and drop what the client is still sending, so closing the connection doesn't wipe out the answer on its way."""
        try:
            left = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
            deadline = time.monotonic() + DISCARD_S
            self.connection.settimeout(1)
            while left > 0 and time.monotonic() < deadline:
                chunk = self.connection.recv(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)
        except (OSError, ValueError):
            pass

    def _json(self, obj, code=200):
        self._reply(code, json.dumps(obj, ensure_ascii=False).encode("utf-8", "replace"), "application/json; charset=utf-8",
                    [("Cache-Control", "no-store")])

    def _host_ok(self):  # blocks DNS rebinding: other sites can't reach this server through a name of their own
        return self.headers.get("Host", "").lower() in self.server.hosts

    def _client_ip(self):
        return self.client_address[0]

    def _on_this_computer(self):
        try:
            return ipaddress.ip_address(self._client_ip()).is_loopback
        except ValueError:
            return False

    def _let_in(self):
        """With the home network turned on, anyone but this computer has to be on that network and have come through the private link. False once they've been answered."""
        lan = self.server.lan
        if not lan or self._on_this_computer():
            return True
        try:
            nearby = ipaddress.ip_address(self._client_ip()) in lan["network"]
            given = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("key", [""])[0]
        except ValueError:
            nearby, given = False, ""
        if not nearby:
            self._reply(403, b"Forbidden")
            return False
        pairs = (part.strip().split("=", 1) for part in self.headers.get("Cookie", "").split(";") if "=" in part)
        held = next((value for name, value in pairs if name == LAN_COOKIE), "")
        key = lan["key"].encode()
        if hmac.compare_digest(held.encode("utf-8", "replace"), key):
            return True
        if self.command == "GET" and hmac.compare_digest(given.encode("utf-8", "replace"), key):  # from here on the phone remembers, and the key leaves the address bar
            self._reply(303, b"", headers=[("Location", "/"), ("Cache-Control", "no-store"), ("Set-Cookie", f"{LAN_COOKIE}={lan['key']}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")])
            return False
        self._reply(403, b"Open the private link shown on your computer.")
        return False

    def _route(self):
        try:
            return urllib.parse.urlsplit(self.path).path
        except ValueError:
            return None

    def _when_ready(self):
        """While this copy is still reading the library, everything waits its turn here, except the question "who is running?"."""
        ready = self.server.ready
        if ready is not None:
            ready.wait()

    def _running(self):
        """What a second start on this computer asks (who_is_on): which settings this copy runs with (the file as given, and as the system resolves it),
        whether it is still reading its library, how long it has been up, whether it opened the browser itself, and where it is on the home network if it is open to one."""
        if not self._on_this_computer():
            return self._reply(404, b"Not found")
        server, mine = self.server, os.path.abspath(config.target_path())
        try:
            real = os.path.realpath(mine)
        except (OSError, ValueError):
            real = mine
        starting = server.ready is not None and not server.ready.is_set()
        up = None if starting or server.began is None else round(time.monotonic() - server.began, 1)
        return self._json(dict(app="pressling", config_file=mine, config_real=real, starting=starting, up=up, opens=bool(server.opens), lan=server.lan_at))

    def do_GET(self):
        if not self._host_ok():
            return self._reply(403, b"Forbidden")
        if not self._let_in():
            return None
        route = self._route()
        if route is None:
            return self._reply(400, b"Bad request")
        if route == "/api/running":
            return self._running()
        self._when_ready()
        if route in STATIC:
            name, ctype = STATIC[route]
            with open(os.path.join(WEB, name), "rb") as f:
                body = f.read()
            headers = [("Cache-Control", "no-cache")] + ([("Content-Security-Policy", CSP)] if route == "/" else [])
            return self._reply(200, body, ctype, headers)
        if route == "/api/all":
            with self.server.settings_lock:  # checked on every load: the drive can go away, or come back
                collection = None
                if not folders.reason():
                    if not self.server.collection:
                        self.server.collection = Collection(app_paths())
                    collection = self.server.collection
            if not collection:
                return self._json(dict(setup=folders.state()))
            from . import curate, export  # here because curate needs this module loaded first
            snap = collection.snapshot()
            snap["meta"]["curate"] = curate.status(self.server)
            snap["meta"]["export"] = export.status(self.server)
            return self._json(snap)
        collection = self.server.collection
        if route == "/api/export":
            from . import export
            return self._json(export.status(self.server))
        if route == "/api/settings":
            return self._json(folders.state())
        if route == "/api/settings/compare":
            from . import curate
            return self._json(curate.compare_state())
        if route == "/api/curate":
            from . import curate
            return self._json(curate.status(self.server))
        if route == "/api/downloads/new":
            return self._json(self.server.collection.new_downloads() if self.server.collection else dict(count=0, names=[]))
        if route == "/api/better/swaps":
            from . import swap
            return self._json(swap.status(self.server))
        if route == "/api/settings/better":
            from . import swap
            how, command = swap.setting()
            return self._json(dict(fetch=how, command=command if not self.server.lan or self._on_this_computer() else ""))  # (your downloader's command is this computer's business)
        m = re.fullmatch(r"/api/(loudness|waveform|edges)/([0-9a-f]{12})", route)
        if m and collection:
            answer = {"loudness": collection.level, "waveform": collection.wave, "edges": collection.edges}[m[1]](m[2])
            return self._json(answer, 404 if answer.get("error") else 200)
        m = re.fullmatch(r"/(cover|audio)/([0-9a-f]{12})", route)
        song = collection.songs.get(m[2]) if m and collection else None  # only indexed files, never a path from the URL
        if not song:
            return self._reply(404, b"Not found")
        return self._cover(song) if m[1] == "cover" else self._audio(song)

    do_HEAD = do_GET

    def _cover(self, song):
        found = library.read_cover(song["_path"])
        if not found:
            return self._reply(404, b"No cover")
        self._reply(200, found[0], found[1], [("Cache-Control", "max-age=604800")])

    def _audio(self, song):
        path = song["_path"]
        try:
            size = os.path.getsize(path)
        except OSError:
            return self._reply(404, b"Not found")
        span = parse_range(self.headers.get("Range"), size)
        if span == "bad":
            return self._reply(416, headers=[("Content-Range", f"bytes */{size}")])
        first, last = span or (0, size - 1)
        self.send_response(206 if span else 200)
        self.send_header("Content-Type", library.AUDIO.get(os.path.splitext(path)[1].lower(), "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(last - first + 1))
        if span:
            self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        left = last - first + 1
        try:
            with open(path, "rb") as f:
                f.seek(first)
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except OSError:  # the file went away mid-song, or the browser stopped listening
            pass
        if left > 0:  # fewer bytes went out than the headers promised (the file got shorter, or moved): hanging up is the only way left to say so, or the browser waits for the rest for ever
            self.close_connection = True

    def do_POST(self):
        self.body_read = False
        if not self._host_ok():
            return self._reply(403, b"Forbidden")
        if not self._let_in():
            return None
        route = self._route()
        if route not in ("/api/better/swap", "/api/settings/better", "/api/decision", "/api/played", "/api/verified", "/api/settings", "/api/settings/compare", "/api/settings/extras", "/api/settings/check", "/api/settings/pick", "/api/spotify/check", "/api/spotify/move", "/api/spotify/stream", "/api/spotify/send", "/api/curate", "/api/curate/stop", "/api/export/list", "/api/export/copy", "/api/export/stop", "/api/export/playlists",
                         "/api/playlists/new", "/api/playlists/add", "/api/playlists/remove", "/api/playlists/move", "/api/playlists/rename", "/api/playlists/delete", "/api/playlists/import", "/api/playlists/describe"):
            return self._reply(404, b"Not found")
        self._when_ready()
        if self.server.lan and route in PC_ONLY and not self._on_this_computer():
            return self._json(dict(error="That can only be done on the computer the app runs on."), 403)
        origin = self.headers.get("Origin")
        try:
            foreign = bool(origin) and urllib.parse.urlsplit(origin).netloc.lower() not in self.server.hosts
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._reply(400, b"Bad request")
        if foreign:
            return self._reply(403, b"Forbidden")
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._reply(415, b"JSON only")
        if size < 0 or size > (IMPORT_BODY_MAX if route == "/api/playlists/import" else EXPORT_BODY_MAX if route.startswith(("/api/export", "/api/playlists")) else 65536):  # these can name every song in the list
            return self._reply(413, b"Too large")
        self.connection.settimeout(BODY_TIMEOUT_S)
        raw = self.rfile.read(size)
        self.connection.settimeout(None)
        self.body_read = True
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError):
            return self._json(dict(error="bad JSON"), 400)
        if not isinstance(body, dict):
            return self._json(dict(error="bad request"), 400)
        if route == "/api/settings/check":
            kind = body.get("kind")
            if not isinstance(kind, str) or kind not in folders.TITLES:
                return self._json(dict(error="bad kind"), 400)
            return self._json(folders.check(kind, body.get("path"), body.get("library")))
        if route == "/api/settings/pick":
            return self._json(folders.pick_folder(str(body.get("kind")), folders.clean(body.get("start"))))
        if route == "/api/settings":
            if self.server.curate_lock.locked():  # a run in progress is writing to the folders it started with
                return self._json(dict(error="Curate is running. Wait for it to finish, or stop it, then change your folders."), 409)
            with self.server.settings_lock:
                problem = folders.save(body)
                if problem:
                    return self._json(dict(field=problem[0], error=problem[1]), 400)
                self.server.collection = Collection(app_paths())
            return self._json(dict(ok=True))
        if not self.server.collection:
            return self._json(dict(error="choose your folders first"), 400)
        if route == "/api/played":
            answer = self.server.collection.played(body.get("id"))
            return self._json(answer, 400 if answer.get("error") else 200)
        if route == "/api/verified":
            if self.server.curate_lock.locked():  # a run in progress is writing to the log
                return self._json(dict(error="Curate is running. Try again when it's finished."), 409)
            answer = self.server.collection.checked(body.get("id"), body.get("undo") is True)
            return self._json(answer, 400 if answer.get("error") else 200)
        if route == "/api/settings/compare":
            from . import curate
            answer = curate.set_compare(self.server, body.get("on"))
            return self._json(answer, 400 if answer.get("error") else 200)
        if route == "/api/settings/extras":
            with self.server.settings_lock:
                problem = save_extras(body)
            return self._json(dict(error=problem), 400) if problem else self._json(dict(extras=extras_state(self.server.collection)))
        if route == "/api/settings/better":
            from . import swap
            with self.server.settings_lock:
                problem = swap.save_setting(body)
            return self._json(dict(error=problem), 400) if problem else self._json(dict(fetch=swap.setting()[0]))
        if route == "/api/better/swap":
            from . import swap
            answer = swap.start(self.server, body.get("id"))
            return self._json(answer, 400 if answer.get("error") else 200)
        if route in ("/api/curate", "/api/curate/stop"):
            from . import curate
            answer = curate.start(self.server, body.get("set_aside") is True) if route == "/api/curate" else curate.stop(self.server)
            return self._json(answer, 400 if answer.get("error") else 200)
        if route.startswith("/api/export"):
            from . import export
            answer = export.handle(self.server, route, body)
            return self._json(answer, 400 if answer.get("error") else 200)
        if route.startswith("/api/playlists"):
            from . import own_playlists
            answer = own_playlists.handle(self.server.collection, route, body)
            return self._json(answer, 400 if answer.get("error") else 200)
        if route in ("/api/spotify/check", "/api/spotify/move", "/api/spotify/stream", "/api/spotify/send"):
            names = body.get("playlists")
            if route.endswith("stream") and (not isinstance(names, list) or not 0 < len(names) <= 50 or not all(isinstance(n, str) and n for n in names)):
                return self._json(dict(error="Choose at least one playlist."), 400)
            if not self.server.spotify_lock.acquire(blocking=False):
                return self._json(dict(arrived=0, missing=0, moved=0, added=0, message="Already talking to Spotify."))
            try:
                from . import spotify
                if route.endswith("stream"):
                    return self._json(spotify.run_stream(self.server, body.get("id"), list(dict.fromkeys(names))))
                if route.endswith("send"):
                    return self._json(spotify.run_send(self.server, body.get("list")))
                return self._json(spotify.run(self.server) if route.endswith("check") else spotify.run_gather(self.server))
            finally:
                self.server.spotify_lock.release()
        error = self.server.collection.decide(body)
        return self._json(dict(error=error), 400) if error else self._json(dict(ok=True))


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"  # on Windows it would silently share a port that's already taken
    request_queue_size = 32  # so that several starts at once, each asking who is here, all get through
    ready = None  # set once the library has been read: until then everything but "who is running?" waits (None in a server made without serve())
    began = None  # when this copy was ready (time.monotonic()), for a second start that finds it
    opens = False  # whether this copy opens the browser itself when it starts
    lan_at = None  # where it is on the home network ("192.168.1.20:8765"), once `--lan` has opened it to one

    def server_bind(self):  # (the standard server also looks this computer's name up here, which can take a second or more and is used for nothing)
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
    settings_lock = threading.Lock()
    spotify_lock = threading.Lock()  # one check at a time
    spotify_client = None  # keeps the sign-in for as long as the app runs
    curate_lock = threading.Lock()  # one run at a time
    curate_state, curate_stop = None, None
    export_lock = threading.Lock()  # one copy at a time
    export_state, export_stop = None, None
    lan = None  # {network, key} once `--lan` has opened it to the home network


def app_paths():
    log = config.path("log") or ""
    return dict(library=config.path("library"), review=config.path("review") or "", review_list=config.path("review_list") or "",
                inbox=config.path("inbox"), log=log, cache=config.path("cache"), work=config.path("work"),
                decisions=os.path.join(os.path.dirname(log) if log else config.path("work"), "decisions.json"))


def suggestions_path(paths):
    return os.path.join(os.path.dirname(paths["decisions"]), "suggestions.json")


def curated_path(paths):
    return os.path.join(os.path.dirname(paths["decisions"]), "curated.json")


def spotify_hits_path(paths):
    return os.path.join(os.path.dirname(paths["decisions"]), "spotify_search.json")


def better_links_path(paths):
    return os.path.join(os.path.dirname(paths["decisions"]), "better_links.json")


def styles_path(paths):
    return os.path.join(os.path.dirname(paths["decisions"]), "styles.json")


def record_of(link):
    """'release/123' or 'master/456' from a Discogs link, or nothing."""
    m = re.search(r"discogs\.com/(release|master)/(\d{1,12})(?!\d)", link or "")
    return f"{m[1]}/{m[2]}" if m else ""


def plays_path(paths):
    return os.path.join(os.path.dirname(paths["decisions"]), "plays.json")


def waves_path(paths):
    return os.path.join(os.path.dirname(paths["decisions"]), "waveforms.json")


def edges_path(paths):
    return os.path.join(os.path.dirname(paths["decisions"]), "sound_edges.json")


def own_lists_path(paths):
    return os.path.join(os.path.dirname(paths["decisions"]), "own_playlists.json")


def levels_path(paths):
    return os.path.join(os.path.dirname(paths["decisions"]), "loudness.json")


def level_key(song):
    """By name and size: a moved song needn't be measured again, a better copy must."""
    return f"{play_key(song)}|{song['bytes']}"


def name_key(artist, title):
    """One song's name, with the line between artist and title kept: "Ab - C" and "A - Bc" are different songs."""
    artist, title = text_key(artist), text_key(title)
    return f"{artist}|{title}" if artist or title else ""


def log_name(row):
    return name_key(row.get("artist"), row.get("title"))


def play_key(song):
    """Counts go by name, so a song keeps its count when it moves to another genre folder or a better copy replaces the file."""
    return (name_key(song["artist"], song["title"]) if song["artist"] and song["title"] else text_key(os.path.splitext(song["file"])[0])) or song["key"]


def is_web_link(text):
    return isinstance(text, str) and bool(re.fullmatch(r"https?://[^\s\"'<>]{1,500}", text))


def spotify_words(hit):
    """How a possible match on Spotify is described on To check."""
    gap = hit.get("gap") or 0
    length = "the same length as yours" if abs(gap) <= 1 else f"{abs(gap)} seconds {'shorter' if gap > 0 else 'longer'} than yours"
    album = f", from {hit['album']}" + (f" ({hit['year']})" if hit.get("year") else "") if hit.get("album") else ""
    return f"Spotify has \"{hit.get('name', '')}\" by {', '.join(hit.get('artists') or [])}{album}, {length}. Listen and see if it's the same recording."


def lan_address():
    """This computer's address on the home network, or "" if it doesn't seem to have one."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # nothing is sent: this only asks which way out the system would use
            address = probe.getsockname()[0]
        found = ipaddress.ip_address(address)
    except (OSError, ValueError):
        return ""
    return address if found.is_private and not found.is_loopback and not found.is_link_local else ""


def lan_key():
    """The private part of the link, kept next to your settings so a bookmark on your phone keeps working."""
    path = os.path.join(os.path.dirname(config.env_file()), "lan_key.txt")
    try:
        with open(path, encoding="utf-8") as f:
            saved = f.read().strip()
        if re.fullmatch(r"[0-9a-f]{10}", saved):
            return saved
    except (FileNotFoundError, ValueError):  # none yet, or not a key at all
        pass
    except OSError:  # it's there and can't be opened just now. A new one saved over it would break the bookmark on your phone for good, so this run gets one of its own and the file is left
        return secrets.token_hex(5)
    made = secrets.token_hex(5)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        saving.write(path, lambda f: f.write(made + "\n"), encoding="utf-8")
    except OSError:  # it still works for this run; the link changes the next time the app starts
        pass
    return made


CONNECT_S = 0.5  # how long a second start waits to reach whatever has the port (a program on this computer takes the call at once, however busy it is)
ASK_S = 1.5  # how long, in all, it then waits for that program to say what it is (one that is slow is asked again)
OLD_ASK_S = 4  # a copy from before the question existed answers through its settings page, which can be slow the first time
ASK_MOST = (1 << 16) + 8192  # more than this is more than the app would ever say
JUST_OPENED_S = 5  # a copy that opened the browser itself this short a time ago doesn't need a second tab


def _held(port):
    """Whether some program has this port on any of this computer's addresses. Taking the port wouldn't tell: this computer's own address
    (127.0.0.1) can often be taken while another program holds the port for every address (a copy started with --lan), and the other way round."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # refused when any address has it
        probe.bind(("0.0.0.0", port))
    except OSError:
        return True
    finally:
        probe.close()
    return False


def _taken(host, port):
    """Whether a program takes calls on this address and port."""
    try:
        socket.create_connection((host, port), timeout=CONNECT_S).close()
    except OSError:
        return False
    return True


def _ask(port, path, wait):
    """Asks the program on this computer's port for a page: straight to it (never through a proxy), with nothing about the person in the request,
    and never longer than `wait` seconds in all once the call is taken. Gives (status, body) when it answered; ("none", b"") when nothing took the call,
    or it was cut off before a word was said (a program that has just gone); ("silent", b"") when it took the call and hadn't answered in time;
    ("other", b"") when what came back isn't a web page's answer."""
    talk, got = socket.socket(socket.AF_INET, socket.SOCK_STREAM), b""
    try:
        talk.settimeout(CONNECT_S)
        try:
            talk.connect(("127.0.0.1", port))
        except OSError:
            return "none", b""
        deadline = time.monotonic() + wait
        talk.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\nConnection: close\r\n\r\n".encode("ascii"))
        while True:
            if len(got) > ASK_MOST:
                return "other", b""
            head, split, body = got.partition(b"\r\n\r\n")
            length = re.search(rb"(?im)^content-length:[ \t]*(\d{1,7})[ \t]*\r?$", head) if split else None
            if length and len(body) >= int(length[1]):
                break  # all of it is here
            left = deadline - time.monotonic()
            if left <= 0:
                return "silent", b""
            talk.settimeout(left)
            chunk = talk.recv(65536)
            if not chunk:
                break
            got += chunk
    except TimeoutError:
        return "silent", b""
    except OSError:
        return ("other" if got else "none"), b""
    finally:
        talk.close()
    head, split, body = got.partition(b"\r\n\r\n")
    status = re.match(rb"HTTP/1\.[01] (\d{3})\b", head)
    return (int(status[1]), body) if status and split else ("other", b"")


def _spelled(path):
    """A path as text in one spelling (its slashes, its dots and, on Windows, the case of its letters), without looking at the disk."""
    return os.path.normcase(os.path.normpath(path))


def who_is_on(port, wait=None):
    """What already has this computer's port:
    ("same", told)    Pressling running with these very settings (told: what it said about itself);
    ("silent", None)  a program that took the call and didn't answer in time;
    ("other", None)   another program, or Pressling with other settings;
    ("none", None)    nothing took the call."""
    wait = ASK_S if wait is None else max(wait, 0.1)
    status, body = _ask(port, "/api/running", wait)
    if status == 404:  # a copy from before this question existed: what it says about its settings is enough
        status, body = _ask(port, "/api/settings", max(wait, OLD_ASK_S))
    if status in ("none", "silent"):
        return status, None
    if status != 200:
        return "other", None
    try:
        told = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        return "other", None
    if not isinstance(told, dict) or not (told.get("app") == "pressling" or "library" in told):
        return "other", None
    theirs = {_spelled(path) for path in (told.get("config_file"), told.get("config_real")) if isinstance(path, str) and os.path.isabs(path)}  # compared as text only: a path that another program names is never looked up on the disk, it could point anywhere
    mine = os.path.abspath(config.target_path())
    try:
        ours = {_spelled(mine), _spelled(os.path.realpath(mine))}
    except (OSError, ValueError):
        ours = {_spelled(mine)}
    return ("same", told) if theirs & ours else ("other", None)


def already_running(port, told, open_browser, here):
    """A second start found this same app, with these same settings, on the port: that copy is shown instead of a second one being started.
    here: this computer's address on the home network when this start was asked to open to it (--lan), else ""."""
    url = f"http://127.0.0.1:{port}/"
    starting, up = told.get("starting") is True, told.get("up")
    just_opened = told.get("opens") is True and (starting or (isinstance(up, (int, float)) and not isinstance(up, bool) and up < JUST_OPENED_S))
    if starting:
        print(f"Pressling is already starting at {url}", flush=True)
        if just_opened:
            print("It opens in your browser by itself once it has read your library.", flush=True)
        elif open_browser:
            print("The page appears once it has read your library.", flush=True)
    else:
        print(f"Pressling is already running at {url}", flush=True)
    if here:
        at, mine = told.get("lan"), f"{here}:{port}"
        if at == mine:  # (only ever this computer's own address: what the other program says is never printed)
            print(f"On your home network: http://{mine}/?key={lan_key()}", flush=True)
        elif isinstance(at, str):
            print("That copy is open to another network than the one this computer is on now. To open it to this one, close it first (Ctrl+C in its window), then run this again.", flush=True)
        elif "lan" in told:
            print("That copy isn't open to your home network. To open it there, close it first (Ctrl+C in its window), then run this again.", flush=True)
        else:  # (a copy from before it could say)
            print("If that copy isn't open to your home network, close it first (Ctrl+C in its window), then run this again.", flush=True)
    if open_browser and not just_opened:  # (a copy that has only just started opens the browser itself)
        webbrowser.open(url)
    return 0


def serve(port=8765, open_browser=True, lan=False):
    if not 1 <= port <= 65535:
        sys.exit("A port is a number from 1 to 65535.")
    here = ""
    if lan:
        here = lan_address()
        if not here:
            sys.exit("This computer doesn't seem to be on a home network, so the app wasn't opened to one. Is it on Wi-Fi or plugged in?")
    last, server, answering, failed = min(port + 19, 65535), None, None, []

    def answer():  # the answering runs in a thread of its own; what goes wrong there is kept for the end
        try:
            server.serve_forever()
        except BaseException as ex:
            failed.append(ex)

    try:
        for candidate in range(port, last + 1):  # the port is taken first, before the library is read, so that a second start finds this copy even while it is still starting
            server, who, told = None, "none", None
            for look in range(3):  # (a port can be taken, or let go, in the moment between looking and taking: so it is looked at again)
                if _held(candidate):
                    who, told = who_is_on(candidate)
                    if who == "silent" and look < 2:
                        continue  # a copy on a very busy computer can be slow to answer: it is asked again
                    if who == "none" and here and _taken(here, candidate):
                        who = "other"  # a program has this port on the network's address only: it is left alone
                    if who != "none":
                        break  # a program is listening there
                try:  # free, or held by nothing that listens: a port only just let go, or a copy in its very first moment
                    server = Server(("0.0.0.0" if here else "127.0.0.1", candidate), Handler)
                    break
                except PermissionError:
                    break  # the system keeps this port for itself
                except OSError:
                    if look < 2:
                        time.sleep(0.15)
            if server:
                break
            if who == "same":  # a second copy would only be in the way: the one that is there is shown
                return already_running(candidate, told, open_browser, here)
        else:
            sys.exit(f"No free port between {port} and {last}.")
        url = f"http://127.0.0.1:{candidate}/"
        server.collection, server.hosts, server.ready, server.opens = None, {f"127.0.0.1:{candidate}", f"localhost:{candidate}"}, threading.Event(), bool(open_browser)
        if here:  # one network, and only with the link. (Set before anything is answered: a call from the network must never get in without it.)
            server.lan = dict(network=ipaddress.ip_network(f"{here}/24", strict=False), key=lan_key())
            server.hosts.add(f"{here}:{candidate}")
            server.lan_at = f"{here}:{candidate}"
        answering = threading.Thread(target=answer, daemon=True)  # from here on it tells a second start who it is; everything else waits until the library has been read
        answering.start()
        collection = None
        if not folders.reason():  # otherwise the page opens on the first-run screen
            print("Reading your library...", flush=True)
            collection = Collection(app_paths())
            collection.refresh()
            saving.sweep(os.path.dirname(collection.paths["decisions"]))  # what a save left behind if the app was killed in the middle of one
        if failed:
            raise failed[0]
        server.collection, server.began = collection, time.monotonic()
        server.ready.set()
        opening = f"Your library is open at {url} ({len(collection.songs)} files)." if collection else f"Open {url} to choose your folders."
        print(f"{opening} Press Ctrl+C to stop.", flush=True)
        if here:
            print(f"On your home network: http://{here}:{candidate}/?key={server.lan['key']}", flush=True)
        if open_browser:
            threading.Timer(0.3, webbrowser.open, [url]).start()
        while answering.is_alive():
            answering.join(0.5)
        if failed:
            raise failed[0]
    except KeyboardInterrupt:
        pass
    finally:  # also when the library couldn't be read: the port must not stay held by a copy that never opens
        if server is not None:
            if answering is not None and answering.is_alive():
                server.shutdown()
            server.server_close()


def show_decisions():
    """Print what was decided in the browser, for the person or their agent."""
    path = app_paths()["decisions"]
    if not os.path.exists(path):
        print("No decisions yet.")
        return 0
    try:
        with open(path, encoding="utf-8-sig") as f:
            loaded = json.load(f)
    except (OSError, ValueError) as ex:
        print(f"Your answers in {path} can't be read: {ex}")
        return 1
    decisions = {k: d for k, d in loaded.items() if isinstance(d, dict) and d.get("action") and "song" in d} if isinstance(loaded, dict) else {}
    if not decisions:
        print("No decisions yet.")
        return 0
    for d in sorted(decisions.values(), key=lambda d: (d["action"], d["song"].lower())):
        extra = "".join(" | " + str(x).replace("\n", ", ") for x in (d.get("genre"), d.get("note")) if x)
        word = "doesn't fit the playlist" if d.get("kind") == "playlist" and d["action"] == "keep" else WORDS.get(d["action"], d["action"])
        print(f"[{word}] {d['song']}{extra}   ({d['file']})")
    print(f"\n{len(decisions)} decisions in {path}")
    return 0
