"""The better-copy swap: Use found version on To check, and `pressling swap`. A copy only comes in when it's the same recording and not worse;
yours goes to Set aside; on any doubt nothing changes. Nothing here fetches from the web: the downloaders are small stand-ins, and the
fingerprint comparison is stood in for (its own tests are elsewhere)."""
import contextlib
import csv
import io
import json
import os
import shutil
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from music_curator import cli, samerec, swap, tagwriter, webapp  # noqa: E402
from test_apply import Base  # noqa: E402
from test_webapp import write_mp3  # noqa: E402

LINK = "https://www.youtube.com/watch?v=abcdefghijk"


class WithASong(Base):
    def setUp(self):
        super().setUp()
        self.paths = webapp.app_paths()
        self.song = write_mp3(os.path.join(self.lib, "Trance", "A - Song.mp3"), artist="A", title="Song", album="", genre="Trance", year="1999")
        self.new = write_mp3(os.path.join(self.root, "fetched", "x.mp3"), frames=260, artist="", title="x", album="", genre="Other", year="")
        for patcher in (mock.patch.object(samerec, "same_recording", return_value=(True, {"how": "fingerprints match"})),
                        mock.patch.object(samerec, "hf_db", return_value=-40.0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        with open(self.song, "rb") as f:
            self.before = f.read()

    def log_rows(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def aside(self):
        root = os.path.join(self.app, "Set aside")
        return [os.path.join(dp, n) for dp, _, names in os.walk(root) for n in names]

    def unchanged(self):
        with open(self.song, "rb") as f:
            self.assertEqual(f.read(), self.before)
        self.assertEqual((self.aside(), self.log_rows()), ([], []))

    def sound(self, path):
        with open(path, "rb") as f:
            return tagwriter.audio_payload(f.read())


class SwappingIn(WithASong):
    def test_the_better_copy_comes_in_and_yours_goes_to_set_aside(self):
        result = swap.swap_in(self.paths, self.song, self.new, LINK, "the official upload")
        self.assertEqual(os.path.normcase(result["dest"]), os.path.normcase(self.song))
        self.assertEqual(self.sound(self.song), self.sound(self.new))
        tags = tagwriter.probe(self.song)["tags"]
        self.assertEqual([tags.get(k) for k in ("artist", "title", "genre", "date", "comment")], ["A", "Song", "Trance", "1999", "https://www.discogs.com/release/1"])
        aside = self.aside()
        self.assertEqual(len(aside), 1)
        self.assertTrue(aside[0].endswith(os.path.join("better copies", "Trance", "A - Song.mp3")), aside[0])
        with open(aside[0], "rb") as f:
            self.assertEqual(f.read(), self.before)  # your copy, untouched
        row = self.log_rows()[-1]
        self.assertEqual((row["status"], os.path.normcase(row["src"])), ("ok", os.path.normcase(aside[0])))
        self.assertIn(f"better copy from {LINK}", row["notes"])
        self.assertIn("the official upload", row["notes"])
        self.assertGreater(result["after"], result["before"] + 1)
        self.assertIn("add it to your Spotify playlists again", swap.said(result))

    def test_not_the_same_recording_changes_nothing(self):
        with mock.patch.object(samerec, "same_recording", return_value=(False, {"how": "no match"})), self.assertRaises(swap.Fail) as no:
            swap.swap_in(self.paths, self.song, self.new, LINK)
        self.assertIn("isn't the same recording", str(no.exception))
        self.unchanged()

    def test_a_worse_copy_changes_nothing(self):
        with mock.patch.object(samerec, "hf_db", side_effect=lambda p: -40.0 if p == self.song else -50.0), self.assertRaises(swap.Fail) as no:
            swap.swap_in(self.paths, self.song, self.new, LINK)
        self.assertIn("isn't better than yours", str(no.exception))
        self.unchanged()

    def test_another_kind_of_file_is_left_for_you(self):
        other = os.path.join(self.root, "fetched", "x.m4a")
        shutil.copy(self.new, other)
        with self.assertRaises(swap.Fail) as no:
            swap.swap_in(self.paths, self.song, other, LINK)
        self.assertIn("m4a", str(no.exception))
        self.unchanged()

    def test_a_song_its_tags_would_rename_is_left_as_it_is(self):
        odd = write_mp3(os.path.join(self.lib, "Trance", "Something Else.mp3"), artist="A", title="Song", album="", genre="Trance", year="1999")
        with self.assertRaises(swap.Fail) as no:
            swap.swap_in(self.paths, odd, self.new, LINK)
        self.assertIn("rename", str(no.exception))
        self.assertTrue(os.path.exists(odd))
        self.unchanged()

    def test_a_write_that_fails_puts_yours_back(self):
        def broken(writer, e):
            return dict(status="error: the disk is full", dest="")
        with mock.patch.object(tagwriter.Writer, "write", broken), self.assertRaises(swap.Fail) as no:
            swap.swap_in(self.paths, self.song, self.new, LINK)
        self.assertIn("back where it was", str(no.exception))
        with open(self.song, "rb") as f:
            self.assertEqual(f.read(), self.before)
        self.assertEqual(self.aside(), [])

    def test_only_a_song_in_your_library_can_be_swapped(self):
        with self.assertRaises(swap.Fail) as no:
            swap.swap_in(self.paths, self.new, self.song)
        self.assertIn("isn't in your library", str(no.exception))
        self.unchanged()


class Fetching(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = os.path.join(self.tmp.name, "into")
        os.makedirs(self.folder)

    def downloader(self, body):
        script = os.path.join(self.tmp.name, "downloader.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write("import os, sys\nfolder, url = sys.argv[1], sys.argv[2]\n" + body)
        return f'"{sys.executable}" "{script}" "{{folder}}" "{{url}}"'

    def test_your_own_downloader_gets_the_link_and_the_folder(self):
        command = self.downloader("open(os.path.join(folder, 'song.mp3'), 'wb').write(b'x')\nopen(os.path.join(folder, 'link.txt'), 'w').write(url)\n")
        got = swap.fetch(LINK, self.folder, "command", command)
        self.assertEqual(os.path.basename(got), "song.mp3")
        with open(os.path.join(self.folder, "link.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), LINK)

    def test_a_downloader_that_saves_nothing_says_so(self):
        command = self.downloader("print('no luck', file=sys.stderr)\n")
        with self.assertRaises(swap.Fail) as no:
            swap.fetch(LINK, self.folder, "command", command)
        self.assertIn("no song came back", str(no.exception))
        self.assertIn("no luck", str(no.exception))

    def test_a_command_without_its_places_is_refused(self):
        with self.assertRaises(swap.Fail) as no:
            swap.fetch(LINK, self.folder, "command", "download.exe")
        self.assertIn("{url} and {folder}", str(no.exception))

    def test_a_link_that_could_split_the_command_is_refused(self):
        for link in ('https://example.com/a" & calc', "https://example.com/a b", "file:///C:/x.mp3", ""):
            with self.subTest(link=link), self.assertRaises(swap.Fail) as no:
                swap.fetch(link, self.folder, "command", self.downloader(""))
            self.assertIn("isn't a web link", str(no.exception))

    def test_with_nobody_chosen_it_says_where_to_choose(self):
        with self.assertRaises(swap.Fail) as no:
            swap.fetch(LINK, self.folder, "", "")
        self.assertIn("Settings", str(no.exception))


class TheSetting(Base):
    def test_your_downloader_needs_both_places(self):
        self.assertIn("{folder}", swap.save_setting({"fetch": "command", "command": "dl {url}"}))
        self.assertEqual(swap.setting(), ("", ""))

    def test_it_is_kept_in_your_settings(self):
        self.assertIsNone(swap.save_setting({"fetch": "command", "command": "  dl {url} --to {folder}  "}))
        self.assertEqual(swap.setting(), ("command", "dl {url} --to {folder}"))
        with open(self.cfg, encoding="utf-8") as f:
            self.assertIn("[better]", f.read())
        self.assertIsNone(swap.save_setting({"fetch": "ytdlp"}))
        self.assertEqual(swap.setting()[0], "ytdlp")

    def test_anything_else_is_refused(self):
        for body in ({"fetch": "wget"}, {"fetch": "", "other": 1}, {"fetch": "command", "command": 5}):
            with self.subTest(body=body):
                self.assertEqual(swap.save_setting(body), "bad request")


class FromThePage(WithASong):
    def setUp(self):
        super().setUp()
        self.collection = webapp.Collection(self.paths)
        self.collection.refresh()
        self.entry = next(s for s in self.collection.songs.values() if s["area"] == "library")
        with open(webapp.better_links_path(self.paths), "w", encoding="utf-8") as f:
            json.dump({self.entry["key"]: dict(url=LINK, why="the official upload", checked=True)}, f)
        with open(self.paths["decisions"], "w", encoding="utf-8") as f:
            json.dump({f"better:{self.entry['key']}": dict(kind="better", action="better", note=LINK), "keep:other": dict(kind="keep", action="keep")}, f)
        self.collection.refresh()
        script = os.path.join(self.root, "downloader.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(f"import shutil, sys\nshutil.copy({self.new!r}, sys.argv[1])\n")
        self.command = f'"{sys.executable}" "{script}" "{{folder}}" "{{url}}"'
        self.server = types.SimpleNamespace(collection=self.collection, curate_lock=threading.Lock())

    def wait(self):
        until = time.monotonic() + 60
        while time.monotonic() < until:
            st = swap.status(self.server).get(self.entry["key"], {})
            if st.get("state") in ("done", "failed"):
                return st
            time.sleep(0.05)
        self.fail("the swap never finished")

    def test_use_found_version_swaps_it_in_the_background(self):
        self.assertIsNone(swap.save_setting({"fetch": "command", "command": self.command}))
        self.assertTrue(swap.start(self.server, self.entry["id"]).get("started"))
        st = self.wait()
        self.assertEqual(st["state"], "done", st)
        self.assertIn("Swapped in", st["said"])
        self.assertEqual(self.sound(self.song), self.sound(self.new))
        with open(webapp.better_links_path(self.paths), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {})  # off To check
        with open(self.paths["decisions"], encoding="utf-8") as f:
            self.assertEqual(list(json.load(f)), ["keep:other"])  # the answer that asked for it is done with; the others stay
        self.assertTrue(self.server.curate_lock.acquire(blocking=False))  # and Curate can run again

    def test_a_copy_that_isnt_the_same_recording_stays_on_to_check(self):
        self.assertIsNone(swap.save_setting({"fetch": "command", "command": self.command}))
        with mock.patch.object(samerec, "same_recording", return_value=(False, {"how": "no match"})):
            swap.start(self.server, self.entry["id"])
            st = self.wait()
        self.assertEqual(st["state"], "failed")
        self.assertIn("isn't the same recording", st["said"])
        with open(webapp.better_links_path(self.paths), encoding="utf-8") as f:
            self.assertIn(self.entry["key"], json.load(f))
        self.unchanged()

    def test_with_no_way_to_fetch_it_says_where_to_choose(self):
        answer = swap.start(self.server, self.entry["id"])
        self.assertIn("Settings", answer["error"])
        self.assertTrue(answer.get("settings"))

    def test_not_while_curate_runs(self):
        self.assertIsNone(swap.save_setting({"fetch": "command", "command": self.command}))
        with self.server.curate_lock:
            self.assertIn("Curate is running", swap.start(self.server, self.entry["id"])["error"])
        self.unchanged()


class TheCommand(WithASong):
    def test_pressling_swap_with_a_file_you_have(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--config", self.cfg, "swap", "Trance/A - Song.mp3", "--file", self.new, "--why", "a cleaner copy"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("Swapped in", out.getvalue())
        self.assertEqual(self.sound(self.song), self.sound(self.new))

    def test_it_needs_a_link_or_a_file(self):
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["--config", self.cfg, "swap", "Trance/A - Song.mp3"])
        self.unchanged()


if __name__ == "__main__":
    unittest.main()
