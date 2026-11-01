#!/usr/bin/env python3
"""
Official Pokémon Sleep news posts, copied word for word into a Discord channel.

Each new post on https://www.pokemonsleep.net/en/news/ becomes one webhook
message in the server's format: the linked title, an optional role ping, then
the article as Discord markdown (headings, bold-underline, lists, links and every
dropdown as a ### section). A post longer than Discord's 2,000 characters takes
the fewest messages possible, split at the most natural point (a section first).
posted.json remembers what was already sent.

The article's images ride along as embeds on the post's first message
(NEWS_IMAGES=off to leave them out). With a bot configured, an "Event Period"
also becomes a Discord scheduled event, linked at the end of the post.

Secrets: NEWS_WEBHOOK_URL; DISCORD_BOT_TOKEN (optional, for events).
Variables (optional): NEWS_PING_ROLE_ID, NEWS_IMAGES, DISCORD_GUILD_ID,
NEWS_EVENT_INVITE (the server's invite code, e.g. pokemonsleep, for event links),
NEWS_EVENT_TZ (time zone for event times, default America/New_York).

    python3 news_to_discord.py                 # post new posts (NEWS_WEBHOOK_URL)
    python3 news_to_discord.py --dry-run       # print what would be sent
    python3 news_to_discord.py --post ID       # (re)post one post, ignoring posted.json
    python3 news_to_discord.py --latest 3      # post the 3 newest posts (testing), ignoring posted.json
    python3 news_to_discord.py --seed          # mark everything listed as posted

With no posted.json yet, a normal run seeds instead of flooding the channel
with every post on the site.
"""
import argparse
import base64
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from pathlib import Path

SITE = "https://www.pokemonsleep.net"
LIST = SITE + "/en/news/page/{}/"
POST = SITE + "/en/news/{}/"
PAGES = 3  # 15 posts: more than ever come out between hourly runs
STATE = Path(__file__).resolve().parent / "posted.json"
KEEP = 300
LIMIT = 2000  # Discord message content limit
AGENT = "Mozilla/5.0 (compatible; pleep-news; +https://github.com/jlau3)"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_listing(page: str) -> list[dict]:
    """Posts on one listing page, newest first: id, title, category."""
    posts = []
    for chunk in re.split(r'<a class="banner_2" href="', page)[1:]:
        post_id = re.match(r"https://www\.pokemonsleep\.net/en/news/(\d{20,})/", chunk)
        title = re.search(r'class="banner_2__title">(.*?)</p>', chunk, re.S)
        if post_id and title:
            category = re.search(r'class="banner_2__category">(.*?)</p>', chunk, re.S)
            posts.append({"id": post_id[1], "title": _text(title[1]), "category": _text(category[1]) if category else ""})
    return posts


# --- Article HTML -> Discord markdown -------------------------------------------------

_ESCAPE = re.compile(r"([\\*_~`|])")


def escape(text: str) -> str:
    """Literal text: markdown characters must not format anything."""
    return _ESCAPE.sub(r"\\\1", text)


def _line_start_safe(line: str) -> str:
    """Text that would read as a heading, list or quote at a line start stays literal."""
    return re.sub(r"^(\s*)(#{1,3} |-# |> |>>> |[-*+] |\d+\. )", lambda m: m[1] + "\\" + m[2], line)


