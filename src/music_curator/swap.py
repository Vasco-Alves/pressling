"""Swap a better copy into your library: "Use found version" on To check, or `pressling swap` in a terminal.

The better copy is fetched the way you chose in Settings, under Better copies: with your own downloader (a command with {url} and {folder}
in it), or with yt-dlp. It's only swapped in when it's the same recording as your copy (by fingerprint) and isn't worse above 16 kHz.
Then your copy goes to the Set aside folder (nothing is deleted), the new one gets the same tags, cover, name and folder, and the log says so.
On any doubt nothing changes: the song stays on To check, with the reason.
"""
import datetime
import glob
import json
import os
import shlex
import re
import subprocess
import tempfile
import threading

from . import apply, config, folders, fpcompare as fp, samerec, saving, tagwriter, webapp

FETCHES = ("command", "ytdlp")  # how a better copy can be fetched ("" is nobody: you or your agent swap it)
AUDIO = (".mp3", ".m4a", ".flac", ".ogg", ".opus", ".webm", ".wav")  # what a downloader may leave in its folder
FETCH_TIMEOUT_S = 15 * 60
QUIETER_DB = 2  # a copy this much quieter above 16 kHz than yours isn't better
WEB_LINK = re.compile(r"https?://[^\s\"'<>`]+")  # a link a downloader can be given: no spaces or quotes that could split its command line


class Fail(Exception):
    """Nothing was swapped. The message says why, in words for the person."""


def setting():
    """How better copies are fetched: ("command" | "ytdlp" | "", the command)."""
    how, command = config.get("better", "fetch"), config.get("better", "command")
    return (how if how in FETCHES else ""), (command if isinstance(command, str) else "")


def save_setting(body):
    """From Settings: {"fetch": "" | "command" | "ytdlp", "command": "..."}. Returns an error, or None once it's saved."""
    how, command = body.get("fetch", ""), body.get("command", "")
    if set(body) - {"fetch", "command"} or how not in ("", *FETCHES) or not isinstance(command, str) or len(command) > 2000:
        return "bad request"
    command = command.strip()
    if how == "command" and ("{url}" not in command or "{folder}" not in command):
        return "The command needs {url} where the link goes and {folder} where the song is saved."
    try:
        config.save_table(config.target_path(), "better", dict(fetch=how, command=command))
        config.load(config.target_path())
    except (OSError, ValueError) as ex:
        return f"Couldn't save that: {ex}"
    return None


def fetch(url, folder, how, command, stopping=None, timeout=FETCH_TIMEOUT_S):
    """Fetch the better copy into `folder` (an empty folder made for it). Returns the song's path there; raises Fail when none came."""
    if not WEB_LINK.fullmatch(url or ""):
        raise Fail("That isn't a web link a downloader can be given.")
    if how == "command":
        if "{url}" not in command or "{folder}" not in command:
            raise Fail("Your downloader's command needs {url} and {folder} in it (Settings, Better copies).")
        line = command.replace("{folder}", folder).replace("{url}", url)
        args = line if os.name == "nt" else shlex.split(line)  # (on Windows the program reads its own command line, as when you type it)
    elif how == "ytdlp":
        args = [config.ytdlp_python(), "-m", "yt_dlp", "--no-playlist", "--quiet", "--no-progress", "-x", "--audio-format", "mp3",
                "--audio-quality", "0", "-o", os.path.join(folder, "%(id)s.%(ext)s"), url]
    else:
        raise Fail("Choose how Pressling fetches better copies first: Settings, under Better copies.")
    try:
        _, err = fp.run_or_give_up(args, timeout, stopping, text=True, encoding="utf-8", errors="replace")
    except fp.GaveUp as ex:
        raise Fail(f"The download didn't finish: {ex}") from ex
    except OSError as ex:
        raise Fail(f"The downloader couldn't be started: {ex.strerror or ex}") from ex
    found = [p for p in glob.glob(os.path.join(glob.escape(folder), "**", "*"), recursive=True) if os.path.splitext(p)[1].lower() in AUDIO]
    if not found:
        said = " ".join((err or "").split())[-240:]
        raise Fail("The download finished, but no song came back." + (f" It said: {said}" if said else ""))
    return max(found, key=os.path.getmtime)


