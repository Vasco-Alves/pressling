"""New downloads: what the page mentions are the songs in your downloads folders that Curate hasn't looked at yet. It only tells you."""
import csv
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from music_curator import tagwriter, webapp  # noqa: E402
from test_apply import Base  # noqa: E402
from test_webapp import write_mp3  # noqa: E402


class NewDownloads(Base):
    def setUp(self):
        super().setUp()
        self.paths = webapp.app_paths()
        self.collection = webapp.Collection(self.paths)

    def download(self, name):
        return write_mp3(os.path.join(self.inbox, name + ".mp3"), artist="", title=name, album="", genre="Other", year="")

    def test_a_new_song_is_mentioned(self):
        self.download("B - New One")
        self.assertEqual(self.collection.new_downloads(), dict(count=1, names=["B - New One.mp3"]))

    def test_songs_already_curated_or_looked_at_are_not(self):
        written, held = self.download("A - Written"), self.download("C - Held")
        self.download("D - Fresh")
        with open(self.log, "w", newline="", encoding="utf-8-sig") as f:
            log = csv.DictWriter(f, fieldnames=tagwriter.LOG_FIELDS)
            log.writeheader()
            log.writerow({k: "" for k in tagwriter.LOG_FIELDS} | dict(date="2026-01-01T00:00:00", status="ok", src=written, dest=os.path.join(self.lib, "X", "A - Written.mp3")))
        with open(webapp.curated_path(self.paths), "w", encoding="utf-8") as f:
            json.dump({"songs": {f"inbox/{os.path.basename(self.inbox)}/C - Held.mp3": {"why": "not found on Discogs"}}}, f)
        self.assertEqual(self.collection.new_downloads(), dict(count=1, names=["D - Fresh.mp3"]))

    def test_only_the_first_few_are_named(self):
        for i in range(8):
            self.download(f"Song {i}")
        got = self.collection.new_downloads()
        self.assertEqual((got["count"], len(got["names"])), (8, 5))

    def test_nothing_new_is_nothing(self):
        self.assertEqual(self.collection.new_downloads(), dict(count=0, names=[]))


if __name__ == "__main__":
    unittest.main()