class _Article(HTMLParser):
    """Walks the article body. Output is a list of parts: ("text", markdown) per
    block (paragraph, heading, dropdown) and ("image", url), in page order."""

    BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "li", "ul", "ol", "table", "tr", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[tuple[str, str]] = []
        self.buf: list[str] = []
        self.prefix = ""          # heading marker for the current block
        self.bold = 0
        self.italic = 0
        self.link: list[tuple[str, int]] = []  # (href, buffer index where the text starts)
        self.skip = 0             # inside script/style
        self.dropdown = 0         # depth of div.news-detail
        self.stack: list[tuple[str, str]] = []  # (tag, class)
        self.in_headline = False
        self.cells: list[str] | None = None
        self.list_kind: list[str] = []
        self.list_n: list[int] = []

    # Inline buffer
    def _emit(self, s: str):
        self.buf.append(s)

    def _flush(self):
        text = "".join(self.buf)
        self.buf = []
        lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n")]
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        prefix, self.prefix = self.prefix, ""
        if not lines:
            return
        lines = [_line_start_safe(l) if i or not prefix else l for i, l in enumerate(lines)]
        lines = [re.sub(r"^[•・]\s*", "- ", l) for l in lines]  # the site's typed bullets become a real list
        if prefix:
            lines[0] = prefix + lines[0]
        self.parts.append(("text", "\n".join(lines)))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        self.stack.append((tag, cls))
        if tag in ("script", "style"):
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "div" and "news-detail" in cls.split():
            self._flush()
            self.dropdown += 1
            return
        if tag == "div" and "news-detail-headline" in cls.split():
            self._flush()
            self.in_headline = True
            self.prefix = "### "  # a dropdown becomes its own ### section
            return
        if tag in self.BLOCKS:
            self._flush()
            if tag == "h1":
                self.prefix = "## "
            elif tag == "h2":
                self.prefix = "### "
            elif tag in ("h3", "h4"):
                self._emit("**")
                self.bold += 1
            elif tag in ("ul", "ol"):
                self.list_kind.append(tag)
                self.list_n.append(0)
            elif tag == "li":
                if self.list_kind and self.list_kind[-1] == "ol":
                    self.list_n[-1] += 1
                    self.prefix = f"{self.list_n[-1]}. "
                else:
                    self.prefix = "- "
            elif tag == "tr":
                self.cells = []
            return
        if tag in ("th", "td") and self.cells is not None:
            self.cells.append("")
            self._cell_start = len(self.buf)
            if tag == "th":
                self._emit("**")
                self.bold += 1
            return
        if tag == "br":
            self._emit("\n")
        elif tag in ("strong", "b"):
            if not self.bold and not self.in_headline:
                self._emit("**")
            self.bold += 1
        elif tag in ("em", "i"):
            if not self.italic:
                self._emit("*")
            self.italic += 1
        elif tag == "a":
            href = a.get("href") or ""
            if href.startswith("/"):
                href = SITE + href
            self.link.append((href, len(self.buf)))
        elif tag == "img":
            src = a.get("src") or ""
            if src.startswith("/"):
                src = SITE + src
            if src:
                self._flush()
                self.parts.append(("image", src))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ("br", "img"):
            self.stack.pop()

    def handle_endtag(self, tag):
        # Pop to the matching open tag (the article's HTML is not always tidy).
        while self.stack:
            open_tag, cls = self.stack.pop()
            if open_tag == tag:
                break
        else:
            return
        if tag in ("script", "style"):
            self.skip -= 1
            return
        if self.skip:
            return
        if tag == "div" and "news-detail-headline" in cls.split():
            self.in_headline = False
            self._flush()
            return
        if tag == "div" and "news-detail" in cls.split():
            self._flush()
            self.dropdown -= 1
            return
        if tag in ("th", "td") and self.cells is not None:
            if tag == "th":
                self._emit("**")
                self.bold -= 1
            cell = "".join(self.buf[self._cell_start:]).strip()
            del self.buf[self._cell_start:]
            self.cells[-1] = cell
            return
        if tag == "tr" and self.cells is not None:
            row, self.cells = " | ".join(c for c in self.cells if c), None
            self._emit(row)
            self._flush()
            return
        if tag in ("h3", "h4"):
            self.bold -= 1
            self._emit("**")
            self._flush()
            return
        if tag in ("ul", "ol"):
            self._flush()
            if self.list_kind:
                self.list_kind.pop()
                self.list_n.pop()
            return
        if tag in self.BLOCKS:
            self._flush()
            return
        if tag in ("strong", "b"):
            self.bold -= 1
            if not self.bold and not self.in_headline:
                self._emit("**")
        elif tag in ("em", "i"):
            self.italic -= 1
            if not self.italic:
                self._emit("*")
        elif tag == "a" and self.link:
            href, start = self.link.pop()
            label = "".join(self.buf[start:]).strip()
            del self.buf[start:]
            if href and label and label != escape(href):
                self._emit(f"[{label}](<{href}>)")
            elif href:
                self._emit(f"<{href}>")
            else:
                self._emit(label)

    def handle_data(self, data):
        if self.skip:
            return
        # Source line breaks are just spaces; <br> makes the real ones. A typed URL
        # is wrapped in <> (no preview) and left unescaped so it stays a link.
        text = data.replace("\r", "").replace("\n", " ")
        for n, piece in enumerate(re.split(r"(https?://[^\s<>]+)", text)):
            self._emit(f"<{piece}>" if n % 2 and not self.link else escape(piece))

    def close(self):
        super().close()
        self._flush()