def swap_in(paths, song_path, new_audio, url="", why=""):
    """Put `new_audio` in place of the library song at `song_path`, with its tags, cover, name and folder; your copy goes to Set aside.
    Returns what happened ({dest, set_aside, before, after, status, how}). Raises Fail when nothing was swapped: then nothing has changed."""
    lib = os.path.normpath(paths["library"])
    song_path = os.path.normpath(song_path)
    if not (os.path.isfile(song_path) and folders._inside(song_path, lib)):
        raise Fail("That song isn't in your library.")
    if not os.path.isfile(new_audio):
        raise Fail("The better copy isn't there.")
    ending, theirs = os.path.splitext(song_path)[1].lower(), os.path.splitext(new_audio)[1].lower()
    if theirs != ending:
        raise Fail(f"The better copy is a {theirs[1:] or 'nameless'} file and yours is {ending[1:]}, so it would change the song's file: it was left for you.")
    old = tagwriter.probe(song_path)
    tags = old["tags"]
    if not tags.get("title") or not tags.get("genre"):
        raise Fail("Your copy has no title or genre tag, so it can't be written again under the same name.")
    folder = os.path.dirname(os.path.relpath(song_path, lib))
    fields = dict(artist=tags.get("artist", ""), title=tags["title"], album=tags.get("album", ""), year=tags.get("date", ""), genre=tags["genre"])
    base = tagwriter.safe_filename(config.rule("filename").format(**fields) if fields["artist"] else fields["title"])
    if base != os.path.splitext(os.path.basename(song_path))[0]:
        raise Fail("Its file name isn't the one its tags give, so a swap would rename it: it was left as it is.")
    with tempfile.TemporaryDirectory(prefix="comparing_") as tmp:
        same, info = samerec.same_recording(song_path, new_audio, tmp)
    if not same:
        raise Fail(f"It isn't the same recording as yours ({info.get('how', 'no match')}), so it wasn't swapped in.")
    mine, found = samerec.hf_db(song_path), samerec.hf_db(new_audio)
    if mine is not None and found is not None and found < mine - QUIETER_DB:
        raise Fail(f"It isn't better than yours: above 16 kHz it has {found:.0f} dB against your {mine:.0f} dB.")
    notes = "; ".join(x for x in (f"better copy from {url}" if url else "better copy", " ".join(str(why).split())[:300], info.get("how", "")) if x)
    entry = dict(key="better", artist=fields["artist"], title=fields["title"], album=fields["album"], year=fields["year"], genre=fields["genre"],
                 discogs=tags.get("comment", ""), folder=folder, notes=notes, audio=new_audio)
    aside = os.path.join(apply._aside_folder(paths), f"{datetime.datetime.now():%Y-%m-%d %H-%M-%S} better copies", folder, os.path.basename(song_path))
    with tempfile.TemporaryDirectory(prefix="cover_") as tmp:
        cover = None
        if old["pics"]:  # your copy's cover goes onto the new one, as a JPEG (the tag says it is one)
            cover = os.path.join(tmp, "cover.jpg")
            for how in (["-c:v", "copy"], ["-c:v", "mjpeg", "-q:v", "2"]):
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", song_path, "-an", *how, "-frames:v", "1", cover],
                               capture_output=True, creationflags=folders.NO_WINDOW)
                if not os.path.isfile(cover):
                    break
                with open(cover, "rb") as f:
                    if f.read(3) == b"\xff\xd8\xff":  # a JPEG already: kept as it is
                        break
            cover = cover if os.path.isfile(cover) and os.path.getsize(cover) else None
        apply._move(song_path, aside)  # (a song that's playing can't be moved: then nothing has changed yet)
        writer, row = None, None
        try:
            writer = tagwriter.Writer(lib, paths["log"], say=lambda _: None)
            row = writer.write(dict(entry, src=aside, cover=cover))
        finally:
            if writer:
                writer.close()
            if not (row and row["status"].startswith(("ok", "check")) and os.path.exists(row["dest"])) and not os.path.exists(song_path):
                apply._move(aside, song_path)  # back where it was
        if not row["status"].startswith(("ok", "check")):
            raise Fail(f"The better copy couldn't be written ({row['status']}), so yours is back where it was.")
    return dict(dest=row["dest"], set_aside=aside, before=old["secs"], after=tagwriter.probe(row["dest"])["secs"], status=row["status"], how=info.get("how", ""))


