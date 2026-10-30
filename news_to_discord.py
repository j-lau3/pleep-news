#!/usr/bin/env python3
"""
Official Pokémon Sleep news posts, copied word for word into a Discord channel.

Each new post on https://www.pokemonsleep.net/en/news/ becomes a run of webhook
messages: the title and link, then the article's text as Discord markdown
(headings, bold, links, the text inside every dropdown) and its images, in the
order they appear. posted.json remembers what was already sent.

    python3 news_to_discord.py                 # post new posts (NEWS_WEBHOOK_URL)
    python3 news_to_discord.py --dry-run       # print what would be sent
    python3 news_to_discord.py --post ID       # (re)post one post, ignoring posted.json
    python3 news_to_discord.py --latest 3      # post the 3 newest posts (testing), ignoring posted.json
    python3 news_to_discord.py --seed          # mark everything listed as posted

With no posted.json yet, a normal run seeds instead of flooding the channel
with every post on the site.
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
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
        self.group = 0            # which dropdown the text belongs to
        self.last_group: int | None = None
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
        if prefix:
            lines[0] = prefix + lines[0]
        md = "\n".join(lines)
        if self.dropdown:
            md = "\n".join("> " + l if l else ">" for l in md.split("\n"))
            # One quote per dropdown: its headline and text stay together.
            if self.parts and self.parts[-1][0] == "text" and self.last_group == self.group:
                self.parts[-1] = ("text", self.parts[-1][1] + "\n" + md)
                return
            self.last_group = self.group
        else:
            self.last_group = None
        self.parts.append(("text", md))

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
            self.group += 1
            return
        if tag == "div" and "news-detail-headline" in cls.split():
            self._flush()
            self.in_headline = True
            self._emit("**")
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
            self._emit("**")
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
        # Source line breaks are just spaces; <br> makes the real ones.
        self._emit(escape(data.replace("\r", "").replace("\n", " ")))

    def close(self):
        super().close()
        self._flush()


def _tidy_bold(md: str) -> str:
    """Discord ignores "** text**": move spaces inside bold markers outside them,
    drop empty pairs, and keep an unmatched marker literal."""
    pieces = md.split("**")
    if len(pieces) % 2 == 0:
        pieces[-2:] = [pieces[-2] + "\\*\\*" + pieces[-1]]
    out = pieces[0]
    for inner, after in zip(pieces[1::2], pieces[2::2]):
        core = inner.strip()
        lead, trail = inner[: len(inner) - len(inner.lstrip())], inner[len(inner.rstrip()):]
        out += lead + (f"**{core}**" if core else "") + trail + after
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
    return {
        "title": _text(title[1]),
        "category": _text(category[1]) if category else "",
        "date": _text(date[1]) if date else "",
        "parts": parts,
    }


# Where a message may end, best first: before a new section, before a dropdown,
# between paragraphs, between lines, after a sentence, between words.
_BREAKS = (r"\n\n(?=#{1,3} )", r"\n\n(?=> \*\*)", r"\n\n", r"\n", r"(?<=[.!?])\s", r"\s")


def _cut_point(text: str) -> int:
    """End of the next message: the best kind of break in the back half of what
    fits, so messages break naturally without coming out tiny."""
    for pattern in _BREAKS:
        cuts = [m.start() for m in re.finditer(pattern, text[: LIMIT + 1]) if LIMIT // 2 <= m.start() <= LIMIT]
        if cuts:
            cut = cuts[-1]
            # A heading or bold subheading at the end moves with the text it introduces.
            last = text.rfind("\n\n", 0, cut)
            if last >= LIMIT // 2 and re.fullmatch(r"#.*|\*\*[^\n]*\*\*", text[last + 2:cut].strip()):
                cut = last
            return cut
    return LIMIT


def _split(text: str) -> list[str]:
    """Messages of at most LIMIT characters, broken at natural points (_BREAKS)."""
    chunks = []
    while len(text) > LIMIT:
        cut = _cut_point(text)
        head, text = text[:cut].rstrip(), text[cut:].lstrip("\n ")
        if head.count("**") % 2:  # don't leave bold open across messages
            head += "**"
            text = "**" + text
        if head.split("\n")[-1].startswith(">") and text and not text.startswith(">"):
            text = "> " + text  # a dropdown split mid-line stays quoted
        chunks.append(head)
    if text.strip():
        chunks.append(text)
    return chunks


def to_messages(post_id: str, post: dict) -> list[dict]:
    """Webhook payloads in order: text messages (links not previewed) and one per image."""
    header = f"# {escape(post['title'])}\n-# {' · '.join(x for x in (escape(post['category']), escape(post['date'])) if x)} · <{POST.format(post_id)}>"
    messages, text = [], header
    def flush_text():
        nonlocal text
        for chunk in _split(text):
            messages.append({"content": chunk, "flags": 4})  # 4: no link previews
        text = ""
    for kind, value in post["parts"]:
        if kind == "image":
            flush_text()
            messages.append({"embeds": [{"image": {"url": value}}]})
        else:
            text = f"{text}\n\n{value}" if text else value
    flush_text()
    for m in messages:
        m["allowed_mentions"] = {"parse": []}
    return messages


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
    messages = to_messages(post_id, post)
    print(f"===== {post['title']} ({len(messages)} messages)")
    for m in messages:
        print(m.get("content") or f"[image] {m['embeds'][0]['image']['url']}")
        print("-----")


def post_one(post_id: str, webhook: str, start: int = 0, sent=lambda n: None) -> int:
    """Sends a post's messages from `start` on, calling sent(n) after each, so a run
    that stops partway resumes there instead of repeating the start. Returns the count."""
    post = parse_post(fetch(POST.format(post_id)))
    messages = to_messages(post_id, post)
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
