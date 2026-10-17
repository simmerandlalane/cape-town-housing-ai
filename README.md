# cape-town-housing-ai

**An evidence-based neighbourhood finder for people moving to Cape Town, built on [SerpApi](https://serpapi.com).**

## Why I built this

I moved to Cape Town without knowing the city. Finding flats wasn't the hard part:
Property24 has thousands. The hard part was knowing **which areas to look in**, and above all one question:

> **Is this area safe for me?**

Then came everything else. Can I get to work without a car? Can I afford it? Is there a grocery store and a gym nearby?
Will I be walking alone from the station in the dark?

The answers were scattered across property sites, Google Maps, news articles, forums and friends-of-friends. Some of it
was outdated, some of it was hearsay, and none of it was organised around *my* situation. A search engine can't tell
you an area is "safe", and neither can an AI. Anyone who claims otherwise is guessing. What I needed was the real
evidence, gathered in one place, with sources I could check, and an honest note on what's still unknown.

So that's what this app does.

## What it does

You describe your situation, and the app researches each candidate suburb with live search data, then gives you a
ranked shortlist **before** you open Property24. Every claim links to its source, unknowns are shown as unknown, and
each area has a link to browse its actual rentals.

## Why it matters

Choosing where to live is one of the biggest decisions in a move. It's usually made in a hurry, in an unfamiliar
city, based on whatever a newcomer manages to piece together. A wrong choice is expensive and hard to undo: a
12-month lease in an area that doesn't fit your life.

**Who it helps**

- **People relocating alone**, especially women, who need safety evidence before anything else.
- **Students and young professionals** arriving for university or a first job, often without a car.
- **Anyone new to the city** who doesn't yet know that a suburb 6 km away can be a 10-minute drive or a 45-minute
  train-and-walk.
- *Later:* universities, employers and relocation services onboarding newcomers.

**What it saves, with real numbers from our own searches** (estimates, October 2026, commuting to Bellville):

| | Without the app | With the app |
|---|---|---|
| **Time spent commuting** | Observatory looks fine on a map: 18 km, or 28 min by car | By train it's **71 min each way** vs **43 min** from Parow. That's about **20 hours a month** difference (28 min × 2 × 22 workdays) |
| **Research effort** | Hours across Property24, Google Maps, news, forums and Facebook groups | One page per area: rent level, commute, amenities and safety evidence, with sources |
| **Scams and dead ends** | A spam site copying real listings ranked in Google's top 9 for our test searches. Some "rentals" were already let, and some posts were a year old | Unknown sites are filtered out, and rented, short-term and outdated results are flagged |
| **Safety** | Hearsay and gut feeling | Dated, linked evidence and an explicit list of what's still unknown, so you know what to check in person |

**Why it's different**

- **Honest by design.** It never claims an area is "safe", never invents data, and shows unknowns instead of hiding
  them. Most "best suburbs" lists and AI chatbots do the opposite.
- **Personal.** It ranks areas against *your* destination, transport, budget and priorities, not a generic top 10.
- **Free to run, private by default.** It runs on SerpApi's free tier (250 searches/month) and uses no AI service:
  nothing is sent to an AI company, and only the search queries themselves (e.g. suburb and destination names) go
  to SerpApi.
- **Built to grow.** The Cape Town-specific parts are a few settings: city name, Google location, suburb list and
  property-site allowlist. Johannesburg, Durban and Pretoria are mostly configuration, not a rewrite.

> **Status:** working web app and command line (research → scoring → safety evidence → explanations). Runs out of
> the box with bundled demo data, so no API key is needed to try it. See [Roadmap](#roadmap) for what's next.

## Example

`uv run scripts/rank_areas.py Parow --destination Bellville --max-rent 9000` (real output, October 2026, trimmed):

```
#1 Parow  score 0.82  (evidence for 100% of your priorities)
   • affordability 0.89     8 of 9 reported rents are within R9,000 (median R7,400).
   • commute       0.57     43 min by public transport to Bellville (Northern Line-35s Wellington Station).
       ? Timetable-based: actual train/bus reliability may differ.
   • amenities     1.00     Found: 12 grocery stores, 11 gyms, 12 parks, 7 pharmacies (Google Maps results, a lower bound).
   📰 1 safety-related news item(s) to read (not scored):
       - 03/31/2026 | News24: ‘He has killed me too’: Father mourns daughter stabbed to death in Parow
   🔗 Browse: https://www.property24.com/apartments-to-rent/parow/western-cape/436
```

## How it works

```
 your preferences ─┐
 (destination, car?, budget, max commute, quiet/lively, amenities, priorities)
                   ▼
 ┌──────────────── research (fixed plan, 10 searches per suburb, cached) ─────────────────┐
 │  google                 → rent levels from property-site snippets + "Browse" links    │
 │  google_maps_directions → peak-hour commute in YOUR travel mode (train lines, fares)  │
 │  google_maps            → grocery stores, gyms, parks, pharmacies (count + ratings)   │
 │  google_news            → recent local news, crime-related items flagged              │
 │  google (x2)            → what residents say; Neighbourhood Watch / CID / CPF groups  │
 │  google_maps            → nearest SAPS police stations, with address and phone        │
 └──────────────────────────────────────┬─────────────────────────────────────────────────┘
                                        ▼
        extract & verify: clean text, parse prices, spam/source checks, keep provenance
                                        ▼
        score each dimension 0–1, or UNKNOWN → weighted by your priorities → rank + explain
```

### How SerpApi is used

| SerpApi resource | Role in this project |
|---|---|
| [serpapi-python](https://github.com/serpapi/serpapi-python) | All research searches (`google`, `google_maps_directions`, `google_maps`, `google_news`) via a cached, budgeted client ([`search.py`](src/ct_housing/search.py)) |
| [serpapi-search-tools](https://serpapi.github.io/serpapi-search-tools-python/) | *Planned:* tools for an optional AI summary of each area's character. Explored during development with a local open-source model, not shipped yet (see [findings](docs/architecture.md#5-findings-from-real-data)) |
| [serpapi-mcp](https://github.com/serpapi/serpapi-mcp) | Development reference: its engine catalogue (served as `serpapi://engines/<name>`) is where we found engines and parameters such as `google_maps_directions` transit options. Not needed at runtime |

[docs/architecture.md](docs/architecture.md) explains why the work is split this way, and what we learned about each
resource.

## Design principles

- **Evidence, not vibes.** Every fact keeps its source URL, the SerpApi `search_id`, the query and the fetch time.
- **Unknown ≠ zero.** If there's no data for a dimension, it's marked *unknown* and left out of the average. The
  *coverage* figure tells you how much of your priorities the score actually covers.
- **Safety is never scored, but it comes first.** Each area opens with a safety brief: walking time on your commute,
  the nearest SAPS stations (with phone numbers), Neighbourhood Watches and City Improvement Districts found, and what
  residents say. Every source is labelled by who's speaking (residents, news, estate agent, or a third-party "safety
  score" site with an unknown method), and anything older than two years is flagged.
- **Untrusted web content.** Results come from an allowlist of known South African property sites. One of the first
  test searches returned a spam domain copying real listings, and it is rejected. Already-rented and short-term
  (Airbnb) pages are excluded too.
- **Weights are yours.** The default equal weights are an example, not a claim about what matters to everyone.

## Quick start

**Prerequisites:** Python 3.12+ and [uv](https://docs.astral.sh/uv/). Optional: a free
[SerpApi account](https://serpapi.com/users/sign_up) (250 searches/month, no card needed) for live research beyond
the demo. Your key stays in your local `.env`, which git ignores.

```bash
git clone https://github.com/simmerandlalane/cape-town-housing-ai.git
cd cape-town-housing-ai
uv sync
uv run streamlit run app.py     # web app at http://localhost:8501
```

**No API key needed to try it.** The app ships with demo data for **Parow, Durbanville and Observatory, commuting
to Bellville**, which are the default settings, so just press *Research areas*. To research other suburbs or
destinations with live data, add a free SerpApi key:

```bash
cp .env.example .env            # then paste your SERPAPI_API_KEY into .env
uv run pytest                   # 70+ offline tests
uv run python evals/run.py      # evaluation report → docs/evaluation.md
```

The web app shows how many **new** searches a run will cost before you press *Research areas*. Changing a priority
re-ranks instantly from stored research and costs no searches.

**Command line** (same engine):

```bash
uv run scripts/rank_areas.py Parow Durbanville Observatory --destination Bellville --max-rent 9000
```

| Option | Meaning |
|---|---|
| `--destination` | Where you commute to, e.g. `Bellville`, `Cape Town CBD`, `UCT` |
| `--max-rent` | Monthly rent budget in rand |
| `--max-commute` | Longest acceptable commute in minutes (default 60) |
| `--car` | You drive. Otherwise commutes use public transport |
| `--lifestyle` | `quiet`, `balanced` or `lively` |
| `--budget` | Maximum live SerpApi searches for this run (default 40) |

**Search cost:** up to 10 searches per suburb for the first run. Results are cached in `data/raw/`, so later runs with
the same suburbs cost nothing.

## Project structure

```
src/ct_housing/
  models.py     data models: Evidence, AreaProfile, scores (with provenance)
  search.py     SerpApi client with on-disk cache and per-run search budget
  research.py   the fixed research plan per suburb
  extract.py    parse SerpApi JSON: prices, commutes, amenities, news
  sources.py    known-site allowlist and result classification
  filters.py    dedupe + budget constraint
  scoring.py    explainable per-dimension scoring and ranking
app.py                  Streamlit web app
scripts/rank_areas.py   command-line entry point
tests/                  70+ offline unit tests built from real search results
evals/                  evaluation: hand-labelled real results, ranking invariants, honesty guardrails
docs/architecture.md    design decisions and findings
```

## Evaluation

[`evals/`](evals/) measures accuracy on **real search results** against hand labels, plus ranking and honesty checks.
It runs offline and in CI. Full report with every failure: [docs/evaluation.md](docs/evaluation.md).

| Check | Score |
|---|---|
| Source classification (spam, already rented, short-term, results page…) | 27 / 27 |
| Rent extraction, precision and recall | 22 / 22, 22 / 22 |
| Resident views: about this suburb? who is speaking? | 26 / 26, 19 / 19 |
| Patrol groups (Neighbourhood Watch / CID / CPF) | 19 / 19 |
| Police stations (SAPS only, own station first) | 11 / 11 |
| Ranking invariants over 400 random scenarios | 1,106 / 1,106 |
| Honesty guardrails (sources on every safety item, no "this area is safe" claims) | 279 / 279 |

The set is small and the labels were written after seeing the code's output, so treat these as "no mistakes found
on this set", not proof of perfection. The randomised ranking checks found a real bug: two areas both over the
commute limit tied at 0 and kept arbitrary order. Ties are now broken by the actual commute and rent.

## Security and privacy

- API keys live only in `.env` (gitignored, `chmod 600`). `.env.example` contains no secrets.
- `serpapi.Client` writes `api_key` into the parameter dict it's given. We pass a copy so the key is never saved
  in the cache. A regression test ([`tests/test_search.py`](tests/test_search.py)) enforces this.
- Raw search responses (`data/raw/`) are gitignored, so they're never published.
- CI runs lint (including [bandit](https://docs.astral.sh/ruff/rules/#flake8-bandit-s) security rules), tests, and a
  [gitleaks](https://github.com/gitleaks/gitleaks) secret scan of the full git history.
- No personal data is stored. Preferences exist only for the run.

## Limitations

- **Search results are not a listings database.** Rents come from snippets on results pages, so treat them as an
  indicator, not a market survey. Use the Browse links for real, current listings.
- **Commutes are Google's timetable or traffic estimates.** Train reliability in practice may differ, and minibus
  taxis aren't in Google's data. A missing transit route is shown as unknown, not as "no transport".
- **Amenity counts are a lower bound.** One Google Maps page holds up to about 20 places, and only addresses naming
  the suburb are counted.
- **Suburbs aren't uniform.** Conditions vary street by street, so the app recommends areas to *explore*, not to
  sign a lease in.

## Roadmap

- [x] Research pipeline, provenance, spam filtering, explainable scoring (CLI)
- [ ] Local-AI summaries of each area's character (quiet ↔ lively), grounded only in fetched sources
- [x] Streamlit app: questionnaire → ranked areas → comparison → Browse on Property24
- [ ] Official SAPS crime statistics (published per police precinct, which needs mapping to suburbs)
- [ ] Other South African cities (Johannesburg, Durban, Pretoria)

## License and credits

Code: [MIT](LICENSE).

Hero photo: [Table Mountain from Bloubergstrand](https://commons.wikimedia.org/wiki/File:Table_Mountain_DanieVDM.jpg)
by Danie van der Merwe, licensed [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/) (resized and compressed).
Map tiles © [CARTO](https://carto.com/attributions), © OpenStreetMap contributors. Search data via [SerpApi](https://serpapi.com).
