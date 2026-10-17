"""Streamlit web app: questionnaire → research → ranked, explained areas, with safety evidence first.

Run:  uv run streamlit run app.py
"""

import html
import re
from pathlib import Path

import pydeck as pdk
import streamlit as st
from dotenv import load_dotenv

from ct_housing.extract import is_safety_related, is_stale
from ct_housing.models import AreaRecommendation, CommuteEstimate, DimensionScore, UserPreferences
from ct_housing.research import new_searches_needed, research_area, searches_per_area, travel_mode
from ct_housing.scoring import rank
from ct_housing.search import CachedSearch, SearchBudgetExceeded

load_dotenv()

# A starting list only; any suburb can be added. No claims are made about these areas here.
SUBURBS = [
    "Bellville", "Brackenfell", "Claremont", "Durbanville", "Gardens", "Goodwood", "Kenilworth", "Kuils River",
    "Milnerton", "Observatory", "Parow", "Rondebosch", "Sea Point", "Table View", "Woodstock",
]  # fmt: skip
AMENITIES = ["grocery store", "gym", "park", "pharmacy", "clinic", "school", "cafe", "library"]
DIMENSIONS = {"affordability": "Cost", "commute": "Commute", "amenities": "Amenities", "lifestyle": "Lifestyle"}
PRIORITY_LEVELS = {"Ignore": 0.0, "Low": 0.5, "Normal": 1.0, "High": 2.0}
LONG_WALK_MIN = 15
ASSETS = Path(__file__).parent / "assets"
# Colour only where it carries meaning: green good, amber caution, slate weak, light blue "no data". Never red:
# next to safety information, red reads as danger, and a weak match is not a dangerous area.
STATUS_RGB = {"green": [47, 125, 79], "orange": [214, 137, 40], "gray": [94, 108, 118], "blue": [143, 170, 186]}
DIMENSION_ICONS = {"affordability": "payments", "commute": "train", "amenities": "storefront", "lifestyle": "park"}
SAFETY_CHECKLIST = [
    "Visit the area twice: once in daylight and once after dark, at the times you'd actually travel.",
    "Walk the route from the station or taxi rank to the front door yourself.",
    "Check the building: controlled access, working lights, secure parking and who holds the keys.",
    "Ask people who live there, e.g. neighbours, local shop owners or the community Facebook group.",
    "Look up the local police station's statistics and any Neighbourhood Watch or City Improvement District.",
    "Never pay a deposit or rent before viewing the property in person and verifying the agent or owner.",
]


def md_escape(text: str | None) -> str:
    """Web content is untrusted: neutralise Markdown so a title can't inject links or formatting."""
    return re.sub(r"([\\`*_\[\]<>#|~$])", r"\\\1", text or "")


def safe_url(url: str) -> str | None:
    return url if url.startswith(("https://", "http://")) else None


def status(score: float | None) -> tuple[str, str]:
    """Words plus colour, so meaning never depends on colour alone."""
    if score is None:
        return "Not enough data", "blue"
    if score >= 0.75:
        return "Strong", "green"
    if score >= 0.4:
        return "Mixed", "orange"
    return "Weak", "gray"


def percent(score: float | None) -> str:
    return f"{score:.0%}" if score is not None else "n/a"


def section_label(text: str) -> None:
    st.html(f'<div class="section-label">{text}</div>')  # static text only


PHOTO_CREDIT = (
    '<a href="https://commons.wikimedia.org/wiki/File:Table_Mountain_DanieVDM.jpg" target="_blank" rel="noopener">'
    "Danie van der Merwe</a>, "
    '<a href="https://creativecommons.org/licenses/by/2.0/" target="_blank" rel="noopener">CC BY 2.0</a>'
)


def render_hero() -> None:
    st.html(f"""
    <div class="hero" role="img" aria-label="Photo of Table Mountain seen across the sea from Bloubergstrand">
      <div class="hero-text">
        <div class="hero-eyebrow">Cape Town area finder</div>
        <h1>Which part of Cape Town should you live in?</h1>
        <p>Commute, rent, amenities and safety evidence for each suburb, with a source for every claim.</p>
      </div>
      <div class="hero-credit">Photo: {PHOTO_CREDIT}</div>
    </div>""")


