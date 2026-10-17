# Architecture and design decisions

This document explains *why* the project is built the way it is, including what we found by reading the source of the
three SerpApi resources and by inspecting real search results.

## 1. The three SerpApi resources

We read each repository's source before choosing roles for them.

| | serpapi-python (v1.1.2) | serpapi-mcp (v2.0.0) | serpapi-search-tools (v1.0.0) |
|---|---|---|---|
| What it is | Thin HTTP client (~450 lines, built on `requests`) | MCP server built on FastMCP | Library that builds tool objects for agent SDKs |
| Interface | `Client.search()`, `.account()`, `.locations()`, `.search_archive()` | One `search(params, mode)` tool for **all ~118 engines**, plus `serpapi://engines/<name>` schema resources | Nine fixed-purpose tools (`web_search`, `news_search`, `maps_search`, …) |
| Output | Dict-like `SerpResults`, or Markdown/HTML text | JSON or Markdown string in an MCP envelope | Compact Markdown by default (token-efficient), `result_limit=10` |
| Key handling | Passed explicitly; **not** read from the environment | `SERPAPI_API_KEY` (stdio) or a per-request header (hosted) | `SERPAPI_API_KEY` / `SERPAPI_KEY` |

**Overlap:** both the MCP server and search-tools depend on serpapi-python and call the same `/search` endpoint.
Giving an agent both would mean two routes to the same API: duplicate tools, confusing for a model, and twice the
risk of wasted credits. So each resource gets its own job:

- **serpapi-python** handles the *deterministic* research. Anything that should happen the same way every time
  (commute, rent, amenities, news) shouldn't depend on an LLM deciding to do it, and it must be cacheable and
  budgeted.
- **serpapi-search-tools** is planned for an optional AI summariser (its Pydantic AI adapter and compact Markdown
  output suit a small local model). It isn't shipped: see finding 7 below.
- **serpapi-mcp** served as a development reference. Its engine catalogue (`engines/*.json`, the same data it serves
  as `serpapi://engines/<name>`) is how we found `google_maps_directions` and its parameters (`travel_mode=3` for transit, `time=arrive_by:…`). search-tools has no directions tool, so that
  engine is called through serpapi-python.

## 2. Data flow

```
UserPreferences
    │
    ▼
research.research_area()          # fixed 7-search plan per suburb, via search.CachedSearch
    │  cache record = {fetched_at, params, response}     (data/raw/*.json, gitignored)
    ▼
extract.*                         # SerpApi JSON → typed models, each with an Evidence record
    │  leads_from_search, commute_from_directions, amenities_from_maps, news_from_search
    ▼
filters.*                         # dedupe by normalised URL; exclude spam / rented / short-term
    ▼
AreaProfile                       # everything known about one suburb; missing parts stay empty
    ▼
scoring.rank()                    # per-dimension 0–1 or None → weighted mean of known → coverage
    ▼
AreaRecommendation                # score, coverage, positives, negatives, unknowns, dealbreakers
```

## 3. Provenance model

Every fact carries an `Evidence` record: URL, domain, source name, title or snippet, the engine's own date label, the
SerpApi `search_id` (the full response can be re-fetched from SerpApi's Search Archive), the query, and the fetch
time.

Values are treated as one of:

- **Reported:** stated by a source (a snippet, Google Maps). Never independently verified.
- **Estimated:** calculated by the app from reported values.
- **Missing:** we looked and found nothing. Shown as unknown, never as zero.

Nothing is labelled "confirmed": a search engine cannot confirm that a flat is still available.

## 4. Scoring

Each dimension scores **0–1, or `None` when evidence is missing**:

| Dimension | Score |
|---|---|
| Affordability | Share of reported rents at or under the user's budget. Flagged when based on fewer than 3 prices |
| Commute | 1.0 up to half the user's max commute, falling linearly to 0 at the max. Over the max is a **dealbreaker**. Uses the user's own mode (car → driving, else transit) |
| Amenities | Per wanted category, `min(count / 3, 1)`, averaged |
| Lifestyle | Match between the area's character (from the AI summary of sources) and the preference: same 1.0, adjacent 0.5, opposite 0 |
| Safety | **Not scored.** A safety brief is shown instead: walking exposure, SAPS stations, patrol groups and labelled, dated resident views |

`overall` is the weighted mean of the **known** dimensions. `coverage` is the share of the user's total weight that
had evidence, and dimensions the user expressed no preference on are left out of it. Ranking: areas without
dealbreakers first, then by score; areas we couldn't score go last.

## 5. Findings from real data

These shaped the code, and each one has a test.

1. **Timezone quirk.** `google_maps_directions` reads `time=arrive_by:<ts>` as a *GMT+0000 wall-clock* time. Our
   first run sent 08:00 Cape Town time (06:00 UTC) and got 05:46 arrivals. We now re-label local wall-clock time as
   UTC ([`research.arrive_by_timestamp`](../src/ct_housing/research.py)).
2. **The API key was saved to cache.** `serpapi.Client` writes `api_key` into the params dict it receives. We saved
   that dict as provenance, so the key ended up in cache files (gitignored, never committed). We now pass a copy,
   and `tests/test_search.py` guards against this.
3. **Spam in the top results.** A `.pics` domain copying real Durbanville listing text ranked in Google's top 9. That's
   why there's a source allowlist rather than a blocklist.
4. **Results pages, not listings.** Only 1 of 27 rental results was an individual listing. Most were results pages
   ("38 apartments to rent in Parow"), and a snippet shows only *examples*, so a page is never rejected because one example is over budget (we found this bug
   on real data).
5. **Hidden characters.** Property24 snippets contain zero-width spaces (`​R ​5 ​000`), so text is
   cleaned before any number is parsed.
6. **No transit route ≠ no transport.** Durbanville → Bellville returned no Google transit route. It's scored as
   unknown with a note (for example, minibus taxis aren't in Google's data), not as 0.
7. **LLMs invent local knowledge.** Asked about Parow with no sources, `qwen3:8b` confidently described "vibrant street
   art … lively nightlife". That's why no AI layer ships in this version. If one is added, it must only summarise
   fetched sources and cite them.

## 6. Cost control

- Free plan: 250 searches/month. One suburb costs up to 10 searches (1 rent + 1 commute + 4 amenities + 1 news +
  3 safety: resident views, patrol groups, police stations).
- `CachedSearch` never repeats a cached query and enforces `max_live_searches` per run (`--budget`).
- `client.account()` is free and shows remaining credits.
