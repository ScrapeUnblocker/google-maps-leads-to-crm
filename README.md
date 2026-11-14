# Google Maps leads to CRM

For agencies and B2B sales teams who sell to local businesses: every week, new businesses from Google Maps that match your searches land in HubSpot, Pipedrive or Airtable, deduped against your CRM and earlier runs, with their website found and a Slack or Discord digest of who is new.

[![CI](https://github.com/ScrapeUnblocker/google-maps-leads-to-crm/actions/workflows/ci.yml/badge.svg)](https://github.com/ScrapeUnblocker/google-maps-leads-to-crm/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Powered by ScrapeUnblocker](https://img.shields.io/badge/powered%20by-ScrapeUnblocker-6c47ff.svg)](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)

You list the kinds of business you sell to ("roofing contractors", "dentists") and the towns you cover. It runs on a GitHub Actions schedule in your own copy of this repo, so there's nothing to host. Businesses come from the [ScrapeUnblocker](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos) Google Local API, one request per search. Duplicates (the same business in two towns' searches, or as an ad and a normal result) are folded together, and your filters on rating, reviews, category and phone are applied. Businesses already delivered, or already in your CRM, are skipped. Each new lead's own website is looked up with one Google search. A small state file remembers what was sent, so nobody lands in your CRM twice.

## What actually arrives

These are real businesses from a test run on 14 Nov 2026. It used two of the default searches, filtered to 300-400 reviews to keep the test small, and delivered to stand-ins for HubSpot, Pipedrive, Airtable, Slack, Discord and the webhook. The Slack digest, one message per run:

```text
*6 new leads from Google Maps* · roofing contractors in Austin, TX and 1 more search
Added to HubSpot (6), Pipedrive (6) and Airtable (6)

*Dynamite Roofing &amp; Construction* · Roofing contractor · 4.9★ (362 reviews)
(512) 505-8008 · <https://dynamiteroof.com/|dynamiteroof.com> · Round Rock, TX · <https://www.google.com/maps/search/?api=1&query=Dynamite+Roofing+%26+Construction+Round+Rock%2C+TX|Maps>
25+ years in business

*Lapeyre Roofing* · Roofing contractor · 5.0★ (349 reviews)
(512) 877-3087 · <https://lapeyreroofing.com/|lapeyreroofing.com> · Austin, TX · <https://www.google.com/maps/search/?api=1&query=Lapeyre+Roofing+Austin%2C+TX|Maps>
3+ years in business · open 24 hours

*HD Roofing and Repairs* · Roofing contractor · 4.9★ (347 reviews)
(512) 458-6800 · <https://hdroofingandrepairs.com/|hdroofingandrepairs.com> · Austin, TX · <https://www.google.com/maps/search/?api=1&query=HD+Roofing+and+Repairs+Austin%2C+TX|Maps>
25+ years in business · open 24 hours

*Ace Roofing Company* · Roofing contractor · 4.8★ (344 reviews)
(512) 836-7663 · <https://aceroofingcompanytown.weebly.com/|aceroofingcompanytown.weebly.com> · Austin, TX · <https://www.google.com/maps/search/?api=1&query=Ace+Roofing+Company+Austin%2C+TX|Maps>
20+ years in business · open 24 hours

*Clear Choice Roofing* · Roofing contractor · 4.9★ (326 reviews)
(512) 640-2643 · <https://www.clearchoiceroofingatx.com/|clearchoiceroofingatx.com> · Austin, TX · <https://www.google.com/maps/search/?api=1&query=Clear+Choice+Roofing+Austin%2C+TX|Maps>
75+ years in business · open 24 hours

*Falcon Roofing* · Roofing contractor · 5.0★ (313 reviews)
(512) 200-1539 · <https://falconroofingtexas.com/|falconroofingtexas.com> · Austin, TX · <https://www.google.com/maps/search/?api=1&query=Falcon+Roofing+Austin%2C+TX|Maps>
25+ years in business
```

In Slack the names are bold, and the domain and **Maps** are links. Discord gets the same digest in Markdown, split over a few posts when it is long. Signals a salesperson can open with get their own line: `runs Google Maps ads`, `N+ years in business`, `open 24 hours`, and `no website found` when the lookup found no site the business owns. A lead that is already in one of your CRMs says `already in HubSpot`. A lead that is in every CRM already is left out of the digest and only counted.

Each lead becomes one record in each CRM. This is the HubSpot company from the same run (`lifecyclestage` is `lead`):

```json
{
  "name": "Lapeyre Roofing",
  "phone": "(512) 877-3087",
  "domain": "lapeyreroofing.com",
  "website": "https://lapeyreroofing.com/",
  "city": "Austin",
  "state": "TX",
  "description": "Roofing contractor · 5.0★ (349 reviews)\nFound on Google Maps for \"roofing contractors in Austin, TX\" on 2026-11-14\n3+ years in business, Open 24 hours\nGoogle Maps: https://www.google.com/maps/search/?api=1&query=Lapeyre+Roofing+Austin%2C+TX",
  "lifecyclestage": "lead"
}
```

In Pipedrive each lead becomes an organization (name, address, website), with a note holding the phone, rating, reviews and signals, and a lead in the Leads Inbox. In Airtable each lead becomes a row:

```json
{
  "Name": "Dynamite Roofing & Construction",
  "Category": "Roofing contractor",
  "Rating": 4.9,
  "Reviews": 362,
  "Phone": "(512) 505-8008",
  "Website": "https://dynamiteroof.com/",
  "Address": "Round Rock, TX",
  "Signals": "25+ years in business",
  "Search": "roofing contractors in Round Rock, TX",
  "Google Maps": "https://www.google.com/maps/search/?api=1&query=Dynamite+Roofing+%26+Construction+Round+Rock%2C+TX",
  "First seen": "2026-11-14",
  "Lead ID": "tel:5125058008"
}
```

The webhook gets one POST per run with the digest as `text` and every lead as JSON (`key`, `name`, `category`, `rating`, `reviews`, `phone`, `website`, `street`, `locality`, `years_in_business`, `sponsored`, `signals`, `search`, `maps_url`, `first_seen` and what each CRM did with it), ready for Zapier, Make, n8n or Clay.

The run log of that test, first run then the next one:

```text
Searching Google Maps: 2 searches, 1 API request each
  roofing contractors in Austin, TX               23 businesses
  roofing contractors in Round Rock, TX           23 businesses
44 businesses found, 6 match your filters, 6 new
Filtered out: 28 fewer than 300 reviews, 9 more than 400 reviews, 1 no phone
Looking up websites: 6 Google searches, 1 API request each
  Dynamite Roofing & Construction                 dynamiteroof.com
  Lapeyre Roofing                                 lapeyreroofing.com
  HD Roofing and Repairs                          hdroofingandrepairs.com
  Ace Roofing Company                             aceroofingcompanytown.weebly.com
  Clear Choice Roofing                            clearchoiceroofingatx.com
  Falcon Roofing                                  falconroofingtexas.com
API requests used: 8

HubSpot: 6 created, 0 already there; Pipedrive: 6 created, 0 already there; Airtable: 6 created, 0 already there
Digest of 6 leads sent to slack, discord, webhook.
Saved leads.json.
```

```text
Searching Google Maps: 2 searches, 1 API request each
  roofing contractors in Austin, TX               23 businesses
  roofing contractors in Round Rock, TX           23 businesses
42 businesses found, 6 match your filters, 0 new
Filtered out: 26 fewer than 300 reviews, 9 more than 400 reviews, 1 no phone
API requests used: 2

Nothing new to deliver.
```

## Setup in three steps

1. **Use this template.** Click **Use this template** > **Create a new repository**. Make it private: your leads list is committed to it, and GitHub pauses scheduled workflows in public repositories after 60 days without activity.
2. **Add secrets.** In your new repo go to **Settings > Secrets and variables > Actions** and add:
   - `SCRAPEUNBLOCKER_KEY`: your API key ([get one here](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)).
   - At least one destination:
     - **HubSpot**: `HUBSPOT_TOKEN`, a service key. In HubSpot go to **Development > Keys > Service keys > Create service key** and add the scopes `crm.objects.companies.read` and `crm.objects.companies.write`. An existing private app token with those scopes works too.
     - **Pipedrive**: `PIPEDRIVE_API_TOKEN`, from **Personal preferences > API** in Pipedrive.
     - **Airtable**: `AIRTABLE_TOKEN`, a [personal access token](https://airtable.com/create/tokens) with `data.records:read`, `data.records:write` and `schema.bases:read` on your base. Also set `AIRTABLE_BASE_ID` (the `app...` part of your base URL), or put it in `config.yml`. Create a table named `Leads` with the columns you want from the list under [Configuration](#configuration). Columns you leave out are simply not written, but keep `Lead ID` so a lead is never added twice.
     - **Slack**: `SLACK_WEBHOOK_URL`, a [Slack incoming webhook](https://api.slack.com/messaging/webhooks) for your sales channel.
     - **Discord**: `DISCORD_WEBHOOK_URL`, from channel settings > Integrations > Webhooks.
     - **Webhook**: `LEADS_WEBHOOK_URL`, any URL that takes a JSON POST (Zapier, Make, n8n, Clay, your own endpoint).
3. **Edit `config.yml`.** Put in the kinds of business you sell to and the towns, neighborhoods or zip codes you cover, then set the filters.

Then open **Actions > Google Maps leads > Run workflow** to run it now instead of waiting for Monday. Tick **Dry run** to see in the log exactly what would be written and posted, without sending or saving anything. The first run delivers up to 25 leads (`max_new_per_run`). Run the workflow again for the next batch, or let the weekly schedule pick them up. After that, only businesses you haven't seen arrive.

Until `SCRAPEUNBLOCKER_KEY` is set, the scheduled workflow prints a notice and exits green without searching anything.

**Try a search first.** This runs one search and shows which businesses pass your filters, without looking up websites or saving anything:

```text
$ google-maps-leads preview "roofing contractors in Cedar Park, TX"
Google Maps: "roofing contractors in Cedar Park, TX" (US)

  Cedar Park Roofing Pros                   5.0★ (85 reviews)       (512) 481-7681    NEW
  Windham Roofing                           4.9★ (81 reviews)       (512) 446-3979    NEW
  Atlas Roofing Company Cedar Park          4.6★ (21 reviews)       (512) 566-5510    NEW
  ...
  Raven Roofing                             5.0★ (3 reviews)        (512) 777-4285    fewer than 10 reviews
  HD Roofing and Repairs                    4.9★ (347 reviews)      (512) 458-6800    NEW
  ...
  4 Roofs (ad)                              4.8★ (51 reviews)       (512) 520-5884    NEW

23 businesses, 22 match your filters, 22 new. API requests used: 1. Nothing was saved.
Add it to config.yml: searches.what: 'roofing contractors', searches.where: 'Cedar Park, TX'
```

Each business shows `NEW`, `already delivered`, or the reason it was filtered out (`fewer than 10 reviews`, `no phone`, ...). `(ad)` marks a business running Google Maps ads.

### Run it on your own machine instead

```bash
git clone https://github.com/<you>/<your-copy>.git && cd <your-copy>
python -m pip install .
cp .env.example .env                      # then fill in the key and a destination
google-maps-leads test-destinations       # checks each destination, no ScrapeUnblocker requests
google-maps-leads run --dry-run           # searches, prints what would be sent, saves nothing
google-maps-leads run
```

For a cron job every Monday at 7:00:

```cron
0 7 * * 1  cd /path/to/your-copy && google-maps-leads run >> leads.log 2>&1
```

Or with Docker, no local Python needed:

```bash
docker run --rm -v "$PWD":/app -w /app --env-file .env python:3.13-slim \
  sh -c "pip install -q . && google-maps-leads run"
```

`google-maps-leads status` shows how many leads were delivered and what each CRM did with them, with no API calls.

## How it works

1. **Collect.** Every `what` is searched in every `where` through the ScrapeUnblocker Google Local API (`POST /maps/google-local`, from the country you set). A search returns up to about 20 businesses with name, category, rating, review count, phone, address and hours, plus whether the listing is an ad.
2. **Fold duplicates.** One business can turn up in two towns' searches, or as an ad and as a normal result with a different, call-tracking phone number. Results sharing a phone are merged. So are results with the same name in the same searched place, and an ad with the name of a business found anywhere. Two normal results with one name in different towns stay two businesses.
3. **Filter.** The `filters` from `config.yml`: minimum rating, minimum and maximum review count, phone required, categories to keep or drop, names to drop, and ads.
4. **Skip what you have.** Leads already in `state/leads.json` (by any of their phones or names) are skipped. Then each CRM is asked: HubSpot for a company with the same phone, website domain or name, Pipedrive for an organization with exactly the same name, and Airtable for a row with the same `Lead ID`. A business that is already there is not added again, and it is remembered so it isn't checked again.
5. **Rank and cap.** Most reviews first. At most `max_new_per_run` leads per run; the rest wait for the next run.
6. **Find the website.** Google Maps search results rarely show a website, so each new lead gets one Google search through serpApi (`POST /serpApi`) for its name and town. A result counts only when it is not a directory or social site, its domain carries the business's brand word or spells its name, and the result shows the business's phone, street or town. `clearchoiceroofingwtx.com` (a West Texas firm of the same name) was rejected that way for the Austin business, which got `clearchoiceroofingatx.com`. A wrong website is worse than none, so when nothing passes, the lead gets `no website found`.
7. **Deliver.** CRMs first, one record per lead, then one digest per run to Slack, Discord and the webhook.
8. **Remember.** Delivered leads go into `state/leads.json`, which the workflow commits back to your repo. The file only changes when a lead was delivered, so a quiet week makes no commit. A lead no CRM accepted is not saved, so it is tried again next run.

## Configuration

Everything lives in [`config.yml`](config.yml). Secrets never go in it.

| Key | Default | What it does |
|---|---|---|
| `searches.what` | `[roofing contractors]` | Kinds of business, as you would type them into Google Maps. |
| `searches.where` | Austin, Round Rock, Cedar Park (TX) | Towns, neighborhoods or zip codes. Every `what` is searched in every `where`. Leave it empty to search `what` as written. |
| `searches.country` | `US` | Where Google searches from (2-letter code). Local results follow it. |
| `searches.language` | `en` | Google's language for names and categories. |
| `searches.per_run` | `0` | `0` runs every search each time. With a long list, `10` runs the next 10 each run and the cursor in the state file moves on, so a big grid is covered over several runs. |
| `filters.min_rating` | `4.0` | Lowest Google rating, 0-5. |
| `filters.min_reviews` | `10` | Fewest reviews. |
| `filters.max_reviews` | `0` | Most reviews, `0` for no limit. Use it to skip the biggest, chain-like players. |
| `filters.require_phone` | `true` | Drop businesses Google shows no phone for. |
| `filters.categories` | `[]` | Keep only these Google categories (any part, any case). Empty keeps all. |
| `filters.exclude_categories` | `[]` | Drop these categories. |
| `filters.exclude_names` | `[]` | Drop names containing any of these, e.g. a franchise you don't sell to. |
| `filters.sponsored` | `include` | `include`, `exclude` or `only`. `only` keeps businesses running Google Maps ads: they already pay for leads. |
| `enrich.website` | `true` | Look up each new lead's website (1 API request per new lead). |
| `delivery.destinations` | all six | Which destinations to use when their secrets are set: `hubspot`, `pipedrive`, `airtable`, `slack`, `discord`, `webhook`. |
| `delivery.max_new_per_run` | `25` | Most leads delivered per run. |
| `delivery.hubspot.properties` | name, phone, domain, website, address, city, state, description | `<HubSpot property>: <lead field>`. Add custom properties (`google_rating: rating`), or set one to `""` to leave it out. |
| `delivery.hubspot.lifecycle_stage` | `lead` | Set on new companies. `""` leaves it out. |
| `delivery.pipedrive.note` | `true` | Add a note with the phone, rating, reviews and signals. |
| `delivery.pipedrive.create_lead` | `true` | Also put the organization in the Leads Inbox. |
| `delivery.pipedrive.custom_fields` | `{}` | `<custom field key>: <lead field>`; the key is the 40-character hash Pipedrive shows for the field. |
| `delivery.airtable.base` | `""` | Your base id (`app...`). `AIRTABLE_BASE_ID` overrides it. |
| `delivery.airtable.table` | `Leads` | Table name or id. |
| `delivery.airtable.merge_on` | `Lead ID` | A lead whose id is already in this column is not added again. |
| `delivery.airtable.fields` | 12 columns | `<column>: <lead field>`: `Name`, `Category`, `Rating`, `Reviews`, `Phone`, `Website`, `Address`, `Signals`, `Search`, `Google Maps`, `First seen`, `Lead ID`. Columns your table doesn't have are skipped. |
| `state` | `state/leads.json` | The delivered-leads file. |

Lead fields for the CRM maps: `name`, `category`, `rating`, `reviews`, `phone`, `website`, `domain`, `address`, `street`, `city`, `region`, `years_in_business`, `signals`, `sponsored`, `search`, `maps_url`, `summary` (the description text above), `key` (the lead id) and `first_seen`.

Google shows a street for some businesses and only the town (or the years in business) for others. So `address`, `street`, `city` and `region` are filled when Google shows them. They are never guessed from the searched place.

## Cost

With the default `config.yml`, each run makes **3 Google Local requests** (1 kind of business × 3 towns) plus **1 serpApi request per new lead** for its website, at most 25 (`max_new_per_run`). So the first run uses at most 28 requests, and later weekly runs use 3 plus one per business that is new to you. A run that finds nothing new uses 3 requests. In the test above, the first run used 8 requests and the next one used 2.

`preview` uses 1 request, and `status` and `test-destinations` use none. Setting `enrich.website: false` drops the per-lead request. A dry run makes the same requests as a real run, because it shows exactly what would be delivered.

What a request costs depends on your plan, see [ScrapeUnblocker](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos).

## Adding another destination

Destinations live in [`src/google_maps_leads/sinks.py`](src/google_maps_leads/sinks.py) behind two small interfaces. A **CRM** has `find(lead)` (the id of a record that is already there, or `None`), `create(lead)` (returns the new record's id), `preview(lead)` (the body `create` would send, for `--dry-run`) and `test()`. A **notify** destination has `send(digest)`, `preview(digest)` and `test()`. For example, a Microsoft Teams channel via a workflow webhook:

```python
class TeamsSink(ChatSink):
    """Microsoft Teams channel via an incoming webhook workflow."""

    name = "teams"
    style = PLAIN
    limit = 20000

    def wrap(self, text: str) -> dict[str, Any]:
        return {"text": text}


REGISTRY["teams"] = SinkSpec(
    "notify",
    ("TEAMS_WEBHOOK_URL",),
    lambda env, http, d: TeamsSink(env["TEAMS_WEBHOOK_URL"].strip(), http),
)
```

Add `"teams"` to `NOTIFY` in `config.py` and to `delivery.destinations`, add the secret to `run.yml`, and it's done. A new CRM follows `HubSpotSink`: search before you create, and return the record id.

## Development

```bash
python -m pip install -e ".[dev]"
make lint      # ruff check + ruff format --check
make test      # offline tests: the SDK, the CRMs and the webhooks are faked
pre-commit install
```

The tests never call the ScrapeUnblocker API or a real CRM. They cover the duplicate folding, the filters, the website matching (with real search results), delivery to each destination, the digest format, the state file and a first run followed by a second run that sends nothing.

`HUBSPOT_API_URL`, `PIPEDRIVE_API_URL` and `AIRTABLE_API_URL` point a destination at another base URL (a proxy, or a local test server).

## Links

- Want the raw data instead? [google-maps-scraper](https://github.com/ScrapeUnblocker/google-maps-scraper) exports Google Maps businesses to JSON or CSV.
- [ScrapeUnblocker](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)
- [API docs](https://docs.scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)
- [Python SDK](https://pypi.org/project/scrapeunblocker/)

## License

[MIT](LICENSE). Copyright (c) 2026 ScrapeUnblocker.
