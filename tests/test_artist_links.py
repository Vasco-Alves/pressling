"""Artist names you can click: each name where a song's artist is shown opens the Songs page with that artist's songs. Standard library only, and node when it's there:

    python -m unittest discover -s tests
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "music_curator", "web")


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as f:
        return f.read()


# The page's own helpers, run in node with a stand-in for the page: el() gives back what it was asked to make, so the test can look at it.
HARNESS = r"""
const made = [];
const el = (tag, attrs, ...kids) => { const n = { tag, attrs: attrs || {}, kids: kids.flat(Infinity), getAttribute: (k) => (attrs || {})[k] }; made.push(n); return n; };
let fxFull = false, routed = 0;
const location = { hash: "" };
const route = () => { routed++; };
%(helpers)s
const S = { lib: [{ genre: "Goa Trance" }, { genre: "Acid" }, { genre: "" }] };
S.songs = [...S.lib, { genre: "Hallucinogen", area: "inbox" }]; // a download whose genre tag is an artist's name (a messy rip): only the library's genres count
%(block)s
const text = (parts) => [parts].flat(Infinity).map((p) => (typeof p === "string" ? p : p.kids.join(""))).join("");
const links = (parts) => [parts].flat(Infinity).filter((p) => typeof p !== "string").map((p) => [p.kids.join(""), p.attrs.href, p.attrs.class, p.attrs.draggable]);
const click = (link) => { const did = []; link.onclick = link.attrs.onclick; link.attrs.onclick({ currentTarget: link, preventDefault: () => did.push("prevent"), stopPropagation: () => did.push("stop") }); return did; };
const out = {};
out.found = {}; // each link, given to the Songs page's own filter: does the page it opens have the song it came from?
for (const [key, artist, area] of %(cases)s) {
  const parts = artistLinks({ artist, area }); out[key] = { text: text(parts), links: links(parts) };
  out.found[key] = links(parts).map(([, href]) => { const p = new URLSearchParams(href.split("?")[1]); return matcher({ q: "", artist: p.get("artist"), area: p.get("area") || "library" })({ artist, area }); });
}
out.album = links(artistLinks({ artist: "Someone", area: "library" }, "Hallucinogen & Someone Else"));
out.line = text(songLine({ artist: "A & B", title: "Song (Mix)", area: "library" }));
out.lineNoArtist = text(songLine({ artist: "", title: "", file: "file.mp3", area: "library" }));
const one = artistLinks({ artist: "Hallucinogen", area: "library" })[0];
out.click = click(one);
location.hash = one.attrs.href;
out.again = [click(one), routed];
fxFull = true;
out.full = click(one);
const line = { scrollLeft: 140 }; // a cut-off line the keyboard slid along to show the name
one.attrs.onblur({ currentTarget: { parentElement: line } });
out.blur = line.scrollLeft;
S.lib = [{ genre: "Hallucinogen" }]; // the library read again: the genre words follow it
out.reread = links(artistLinks({ artist: "Hallucinogen", area: "library" }));
console.log(JSON.stringify(out));
"""

CASES = [
    ("one", "Hallucinogen", "library"),
    ("two", "Astral Projection & Hallucinogen", "library"),
    ("three", "A & B & C", "library"),
    ("genre", "Goa Trance", "library"),
    ("genreCase", "goa trance", "library"),
    ("various", "Various", "library"),
    ("variousArtists", "Various Artists", "library"),
    ("ost", "Final Fantasy VII OST", "library"),
    ("mixed", "Various & Hallucinogen", "library"),
    ("none", "", "library"),
    ("review", "Hallucinogen", "review"),
    ("inbox", "Hallucinogen", "inbox"),
    ("odd", "Name With+Plus & Ünïcode", "library"),
    ("spaced", "Tight&Joined", "library"),
]


def block_of(app):
    start = app.index("/* ---------- artist names you can click ---------- */")
    end = app.index("\nfunction albumItems(", start)
    return app[start:end]


@unittest.skipUnless(shutil.which("node"), "needs node to run the page's own code")
class TheLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = read("app.js")
        helpers = "\n".join(re.search(rf"^const {name} = .*$", app, re.M).group(0) for name in ("fold", "q2", "genresOf"))
        start = app.index("\nfunction matcher(")  # the Songs page's own filter, which a link's page uses
        helpers += app[start:app.index("\n}\n", start) + 3]
        script = HARNESS % dict(helpers=helpers, block=block_of(app), cases=json.dumps(CASES))
        run = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if run.returncode:
            raise AssertionError(run.stderr)
        cls.out = json.loads(run.stdout)

    def test_a_name_is_a_link_to_the_songs_page_with_that_artist(self):
        self.assertEqual(self.out["one"], {"text": "Hallucinogen", "links": [["Hallucinogen", "#/songs?artist=Hallucinogen", "artist-link", "false"]]})

    def test_two_names_are_two_links_and_read_as_before(self):
        self.assertEqual(self.out["two"]["text"], "Astral Projection & Hallucinogen")
        self.assertEqual([link[:2] for link in self.out["two"]["links"]], [["Astral Projection", "#/songs?artist=Astral+Projection"], ["Hallucinogen", "#/songs?artist=Hallucinogen"]])
        self.assertEqual([link[0] for link in self.out["three"]["links"]], ["A", "B", "C"])

    def test_what_is_not_an_artist_stays_words(self):
        for key, words in (("genre", "Goa Trance"), ("genreCase", "goa trance"), ("various", "Various"), ("variousArtists", "Various Artists"), ("ost", "Final Fantasy VII OST")):
            self.assertEqual(self.out[key], {"text": words, "links": []}, key)
        self.assertEqual(self.out["none"], {"text": "", "links": []})
        self.assertEqual(self.out["mixed"]["text"], "Various & Hallucinogen")
        self.assertEqual([link[0] for link in self.out["mixed"]["links"]], ["Hallucinogen"])

    def test_a_song_not_in_the_library_yet_finds_its_artist_everywhere(self):
        for key in ("review", "inbox"):
            self.assertEqual(self.out[key]["links"][0][1], "#/songs?artist=Hallucinogen&area=all", key)

    def test_names_go_into_the_address_safely_and_split_as_the_filter_does(self):
        self.assertEqual(self.out["odd"]["text"], "Name With+Plus & Ünïcode")
        self.assertEqual([link[1] for link in self.out["odd"]["links"]], ["#/songs?artist=Name+With%2BPlus", "#/songs?artist=%C3%9Cn%C3%AFcode"])
        self.assertEqual([link[0] for link in self.out["spaced"]["links"]], ["Tight&Joined"], "only ' & ' parts two names, as on the Songs page")

    def test_an_album_names_its_own_artist(self):
        self.assertEqual([link[:2] for link in self.out["album"]], [["Hallucinogen", "#/songs?artist=Hallucinogen"], ["Someone Else", "#/songs?artist=Someone+Else"]])

    def test_a_song_line_reads_like_its_name(self):
        self.assertEqual(self.out["line"], "A & B - Song (Mix)")
        self.assertEqual(self.out["lineNoArtist"], "file.mp3")

    def test_a_click_is_the_names_alone(self):
        self.assertEqual(self.out["click"], ["stop"], "the row's own click doesn't happen as well, and the link goes where it says")
        self.assertEqual(self.out["again"], [["stop", "prevent"], 1], "on that page already: it's shown again")
        self.assertEqual(self.out["full"], ["prevent"], "in Full view a name is only words, and the click goes on as before")

    def test_a_cut_off_line_goes_back_to_its_start_when_the_keyboard_leaves_a_name(self):
        self.assertEqual(self.out["blur"], 0)

    def test_the_genre_words_follow_the_library(self):
        self.assertEqual(self.out["reread"], [])

    def test_a_downloads_genre_does_not_hide_an_artist(self):
        self.assertEqual(self.out["one"]["links"][0][0], "Hallucinogen", "a download tagged with the genre 'Hallucinogen' leaves the artist a link")

    def test_each_link_opens_a_page_with_the_song_it_came_from(self):
        for key, found in self.out["found"].items():
            self.assertTrue(all(found), f"{key}: the Songs page's filter, given the link, leaves the song out: {found}")
        self.assertEqual(len(self.out["found"]["two"]), 2)


class ThePlaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = read("app.js")
        cls.css = read("style.css")

    def test_every_place_a_songs_artist_is_shown_uses_the_links(self):
        for place in ('el("div", { class: "was", title: s.artist }, artistLinks(s))',  # Overview: Most played
                      'el("span", { class: "t-sub", title: s.artist }, artistLinks(s)),',  # the Songs page's rows
                      'el("span", { class: "t-sub", title: s.artist }, artistLinks(s)));',  # covers, and Recently added
                      'el("h2", {}, s.title), el("div", { class: "by" }, artistLinks(s))',  # the song's panel
                      'el("h1", {}, s.title || s.file),',  # Now Playing ...
                      '$("#np-artist").replaceChildren(...artistLinks(s)',  # the play bar
                      '["Artist", s.artist && artistLinks(s)]',  # Double-check
                      'el("h2", {}, songLine(it.s))',  # Quick To check
                      'by: who.length === 1 ? artistLinks(songs[0], who[0])'):  # an album's head
            self.assertIn(place, self.app)
        now = self.app[self.app.index('el("h1", {}, s.title || s.file),'):][:200]
        self.assertIn('el("div", { class: "by" }, artistLinks(s))', now)
        self.assertEqual(self.app.count('class: "now", title: s.file }, songLine(s))'), 2, "the Spotify to-do: To add and To take out")
        self.assertNotIn("textContent = P.label", self.app)

    def test_full_view_keeps_its_plain_title(self):
        self.assertIn('el("div", { class: "fx-title" }, el("strong", {}, s.title || s.file), el("span", {}, s.artist),', self.app)
        self.assertIn("body.fx-full .artist-link { cursor: inherit; text-decoration: none; }", self.css)

    def test_a_name_looks_as_before_until_the_mouse_is_on_it(self):
        self.assertIn("@media (hover: hover) { .artist-link:hover { text-decoration: underline;", self.css, "the mouse on a name: a quiet underline (only with a mouse, so a tap leaves none behind)")
        self.assertIn(".artist-link:focus-visible { text-decoration: underline;", self.css, "the keyboard on a name: the same")
        rules = re.findall(r"([^{}]*\.artist-link[^{}]*)\{([^{}]*)\}", self.css)  # every rule for the names, wherever it is in the file
        self.assertGreaterEqual(len(rules), 3)
        for where, what in rules:
            self.assertTrue(":hover" in where or ":focus-visible" in where or "fx-full" in where, where)
            self.assertNotIn("color", what.replace("text-decoration-color", ""), "no color of its own: it keeps the look of the words around it")

    def test_on_a_touch_screen_a_rows_words_end_before_its_buttons(self):
        size = int(re.search(r"^\.row-add \{[^}]*\bwidth: (\d+)px", self.css, re.M).group(1))
        heart = int(re.search(r"^\.row-heart \{ right: (\d+)px; \}", self.css, re.M).group(1))
        heart_in_a_playlist = int(re.search(r"^\.row-out \+ \.row-list \+ \.row-add \+ \.row-heart \{ right: (\d+)px; \}", self.css, re.M).group(1))
        cell = int(re.search(r"^\.table td \{ padding: \d+px (\d+)px;", self.css, re.M).group(1))  # the room between a row's words and its edge already
        room = re.search(r"@media \(hover: none\) \{ \.t-cell \.t-sub \{ margin-right: (\d+)px; \} \.t-cell:has\(\.row-out\) \.t-sub \{ margin-right: (\d+)px; \} \}", self.css)
        self.assertTrue(room)
        self.assertGreaterEqual(int(room.group(1)) + cell, heart + size, "the heart is the furthest button from the edge")
        self.assertGreaterEqual(int(room.group(2)) + cell, heart_in_a_playlist + size, "in a playlist too")

    def test_an_artists_page_says_so_and_has_a_way_back(self):
        head = self.app[self.app.index("function artistHead("):]
        head = head[:head.index("\n}")]
        self.assertIn('href: "#/songs" }, icon("music", 14), "All songs")', head)
        self.assertIn(".shelf-head:has(.all-songs) { flex-wrap: wrap; }", self.css)
        self.assertIn('f.artist ? [artistHead(f.artist, list)]', self.app)


if __name__ == "__main__":
    unittest.main()
