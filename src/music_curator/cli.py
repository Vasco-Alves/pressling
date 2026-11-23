"""The `pressling` command line (`curator`, its old name, is the same command). Each command is a thin wrapper around one module."""
import argparse
import os
import shutil
import subprocess
import sys

from . import __version__, config


def _setup(args):
    try:
        config.load(args.config)
    except config.BadConfig as ex:
        sys.exit(str(ex))
    from . import discogs, vgmdb
    cache = config.path("cache")
    discogs.CACHE_DIR = os.path.join(cache, "discogs")
    discogs.UA = config.get("discogs", "user_agent") or discogs.UA
    vgmdb.CACHE_DIR = os.path.join(cache, "vgmdb")
    if args.cmd not in ("serve", "doctor", "decisions", "apply", "suggest", "curate", "verified", "maybe-better"):  # these have to work even when the folders are on a drive that's gone
        for key in ("cache", "work"):
            os.makedirs(config.path(key), exist_ok=True)


def _need(key):
    p = config.path(key)
    if not p:
        sys.exit(f"Set [paths] {key} in your settings ({config.target_path()}), or start Pressling (`pressling`) and pick your folders there.")
    return p


def cmd_init(args):
    here = [os.path.join(os.getcwd(), name) for name in (config.FILE_NAME, config.OLD_FILE_NAME)]
    taken = next((p for p in here if os.path.exists(p)), None)
    if taken:
        sys.exit(f"{os.path.basename(taken)} already exists here; not overwriting it.")
    target = here[0]
    with open(target, "w", encoding="utf-8") as f:
        f.write(config.EXAMPLE)
    print(f"Wrote {target}. Edit the paths, then run `pressling doctor`.")
    usual = config.user_config_path()
    if os.path.normcase(os.path.abspath(usual)) == os.path.normcase(target):
        print("This is where your usual settings live, so Pressling uses this file from any folder.")
    elif os.path.exists(usual):  # a settings file in the folder you start from comes before your usual settings
        print(f"Careful: when Pressling is started from this folder, it now uses this file instead of your usual settings ({usual}). "
              f"Delete this file to go back to them. From any other folder, use it with: pressling --config \"{target}\" <command>")
    else:
        print(f"Pressling uses this file when it's started from this folder, and from anywhere else with: pressling --config \"{target}\" <command>. "
              f"(The app keeps your usual settings in {usual}, made the first time it opens.)")
    named = [name for name in config.ENV_NAMES if os.environ.get(name)]
    if named:
        print(f"Note: {named[0]} is set ({os.environ[named[0]]}), and the file it names comes before this one.")


def cmd_doctor(args):
    ok = True
    print("config:", config.config_path() or "(none found, using defaults)")
    for key in ("library", "log", "cache", "work"):
        print(f"  {key:<8} {config.path(key) or '(not set)'}")
    print("  inbox   ", ", ".join(config.path("inbox")) or "(not set)")
    ff = shutil.which("ffmpeg")
    print("ffmpeg:", ff or "MISSING (install ffmpeg and put it on PATH)")
    if ff:
        muxers = subprocess.run([ff, "-hide_banner", "-muxers"], capture_output=True, text=True).stdout
        has = "chromaprint" in muxers
        print("  chromaprint muxer:", "yes" if has else "MISSING (fingerprint checks need an ffmpeg build with chromaprint)")
        ok &= has
    fp = shutil.which("ffprobe")
    print("ffprobe:", fp or "MISSING (it comes with ffmpeg: install a full ffmpeg and put it on PATH)")
    ok &= bool(ff and fp)
    from . import discogs
    try:
        has_token = bool(discogs.token())
    except SystemExit:
        has_token = False
    print("DISCOGS_TOKEN:", "set" if has_token else f"MISSING (create one at https://www.discogs.com/settings/developers, then put a line DISCOGS_TOKEN=your_token in {config.env_file()})")
    ok &= has_token
    py = config.ytdlp_python()
    try:
        found = subprocess.run([py, "-c", "import yt_dlp; print(yt_dlp.version.__version__)"], capture_output=True, text=True).stdout.strip()
    except OSError:
        found = ""
    print("yt-dlp:", found or f"not available in {py} (optional: to compare your files with videos online and find official YouTube uploads)")
    print("OK" if ok else "Some required pieces are missing.")
    return 0 if ok else 1


