"""A download the log has written counts as done however its path was written, in Curate as on the page: a plan written by hand may give
"D:/Music/Downloads/a.mp3" for what Curate reads as "D:/Music/Downloads\\a.mp3". Nothing here reaches the network."""
import csv
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from music_curator import parse, tagwriter  # noqa: E402
from test_curate import WithDiscogs  # noqa: E402


def another_form(path):
    """The same file, written another way: a "." in the middle, and on Windows forward slashes."""
    path = os.path.join(os.path.dirname(path), ".", os.path.basename(path))
    return path.replace(os.sep, "/") if os.sep == "\\" else path


class DoneWhateverTheForm(WithDiscogs):
    def logged(self, *rows):
        with open(self.log, "w", newline="", encoding="utf-8-sig") as f:
            log = csv.DictWriter(f, fieldnames=tagwriter.LOG_FIELDS)
            log.writeheader()
            for row in rows:
                log.writerow({k: "" for k in tagwriter.LOG_FIELDS} | row)

    def test_a_song_the_log_wrote_in_another_form_is_done_and_not_looked_up(self):
        src = self.file("A - Song")
        self.logged(dict(date="2026-01-01T00:00:00", status="ok", src=another_form(src), dest=os.path.join(self.lib, "Acid", "A - Song.mp3")))
        items, _ = parse.read_inbox([self.inbox], self.log)
        self.assertEqual([it["cat"] for it in items], ["done"])
        _, summary = self.run_it()
        self.assertEqual((summary["waiting"], summary["looked"], self.fake.calls), (0, 0, []))

    def test_one_taken_back_in_another_form_waits_with_what_you_said(self):
        src = self.file("A - Song")
        self.logged(dict(date="2026-01-01T00:00:00", status="ok", src=another_form(src)),
                    dict(date="2026-01-02T00:00:00", status=f"{tagwriter.TAKEN_BACK} (back in your downloads)", src=another_form(src), notes=f"{tagwriter.NOT_RIGHT}: the dub"))
        items, _ = parse.read_inbox([self.inbox], self.log)
        self.assertEqual((items[0]["cat"], items[0]["back"]), ("candidate", "the dub"))

    def test_the_key_is_the_same_for_every_form(self):
        src = os.path.join(self.inbox, "A - Song.mp3")
        self.assertEqual(tagwriter.path_key(another_form(src)), tagwriter.path_key(src))
        self.assertEqual(tagwriter.path_key(""), "")


if __name__ == "__main__":
    unittest.main()
