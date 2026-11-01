"""Article rendering, message splitting and listing parsing, on trimmed copies of the official markup."""
import importlib.util
import os
import unittest
from unittest.mock import patch
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
        self.assertIn("more **__Ghost-type Pokémon__**!\nDuring this event, you may even meet **__Misdreavus__** and", text)
        # Literal markdown characters stay literal.
        self.assertIn(r"\*stars\* or snake\_case.", text)
        self.assertIn("Event Period:\n- Oct. 26 (Mon) at 4:00 a.m. to Nov. 2 (Mon) at 3:59 a.m.", text)
        self.assertIn("**__Much Greater Appearance Rate__**\n**__Eevee (Halloween)__**, **__Misdreavus__**, and **__Mismagius__**", text)
        self.assertIn("See [this post](<https://www.pokemonsleep.net/en/news/2/>).", text)
        self.assertIn(r"\# not a heading", text)

    def test_dropdown_is_a_section_with_its_headline(self):
        texts = [v for k, v in self.post["parts"] if k == "text"]
        i = texts.index("### Expert Mode Bonuses")
        self.assertEqual(texts[i + 1], "If you participate in an expert area, the bonuses change.\n"
                                       "- Snorlax’s main favorite Berry will be **__Bluk Berries__**.")

    def test_messages_in_the_server_format(self):
        with patch.dict(os.environ, {"NEWS_PING_ROLE_ID": "1167529296332595291"}):
            messages = news.to_messages("123", self.post)
        self.assertEqual(len(messages), 1, "a post that fits is one message")
        content = messages[0]["content"]
        self.assertTrue(content.startswith("# [Event: Halloween 2026 — Double Candy Research](<https://www.pokemonsleep.net/en/news/123/>)\n<@&1167529296332595291>\n\n## Outline\n"))
        self.assertIn("### Other\n**__Much Greater", content)  # a heading runs straight into its text
        self.assertNotIn("view.sleep.pokemon.co.jp", content)  # no images unless NEWS_IMAGES=on
        self.assertEqual(messages[0]["allowed_mentions"], {"parse": [], "roles": ["1167529296332595291"]})
        self.assertLessEqual(len(content), news.LIMIT)

    def test_images_ride_on_the_first_message_and_no_ping_unless_configured(self):
        with patch.dict(os.environ, {"NEWS_PING_ROLE_ID": "", "NEWS_IMAGES": ""}):
            messages = news.to_messages("123", self.post)
        self.assertNotIn("<@&", messages[0]["content"])
        self.assertEqual(messages[0]["allowed_mentions"], {"parse": []})
        self.assertEqual([e["image"]["url"] for e in messages[0]["embeds"]], [
            "https://view.sleep.pokemon.co.jp/news/image/1/Halloween.jpg",
            "https://view.sleep.pokemon.co.jp/news/image/1/second.png"])
        self.assertNotIn("flags", messages[0], "embed suppression would hide the images")
        with patch.dict(os.environ, {"NEWS_IMAGES": "off"}):
            self.assertNotIn("embeds", news.to_messages("123", self.post)[0])

    def test_event_period_and_link(self):
        self.assertEqual(self.post["period"], (1792987200, 1793591999))
        messages = news.to_messages("123", self.post, "https://discord.gg/pokemonsleep?event=42")
        self.assertTrue(messages[-1]["content"].endswith("\n\nhttps://discord.gg/pokemonsleep?event=42"))

    def test_event_uses_local_wall_clock_in_the_configured_zone(self):
        with patch.dict(os.environ, {"NEWS_EVENT_TZ": "America/New_York"}):
            e = news.event_payload("123", self.post, now=1792000000)
        # Oct. 26 at 4:00 a.m. New York time (EDT), not 4:00 UTC.
        self.assertEqual(e["scheduled_start_time"], "2026-10-26T08:00:00+00:00")
        self.assertEqual((e["name"], e["entity_type"], e["privacy_level"]), ("Halloween 2026 — Double Candy Research", 3, 2))
        self.assertEqual(e["entity_metadata"]["location"], "https://www.pokemonsleep.net/en/news/123/")
        self.assertIsNone(news.event_payload("123", self.post, now=1793700000), "an event that's over gets none")

    def test_event_is_created_once(self):
        calls = []
        def bot(method, path, payload=None):
            calls.append(method)
            if method == "GET":
                return [{"id": "9", "entity_metadata": {"location": "https://www.pokemonsleep.net/en/news/123/"}}] if len(calls) > 2 else []
            return {"id": "9"}
        env = {"DISCORD_GUILD_ID": "1", "DISCORD_BOT_TOKEN": "t", "NEWS_EVENT_INVITE": "pokemonsleep"}
        with patch.dict(os.environ, env), patch.object(news, "_bot", bot), patch.object(news, "_cover", lambda post: None), \
                patch.object(news.time, "time", lambda: 1792000000), patch("builtins.print"):  # a fake bot: keep the real run's log clean
            self.assertEqual(news.ensure_event("123", self.post), "https://discord.gg/pokemonsleep?event=9")
            self.assertEqual(news.ensure_event("123", self.post), "https://discord.gg/pokemonsleep?event=9")
        self.assertEqual(calls, ["GET", "POST", "GET"], "the second run reused the event")

    def test_no_event_without_a_bot(self):
        with patch.dict(os.environ, {"DISCORD_GUILD_ID": "", "DISCORD_BOT_TOKEN": ""}):
            self.assertIsNone(news.ensure_event("123", self.post))

    def test_typed_urls_do_not_unfurl(self):
        post = news.parse_post(POST.replace("<p># not a heading</p>", "<p>See https://example.com/a_b now</p>"))
        text = "\n\n".join(v for k, v in post["parts"] if k == "text")
        self.assertIn("See <https://example.com/a_b> now", text)


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

    def test_fewest_messages_split_at_a_section(self):
        para = "word " * 70  # ~350 characters
        text = "\n\n".join([para] * 4 + ["## Section\n" + para] + [para] * 4)  # ~3,200: two messages
        chunks = news._split(text)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[1].startswith("## Section"))

    def test_one_message_when_it_fits(self):
        text = "## A\n" + "word " * 300 + "\n\n## B\n" + "word " * 50
        self.assertEqual(news._split(text), [text])

    def test_long_post_takes_the_fewest_messages_that_break_naturally(self):
        para = ("word " * 69 + "end.").strip()
        text = "\n\n".join([para] * 16)  # ~5,700: 3 would cut a paragraph, so 4
        chunks = news._split(text)
        self.assertEqual(len(chunks), 4)
        self.assertTrue(all(len(c) <= news.LIMIT for c in chunks))
        self.assertTrue(all(set(c.split("\n\n")) == {para} for c in chunks), "a paragraph was cut")

    def test_unbreakable_text_still_fits(self):
        chunks = news._split("x" * 4500)
        self.assertTrue(len(chunks) == 3 and all(len(c) <= news.LIMIT for c in chunks))


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
