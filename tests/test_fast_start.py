"""A start that opens almost nothing. What was read from each song file, and from each saved Discogs answer, is kept in one file each beside the
answers, so a start opens only files that are new or have changed (by size or time). Opening thousands of files is what's slow the first time after
the computer starts. Nothing here reaches the network."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from music_curator import library, webapp  # noqa: E402
from test_apply import Base  # noqa: E402
from test_webapp import write_mp3  # noqa: E402


class SongDetailsKept(Base):
    def setUp(self):
        super().setUp()
        self.paths = webapp.app_paths()
        self.files = [write_mp3(os.path.join(self.lib, "Acid", f"A - Song {i}.mp3"), artist="A", title=f"Song {i}") for i in range(3)]
        self.files.append(write_mp3(os.path.join(self.inbox, "B - New.mp3"), artist="B", title="New"))

    def start(self):
        collection = webapp.Collection(self.paths)
        collection.refresh()
        return collection

    def kept(self):
        with open(webapp.song_details_path(self.paths), encoding="utf-8") as f:
            return json.load(f)["songs"]

    def test_a_second_start_opens_no_song_file_and_shows_the_same(self):
        first = self.start()
        with mock.patch.object(webapp.library, "read_song", side_effect=AssertionError("a song file was opened")):
            second = self.start()
        self.assertEqual(second.songs, first.songs)
        self.assertEqual(len(second.songs), 4)

    def test_a_file_that_changed_is_read_again(self):
        self.start()
        write_mp3(self.files[0], artist="A", title="Song 0, sung again")
        with mock.patch.object(webapp.library, "read_song", wraps=library.read_song) as read:
            songs = self.start().songs.values()
        self.assertEqual(read.call_count, 1)
        self.assertIn("Song 0, sung again", [s["title"] for s in songs])

    def test_a_file_that_went_is_forgotten(self):
        self.start()
        os.remove(self.files[1])
        self.start()
        self.assertEqual(len(self.kept()), 3)
        self.assertNotIn(webapp.norm(self.files[1]), self.kept())

    def test_a_new_file_is_read_and_kept(self):
        self.start()
        new = write_mp3(os.path.join(self.lib, "Acid", "C - Fresh.mp3"), artist="C", title="Fresh")
        with mock.patch.object(webapp.library, "read_song", wraps=library.read_song) as read:
            self.start()
        self.assertEqual([c.args[0] for c in read.call_args_list], [new])
        self.assertIn(webapp.norm(new), self.kept())

    def test_nothing_is_written_when_nothing_changed(self):
        self.start()
        before = os.stat(webapp.song_details_path(self.paths)).st_mtime_ns
        self.start()
        self.assertEqual(os.stat(webapp.song_details_path(self.paths)).st_mtime_ns, before)

    def test_a_damaged_or_older_kind_of_kept_file_means_reading_them_all(self):
        self.start()
        for spoil in (lambda: open(webapp.song_details_path(self.paths), "w", encoding="utf-8").write("{not json"),
                      lambda: mock.patch.object(webapp.library, "READ_VERSION", library.READ_VERSION + 1).start()):
            with self.subTest(spoil=spoil):
                spoil()
                with mock.patch.object(webapp.library, "read_song", wraps=library.read_song) as read:
                    songs = self.start().songs
                self.assertEqual((read.call_count, len(songs)), (4, 4))
        mock.patch.stopall()

    def test_details_with_something_missing_are_read_again(self):
        self.start()
        with open(webapp.song_details_path(self.paths), encoding="utf-8") as f:
            kept = json.load(f)
        name = webapp.norm(self.files[2])
        del kept["songs"][name][2]["secs"]
        with open(webapp.song_details_path(self.paths), "w", encoding="utf-8") as f:
            json.dump(kept, f)
        with mock.patch.object(webapp.library, "read_song", wraps=library.read_song) as read:
            songs = self.start().songs
        self.assertEqual(([c.args[0] for c in read.call_args_list], len(songs)), ([self.files[2]], 4))

    def test_it_lives_beside_the_answers(self):
        self.start()
        self.assertEqual(os.path.dirname(webapp.song_details_path(self.paths)), os.path.dirname(self.paths["decisions"]))
        self.assertTrue(os.path.exists(webapp.song_details_path(self.paths)))


def release(rid, master=0, year=1995, label="Label"):
    return dict(id=rid, master_id=master, labels=[dict(name=label)], country="UK", year=year, formats=[dict(name="Vinyl")], title=f"Record {rid}",
                tracklist=[dict(type_="track", title="One"), dict(type_="track", title="Two")])


class DiscogsIndexKept(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pressling-index-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.folder, self.kept = os.path.join(self.root, "discogs"), os.path.join(self.root, "discogs_index.json")
        os.makedirs(self.folder)
        for name, body in (("r1.json", release(1, master=9, year=1996)), ("r2.json", release(2, master=9, year=1993)), ("search.json", {"results": []})):
            self.save(name, body)

    def save(self, name, body):
        with open(os.path.join(self.folder, name), "w", encoding="utf-8") as f:
            json.dump(body, f)

    def test_kept_the_second_time_nothing_is_opened_and_the_answer_is_the_same(self):
        first = webapp.discogs_index(self.folder, self.kept)
        with mock.patch.object(webapp, "_release_info", side_effect=AssertionError("a saved answer was opened")):
            second = webapp.discogs_index(self.folder, self.kept)
        self.assertEqual(second, first)
        self.assertEqual(first, webapp.discogs_index(self.folder))  # the same as without keeping anything
        self.assertEqual((sorted(first[0]), first[1][9]["year"]), ([1, 2], 1993))  # the earliest pressing stands for its master

    def test_only_a_new_or_changed_answer_is_opened(self):
        webapp.discogs_index(self.folder, self.kept)
        self.save("r3.json", release(3))
        self.save("r1.json", release(1, master=9, year=1996, label="Another Label"))
        with mock.patch.object(webapp, "_release_info", wraps=webapp._release_info) as read:
            releases, _ = webapp.discogs_index(self.folder, self.kept)
        self.assertEqual(sorted(c.args[1] for c in read.call_args_list), ["r1.json", "r3.json"])
        self.assertEqual((releases[1]["label"], releases[3]["title"]), ("Another Label", "Record 3"))

    def test_an_answer_that_went_is_forgotten(self):
        webapp.discogs_index(self.folder, self.kept)
        os.remove(os.path.join(self.folder, "r2.json"))
        releases, masters = webapp.discogs_index(self.folder, self.kept)
        self.assertEqual((sorted(releases), masters[9]["year"]), ([1], 1996))
        with open(self.kept, encoding="utf-8") as f:
            self.assertNotIn("r2.json", json.load(f)["answers"])

    def test_a_damaged_kept_file_means_reading_them_all(self):
        webapp.discogs_index(self.folder, self.kept)
        with open(self.kept, "w", encoding="utf-8") as f:
            f.write("[1, 2")
        with mock.patch.object(webapp, "_release_info", wraps=webapp._release_info) as read:
            releases, _ = webapp.discogs_index(self.folder, self.kept)
        self.assertEqual((read.call_count, sorted(releases)), (3, [1, 2]))

    def test_no_folder_is_nothing(self):
        self.assertEqual(webapp.discogs_index(os.path.join(self.root, "nowhere"), self.kept), ({}, {}))


if __name__ == "__main__":
    unittest.main()