def _tidy_bold(md: str) -> str:
    """Bold in the server's format is bold-underline (**__text__**). Discord ignores
    "** text**", so spaces move outside the markers; empty pairs are dropped and an
    unmatched marker stays literal."""
    pieces = md.split("**")
    if len(pieces) % 2 == 0:
        pieces[-2:] = [pieces[-2] + "\\*\\*" + pieces[-1]]
    out = pieces[0]
    for inner, after in zip(pieces[1::2], pieces[2::2]):
        core = inner.strip()
        lead, trail = inner[: len(inner) - len(inner.lstrip())], inner[len(inner.rstrip()):]
        out += lead + (f"**__{core}__**" if core else "") + trail + after
    return out


def parse_post(page: str) -> dict:
    """Title, category, date and the article as ordered parts."""
    title = re.search(r'<h1 class="header_4__h1">(.*?)</h1>', page, re.S)
    category = re.search(r'class="header_4__category">\s*<a[^>]*>(.*?)</a>', page, re.S)
    date = re.search(r'class="header_4__date">\s*<time[^>]*>(.*?)</time>', page, re.S)
    start = page.find('<div class="article_2__content')
    end = page.find("</article>", start)
    if not title or start < 0 or end < 0:
        raise ValueError("not a news post page (layout changed?)")
    body = page[page.find(">", start) + 1:end]
    body = body[: body.rfind("</div>")]  # the content div's own close
    parser = _Article()
    parser.feed(body)
    parser.close()
    parts = [(kind, _tidy_bold(value) if kind == "text" else value) for kind, value in parser.parts]
    period = None
    at = body.find("Event Period")
    if at >= 0:
        times = re.findall(r"data-unixtime=['\"](\d+)['\"]", body[at:at + 1500])
        if len(times) >= 2:
            period = (int(times[0]), int(times[1]))
    return {
        "period": period,
        "title": _text(title[1]),
        "category": _text(category[1]) if category else "",
        "date": _text(date[1]) if date else "",
        "parts": parts,
    }


# Where a message may break, best first: before a section, before a subsection
# (a dropdown), between paragraphs, between lines; then, only as a last resort,
# after a sentence or between words.
_GOOD_BREAKS = (r"\n\n(?=## )", r"\n\n(?=### )", r"\n\n", r"\n")
_LAST_RESORT = (r"(?<=[.!?])\s", r"\s")


def _cut(text: str, at: int) -> tuple[str, str]:
    head, rest = text[:at].rstrip(), text[at:].lstrip("\n ")
    if head.count("**") % 2:  # don't leave bold open across messages
        head, rest = head + "__**", "**__" + rest
    return head, rest


def _plan(text: str, n: int, breaks: tuple) -> list[str] | None:
    """Exactly-n-or-fewer messages using only these kinds of break, each cut the best
    kind available nearest an even split; None if no such plan exists."""
    if len(text) <= LIMIT:
        return [text]
    if n <= 1:
        return None
    lo, ideal = max(1, len(text) - (n - 1) * LIMIT), len(text) / n
    for pattern in breaks:
        cuts = [m.start() for m in re.finditer(pattern, text[: LIMIT + 1]) if lo <= m.start() <= LIMIT]
        for at in sorted(cuts, key=lambda c: abs(c - ideal))[:6]:
            head, rest = _cut(text, at)
            later = _plan(rest, n - 1, breaks)
            if later is not None:
                return [head] + later
    return None