def render_footer() -> None:
    st.html(f"""
    <div class="site-footer">
      Data from Google Search, Maps, Directions and News via
      <a href="https://serpapi.com" target="_blank" rel="noopener">SerpApi</a>. Photo: {PHOTO_CREDIT}.
      Rents are indicators from search results, commutes are Google Maps estimates for a weekday 08:00 arrival, and
      nothing here says an area is safe or unsafe. Always visit, and verify any listing before paying.
    </div>""")


# ── Questionnaire ────────────────────────────────────────────────────────────────────────────────────────────────


def preferences_form() -> tuple[UserPreferences, list[str], int]:
    sb = st.sidebar
    sb.subheader("1. Your commute")
    destination = sb.text_input("Where will you travel to most days?", "Bellville", help="Work, campus or an area")
    has_car = sb.toggle("I'll have a car", value=False, help="Off means we check public transport routes")
    max_commute = sb.slider("Longest commute you'd accept (minutes, one way)", 15, 120, 60, step=5)

    sb.subheader("2. Budget")
    max_rent = sb.number_input("Monthly rent budget (rand)", 0, 100_000, 9_000, step=500, help="0 means no limit")

    sb.subheader("3. Lifestyle")
    feel = sb.segmented_control(
        "Neighbourhood feel", ["Quiet", "Balanced", "Lively"], help="Leave unselected if unsure"
    )
    amenities = sb.pills(
        "Important to have nearby",
        AMENITIES,
        selection_mode="multi",
        default=["grocery store", "gym", "park", "pharmacy"],
    )

    sb.subheader("4. What matters most")
    sb.caption("Changing these re-ranks instantly and costs no searches.")
    weights = {}
    for dim, label in DIMENSIONS.items():
        level = sb.select_slider(label, list(PRIORITY_LEVELS), value="Normal", key=f"w-{dim}")
        weights[dim] = PRIORITY_LEVELS[level]

    sb.subheader("5. Areas to compare")
    chosen = sb.multiselect("Suburbs", SUBURBS, default=["Parow", "Durbanville", "Observatory"])
    extra = sb.text_input("Other suburbs (comma separated)", "", placeholder="e.g. Pinelands, Mowbray")
    chosen += [s.strip().title() for s in extra.split(",") if s.strip()]
    with sb.expander("Search limit"):
        budget = st.number_input("Max new searches per run", 0, 100, 40, help="Free SerpApi plan: 250 per month")

    prefs = UserPreferences(
        destination=destination.strip() or "Bellville",
        has_car=has_car,
        max_commute_min=max_commute,
        max_rent_zar=max_rent or None,
        lifestyle=feel.lower() if feel else None,
        amenities_wanted=list(amenities or []),
        weights=weights,
    )
    return prefs, list(dict.fromkeys(chosen)), budget


def research_signature(prefs: UserPreferences, suburbs: list[str]) -> tuple:
    """Inputs that change which searches run. Priorities only re-rank, so they're excluded."""
    return (prefs.destination, travel_mode(prefs), tuple(prefs.amenities_wanted), tuple(suburbs))


def run_research(prefs: UserPreferences, suburbs: list[str], budget: int) -> None:
    searcher = CachedSearch(max_live_searches=budget)
    profiles, progress = [], st.progress(0.0, "Starting research…")
    try:
        for i, suburb in enumerate(suburbs):
            progress.progress(i / len(suburbs), f"Researching {suburb} ({i + 1} of {len(suburbs)})…")
            profiles.append(research_area(searcher, suburb, prefs))
    except SearchBudgetExceeded:
        st.warning(f"Stopped after {len(profiles)} area(s): the search limit of {budget} was reached.")
    except Exception as exc:  # noqa: BLE001 - show any API failure to the user rather than crash the page
        st.error(f"Research failed: {exc}")
    progress.empty()
    st.session_state.profiles = profiles
    st.session_state.signature = research_signature(prefs, suburbs)
    st.session_state.live_searches = searcher.live_searches
    st.session_state.demo_hits = searcher.demo_hits


# ── Results ──────────────────────────────────────────────────────────────────────────────────────────────────────


def main_commute(rec: AreaRecommendation) -> CommuteEstimate | None:
    return next(iter(rec.profile.commutes), None)


def limits_broken(rec: AreaRecommendation) -> str:
    reasons = []
    for d in rec.dealbreakers:
        if d.startswith("Commute"):
            reasons.append("Commute too long")
        elif "budget" in d:
            reasons.append("Over budget")
        else:
            reasons.append(d)
    return ", ".join(reasons) or "No"


