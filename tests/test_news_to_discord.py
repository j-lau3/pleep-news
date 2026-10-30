"""Article rendering, message splitting and listing parsing, on trimmed copies of the official markup."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("news", Path(__file__).resolve().parents[1] / "news_to_discord.py")
news = importlib.util.module_from_spec(spec)
spec.loader.exec_module(news)

# Shape of "Event: Halloween 2026 — Double Candy Research", trimmed.
POST = """<html><body><nav><img src="/img/logo-4.png"></nav>
<div id="wrapper"><article id="post" class="article_2">
  <header class="header_4">
    <div class="header_4__category">
  <a class='icon_5' href='https://www.pokemonsleep.net/en/news/category/event/'>Events</a>
</div>    <p class="header_4__date">
          <time datetime="" data-time="1791946800">10/14/2026</time>
    </p>
    <h1 class="header_4__h1">
      Event: Halloween 2026 — Double Candy Research    </h1>
  </header>
  <div class="article_2__content wp_content_4">
    <p><img decoding="async" src="https://view.sleep.pokemon.co.jp/news/image/1/Halloween.jpg"></p>
<h1>Outline</h1>
<p>This week-long event is a special opportunity to encounter more <strong>Ghost-type Pokémon</strong>!<br />
During this event, you may even meet <strong>Misdreavus </strong>and *stars* or snake_case.</p>
<p>Event Period:<br />
• <time data-unixtime='1792987200'>Oct. 26 (Mon) at 4:00 a.m.</time> to <time data-unixtime='1793591999'>Nov. 2 (Mon) at 3:59 a.m.</time></p>
<div class="news-detail">
<div class="news-detail-header">
<div class="news-detail-icon"></div>
<div class="news-detail-headline">Expert Mode Bonuses</div>
<div class="news-detail-right-icon"></div>
</div>
<div class="body">
<div class="bodyContainer">
<div class="news-detail-normal-text">If you participate in an expert area, the bonuses change.<br />
• Snorlax’s main favorite Berry will be <strong>Bluk Berries</strong>.</div>
</div>
</div>
<div class="news-detail-footer"></div>
</div>
<h2>Other</h2>
<p><strong>Much Greater Appearance Rate</strong><br />
<b>Eevee (Halloween)</b>, <b>Misdreavus</b>, and <b>Mismagius</b></p>
<p>See <a href="/en/news/2/">this post</a>.</p>
<p><img src="https://view.sleep.pokemon.co.jp/news/image/1/second.png"></p>
<p># not a heading</p>
  </div>
</article>
<footer><img src="/img/logo-4.png"></footer></body></html>"""

LISTING = """<ul class="items">
<li><a class="banner_2" href="https://www.pokemonsleep.net/en/news/343537333337383034353836373435383632/">
<p class="banner_2__category"><span class='icon_5'>Events</span></p>
<p class="banner_2__title">
  Event: New Moon Day #19   </p></a></li>