def _split(text: str) -> list[str]:
    """A post is one message when it fits. Otherwise it takes the fewest messages
    that can all break naturally (a section, then a dropdown, a paragraph, a line);
    one extra message beats cutting a sentence. Only a post with no natural break
    at all is cut after a sentence or between words."""
    least = -(-len(text) // LIMIT)
    for n in range(least, least + 3):
        plan = _plan(text, n, _GOOD_BREAKS)
        if plan:
            return [c for c in plan if c.strip()]
    plan = _plan(text, least + 3, _GOOD_BREAKS + _LAST_RESORT)
    if plan:
        return [c for c in plan if c.strip()]
    chunks = []
    while len(text) > LIMIT:
        head, text = _cut(text, LIMIT)
        chunks.append(head)
    return chunks + ([text] if text.strip() else [])


def _ping_role() -> str | None:
    role = os.environ.get("NEWS_PING_ROLE_ID", "").strip()
    return role if role.isdigit() else None


def to_messages(post_id: str, post: dict, event_link: str | None = None) -> list[dict]:
    """Webhook payloads in order. The post's text is one message (or the fewest that
    fit, see _split) with the event link last; the images ride as embeds on the first
    message. Links are wrapped in <> so only images and the event card show."""
    title = escape(post["title"]).replace("[", "\\[").replace("]", "\\]")
    role = _ping_role()
    header = f"# [{title}](<{POST.format(post_id)}>)" + (f"\n<@&{role}>" if role else "")
    text = header
    for kind, value in post["parts"]:
        if kind != "text":
            continue
        if text == header:
            text = f"{text}\n\n{value}"  # a blank line between the title (and ping) and the article
        else:
            # A heading runs straight into what it introduces; other blocks get a blank line.
            heading = text.rsplit("\n", 1)[-1].startswith("#")
            text = f"{text}\n{value}" if heading else f"{text}\n\n{value}"
    if event_link:
        text = f"{text}\n\n{event_link}"
    messages = [{"content": chunk} for chunk in _split(text)]
    if os.environ.get("NEWS_IMAGES", "").lower() != "off":
        images = [value for kind, value in post["parts"] if kind == "image"][:10]  # Discord's embed limit
        if images:
            messages[0]["embeds"] = [{"image": {"url": url}} for url in images]
    for n, m in enumerate(messages):
        # Only the post's first message pings, and only the configured role.
        m["allowed_mentions"] = {"parse": [], "roles": [role]} if role and n == 0 else {"parse": []}
    return messages


# --- Discord scheduled events (needs a bot: webhooks can't create events) -------------

API = "https://discord.com/api/v10"


def _bot(method: str, path: str, payload: dict | None = None):
    token = os.environ["DISCORD_BOT_TOKEN"]
    body = json.dumps(payload).encode() if payload is not None else None
    for attempt in range(5):
        req = urllib.request.Request(API + path, data=body, method=method, headers={
            "Authorization": f"Bot {token}", "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/jlau3/pleep-news, 1)"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 2)) + 0.5)
                continue
            raise RuntimeError(f"Discord bot {e.code}: {e.read()[:300]!r}") from None
    raise RuntimeError("Discord kept rate limiting the bot")


def event_name(post: dict) -> str:
    return post["title"][:100]  # the full title, "Event: New Moon Day #19", as the server names events


def _plain(md: str) -> str:
    return re.sub(r"\\(.)", r"\1", md.replace("**__", "").replace("__**", "")).replace("<", "").replace(">", "")


