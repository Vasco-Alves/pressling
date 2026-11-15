"""Tests for how Now Playing is laid out on different windows (style.css): nothing on it covers anything else.

    python -m unittest discover -s tests
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "music_curator", "web")
# the three kinds of big window, as style.css names them (each must be there exactly once)
OWN_ROW = re.compile(r"@media \(max-width: (\d+)px\), \(max-height: (\d+)px\) \{")  # the effects bar gets a row of its own
FLOATING = re.compile(r"@media \(width > (\d+)px\) and \(height > (\d+)px\) and \(aspect-ratio > (\d+)/(\d+)\) \{")  # it sits at the top right, beside the record
TALL = re.compile(r"@media \(max-aspect-ratio: (\d+)/(\d+)\) \{")  # a tall window: one column
NARROW = re.compile(r"@container content \(max-width: (\d+)px\) \{")  # a phone (there is more than one of these)
WIDER = re.compile(r"@container content \(width > (\d+)px\) \{")  # anything wider than a phone


def css():
    with open(os.path.join(WEB, "style.css"), encoding="utf-8") as f:
        return re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)  # (without its comments)


def blocks(text, head):
    """What is inside the braces of every rule whose head matches (a pattern, or exact text), with the numbers in each head."""
    found = []
    for m in (head.finditer(text) if isinstance(head, re.Pattern) else re.finditer(re.escape(head), text)):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        found.append((text[m.end():i - 1], m.groups()))
    return found


def block(text, head):
    """What is inside the braces of the one rule whose head matches (it must be there exactly once), and the numbers in its head."""
    found = blocks(text, head)
    assert len(found) == 1, (getattr(head, "pattern", head), len(found))
    return found[0]


def top_level(text):
    """The stylesheet without its @media and @container parts: the rules for every window."""
    out, i = [], 0
    while i < len(text):
        at = text.find("@", i)
        if at < 0:
            out.append(text[i:]); break
        out.append(text[i:at])
        depth, j = 0, text.index("{", at)
        while True:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
            if not depth:
                break
        i = j
    return "".join(out)


def rule(text, selector):
    """The declarations of the one plain rule for selector in text, as a dictionary."""
    found = re.findall(r"(?:^|[}{\n])\s*" + re.escape(selector) + r"\s*\{([^{}]*)\}", text)
    assert len(found) == 1, (selector, len(found))
    return {k.strip(): v.strip() for k, v in (d.split(":", 1) for d in found[0].split(";") if ":" in d)}


def px(value):
    return float(re.fullmatch(r"(-?[\d.]+)px", value).group(1))


class NowPlayingLayout(unittest.TestCase):
    def test_the_stylesheet_is_whole(self):
        text = css()
        self.assertEqual(text.count("{"), text.count("}"))
        with open(os.path.join(WEB, "style.css"), encoding="utf-8") as f:
            self.assertNotIn("backdrop-filter", f.read(), "it made a big window slow (see glass.css)")

    def test_every_window_is_one_kind_and_only_one(self):
        # the effects bar floats beside the record only where the rule that gives it a row of its own stops (wider than 1400 and taller than 1060 dots),
        # a window is either tall or not, and a tall window gets one column just where the phone's rules stop: no gap between them, even at a zoom with fractions of a dot
        text = css()
        _, own = block(text, OWN_ROW)
        _, floating = block(text, FLOATING)
        tall_inside, tall = block(text, TALL)
        self.assertEqual(floating[:2], own, "the floating bar starts exactly where its own row stops")
        self.assertEqual(floating[2:], tall, "a window is either tall or not: never both, never neither")
        _, wider = block(tall_inside, WIDER)
        phone = [n for chunk, (n,) in blocks(text, NARROW) if ".now-page {" in chunk]  # (the phone's own Now Playing rules)
        self.assertEqual([wider[0]], phone, "a tall window is either a phone or gets one column")

    def test_up_next_and_about_this_song_start_below_the_bar_however_tall_it_is(self):
        inside, _ = block(css(), FLOATING)
        page, bar = rule(inside, ".now-page:has(> .fx-bar)"), rule(inside, ".fx-bar")
        self.assertEqual(bar["position"], "relative")  # in the page's own grid, not floating over it (where it covered the side's buttons)
        self.assertEqual(bar["grid-area"], "1 / 1 / 2 / -1")  # a row of its own...
        self.assertRegex(page["grid-template-rows"], r"^minmax\([\d.]+px, auto\) minmax\(0, 1fr\)$")  # ...as tall as the bar needs, and the rest for the side
        self.assertEqual(page["row-gap"], "0")  # (the page's usual 20px gap between rows would push the side down even with one line)
        self.assertEqual(bar["place-self"], "start end")  # still at the top right, only as wide as its buttons
        self.assertEqual(rule(inside, ".fx-bar ~ .now-queue")["grid-area"], "2 / 2 / 3 / 3")  # the side in the row under it
        stage = rule(inside, ".fx-bar ~ .now-stage")
        self.assertEqual(stage["grid-area"], "1 / 1 / 3 / 2")  # the record across both, so it stays in the middle
        self.assertEqual(stage["align-self"], "safe center")  # (and a record with a very long title under it starts at the top, where it always did, not higher under the bar)
        self.assertEqual(rule(inside, ".now-page.side-off .now-queue")["grid-area"], "auto")  # folded away, the side's button keeps its place at the edge
        wide, _ = block(inside, "@media (min-width: 1500px) {")
        self.assertEqual(rule(wide, "body.fx-full .fx-bar"), {"place-self": "auto", "margin-top": "0"})  # full view there keeps its bar fixed across the top
        fixed = [chunk for chunk, _ in blocks(css(), "@media (min-width: 1500px) {") if "fixed" in rule(chunk, "body.fx-full .fx-bar").get("position", "")]
        self.assertEqual(len(fixed), 1)  # (the rule that fixes it there)

    def test_with_the_bar_on_one_line_everything_is_where_it_was(self):
        # the bar used to float 14 dots above the page with the side 40 dots down: the new row and the space under it add up to the same
        text = css()
        inside, _ = block(text, FLOATING)
        before_top = px(rule(top_level(text), ".fx-bar")["top"])
        before_gap = px(rule(top_level(text), ".fx-bar ~ .now-queue")["padding-top"])
        rows = rule(inside, ".now-page:has(> .fx-bar)")["grid-template-rows"]
        least = px(re.match(r"minmax\(([\d.]+px)", rows).group(1))
        self.assertEqual(px(rule(inside, ".fx-bar")["margin-top"]), before_top)
        self.assertEqual(least + px(rule(inside, ".fx-bar ~ .now-queue")["padding-top"]), before_gap)

    def test_about_this_song_is_only_as_tall_as_what_it_has_to_say(self):
        # on every window: with an effect on it is a dark box, and it was as tall as the page with a few facts at the top
        about = rule(top_level(css()), ".now-about-song")
        self.assertEqual(about["flex"], "0 1 auto")  # as tall as its facts, shorter only when the side is full
        self.assertEqual(about["min-height"], "0")
        self.assertEqual(about["overflow-y"], "auto")  # and then it scrolls inside itself

    def test_on_a_tall_window_the_side_is_only_as_tall_as_what_it_has_to_say(self):
        tall, _ = block(css(), TALL)
        inside, _ = block(tall, WIDER)
        page = rule(inside, ".now-page:not(.now-nothing)")
        self.assertEqual((page["display"], page["flex-direction"]), ("flex", "column"))  # one column
        side = rule(inside, ".now-page .now-queue")
        self.assertEqual(side["flex"], "0 1 auto")  # as tall as it needs, and shorter only when the screen is full
        self.assertEqual(side["min-height"], "0")
        self.assertEqual(side["margin-bottom"], "auto")
        self.assertEqual(rule(inside, ".now-page .now-queue .tracks")["flex"], "0 1 auto")  # a long list scrolls inside itself, not the page
        self.assertEqual(rule(inside, ".now-page .now-stage")["margin-top"], "auto")  # the record and the side together in the middle of what's left under the bar
        self.assertIn("vh", rule(inside, ".now-page .now-stage .sleeve")["width"])  # the record grows with the window's height, leaving room for the side
        off = rule(inside, ".now-page.side-off .now-queue")
        self.assertEqual((off["position"], off["transform"]), ("static", "none"))  # folded away, its button sits under the record, not over it at the edge

    def test_full_view_keeps_its_bar_where_it_was(self):
        # full view hides the close button and has its own place for the bar: the rules that move the bar clear of that button leave it alone
        text = css()
        tall, _ = block(text, TALL)
        inside, _ = block(tall, WIDER)
        moved = [b for chunk, _ in blocks(text, NARROW) for b in re.findall(r"([^{};]*\.fx-bar)\s*\{[^{}]*margin-top", chunk)] + re.findall(r"([^{};]*\.fx-bar)\s*\{[^{}]*margin-top", inside)
        self.assertEqual(len(moved), 2, moved)
        for selector in moved:
            self.assertTrue(selector.strip().startswith("body:not(.fx-full) "), selector)

    def test_the_bar_starts_below_the_close_button(self):
        text = css()
        close, button = rule(top_level(text), ".now-close"), rule(top_level(text), ".icon-btn")
        bottom = px(close["top"]) + px(button["height"])
        tall, _ = block(text, TALL)
        inside, _ = block(tall, WIDER)
        narrow = [rule(chunk, "body:not(.fx-full) .fx-bar") for chunk, _ in blocks(text, NARROW) if "body:not(.fx-full) .fx-bar" in chunk]
        self.assertEqual(len(narrow), 1)
        self.assertGreater(px(narrow[0]["margin-top"]), bottom)  # on a phone
        self.assertGreater(px(rule(inside, "body:not(.fx-full) .now-page .fx-bar")["margin-top"]), bottom)  # on a tall window

    def test_the_close_button_is_above_the_effects_bar(self):
        text = top_level(css())
        close, bar = rule(text, ".now-close"), rule(text, ".fx-bar")
        self.assertGreater(int(close["z-index"]), int(bar["z-index"]))  # the bar's empty box reaches over the button: on the same layer the bar, later on the page, took its clicks


if __name__ == "__main__":
    unittest.main()
