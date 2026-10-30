# pleep-news

Copies every new [official Pokémon Sleep news post](https://www.pokemonsleep.net/en/news/) into a Discord channel, word for word.

Each post becomes a run of messages in page order:

- the title, category, date and a link to the post
- the article as Discord markdown: headings, bold, links, line breaks and bullets as written
- every dropdown (the tap-to-open "Notes", "Expert Mode Bonuses" and so on), opened up as a quote block under its bold headline
- each image, where it appears in the post

Text past Discord's 2,000-character limit continues in the next message, broken at the most natural point that fits: before a new section, before a dropdown, between paragraphs, between lines, then after a sentence. A heading never ends a message apart from its text. Nobody gets pinged, and links don't unfurl previews.

Each post is sent once. When several come out together, they go out oldest first, one whole post after another. If Discord fails partway through a post, the next run picks up at the message it stopped on instead of repeating the start; a post that fails three runs in a row is skipped so it can't hold up the rest.

## Setup

1. In the Discord channel: **Edit Channel → Integrations → Webhooks → New Webhook**, then **Copy Webhook URL**.
2. In this repo: **Settings → Secrets and variables → Actions → New repository secret**, named `NEWS_WEBHOOK_URL`, with that URL.
3. **Actions → News to Discord → Run workflow** to test:
   - `latest`: post the N newest posts
   - `post_id`: post one post (the long number in its URL)
   - `dry_run`: only print to the run log

   Test runs don't change what counts as already posted.

After that it runs every hour. The first scheduled run only records the posts already on the site, so the channel isn't flooded; from then on each new post is sent once. `posted.json` (committed by the workflow) remembers what was sent.

## Locally

```sh
python3 news_to_discord.py --dry-run --latest 2   # preview the two newest posts
python3 -m unittest discover -s tests
```

No dependencies beyond Python 3.10+.