def event_payload(post_id: str, post: dict, now: float | None = None) -> dict | None:
    """The scheduled event for a post's Event Period, or None (no period, or already over)."""
    if not post.get("period"):
        return None
    now = time.time() if now is None else now
    # The game's times are local ("4:00 a.m." wherever you are); the site stores that
    # wall clock as UTC. A Discord event has one start, so read it in NEWS_EVENT_TZ.
    zone = ZoneInfo(os.environ.get("NEWS_EVENT_TZ", "").strip() or "America/New_York")
    local = lambda t: int(datetime.fromtimestamp(t, timezone.utc).replace(tzinfo=zone).timestamp())
    start, end = (local(t) for t in post["period"])
    if end <= now + 600:
        return None
    start = max(start, int(now) + 300)  # Discord needs a start in the future
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    intro = next((v for k, v in post["parts"] if k == "text" and not v.startswith("#")), "")
    return {"name": event_name(post), "privacy_level": 2, "entity_type": 3,  # 3: external
            "scheduled_start_time": iso(start), "scheduled_end_time": iso(end),
            "entity_metadata": {"location": POST.format(post_id)},
            # As the server writes them: the opening paragraph, then the post's link.
            "description": _plain(intro)[:900] + f"\n\nFor more information: {POST.format(post_id)}"}


def _cover(post: dict) -> str | None:
    url = next((v for k, v in post["parts"] if k == "image"), None)
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data, kind = resp.read(8_000_001), resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
    except (urllib.error.URLError, OSError):
        return None
    if len(data) > 8_000_000 or kind not in ("image/jpeg", "image/png", "image/gif"):
        return None
    return f"data:{kind};base64," + base64.b64encode(data).decode()


def ensure_event(post_id: str, post: dict) -> str | None:
    """Creates the post's scheduled event once (an existing one for the same post is
    reused, so retries and re-runs don't duplicate it) and returns its link."""
    guild = os.environ.get("DISCORD_GUILD_ID", "").strip()
    if not guild.isdigit() or not os.environ.get("DISCORD_BOT_TOKEN"):
        return None
    payload = event_payload(post_id, post)
    if not payload:
        return None
    location = payload["entity_metadata"]["location"]
    existing = next((e for e in _bot("GET", f"/guilds/{guild}/scheduled-events") or []
                     if (e.get("entity_metadata") or {}).get("location") == location), None)
    if existing:
        event_id = existing["id"]
    else:
        cover = _cover(post)
        event_id = _bot("POST", f"/guilds/{guild}/scheduled-events", {**payload, **({"image": cover} if cover else {})})["id"]
        print(f"created event: {payload['name']} ({event_id})")
    invite = os.environ.get("NEWS_EVENT_INVITE", "").strip().removeprefix("https://discord.gg/")
    return f"https://discord.gg/{invite}?event={event_id}" if invite else f"https://discord.com/events/{guild}/{event_id}"


# --- Discord ---------------------------------------------------------------------------

def send(webhook: str, payload: dict) -> None:
    body = json.dumps(payload).encode()
    for attempt in range(5):
        req = urllib.request.Request(webhook + ("&" if "?" in webhook else "?") + "wait=true", data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30):
                time.sleep(0.6)  # stay under the webhook rate limit
                return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 2)) + 0.5)
                continue
            raise RuntimeError(f"Discord {e.code}: {e.read()[:300]!r}") from None
    raise RuntimeError("Discord kept rate limiting")


def preview(post_id: str) -> None:
    post = parse_post(fetch(POST.format(post_id)))
    event = event_payload(post_id, post)
    messages = to_messages(post_id, post, "https://discord.gg/<invite>?event=<new event id>" if event else None)
    print(f"===== {post['title']} ({len(messages)} messages)")
    if event:
        print(f"[event] {event['name']}: {event['scheduled_start_time']} to {event['scheduled_end_time']} (created only with a bot configured)")
    for m in messages:
        print(m["content"])
        for e in m.get("embeds", []):
            print(f"[image] {e['image']['url']}")
        print("-----")


