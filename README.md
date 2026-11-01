# pleep-news

Copies every new [official Pokémon Sleep news post](https://www.pokemonsleep.net/en/news/) into a Discord channel, word for word.

Each post becomes one message in the server's format:

```
# [Event: New Moon Day #19](<https://www.pokemonsleep.net/en/news/...>)
<@&role>

## Outline
Delightful encounters take place on the night of a new moon!
...
- Nov. 8 (Sun) at 4:00 a.m. to Nov. 11 (Wed) at 3:59 a.m.

## Details
### About Special Appearance Rates
...
```

- the title links to the post; the optional role ping goes underneath
- the article word for word: headings, **__bold-underline__**, links and `-` lists
- every dropdown (the tap-to-open "Notes", "Expert Mode Bonuses" and so on) as its own `###` section
- no images unless `NEWS_IMAGES=on`

A post that fits Discord's 2,000 characters is one message. A longer one takes the fewest messages that can all break naturally (two for most events): before a section, then before a dropdown section, between paragraphs or between lines, each cut near an even split. One extra message beats cutting a sentence; only text with no natural break at all is cut after a sentence or between words. Only the first message pings, only the configured role, and links don't unfurl previews.

Each post is sent once. When several come out together, they go out oldest first, one whole post after another. If Discord fails partway through a post, the next run picks up at the message it stopped on instead of repeating the start; a post that fails three runs in a row is skipped so it can't hold up the rest.

## Setup

1. In the Discord channel: **Edit Channel → Integrations → Webhooks → New Webhook**, then **Copy Webhook URL**.
2. In this repo: **Settings → Secrets and variables → Actions → New repository secret**, named `NEWS_WEBHOOK_URL`, with that URL.
   Optional, on the **Variables** tab: `NEWS_PING_ROLE_ID` (the role to ping: Server Settings → Roles → right-click the role → Copy Role ID, with Developer Mode on) and `NEWS_IMAGES` = `on` to also send images. Set the role only for the server it belongs to; elsewhere it shows as an unknown role and pings no one.
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