def main(argv=None, prog="pressling"):
    p = argparse.ArgumentParser(prog=prog, description="Pressling: a player for your own music files with a curator built in. It verifies songs against "
                                "Discogs (and fallbacks), settles versions by length and fingerprints, and writes clean tags + covers.")
    p.add_argument("--version", action="version", version=f"Pressling {__version__}")
    p.add_argument("--config", help="the settings file to use. Without it: the file $PRESSLING_CONFIG names, else a pressling.toml (or "
                   "curator.toml) in the folder you start from, else your usual settings (" + config.user_config_path().replace("%", "%%") + ")")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    def add(name, help_, *args):
        sp = sub.add_parser(name, help=help_, description=help_)
        for a in args:
            sp.add_argument(*a[0], **a[1])
        return sp

    add("init", "write an example pressling.toml in the current folder, to edit by hand (the app makes your usual settings itself)")
    add("doctor", "check ffmpeg/chromaprint, the Discogs token, paths and yt-dlp")
    add("parse", "parse inbox folders (filenames + existing tags) into an items JSON",
        (["out"], {"help": "items JSON to write"}), (["dirs"], {"nargs": "*", "help": "folders (default: [paths] inbox)"}))
    add("resolve", "match items against Discogs and decide tags (status: ready / review / notfound / dup)",
        (["items"], {}), (["out"], {}))
    add("scan", "wide Discogs scan: every pressing and version of a song, with lengths", (["items"], {}))
    add("videos", "fingerprint files against the YouTube videos Discogs links on the release; what it fetches is deleted straight after",
        (["items"], {}), (["out"], {}))
    add("other", "fallback sources for songs Discogs lacks: MusicBrainz, official YouTube uploads",
        (["items"], {}), (["out"], {}))
    add("ost", "game-soundtrack lookup (\"<Game> OST - <Track>\" files)", (["items"], {}), (["out"], {}))
    add("better", "find better copies (complete, right speed, better quality) and compare them with yours; gives links and keeps no audio; needs yt-dlp",
        (["items"], {}), (["out"], {}))
    reference = (["reference"], {"help": "a file, or a YouTube link (needs yt-dlp; fetched for the comparison, then deleted)"})
    add("same", "is COPY the same recording as REFERENCE? (handles speed changes and cuts)",
        (["copy"], {}), reference, (["--tmp"], {"help": "scratch folder (default: [paths] work)"}))
    add("compare", "quick whole-file fingerprint comparison", (["a"], {}), (["b"], {}))
    add("drift", "chunk-by-chunk alignment, to tell 'sped up' from 'edited'", (["copy"], {}), reference)
    add("curate", "curate with no AI: look up what's waiting, add the songs the evidence settles, leave the rest on To check",
        (["--dry-run"], {"action": "store_true", "help": "show what would happen and change nothing"}),
        (["--from"], {"dest": "from_folders", "action": "append", "metavar": "FOLDER",
                      "help": "a downloads folder to curate this time, in place of the ones in Settings (give it again for more)"}),
        (["--to"], {"dest": "to_folder", "metavar": "FOLDER",
                    "help": "the library to put the songs in this time, in place of the one in Settings (made if it doesn't exist)"}))
    add("verified", "record that Auto-matched songs were checked properly (for your agent, after a Curate run)",
        (["songs"], {"nargs": "+", "help": "library files, as Genre/Artist - Title.mp3 or just the file name"}),
        (["--how"], {"required": True, "help": "what was checked, for the log"}))
    add("maybe-better", "leave a link to a possibly better copy of a library song on To check (for your agent); the song isn't touched",
        (["song"], {"help": "library file, as Genre/Artist - Title.mp3 or just the file name"}),
        (["url"], {"help": "where the copy is"}),
        (["--why"], {"required": True, "help": "what makes it better, in a few words"}),
        (["--checked"], {"action": "store_true", "help": "its audio was compared with the song and `pressling better` accepted it; without this the page calls it a guess"}))
    add("swap", "swap a better copy in for a library song, from a link (fetched the way Settings says) or a file; only when it's the same recording, and your copy goes to Set aside",
        (["song"], {"help": "library file, as Genre/Artist - Title.mp3 or just the file name"}),
        (["url"], {"nargs": "?", "help": "where the better copy is"}),
        (["--file"], {"help": "a better copy you already have, in place of the link"}),
        (["--why"], {"default": "", "help": "what makes it better, for the log"}))
    add("write", "copy audio into the library with clean tags + cover, and append to the log",
        (["plan"], {}), (["--dry-run"], {"action": "store_true"}))
    add("serve", "open the library in your browser: explore it, follow the progress, check songs by ear",
        (["--port"], {"type": int, "default": 8765, "help": "first port to try (default 8765)"}),
        (["--no-browser"], {"action": "store_true", "help": "don't open the browser"}),
        (["--lan"], {"action": "store_true", "help": "also answer on your home network, behind a private link, so your phone can use it"}))
    add("decisions", "list what you decided in the browser (for you or your agent to apply)")
    add("apply", "carry out your browser answers that need no research (genre changes) and list the rest for your agent",
        (["--dry-run"], {"action": "store_true", "help": "show what would happen and change nothing"}),
        (["--set-aside"], {"action": "store_true", "help": "also move songs marked 'not worth keeping' to the Set aside folder (nothing is deleted)"}))
    add("genres", "get every style Discogs lists for the records in your library, for the page (files keep their one genre)")
    add("suggest", "suggest playlists your songs are a confident fit for, to judge by ear on the To check page",
        (["playlists"], {"help": 'JSON file: [{"name": ..., "tracks": [{"name": ..., "artists": [...]}]}]'}),
        (["name"], {"nargs": "?", "help": "only songs that fit this playlist (or part of its name); default: all your playlists"}),
        (["--count"], {"type": int, "default": 40, "help": "how many ideas (default 40)"}))
    add("discogs", "raw Discogs client: release <id> | master <id> | versions <id> | search <artist> <track> | "
                   "image <url> <file>", (["rest"], {"nargs": argparse.REMAINDER}))
    add("vgmdb", "VGMdb via the vgmdb.info mirror: search <q> | album <id> | product <id> (often unavailable)",
        (["rest"], {"nargs": argparse.REMAINDER}))

    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # a song name must never stop a command halfway, and an AI app reading through a pipe needs it intact
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace", **({} if stream.isatty() else {"encoding": "utf-8"}))
    if args.cmd == "init":
        return cmd_init(args)
    _setup(args)
    cache, work = config.path("cache"), config.path("work")
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "parse":
        from . import parse
        dirs = args.dirs or config.path("inbox")
        if not dirs:
            sys.exit("Give folders or set [paths] inbox.")
        return parse.main([args.out, config.path("log") or os.devnull] + dirs)
    if args.cmd == "resolve":
        from . import resolve
        return resolve.main([args.items, args.out, config.path("log") or os.devnull, os.path.join(cache, "discogs"), _need("library")])
    if args.cmd == "scan":
        from . import deep_scan
        return deep_scan.main([os.path.join(cache, "discogs"), args.items])
    if args.cmd == "videos":
        from . import videos
        return videos.main([args.items, args.out, os.path.join(work, "videos"), os.path.join(cache, "discogs")])
    if args.cmd == "other":
        from . import other_sources
        return other_sources.main([args.items, args.out, os.path.join(cache, "other")])
    if args.cmd == "ost":
        from . import ost
        return ost.main([args.items, args.out, os.path.join(cache, "discogs"), _need("library")])
    if args.cmd == "better":
        from . import better_copies
        return better_copies.main([args.items, args.out, os.path.join(work, "better"), os.path.join(cache, "discogs")])
    if args.cmd == "same":
        from . import samerec
        with samerec.local(args.reference, os.path.join(work, "same")) as reference:
            return samerec.main([args.copy, reference, args.tmp or os.path.join(work, "same")])
    if args.cmd == "compare":
        from . import fpcompare
        return fpcompare.main([args.a, args.b])
    if args.cmd == "drift":
        from . import fpdrift, samerec
        with samerec.local(args.reference, os.path.join(work, "drift")) as reference:
            return fpdrift.main([args.copy, reference])
    if args.cmd == "curate":
        from . import curate
        return curate.main(args.dry_run, args.from_folders, args.to_folder)
    if args.cmd == "verified":
        from . import curate
        _need("library"), _need("log")
        return _in_turn(lambda: curate.mark_verified(args.songs, args.how))
    if args.cmd == "maybe-better":
        from . import curate
        _need("library"), _need("log")
        return curate.leave_better_link(args.song, args.url, args.why, args.checked)
    if args.cmd == "swap":
        from . import swap
        if bool(args.url) == bool(args.file):
            sys.exit("Give either the link to the better copy or --file with one you have.")
        _need("library"), _need("log")
        return swap.main(args.song, args.url, args.file, args.why)
    if args.cmd == "write":
        from . import tagwriter
        library, log = _need("library"), _need("log")
        return _in_turn(lambda: tagwriter.main([args.plan, library, log] + (["--dry-run"] if args.dry_run else [])), args.dry_run)
    if args.cmd == "serve":
        from . import webapp
        return webapp.serve(args.port, not args.no_browser, args.lan)
    if args.cmd == "decisions":
        from . import webapp
        return webapp.show_decisions()
    if args.cmd == "apply":
        from . import apply as applier
        _need("library"), _need("log")
        return _in_turn(lambda: applier.main(args.dry_run, args.set_aside), args.dry_run)
    if args.cmd == "genres":
        from . import genres
        _need("library"), _need("log")
        return genres.main()
    if args.cmd == "suggest":
        from . import suggest
        _need("library"), _need("log")
        return suggest.main(args.playlists, args.name, args.count)
    if args.cmd == "discogs":
        from . import discogs
        return discogs.main(args.rest)
    if args.cmd == "vgmdb":
        from . import vgmdb
        return vgmdb.main(args.rest)


WAIT_FOR_TURN_S = 2  # how long a command waits for something that is writing to the log. Another command adding a row is done well within it; Curate is not


def _in_turn(run, dry_run=False):
    """Run something that writes to the log or the library while nothing else does: Curate, in the app or in another window, takes the same turn.
    Two writers at once lose each other's rows. A dry run changes nothing, so it doesn't wait for one."""
    if dry_run:
        return run()
    from . import curate
    try:
        turn = curate._OneAtATime(os.path.join(os.path.dirname(config.path("log")), "curate.lock"), wait=WAIT_FOR_TURN_S,
                                  said="Something else is writing to the log just now: Curate in the app, or another pressling command. Run this again when it has finished.")
        turn.__enter__()
    except curate.Stop as ex:
        sys.exit(str(ex))
    except OSError:  # the log's folder isn't there yet, so nothing else can be writing in it
        return run()
    try:
        return run()
    finally:
        turn.__exit__()


def pressling(argv=None):
    """The `pressling` command, and `curator`, its old name: on its own it opens the app, and with a command it runs that tool."""
    argv = sys.argv[1:] if argv is None else argv
    return main(argv or ["serve"], prog="pressling")


if __name__ == "__main__":
    sys.exit(main())