def post_one(post_id: str, webhook: str, start: int = 0, sent=lambda n: None) -> int:
    """Sends a post's messages from `start` on, calling sent(n) after each, so a run
    that stops partway resumes there instead of repeating the start. Returns the count."""
    post = parse_post(fetch(POST.format(post_id)))
    messages = to_messages(post_id, post, ensure_event(post_id, post))
    for n in range(start, len(messages)):
        send(webhook, messages[n])
        sent(n + 1)
    print(f"posted: {post['title']} ({len(messages) - start} messages)")
    return len(messages)


# posted.json: {"posted": [ids, oldest first], "partial": {id: messages sent},
# "failures": {id: failed runs}}. A post failing MAX_TRIES runs in a row is skipped.
MAX_TRIES = 3


def load_state() -> dict | None:
    if not STATE.exists():
        return None
    state = json.loads(STATE.read_text())
    return {"posted": state.get("posted", []), "partial": state.get("partial", {}), "failures": state.get("failures", {})}


def save_state(state: dict) -> None:
    state["posted"] = state["posted"][-KEEP:]
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def run(listed: list[dict], state: dict, webhook: str, save=save_state) -> int:
    """Sends every listed post not yet posted, oldest first; a burst of posts goes
    out in order, one post after another. Returns how many failed."""
    new = [p for p in reversed(listed) if p["id"] not in state["posted"]]
    failed = 0
    for p in new:
        pid = p["id"]
        def sent(n, pid=pid):
            state["partial"][pid] = n
            save(state)
        try:
            post_one(pid, webhook, state["partial"].get(pid, 0), sent)
        except Exception as e:  # the others still go out; this one is retried next run
            failed += 1
            tries = state["failures"][pid] = state["failures"].get(pid, 0) + 1
            if tries >= MAX_TRIES:
                print(f"skipped after {tries} failed runs: {p['title']}: {e}", file=sys.stderr)
                state["posted"].append(pid)
                state["partial"].pop(pid, None)
            else:
                print(f"failed ({tries}/{MAX_TRIES}), retrying next run: {p['title']}: {e}", file=sys.stderr)
            save(state)
            continue
        state["posted"].append(pid)
        state["partial"].pop(pid, None)
        state["failures"].pop(pid, None)
        save(state)
    print(f"{len(new) - failed} posted, {failed} failed" if new else "no new posts")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print instead of posting")
    ap.add_argument("--post", metavar="ID", help="post this one news post, ignoring posted.json")
    ap.add_argument("--latest", type=int, metavar="N", help="post the N newest posts, ignoring posted.json")
    ap.add_argument("--seed", action="store_true", help="mark every listed post as posted, send nothing")
    args = ap.parse_args()
    webhook = os.environ.get("NEWS_WEBHOOK_URL")
    if not webhook and not args.dry_run and not args.seed:
        print("NEWS_WEBHOOK_URL is not set (use --dry-run to preview)", file=sys.stderr)
        return 1
    if args.post:
        preview(args.post) if args.dry_run else post_one(args.post, webhook)
        return 0

    listed = []
    for n in range(1, PAGES + 1):
        listed += parse_listing(fetch(LIST.format(n)))
    if not listed:
        print("news listing had no posts (layout changed?)", file=sys.stderr)
        return 1
    if args.latest:
        for p in reversed(listed[: args.latest]):  # oldest of them first
            preview(p["id"]) if args.dry_run else post_one(p["id"], webhook)
        return 0
    state = load_state()
    if state is None or args.seed:
        state = state or {"posted": [], "partial": {}, "failures": {}}
        state["posted"] += [p["id"] for p in reversed(listed) if p["id"] not in state["posted"]]
        if not args.dry_run:
            save_state(state)
        print(f"recorded {len(state['posted'])} existing posts; new ones from now on get posted")
        return 0
    if args.dry_run:
        for p in reversed(listed):
            if p["id"] not in state["posted"]:
                preview(p["id"])
        return 0
    return 1 if run(listed, state, webhook) else 0


if __name__ == "__main__":
    sys.exit(main())