def comparison_rows(recs: list[AreaRecommendation], prefs: UserPreferences) -> list[dict]:
    rows = []
    for r in recs:
        c = main_commute(r)
        safety = r.profile.safety
        if c and c.available:
            how = "by car" if c.mode == "driving" else "by public transport"
            commute = f"{c.duration_min} min {how}"
        else:
            commute = "No route found"
        walking = f"{c.walk_min} min (longest {c.longest_walk_min})" if c and c.walk_min is not None else "–"
        if not safety.researched:
            patrols = police = "–"
        else:
            patrols = f"Found ({len(safety.patrol_groups)})" if safety.patrol_groups else "None found"
            named = any(r.suburb.lower() in s.name.lower() for s in safety.police_stations)
            police = "In suburb" if named else ("Nearby only" if safety.police_stations else "None found")
        rows.append({
            "Suburb": r.suburb,
            "Fit for you": r.overall,
            "Data found": r.coverage,
            "Typical rent / month": f"R{r.profile.rent.median_zar:,}" if r.profile.rent.median_zar else "Unknown",
            "Commute (one way)": commute,
            "Walking part of it": walking,
            "Neighbourhood Watch / CID": patrols,
            "Police station": police,
            "Breaks your limits?": limits_broken(r),
        })  # fmt: skip
    return rows


def render_comparison(recs: list[AreaRecommendation], prefs: UserPreferences) -> None:
    col = st.column_config
    st.dataframe(
        comparison_rows(recs, prefs),
        hide_index=True,
        width="stretch",
        column_config={
            "Fit for you": col.ProgressColumn(
                format="percent", min_value=0, max_value=1,
                help="How well the suburb matches your priorities, using only what we found data for",
            ),
            "Data found": col.ProgressColumn(
                format="percent", min_value=0, max_value=1,
                help="How much of what you asked about we found data for. Low means treat the ranking with caution",
            ),
            "Typical rent / month": col.TextColumn(help="Middle value of rents advertised on property sites"),
            "Commute (one way)": col.TextColumn(
                help=f"Google Maps estimate, weekday, arriving at {prefs.destination} by 08:00"
            ),
            "Walking part of it": col.TextColumn(
                help="Minutes of the public transport route spent walking, and the longest single walk"
            ),
            "Neighbourhood Watch / CID": col.TextColumn(
                help="Community safety groups found online: Neighbourhood Watches, City Improvement Districts "
                "(paid patrols and cleaning) and Community Police Forums"
            ),
            "Police station": col.TextColumn(
                help="Whether a SAPS police station is in the suburb itself or only nearby"
            ),
            "Breaks your limits?": col.TextColumn(help="Whether the suburb breaks your maximum commute or budget"),
        },
    )  # fmt: skip
    st.caption("Hover over a column name for what it means.")


def render_map(recs: list[AreaRecommendation], prefs: UserPreferences) -> None:
    points, destination = [], None
    for r in recs:
        c = main_commute(r)
        if c and c.start_coords:
            label, colour = status(r.overall)
            lat, lon = c.start_coords
            points.append({"name": r.suburb, "lat": lat, "lon": lon, "color": STATUS_RGB[colour],
                           "info": f"{label} match · {percent(r.overall)}"})  # fmt: skip
        if c and c.end_coords:
            destination = c.end_coords
    if not points:
        return
    layers = [
        pdk.Layer("ScatterplotLayer", data=points, get_position="[lon, lat]", get_fill_color="color",
                  get_radius=650, radius_min_pixels=9, stroked=True, get_line_color=[255, 255, 255],
                  line_width_min_pixels=2, pickable=True),
    ]  # fmt: skip
    if destination:
        dest = [{"name": prefs.destination, "lat": destination[0], "lon": destination[1], "info": "Your destination"}]
        layers += [
            pdk.Layer("ScatterplotLayer", data=dest, get_position="[lon, lat]", get_fill_color=[29, 78, 107],
                      get_radius=500, radius_min_pixels=7, stroked=True, get_line_color=[255, 255, 255],
                      line_width_min_pixels=3, pickable=True),
        ]  # fmt: skip
    all_points = points + ([{"lat": destination[0], "lon": destination[1]}] if destination else [])
    view = pdk.data_utils.compute_view([[p["lon"], p["lat"]] for p in all_points], view_proportion=1)
    view.zoom = min(view.zoom, 11.5)
    deck = pdk.Deck(layers=layers, initial_view_state=view, map_provider="carto", map_style="light",
                    tooltip={"text": "{name}\n{info}"})  # fmt: skip
    st.pydeck_chart(deck, height=380)

    # Legend as real text (accessible, and in the site's font) rather than labels drawn on the map canvas.
    items = [
        f'<span class="legend-item"><span class="dot" style="background: rgb({",".join(map(str, p["color"]))})"></span>'
        f'{html.escape(p["name"])} <span class="legend-sub">{html.escape(p["info"])}</span></span>'
        for p in points
    ]
    if destination:
        items.append(
            '<span class="legend-item"><span class="dot dest"></span>'
            f'{html.escape(prefs.destination)} <span class="legend-sub">your destination</span></span>'
        )
    st.html(f'<div class="map-legend">{"".join(items)}</div>')