<li><a class="banner_2" href="https://www.pokemonsleep.net/en/news/343538313035343932353134373939363138/">
<p class="banner_2__category"><span class='icon_5'>Performance Issues</span></p>
<p class="banner_2__title">Bug Fix Notice</p></a></li>
</ul>"""


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.post = news.parse_post(POST)

    def test_header(self):
        self.assertEqual((self.post["title"], self.post["category"], self.post["date"]),
                         ("Event: Halloween 2026 — Double Candy Research", "Events", "10/14/2026"))

    def test_parts_in_page_order_with_only_article_images(self):
        kinds = [k for k, _ in self.post["parts"]]
        self.assertEqual(kinds[0], "image")
        self.assertEqual([v for k, v in self.post["parts"] if k == "image"], [
            "https://view.sleep.pokemon.co.jp/news/image/1/Halloween.jpg",
            "https://view.sleep.pokemon.co.jp/news/image/1/second.png",
        ])

    def test_text_word_for_word_as_markdown(self):
        text = "\n\n".join(v for k, v in self.post["parts"] if k == "text")
        self.assertIn("## Outline", text)
        self.assertIn("### Other", text)
        # Bold, with the space Discord needs outside the markers; <br> is a line break.
        self.assertIn("more **Ghost-type Pokémon**!\nDuring this event, you may even meet **Misdreavus** and", text)
        # Literal markdown characters stay literal.
        self.assertIn(r"\*stars\* or snake\_case.", text)
        self.assertIn("Event Period:\n• Oct. 26 (Mon) at 4:00 a.m. to Nov. 2 (Mon) at 3:59 a.m.", text)
        self.assertIn("**Much Greater Appearance Rate**\n**Eevee (Halloween)**, **Misdreavus**, and **Mismagius**", text)
        self.assertIn("See [this post](<https://www.pokemonsleep.net/en/news/2/>).", text)
        self.assertIn(r"\# not a heading", text)

    def test_dropdown_is_one_quote_with_its_headline(self):
        quotes = [v for k, v in self.post["parts"] if k == "text" and v.startswith(">")]
        self.assertEqual(quotes, [
            "> **Expert Mode Bonuses**\n"
            "> If you participate in an expert area, the bonuses change.\n"
            "> • Snorlax’s main favorite Berry will be **Bluk Berries**."
        ])

    def test_messages(self):
        messages = news.to_messages("123", self.post)
        self.assertTrue(messages[0]["content"].startswith("# Event: Halloween 2026 — Double Candy Research\n-# Events · 10/14/2026 · <"))
        self.assertEqual(messages[1]["embeds"][0]["image"]["url"], "https://view.sleep.pokemon.co.jp/news/image/1/Halloween.jpg")
        for m in messages:
            self.assertEqual(m["allowed_mentions"], {"parse": []})
            self.assertLessEqual(len(m.get("content", "")), news.LIMIT)


class SplitTests(unittest.TestCase):
    def test_long_text_splits_at_blocks_and_keeps_headings_with_their_text(self):
        para = "word " * 150  # ~750 characters
        text = f"{para}\n\n{para}\n\n## Heading\n\n{para}"
        chunks = news._split(text)
        self.assertTrue(all(len(c) <= news.LIMIT for c in chunks))
        self.assertTrue(chunks[1].startswith("## Heading"))
        self.assertEqual("".join(chunks).replace(" ", "").replace("\n", ""), text.replace(" ", "").replace("\n", ""))

    def test_bold_never_left_open_across_messages(self):
        text = "**" + "bold " * 500 + "**"
        for chunk in news._split(text):
            self.assertEqual(chunk.count("**") % 2, 0)


class SplitChoiceTests(unittest.TestCase):
    def test_prefers_a_section_break_over_a_later_paragraph_break(self):
        para = "word " * 70  # ~350 characters
        text = "\n\n".join([para] * 3 + ["## Section", para] + [para] * 3)
        first = news._split(text)[0]
        self.assertTrue(first.endswith(para.strip()))
        self.assertNotIn("## Section", first)

    def test_long_paragraph_breaks_after_a_sentence(self):
        text = ("This is a sentence. " * 150).strip()
        chunks = news._split(text)
        self.assertTrue(all(len(c) <= news.LIMIT for c in chunks))
        self.assertTrue(chunks[0].endswith("."))

    def test_dropdown_stays_quoted_when_split_mid_line(self):
        text = "> **Notes**\n> " + "A long note sentence here. " * 120
        chunks = news._split(text)
        self.assertTrue(all(c.startswith(">") for c in chunks))


class RunTests(unittest.TestCase):
    """Several new posts at once, a send that stops partway, and a post that keeps failing."""

    def setUp(self):
        self.sent = []          # (post id, message index)
        self.broken = set()     # post ids whose sends fail
        self.stop_after = {}    # post id -> fail after this many messages, once
        self.saves = 0

        def post_one(post_id, webhook, start=0, sent=lambda n: None):
            for n in range(start, 3):  # every fake post is 3 messages
                if post_id in self.broken or self.stop_after.get(post_id) == n:
                    self.stop_after.pop(post_id, None)
                    raise RuntimeError("Discord 500")
                self.sent.append((post_id, n))
                sent(n + 1)
            return 3
        self.real, news.post_one = news.post_one, post_one

    def tearDown(self):
        news.post_one = self.real

    def save(self, state):
        self.saves += 1

    def listing(self, *ids):  # newest first, like the site
        return [{"id": i, "title": i, "category": ""} for i in ids]

    def test_burst_goes_out_oldest_first_each_post_whole(self):
        state = {"posted": ["old"], "partial": {}, "failures": {}}
        failed = news.run(self.listing("c", "b", "a", "old"), state, "hook", self.save)
        self.assertEqual(failed, 0)
        self.assertEqual(self.sent, [(i, n) for i in ("a", "b", "c") for n in range(3)])
        self.assertEqual(state["posted"], ["old", "a", "b", "c"])
        # Nothing is sent twice on the next run.
        self.sent = []
        news.run(self.listing("c", "b", "a", "old"), state, "hook", self.save)
        self.assertEqual(self.sent, [])

    def test_a_post_stopped_partway_resumes_without_repeating(self):
        state = {"posted": [], "partial": {}, "failures": {}}
        self.stop_after["a"] = 2
        self.assertEqual(news.run(self.listing("b", "a"), state, "hook", self.save), 1)
        self.assertEqual(state["partial"], {"a": 2})
        self.assertEqual(state["posted"], ["b"])  # the next post still went out
        news.run(self.listing("b", "a"), state, "hook", self.save)
        self.assertEqual([m for m in self.sent if m[0] == "a"], [("a", 0), ("a", 1), ("a", 2)])
        self.assertEqual(state, {"posted": ["b", "a"], "partial": {}, "failures": {}})

    def test_a_post_that_keeps_failing_is_skipped_after_three_runs(self):
        state = {"posted": [], "partial": {}, "failures": {}}
        self.broken.add("a")
        for _ in range(news.MAX_TRIES):
            news.run(self.listing("a"), state, "hook", self.save)
        self.assertIn("a", state["posted"])
        self.assertEqual(self.sent, [])


class ListingTests(unittest.TestCase):
    def test_listing(self):
        self.assertEqual(news.parse_listing(LISTING), [
            {"id": "343537333337383034353836373435383632", "title": "Event: New Moon Day #19", "category": "Events"},
            {"id": "343538313035343932353134373939363138", "title": "Bug Fix Notice", "category": "Performance Issues"},
        ])


if __name__ == "__main__":
    unittest.main()
