"""Streamlit dashboard: the answers to Q1-Q3 and the benchmark results.

Reads everything from PostgreSQL (the db service): the question views made by
sql/questions.sql and the benchmark_results table filled by bench/benchmark.py.
Runs in the app container (docker compose up -d), on http://localhost:8501.
"""

import os

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

st.set_page_config(page_title="F1 pipeline", layout="wide")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def connect():
    # PGHOST=db is set in docker-compose.yml; the rest comes from .env
    return psycopg.connect(host=os.environ.get("PGHOST", "db"),
                           dbname=os.environ["POSTGRES_DB"],
                           user=os.environ["POSTGRES_USER"],
                           password=os.environ["POSTGRES_PASSWORD"],
                           connect_timeout=5)


@st.cache_data(ttl=30, show_spinner=False)
def query(sql, params=None):
    """A query's rows as a DataFrame. Cached for 30 s, so the page reloads quickly
    but shows new benchmark runs soon after they are stored."""
    with connect() as conn:
        cur = conn.execute(sql, params)
        return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])


def missing(what, how):
    st.info(f"{what} is not in the database yet. {how}")


# ---------------------------------------------------------------------------
# Colours (light and dark steps of the same eight categorical hues)
# ---------------------------------------------------------------------------

DARK = st.context.theme.type == "dark"
SLOTS = (["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"] if DARK else
         ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"])
ONE = SLOTS[0]   # single-series charts
SURFACE = "#0e1117" if DARK else "#ffffff"   # Streamlit's background: rings around overlapping dots

# Fixed order: an implementation keeps its colour whatever is filtered out
IMPLEMENTATIONS = ["pandas", "pandas-lambda", "sql", "c", "rust", "r"]
LABELS = {"pandas": "pandas (vectorised)", "pandas-lambda": "pandas (lambda)", "sql": "SQL",
          "c": "C", "rust": "Rust", "r": "R"}
IMPL_COLOURS = alt.Scale(domain=[LABELS[i] for i in IMPLEMENTATIONS], range=SLOTS)
# startup -> parse -> compute is a sequence, so one hue from light to dark
PHASES = ["startup", "parse", "compute"]
PHASE_COLOURS = alt.Scale(domain=PHASES, range=["#86b6ef", "#2a78d6", "#104281"] if not DARK
                          else ["#184f95", "#3987e5", "#9ec5f4"])
SCALE = {"25pct": 0.25, "50pct": 0.5, "data": 1, "x10": 10, "x50": 50}   # size vs data/


ROW = alt.Step(28)   # height per bar in horizontal bar charts


def bars(chart):
    return chart.mark_bar(cornerRadiusEnd=4, size=14)


def full_height(df):
    """Height that shows every row of st.dataframe without scrolling."""
    return 35 * (len(df) + 1) + 3


def seconds(value):
    if pd.isna(value):
        return "–"
    if value < 0.01:
        return f"{value * 1000:.1f} ms"
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("F1 data pipeline")
try:
    loaded = query("SELECT count(*) AS races, min(year) AS first, max(year) AS last FROM races_clean")
except psycopg.OperationalError as error:
    st.error(f"Cannot connect to the database: {error}\n\nStart it with `docker compose up -d --wait`.")
    st.stop()
except psycopg.errors.UndefinedTable:
    loaded = None

if loaded is not None:
    r = loaded.iloc[0]
    st.caption(f"Answers from the data currently in the database: {r.races:,} races, {r.first}–{r.last}. "
               "The notebook's numbers are for `data/` (1,125 races); a benchmark run on a scaled "
               "dataset leaves that dataset loaded.")

QUESTIONS_MISSING = ("Load the data and run `sql/clean.sql` and `sql/questions.sql` "
                     "(README, Database), or run the benchmark with SQL.")

q1_tab, q2_tab, q3_tab, bench_tab = st.tabs(
    ["Q1 · front row to podium", "Q2 · home advantage", "Q3 · mechanical retirements", "Benchmark"])

# ---------------------------------------------------------------------------
# Q1
# ---------------------------------------------------------------------------

with q1_tab:
    st.subheader("Which circuits convert a front-row start into a podium most often, and did that change after 2014?")
    by = st.radio("Front row by", ["grid", "qualifying"], horizontal=True,
                  help="grid: where the car started (after penalties); qualifying: the qualifying position")
    try:
        q1 = query("""
            SELECT circuit,
                   max(rate) FILTER (WHERE era = '1994–2013')::float8   AS "rate 1994–2013",
                   max(rate) FILTER (WHERE era = '2014–2024')::float8   AS "rate 2014–2024",
                   max(starts) FILTER (WHERE era = '1994–2013')         AS "starts 1994–2013",
                   max(starts) FILTER (WHERE era = '2014–2024')         AS "starts 2014–2024"
            FROM q1_conversion WHERE front_row_by = %s
            GROUP BY circuit ORDER BY "rate 2014–2024" DESC, circuit""", (by,))
        corr = query("SELECT circuits, rank_corr::float8 AS rank_corr FROM q1_rank_correlation "
                     "WHERE front_row_by = %s", (by,))
    except psycopg.errors.UndefinedTable:
        missing("Q1", QUESTIONS_MISSING)
    else:
        a, b = st.columns(2)
        a.metric("Circuits with ≥ 10 front-row starts in both eras", int(corr.circuits.iloc[0]) if len(corr) else 0)
        a.metric("Rank correlation between the eras", f"{corr.rank_corr.iloc[0]:+.2f}" if len(corr) and
                 pd.notna(corr.rank_corr.iloc[0]) else "–",
                 help="Spearman: +1 = the same order of circuits in both eras, 0 = no relation")
        b.markdown("Rate = podiums / front-row starts, in %. Only circuits with at least 10 front-row "
                   "starts in each era. A rank correlation near 0 means the ranking of circuits "
                   "changed almost completely after 2014.")

        long = q1.melt(id_vars="circuit", value_vars=["rate 1994–2013", "rate 2014–2024"],
                       var_name="era", value_name="rate")
        long["era"] = long["era"].str.removeprefix("rate ")
        order = list(q1.circuit)
        y = alt.Y("circuit:N", sort=order, title=None, axis=alt.Axis(labelLimit=260))
        rule = alt.Chart(q1).mark_rule(strokeWidth=2, opacity=0.4).encode(
            y=y, x=alt.X("rate 1994–2013:Q"), x2="rate 2014–2024:Q")
        dots = alt.Chart(long).mark_circle(size=110, opacity=1, stroke=SURFACE, strokeWidth=2).encode(
            y=y,
            x=alt.X("rate:Q", title="podium rate from the front row (%)", scale=alt.Scale(zero=False),
                    axis=alt.Axis(tickCount=8)),
            color=alt.Color("era:N", scale=alt.Scale(domain=["1994–2013", "2014–2024"], range=SLOTS[:2]),
                            legend=alt.Legend(title=None, orient="top")),
            tooltip=["circuit", "era", alt.Tooltip("rate:Q", format=".1f")])
        st.altair_chart((rule + dots).properties(height=26 * len(q1) + 40), width="stretch")
        st.dataframe(q1, hide_index=True, width="stretch", height=full_height(q1), column_config={
            "rate 1994–2013": st.column_config.NumberColumn(format="%.1f"),
            "rate 2014–2024": st.column_config.NumberColumn(format="%.1f")})

# ---------------------------------------------------------------------------
# Q2
# ---------------------------------------------------------------------------

with q2_tab:
    st.subheader("Do drivers finish on the podium more often at home, after controlling for starting position?")
    try:
        q2 = query("""
            SELECT grid_group, rate_home::float8 AS "rate home", rate_away::float8 AS "rate away",
                   starts_home AS "starts home", starts_away AS "starts away",
                   difference_pp::float8 AS "difference (pp)"
            FROM q2_home_by_grid ORDER BY group_order""")
        adv = query("""
            SELECT starts_home, home_share::float8 AS home_share, raw_rate_home::float8 AS raw_home,
                   raw_rate_away::float8 AS raw_away, home_advantage_pp::float8 AS advantage
            FROM q2_home_advantage""").iloc[0]
    except psycopg.errors.UndefinedTable:
        missing("Q2", QUESTIONS_MISSING)
    else:
        a, b, c = st.columns(3)
        a.metric("Home starts", f"{adv.starts_home:,}", help=f"{adv.home_share:.1f}% of all starts")
        b.metric("Raw podium rate, home vs away", f"{adv.raw_home:.1f}% vs {adv.raw_away:.1f}%")
        c.metric("Home advantage within grid groups", f"{adv.advantage:+.2f} pp",
                 help="The differences per grid group, weighted by the number of home starts")
        st.markdown("A home race: the circuit is in the driver's home country. Comparing inside "
                    "the same grid group removes the effect of starting further up.")
        long = q2.melt(id_vars="grid_group", value_vars=["rate home", "rate away"],
                       var_name="where", value_name="rate")
        long["where"] = long["where"].str.removeprefix("rate ")
        starts = q2.melt(id_vars="grid_group", value_vars=["starts home", "starts away"],
                         var_name="where", value_name="starts")
        long["starts"] = starts["starts"].values
        chart = alt.Chart(long).mark_bar(cornerRadiusEnd=4, size=22).encode(
            x=alt.X("grid_group:N", sort=list(q2.grid_group), title="starting position",
                    axis=alt.Axis(labelAngle=0)),
            xOffset=alt.XOffset("where:N", sort=["home", "away"]),
            y=alt.Y("rate:Q", title="podium rate (%)"),
            color=alt.Color("where:N", scale=alt.Scale(domain=["home", "away"], range=SLOTS[:2]),
                            legend=alt.Legend(title=None, orient="top")),
            tooltip=["grid_group", "where", alt.Tooltip("rate:Q", format=".2f"), alt.Tooltip("starts:Q", format=",")])
        st.altair_chart(chart.properties(height=320), width="stretch")
        st.dataframe(q2, hide_index=True, width="stretch", column_config={
            c: st.column_config.NumberColumn(format="%.2f") for c in ("rate home", "rate away", "difference (pp)")})

# ---------------------------------------------------------------------------
# Q3
# ---------------------------------------------------------------------------

with q3_tab:
    st.subheader("Which constructors have the highest mechanical-retirement rate in 2014–2021 vs. 2022–2024?")
    try:
        q3 = query("""
            SELECT period, constructor, starts, mechanical, rate::float8 AS rate
            FROM q3_mechanical_rate ORDER BY period, rate DESC, constructor""")
    except psycopg.errors.UndefinedTable:
        missing("Q3", QUESTIONS_MISSING)
    else:
        st.markdown("Rate = mechanical retirements / race starts, in %. Constructors with at least "
                    "30 starts in the period; renamed teams count separately.")
        periods = list(q3.groupby("period", sort=True))
        for col, (period, rows) in zip(st.columns(2), periods):
            total = 100 * rows.mechanical.sum() / rows.starts.sum()
            col.metric(f"{period}, all constructors", f"{total:.1f}%")
        # One chart with a panel per period: they share the rate axis, so the
        # bars compare directly. No field or tooltip may be called "constructor":
        # Vega builds the tooltip as a JavaScript object, where that name is a
        # built-in property, and the chart then stays empty.
        chart = bars(alt.Chart(q3.rename(columns={"constructor": "team"}))).encode(
            y=alt.Y("team:N", sort="-x", title=None),
            x=alt.X("rate:Q", title="mechanical retirements (%)"),
            color=alt.value(ONE),
            tooltip=["period", alt.Tooltip("team:N", title="team"), alt.Tooltip("rate:Q", format=".2f"),
                     "starts", "mechanical"],
        ).properties(width=480, height=ROW).facet(
            column=alt.Column("period:N", title=None, header=alt.Header(labelFontSize=14))
        ).resolve_scale(y="independent")
        st.altair_chart(chart, width="content")
        for col, (period, rows) in zip(st.columns(2), periods):
            col.dataframe(rows.drop(columns="period"), hide_index=True, width="stretch", height=full_height(rows),
                          column_config={"rate": st.column_config.NumberColumn(format="%.2f")})

# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

with bench_tab:
    try:
        results = query("SELECT * FROM benchmark_results")
    except psycopg.errors.UndefinedTable:
        results = pd.DataFrame()
    if results.empty:
        st.info("No benchmark results in the database yet. Run `python3 bench/benchmark.py` (README, Benchmark).")
        st.stop()

    datasets = sorted(results.dataset.unique(), key=lambda d: SCALE.get(d, 1e9))
    left, right = st.columns([1, 2])
    dataset = left.selectbox("Dataset", datasets, index=datasets.index("data") if "data" in datasets else 0)
    only_checked = right.checkbox("Only results whose output matched pandas", value=True)
    if only_checked:
        results = results[results.output_checked]

    # The latest run of every (dataset, implementation): one harness call does
    # not have to include every implementation
    latest = results.groupby(["dataset", "implementation"]).run_id.transform("max")
    results = results[results.run_id == latest]
    results = results[results.implementation.isin(IMPLEMENTATIONS + ["csv"])].copy()
    results["name"] = results.implementation.map(LABELS)
    here = results[results.dataset == dataset]
    impls = [i for i in IMPLEMENTATIONS if i in set(here.implementation)]
    names = [LABELS[i] for i in impls]
    if not impls:
        st.info(f"No checked results for {dataset}.")
        st.stop()
    runs = here.drop_duplicates("run_id")
    st.caption(f"Medians of {here.runs.max()} runs. Latest run per implementation: "
               + "; ".join(f"{r.run_id} ({', '.join(sorted(set(here[here.run_id == r.run_id].implementation) - {'csv'}))})"
                           for r in runs.itertuples()) + f". Machine: {runs.machine.iloc[-1]}.")

    st.markdown("#### Where the time goes")
    phases = (here[here.kind.isin(PHASES)].groupby(["name", "kind"], as_index=False).value.sum()
              .rename(columns={"kind": "phase"}))
    phases["order"] = phases.phase.map(PHASES.index)
    totals = here[(here.kind == "total")].set_index("name").value
    # stroke in the background colour: a 2 px gap between the stacked parts
    chart = alt.Chart(phases).mark_bar(size=18, stroke=SURFACE, strokeWidth=2).encode(
        y=alt.Y("name:N", sort=names, title=None),
        x=alt.X("sum(value):Q", title="seconds (startup + parse + compute)", axis=alt.Axis(tickCount=8)),
        color=alt.Color("phase:N", scale=PHASE_COLOURS, legend=alt.Legend(title=None, orient="top")),
        order=alt.Order("order:Q"),
        tooltip=["name", "phase", alt.Tooltip("value:Q", title="seconds", format=".4f")])
    st.altair_chart(chart.properties(height=ROW), width="stretch")
    table = phases.pivot(index="name", columns="phase", values="value").reindex(names)
    table["total (wall clock)"] = totals.reindex(names)
    st.dataframe(table[[p for p in PHASES if p in table] + ["total (wall clock)"]].map(seconds),
                 width="stretch")
    st.caption("Not the same work in every row: pandas and SQL clean all 14 tables and build all "
               "features (SQL: 18 of 22) plus Q1–Q3; C, Rust and R clean only what form (6.4) needs "
               "and do form and Q1. The steps below compare like with like.")

    st.markdown("#### Step by step")
    steps = here[((here.kind == "parse") | (here.kind == "compute") | (here.step == "form"))
                 & here.step.isin(["load", "clean", "features", "form", "q1", "q2", "q3"])]
    step_order = [s for s in ["load", "clean", "form", "q1", "features", "q2", "q3"] if s in set(steps.step)]
    shown = st.multiselect("Steps", step_order, default=[s for s in ["load", "form", "q1"] if s in step_order])
    if shown:
        chart = bars(alt.Chart(steps[steps.step.isin(shown)])).encode(
            y=alt.Y("name:N", sort=names, title=None),
            x=alt.X("value:Q", title="seconds"),
            color=alt.value(ONE),
            tooltip=["name", "step", alt.Tooltip("value:Q", title="seconds", format=".4f"),
                     alt.Tooltip("min_value:Q", title="min", format=".4f"),
                     alt.Tooltip("max_value:Q", title="max", format=".4f")],
        ).properties(width=260, height=ROW).facet(
            facet=alt.Facet("step:N", sort=shown, title=None), columns=3
        ).resolve_scale(x="independent")
        st.altair_chart(chart, width="content")
        st.dataframe(steps.pivot_table(index="name", columns="step", values="value")
                     .reindex(names)[[s for s in step_order if s in set(steps.step)]].map(seconds),
                     width="stretch")
        st.caption("Each step has its own scale. clean in C, Rust and R covers only the results "
                   "table; form in pandas and SQL is part of features.")

    st.markdown("#### Memory")
    rss = here[(here.kind == "memory") & (here.step == "process max RSS")]
    db_peak = here[(here.kind == "memory") & (here.step == "peak") & (here.implementation == "sql")]
    memory = pd.concat([rss, db_peak])
    memory["measure"] = memory.step.map({"process max RSS": "process max RSS", "peak": "db container peak"})
    chart = bars(alt.Chart(memory)).encode(
        y=alt.Y("name:N", sort=names, title=None),
        x=alt.X("value:Q", title="MiB", axis=alt.Axis(tickCount=8)),
        color=alt.value(ONE),
        tooltip=["name", "measure", alt.Tooltip("value:Q", title="MiB", format=",.0f")])
    st.altair_chart(chart.properties(height=ROW), width="stretch")
    st.caption("Max RSS of the process (getrusage for pandas, GNU time for C, Rust and R). "
               "SQL: the db container's peak in docker stats, which includes Postgres' shared buffers "
               "and the page cache of what it read.")

    storage = here[here.kind == "storage"]
    if not storage.empty:
        st.markdown("#### Storage")
        size = storage.set_index(["implementation", "step"]).value
        a, b, c = st.columns(3)
        a.metric("14 CSV files", f"{size['csv', 'all tables'] / 1e6:,.1f} MB")
        b.metric("The 14 tables in Postgres", f"{size['sql', 'all tables'] / 1e6:,.1f} MB",
                 f"{size['sql', 'all tables'] / size['csv', 'all tables']:.1f}× the CSV", delta_color="off")
        c.metric("Whole database", f"{size['sql', 'database'] / 1e6:,.1f} MB",
                 help="with the _clean and features_ tables")
        per_table = storage[~storage.step.isin(["all tables", "database"])].pivot(
            index="step", columns="implementation", values="value").rename(
            columns={"csv": "CSV (MB)", "sql": "Postgres (MB)"}) / 1e6
        st.dataframe(per_table.sort_values("CSV (MB)", ascending=False).round(2), width="stretch",
                     height=full_height(per_table))

    st.markdown("#### Scaling")
    scaled = results[results.dataset.isin(SCALE) & results.implementation.isin(IMPLEMENTATIONS)].copy()
    if scaled.dataset.nunique() < 2:
        st.info("Run the benchmark on more than one dataset to see how the times grow, e.g. "
                "`python3 bench/benchmark.py --datasets 25pct data x10`.")
    else:
        what = st.selectbox("Measure", ["total", "load", "form", "q1"])
        rows = scaled[(scaled.step == what) & (scaled.unit == "s")].copy()
        rows["size"] = rows.dataset.map(SCALE)
        present = [LABELS[i] for i in IMPLEMENTATIONS if i in set(rows.implementation)]
        base = alt.Chart(rows).encode(
            x=alt.X("size:Q", scale=alt.Scale(type="log"), title="dataset size (× data/)",
                    axis=alt.Axis(values=sorted(rows["size"].unique()), format="~g")),
            y=alt.Y("value:Q", scale=alt.Scale(type="log"), title="seconds (log)"),
            color=alt.Color("name:N", scale=IMPL_COLOURS, sort=present,
                            legend=alt.Legend(title=None, orient="top", values=present)),
            tooltip=["name", "dataset", alt.Tooltip("value:Q", title="seconds", format=".4f")])
        chart = base.mark_line(strokeWidth=2) + base.mark_circle(size=80, opacity=1)
        st.altair_chart(chart.properties(height=360), width="stretch")
        st.dataframe(rows.pivot_table(index="name", columns="dataset", values="value")
                     .reindex(present)[[d for d in SCALE if d in set(rows.dataset)]].map(seconds),
                     width="stretch")
        st.caption("Both axes are logarithmic: a line with slope 1 grows in proportion to the data.")

    with st.expander("All rows of benchmark_results shown on this page"):
        st.dataframe(results.drop(columns=["name"]).sort_values(["dataset", "implementation", "kind", "step"]),
                     hide_index=True, width="stretch")