def render_article(a) -> None:
    url = safe_url(a.url)
    title = f"[{md_escape(a.title)}]({url})" if url else md_escape(a.title)
    when = a.published_at.strftime("%d %b %Y") if a.published_at else a.result_date or "undated"
    st.markdown(f"{title}  \n{md_escape(a.source_name)} · {when}")


def render_safety(rec: AreaRecommendation, prefs: UserPreferences) -> None:
    brief = rec.profile.safety
    with st.container(border=True):
        st.markdown("#### :material/shield: Safety evidence")
        st.caption(
            "We never rate an area as safe or unsafe. Safety varies street by street and by time of day, and no search "
            "or AI can know it. Here is what we found, with sources, so you can judge for yourself."
        )

        left, right = st.columns(2, gap="medium")
        with left:
            render_walking(rec, prefs)
            render_police(brief, rec.suburb)
        with right:
            render_patrols(brief, rec.suburb)

        st.divider()
        render_resident_views(brief, rec.suburb)

        st.divider()
        news = rec.profile.news
        crime = [a for a in news if is_safety_related(a)]
        st.markdown(f"**:material/newspaper: Crime in recent local news:** {len(crime)} of {len(news)} headlines")
        st.caption("From a general news search, so not a crime check. 0 headlines does not mean no crime.")
        for a in crime:
            render_article(a)

        with st.expander("Before you sign a lease: safety checklist"):
            for i, item in enumerate(SAFETY_CHECKLIST):
                st.checkbox(item, key=f"check-{rec.suburb}-{i}")
        st.caption("Emergency: SAPS 10111 · from any mobile 112")


def render_walking(rec: AreaRecommendation, prefs: UserPreferences) -> None:
    c = main_commute(rec)
    st.markdown("**:material/directions_walk: Walking on your commute**")
    if prefs.has_car:
        st.markdown("You'd drive. Check for secure parking at home and at your destination.")
    elif c and c.available and c.walk_min is not None:
        leave = f", leaving around {c.depart_time}" if c.depart_time else ""
        st.markdown(f"{c.walk_min} min in total, longest stretch {c.longest_walk_min} min{leave}.")
        if (c.longest_walk_min or 0) >= LONG_WALK_MIN:
            st.warning(
                f"Includes a {c.longest_walk_min}-minute walk. If you'll often walk it alone, try it at your actual "
                "travel times, and consider a lift, e-hailing or a closer stop for that stretch.",
                icon=":material/warning:",
            )
        st.caption(f"Walks are to the centre of {prefs.destination}. Enter your exact workplace for your real route.")
    else:
        st.markdown("Unknown: Google Maps found no public transport route.")


def render_police(brief, suburb: str) -> None:
    st.markdown("**:material/local_police: Police**")
    if not brief.researched:
        st.caption("Not researched yet.")
        return
    if not brief.police_stations:
        st.markdown("No SAPS station found in the search. Look it up on the SAPS website below.")
    for station in brief.police_stations:
        prefix = "" if suburb.lower() in station.name.lower() else "Nearby: "
        digits = re.sub(r"\D", "", station.phone or "")
        phone = f" · [{md_escape(station.phone)}](tel:{digits})" if digits else ""
        st.markdown(f"{prefix}**{md_escape(station.name)}**  \n{md_escape(station.address)}{phone}")
    if brief.police_stations and suburb.lower() not in brief.police_stations[0].name.lower():
        st.caption(f"No station named after {suburb}; its police precinct may be one of these.")
    st.markdown(
        "[Official SAPS crime statistics](https://www.saps.gov.za/services/crimestats.php) are published per police "
        "station, roughly every quarter."
    )