def forget(paths, key, collection=None):
    """The link and the answer that asked for this swap are done with: the song comes off To check."""
    path = webapp.better_links_path(paths)
    try:
        with open(path, encoding="utf-8-sig") as f:
            links = json.load(f)
        if isinstance(links, dict) and key in links:
            links.pop(key)
            saving.write(path, lambda f: json.dump(links, f, ensure_ascii=False, indent=1), encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass  # (no links file: nothing to take off)
    if collection is not None:
        collection.update_answers(lambda answers: answers.pop(f"better:{key}", None))


def said(result):
    """What the person is told once a copy is swapped in."""
    gap = round(result["after"] - result["before"])
    length = f" It's {abs(gap)} s {'longer' if gap > 0 else 'shorter'}, so add it to your Spotify playlists again." if abs(result["after"] - result["before"]) > 1 else ""
    check = " Its tags need a look: " + result["status"][len("check: "):] + "." if result["status"].startswith("check") else ""
    return f"Swapped in. Your old copy is in the Set aside folder.{length}{check}"


def start(server, song_id):
    """Use found version, pressed on To check: the better copy is fetched and swapped in, in the background. The page follows it through status()."""
    from . import curate
    collection = server.collection
    song = collection.songs.get(str(song_id)) if collection else None
    if not song or song.get("area") != "library":
        return dict(error="That song isn't in your library.")
    link = collection.better.get(song["key"]) or {}
    how, command = setting()
    if not how:
        return dict(error="Choose how Pressling fetches better copies first: Settings, under Better copies.", settings=True)
    if not link.get("url"):
        return dict(error="There's no better copy linked for this song.")
    if not server.curate_lock.acquire(blocking=False):
        return dict(error="Curate is running, or another swap. Try again when it's finished.")
    swaps = server.__dict__.setdefault("swaps", {})
    key, paths = song["key"], collection.paths
    swaps[key] = dict(state="fetching")

    def work():
        try:
            with curate._OneAtATime(os.path.join(os.path.dirname(paths["log"]), "curate.lock"), said="Curate is running on this library in another window. Try again when it's finished."):
                with samerec.scratch(os.path.join(paths["work"], "fetched")) as folder:
                    new = fetch(link["url"], folder, how, command)
                    swaps[key] = dict(state="comparing")
                    result = swap_in(paths, song["_path"], new, link["url"], link.get("why", ""))
                forget(paths, key, collection)
            swaps[key] = dict(state="done", said=said(result))
        except (Fail, curate.Stop) as ex:
            swaps[key] = dict(state="failed", said=str(ex))
        except Exception as ex:  # noqa: BLE001  (whatever it was, the page is told, and nothing is left half done: swap_in puts your copy back)
            swaps[key] = dict(state="failed", said=f"Something went wrong, so nothing was swapped: {ex}")
        finally:
            server.curate_lock.release()

    threading.Thread(target=work, daemon=True, name="swap").start()
    return dict(started=True, key=key)


def status(server):
    """Each swap asked for since the app started: {song key: {state: fetching | comparing | done | failed, said}}."""
    return dict(server.__dict__.get("swaps", {}))


def main(song_name, url=None, file=None, why=""):
    """`pressling swap SONG (URL | --file FILE)`: the same swap from a terminal (for you or your agent). With a link, it's fetched the way Settings says."""
    from . import curate
    paths = webapp.app_paths()
    collection = webapp.Collection(paths)
    collection.refresh()
    song = curate._library_song([s for s in collection.songs.values() if s["area"] == "library"], song_name)
    if not song:
        return 1
    try:
        with curate._OneAtATime(os.path.join(os.path.dirname(paths["log"]), "curate.lock"), said="Curate is running on this library. Try again when it's finished."):
            if file:
                result = swap_in(paths, song["_path"], os.path.abspath(file), url or "", why)
            else:
                how, command = setting()
                with samerec.scratch(os.path.join(paths["work"], "fetched")) as folder:
                    result = swap_in(paths, song["_path"], fetch(url, folder, how, command), url, why)
            forget(paths, song["key"], collection)
    except (Fail, curate.Stop) as ex:
        print(f"  {song_name}: {ex}")
        return 1
    print(f"  {song_name}: {said(result)}")
    return 0
