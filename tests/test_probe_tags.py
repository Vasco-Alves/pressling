"""ffprobe's names for a song's tags, as tagwriter.probe hands them on. Since ffmpeg 9, ffprobe names a comment by its language ("comment-eng") when it isn't "und";
the check after a write and the "downloaded from" link still find it as the comment. Nothing here runs ffprobe: its answer is made up."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from music_curator import tagwriter  # noqa: E402

LINK = "https://www.discogs.com/release/1"


def probed(tags):
    """What tagwriter.probe makes of an ffprobe that answers with these tags."""
    answer = json.dumps({"format": {"duration": "61.5", "tags": tags}, "streams": [{"codec_type": "audio", "codec_name": "mp3"}]})
    with mock.patch.object(tagwriter.subprocess, "run", return_value=mock.Mock(stdout=answer)):
        return tagwriter.probe("song.mp3")


class CommentByLanguage(unittest.TestCase):
    def test_a_comment_named_by_its_language_is_the_comment(self):
        self.assertEqual(probed({"title": "Song", "comment-eng": LINK})["tags"]["comment"], LINK)

    def test_any_language_and_any_case(self):
        self.assertEqual(probed({"COMMENT-deu": LINK})["tags"]["comment"], LINK)

    def test_a_plain_comment_wins_over_one_named_by_its_language(self):
        self.assertEqual(probed({"comment": LINK, "comment-eng": "something else"})["tags"]["comment"], LINK)

    def test_the_other_tags_are_as_ffprobe_named_them(self):
        got = probed({"TITLE": "Song", "Artist": "A", "comment-eng": LINK})
        self.assertEqual((got["tags"]["title"], got["tags"]["artist"], got["tags"]["comment-eng"]), ("Song", "A", LINK))
        self.assertEqual((got["secs"], got["codec"], got["pics"]), (61.5, "mp3", []))

    def test_no_comment_is_no_comment(self):
        self.assertNotIn("comment", probed({"title": "Song"})["tags"])


if __name__ == "__main__":
    unittest.main()