def render_patrols(brief, suburb: str) -> None:
    st.markdown("**:material/groups: Patrols and community safety**")
    if not brief.researched:
        st.caption("Not researched yet.")
        return
    if not brief.patrol_groups:
        st.markdown(
            "No Neighbourhood Watch, City Improvement District or Community Police Forum found in search results. "
            "Ask locally: this doesn't mean there isn't one."
        )
        return
    kinds = sorted({g.source_name for g in brief.patrol_groups})
    st.markdown("Found: " + ", ".join(kinds) + ".")
    for g in brief.patrol_groups[:5]:
        url = safe_url(g.url)
        title = f"[{md_escape(g.title)}]({url})" if url else md_escape(g.title)
        st.markdown(f"- {title}  \n  {md_escape(g.source_name)} · {md_escape(g.domain)}")
    st.caption(
        "City Improvement Districts fund extra patrols and cleaning. Neighbourhood Watches are volunteers. "
        "Check how active a group is before relying on it."
    )


VIEW_LABELS = {
    "discussion": ("Residents' discussion", "gray"),
    "rating_site": ("Third-party safety score, method unknown", "orange"),  # amber = read with care
    "estate_agent": ("Estate agent, may be promotional", "orange"),
    "news": ("News", "gray"),
    "other": ("Website", "gray"),
}


def render_resident_views(brief, suburb: str) -> None:
    st.markdown(f"**:material/forum: What people say about living in {md_escape(suburb)}**")
    if not brief.researched:
        st.caption("Not researched yet.")
        return
    if not brief.resident_views:
        st.markdown("Nothing found that discusses this suburb by name.")
        return
    st.caption("Opinions, not facts, and often dated. Useful for knowing which questions to ask locally.")
    primary = [v for v in brief.resident_views if v.kind in ("discussion", "news")]
    secondary = [v for v in brief.resident_views if v.kind not in ("discussion", "news")]
    for view in primary:
        render_view(view)
    if not primary:
        st.markdown("No residents' discussions or news found for this suburb by name.")
    if secondary:
        with st.expander(f"More sources ({len(secondary)}): rating sites, estate agents and other websites"):
            for view in secondary:
                render_view(view)


def render_view(view) -> None:
    e = view.evidence
    label, colour = VIEW_LABELS[view.kind]
    with st.container(border=True):
        badges = st.columns([3, 1])
        badges[0].badge(label, color=colour)
        if is_stale(e):
            badges[1].badge("Older than 2 years", color="orange")
        url = safe_url(e.url)
        st.markdown(f"[{md_escape(e.title)}]({url})" if url else md_escape(e.title))
        if e.snippet:
            st.markdown(f"> {md_escape(e.snippet)}")
        st.caption(" · ".join(x for x in [md_escape(e.domain), e.result_date] if x))


def render_dimension(dim: DimensionScore) -> None:
    label, colour = status(dim.score)
    with st.container(border=True):
        head, badge = st.columns([3, 2])
        head.markdown(f":material/{DIMENSION_ICONS.get(dim.name, 'info')}: **{DIMENSIONS.get(dim.name, dim.name)}**")
        badge.badge(f"{label} · {percent(dim.score)}" if dim.score is not None else label, color=colour)
        st.markdown(md_escape(dim.summary))
        for p in dim.positives:
            st.markdown(f"- Works for you: {md_escape(p)}")
        for n in dim.negatives:
            st.markdown(f"- Watch out: {md_escape(n)}")
        for u in dim.unknowns:
            st.caption(f"Not known: {md_escape(u)}")


def render_area(rec: AreaRecommendation, prefs: UserPreferences) -> None:
    label, colour = status(rec.overall)
    st.badge(f"{label} match · {percent(rec.overall)} · we found evidence for {rec.coverage:.0%} of your priorities",
             color=colour)  # fmt: skip
    c = main_commute(rec)
    median = rec.profile.rent.median_zar
    tiles = [
        (":material/payments: Typical rent", f"R{median:,}" if median else "Unknown",
         f"From {len(rec.profile.rent.prices_zar)} advertised prices"),
        (":material/train: Commute", f"{c.duration_min} min" if c and c.available else "No route",
         f"To {prefs.destination}, arriving 08:00"),
        (":material/directions_walk: Walking on route", f"{c.walk_min} min" if c and c.walk_min is not None else "n/a",
         "Total walking on the public transport route"),
        (":material/storefront: Amenities found", str(sum(a.count for a in rec.profile.amenities)),
         "Places in Google Maps results for this suburb"),
    ]  # fmt: skip
    for col, (tile_label, value, help_text) in zip(st.columns(4), tiles, strict=True):
        with col.container(border=True):
            st.metric(tile_label, value, help=help_text)
    for d in rec.dealbreakers:
        st.warning(d, icon=":material/block:")

    render_safety(rec, prefs)

    dims = [d for d in rec.dimensions if d.weight > 0 and d.asked]
    left, right = st.columns(2)
    for i, dim in enumerate(dims):
        with left if i % 2 == 0 else right:
            render_dimension(dim)

    with st.expander(f"All recent local news ({len(rec.profile.news)})"):
        for a in rec.profile.news:
            render_article(a)

    links = {}  # one button per site, keeping the top-ranked page for each
    for e in rec.profile.browse_links:
        if safe_url(e.url):
            links.setdefault(e.source_name or e.domain, e.url)
    links = list(links.items())
    if links:
        st.markdown("**Find flats in this area**")
        for col, (name, url) in zip(st.columns(len(links)), links, strict=False):
            col.link_button(f"Browse rentals on {name}", url, width="stretch", icon=":material/open_in_new:")


def render_empty_state() -> None:
    st.html('<div id="how-it-works" class="section-label">How it works</div>')
    st.html("""
    <div class="steps">
      <div class="step"><div class="step-num">1</div><h4>Tell us your situation</h4>
        <p>Where you'll travel to most days, your budget, and what matters most to you.</p></div>
      <div class="step"><div class="step-num">2</div><h4>We research each area</h4>
        <p>Live commutes at rush hour, rent levels, shops and parks nearby, and local news.</p></div>
      <div class="step"><div class="step-num">3</div><h4>Compare the evidence</h4>
        <p>A ranked shortlist with sources, honest unknowns, and safety evidence first.</p></div>
    </div>""")
    st.info("Fill in the sidebar, then press **Research areas**.", icon=":material/arrow_back:")


def main() -> None:
    st.set_page_config(page_title="Cape Town Area Finder", layout="wide")
    st.html(f"<style>{(ASSETS / 'style.css').read_text()}</style>")
    render_hero()

    prefs, suburbs, budget = preferences_form()
    searcher = CachedSearch(max_live_searches=budget)
    needed = new_searches_needed(searcher, suburbs, prefs)
    st.sidebar.divider()
    st.sidebar.caption(
        f"This will use **{needed} new search{'es' if needed != 1 else ''}** "
        f"(up to {searches_per_area(prefs)} per area; results you've already fetched are free)."
    )
    if not searcher.api_key and needed:
        st.sidebar.error("SERPAPI_API_KEY is not set. Add it to `.env` (see `.env.example`).")
    if st.sidebar.button("Research areas", type="primary", disabled=not suburbs, width="stretch"):
        run_research(prefs, suburbs, budget)

    profiles = st.session_state.get("profiles")
    if not profiles:
        render_empty_state()
        render_footer()
        return
    if st.session_state.get("signature") != research_signature(prefs, suburbs):
        st.warning(
            "You changed where you travel, how you travel, amenities or suburbs. Press **Research areas** to update."
        )

    # Ranking is recomputed from stored research on every change: moving a priority costs no searches.
    recs = rank(profiles, prefs)
    mode = "car" if prefs.has_car else "public transport"
    budget_text = f"budget R{prefs.max_rent_zar:,}" if prefs.max_rent_zar else "no budget limit"
    section_label("Your shortlist")
    st.caption(
        f"Travelling to {prefs.destination} by {mode} · {budget_text} · max {prefs.max_commute_min} min · "
        f"{st.session_state.get('live_searches', 0)} new searches used"
    )
    if st.session_state.get("demo_hits"):
        st.info(
            "Showing bundled example data fetched on 17 Oct 2026, so no API key or searches were needed. "
            "Add your own SerpApi key to research other suburbs or destinations with live data.",
            icon=":material/info:",
        )
    render_comparison(recs, prefs)
    render_map(recs, prefs)

    section_label("Area by area")

    tabs = st.tabs([f"{i}. {r.suburb}" for i, r in enumerate(recs, 1)])
    for tab, rec in zip(tabs, recs, strict=True):
        with tab:
            render_area(rec, prefs)

    render_footer()


main()
