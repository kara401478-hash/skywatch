"""
✈️  Live Global Flight Tracker
Real-time aircraft positions via OpenSky Network API
Features: country/altitude filter, airport arrivals/departures
"""

import os
import time
import requests
from dotenv import load_dotenv
from dash import Dash, dcc, html, Input, Output, State, callback
import plotly.graph_objects as go

load_dotenv()

# ── OpenSky Auth ───────────────────────────────────────────────────────────────
CLIENT_ID     = os.getenv("OPENSKY_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("OPENSKY_CLIENT_SECRET", "")
TOKEN_URL     = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
API_BASE      = "https://opensky-network.org/api"

_token_cache = {"token": None, "expires_at": 0}

def get_token():
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 30:
        return _token_cache["token"]
    if not CLIENT_ID or not CLIENT_SECRET:
        return None
    try:
        resp = requests.post(TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = now + data.get("expires_in", 300)
        return _token_cache["token"]
    except Exception as e:
        print(f"[Auth error] {e}")
        return None


# ── Flight data cache (shared across callbacks) ────────────────────────────────
_flights_cache = []

def fetch_states():
    global _flights_cache
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.get(f"{API_BASE}/states/all", headers=headers, timeout=15)
        resp.raise_for_status()
        raw = resp.json().get("states", []) or []
    except Exception as e:
        print(f"[API error] {e}")
        raw = []

    flights = []
    for s in raw:
        if s[5] is None or s[6] is None:
            continue
        if s[8]:  # on ground
            continue
        flights.append({
            "icao24":   s[0],
            "callsign": (s[1] or "").strip() or s[0],
            "country":  s[2] or "Unknown",
            "lon":      s[5],
            "lat":      s[6],
            "alt":      round(s[7] / 0.3048) if s[7] else 0,
            "velocity": round(s[9] * 1.94384) if s[9] else None,
            "heading":  round(s[10]) if s[10] else None,
        })

    _flights_cache = flights if flights else _demo_flights()
    return _flights_cache


def fetch_airport_flights(icao_airport, mode="arrivals"):
    """Fetch arrivals or departures for an airport in the last 2 hours."""
    token = get_token()
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    end   = int(time.time())
    begin = end - 7200  # last 2 hours
    endpoint = "arrival" if mode == "arrivals" else "departure"
    try:
        resp = requests.get(
            f"{API_BASE}/flights/{endpoint}",
            params={"airport": icao_airport.upper(), "begin": begin, "end": end},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        print(f"[Airport API error] {e}")
        return []


def _demo_flights():
    import random
    random.seed(int(time.time() / 60))
    demos = []
    for i in range(150):
        demos.append({
            "icao24":   f"demo{i:04x}",
            "callsign": f"DEMO{i:03d}",
            "country":  "Demo",
            "lon":      random.uniform(-170, 170),
            "lat":      random.uniform(-60, 75),
            "alt":      random.randint(15000, 42000),
            "velocity": random.randint(280, 520),
            "heading":  random.randint(0, 359),
        })
    return demos


def heading_to_arrow(deg):
    arrows = ["↑","↗","→","↘","↓","↙","←","↖"]
    if deg is None:
        return "·"
    return arrows[round(deg / 45) % 8]


def build_figure(flights):
    if not flights:
        flights = [{"lon":0,"lat":0,"alt":0,"callsign":"","country":"","velocity":None,"heading":None}]

    lons  = [f["lon"] for f in flights]
    lats  = [f["lat"] for f in flights]
    alts  = [f["alt"] or 0 for f in flights]
    max_a = max(alts) if alts else 45000

    def alt_color(a):
        n = max(0.0, min(1.0, a / max_a))
        return f"rgb({int(n*255)},{int(n*160)},{int((1-n)*255)})"

    colors = [alt_color(a) for a in alts]

    hover_texts = []
    for f in flights:
        arr     = heading_to_arrow(f["heading"])
        alt_str = f"{f['alt']:,} ft"     if f["alt"]      else "N/A"
        spd_str = f"{f['velocity']} kts" if f["velocity"] else "N/A"
        hdg_str = f"{f['heading']}°"     if f["heading"]  else "N/A"
        hover_texts.append(
            f"<b>{f['callsign']}</b>  {arr}<br>"
            f"🌍 {f['country']}<br>"
            f"✈ Alt: {alt_str}<br>"
            f"💨 Speed: {spd_str}<br>"
            f"🧭 Hdg: {hdg_str}"
        )

    fig = go.Figure()
    fig.add_trace(go.Scattergeo(
        lon=lons, lat=lats,
        mode="markers",
        marker=dict(
            size=5, color=colors, opacity=0.85, symbol="triangle-up",
            line=dict(width=0.3, color="rgba(255,255,255,0.2)"),
        ),
        text=hover_texts,
        hovertemplate="%{text}<extra></extra>",
        showlegend=False,
    ))

    fig.update_layout(
        geo=dict(
            projection_type="natural earth",
            showland=True,       landcolor="#0d1b2a",
            showocean=True,      oceancolor="#060d17",
            showcountries=True,  countrycolor="rgba(255,255,255,0.07)",
            showcoastlines=True, coastlinecolor="rgba(255,255,255,0.15)",
            showframe=False,     bgcolor="#060d17",
        ),
        paper_bgcolor="#060d17",
        margin=dict(l=0, r=0, t=0, b=0),
        height=580,
        font=dict(family="'Courier New', monospace", color="#aaa"),
    )
    return fig


# ── Dash app ───────────────────────────────────────────────────────────────────
app = Dash(__name__, title="✈ Live Flight Tracker")
REFRESH_MS = 60_000

# Pre-populate cache
fetch_states()

app.layout = html.Div(
    style={"backgroundColor":"#060d17","minHeight":"100vh",
           "fontFamily":"'Courier New', monospace","color":"#ccc"},
    children=[

        # ── Header ────────────────────────────────────────────────────────────
        html.Div(style={
            "background":"linear-gradient(90deg,#060d17 0%,#0a2744 50%,#060d17 100%)",
            "borderBottom":"1px solid rgba(0,200,255,0.2)",
            "padding":"16px 32px","display":"flex","alignItems":"center","justifyContent":"space-between",
        }, children=[
            html.Div([
                html.Span("✈", style={"fontSize":"26px","marginRight":"10px","color":"#00d2d3"}),
                html.Span("LIVE FLIGHT TRACKER", style={
                    "fontSize":"18px","fontWeight":"bold","letterSpacing":"4px","color":"#e0f7fa"}),
            ]),
            html.Div(id="header-stats", style={"fontSize":"11px","color":"#4a6fa5","letterSpacing":"1px"}),
        ]),

        # ── Filter bar ────────────────────────────────────────────────────────
        html.Div(style={
            "padding":"10px 32px","backgroundColor":"#0a1628",
            "borderBottom":"1px solid rgba(255,255,255,0.05)",
            "display":"flex","alignItems":"center","gap":"24px","flexWrap":"wrap",
        }, children=[

            # Country filter
            html.Div([
                html.Label("COUNTRY", style={"fontSize":"10px","letterSpacing":"2px","color":"#4a6fa5","display":"block","marginBottom":"4px"}),
                dcc.Dropdown(
                    id="filter-country",
                    options=[],  # populated by callback
                    placeholder="All countries…",
                    multi=True,
                    clearable=True,
                    style={"width":"260px","backgroundColor":"#0d1b2a","fontSize":"12px",
                           "border":"1px solid rgba(0,200,255,0.2)","borderRadius":"4px"},
                ),
            ]),

            # Altitude filter
            html.Div([
                html.Label("ALTITUDE (ft)", style={"fontSize":"10px","letterSpacing":"2px","color":"#4a6fa5","display":"block","marginBottom":"4px"}),
                html.Div(style={"display":"flex","alignItems":"center","gap":"8px"}, children=[
                    dcc.Input(id="alt-min", type="number", placeholder="Min", value=0,
                              style={"width":"80px","backgroundColor":"#0d1b2a","color":"#ccc",
                                     "border":"1px solid rgba(0,200,255,0.2)","borderRadius":"4px",
                                     "padding":"4px 8px","fontSize":"12px"}),
                    html.Span("–", style={"color":"#4a6fa5"}),
                    dcc.Input(id="alt-max", type="number", placeholder="Max", value=60000,
                              style={"width":"80px","backgroundColor":"#0d1b2a","color":"#ccc",
                                     "border":"1px solid rgba(0,200,255,0.2)","borderRadius":"4px",
                                     "padding":"4px 8px","fontSize":"12px"}),
                ]),
            ]),

            # Apply filter button
            html.Button("APPLY FILTER", id="btn-filter",
                style={"marginTop":"16px","backgroundColor":"rgba(0,200,255,0.1)",
                       "color":"#00d2d3","border":"1px solid rgba(0,200,255,0.3)",
                       "borderRadius":"4px","padding":"6px 16px","fontSize":"11px",
                       "letterSpacing":"2px","cursor":"pointer"}),

            # Reset
            html.Button("RESET", id="btn-reset",
                style={"marginTop":"16px","backgroundColor":"transparent",
                       "color":"#4a6fa5","border":"1px solid rgba(255,255,255,0.1)",
                       "borderRadius":"4px","padding":"6px 12px","fontSize":"11px",
                       "letterSpacing":"2px","cursor":"pointer"}),

            html.Div(id="filter-count",
                     style={"marginTop":"16px","fontSize":"11px","color":"#4a9fd4"}),
        ]),

        # ── Altitude legend ───────────────────────────────────────────────────
        html.Div(style={
            "padding":"6px 32px","backgroundColor":"#080f1c",
            "borderBottom":"1px solid rgba(255,255,255,0.04)",
            "display":"flex","alignItems":"center","gap":"12px","fontSize":"10px","color":"#4a6fa5",
        }, children=[
            html.Span("ALTITUDE", style={"letterSpacing":"2px"}),
            html.Div(style={
                "width":"140px","height":"6px","borderRadius":"3px",
                "background":"linear-gradient(90deg,rgb(0,0,255),rgb(128,80,128),rgb(255,160,0))",
            }),
            html.Span("LOW → HIGH"),
            html.Span("▲ = aircraft", style={"marginLeft":"auto","color":"#555"}),
        ]),

        # ── Map ───────────────────────────────────────────────────────────────
        dcc.Graph(
            id="live-map",
            figure=build_figure(_flights_cache),
            config={"scrollZoom":True,"displayModeBar":True,"displaylogo":False,
                    "modeBarButtonsToRemove":["select2d","lasso2d"]},
            style={"height":"580px"},
        ),

        # ── Airport panel ─────────────────────────────────────────────────────
        html.Div(style={
            "padding":"12px 32px","backgroundColor":"#0a1628",
            "borderTop":"1px solid rgba(0,200,255,0.1)",
        }, children=[
            html.Div(style={"display":"flex","alignItems":"center","gap":"16px","flexWrap":"wrap"}, children=[
                html.Label("AIRPORT  ICAO CODE",
                           style={"fontSize":"10px","letterSpacing":"2px","color":"#4a6fa5"}),
                dcc.Input(
                    id="airport-icao",
                    type="text",
                    placeholder="例: RJTT (羽田)  KJFK  EGLL",
                    maxLength=4,
                    style={"width":"220px","backgroundColor":"#0d1b2a","color":"#e0f7fa",
                           "border":"1px solid rgba(0,200,255,0.25)","borderRadius":"4px",
                           "padding":"5px 10px","fontSize":"13px","letterSpacing":"2px"},
                ),
                html.Div(style={"display":"flex","gap":"8px"}, children=[
                    html.Button("ARRIVALS", id="btn-arrivals",
                        style={"backgroundColor":"rgba(0,200,255,0.12)","color":"#00d2d3",
                               "border":"1px solid rgba(0,200,255,0.3)","borderRadius":"4px",
                               "padding":"5px 14px","fontSize":"11px","letterSpacing":"1px","cursor":"pointer"}),
                    html.Button("DEPARTURES", id="btn-departures",
                        style={"backgroundColor":"rgba(255,200,0,0.1)","color":"#ffd32a",
                               "border":"1px solid rgba(255,200,0,0.3)","borderRadius":"4px",
                               "padding":"5px 14px","fontSize":"11px","letterSpacing":"1px","cursor":"pointer"}),
                ]),
                html.Div(id="airport-status", style={"fontSize":"11px","color":"#4a6fa5"}),
            ]),
            html.Div(id="airport-table", style={"marginTop":"10px"}),
        ]),

        # ── Status bar ────────────────────────────────────────────────────────
        html.Div(style={
            "padding":"8px 32px","backgroundColor":"#060d17",
            "borderTop":"1px solid rgba(255,255,255,0.04)",
            "display":"flex","alignItems":"center","justifyContent":"space-between",
        }, children=[
            html.Div(id="status-bar", style={"fontSize":"11px","color":"#4a9fd4"}),
            html.Div(id="next-refresh", style={"fontSize":"11px","color":"#4a6fa5"}),
        ]),

        dcc.Interval(id="interval",       interval=REFRESH_MS, n_intervals=0),
        dcc.Interval(id="countdown-tick", interval=1_000,      n_intervals=0),
        dcc.Store(id="last-refresh-ts",   data=time.time()),
        dcc.Store(id="filtered-flights",  data=[]),
    ]
)


# ── Callbacks ──────────────────────────────────────────────────────────────────

# 1. Fetch live data & update country dropdown options
@callback(
    Output("live-map",          "figure"),
    Output("header-stats",      "children"),
    Output("status-bar",        "children"),
    Output("last-refresh-ts",   "data"),
    Output("filter-country",    "options"),
    Output("filtered-flights",  "data"),
    Input("interval",           "n_intervals"),
    Input("btn-filter",         "n_clicks"),
    Input("btn-reset",          "n_clicks"),
    State("filter-country",     "value"),
    State("alt-min",            "value"),
    State("alt-max",            "value"),
    prevent_initial_call=False,
)
def refresh_map(n_intervals, n_filter, n_reset, countries, alt_min, alt_max):
    from dash import ctx
    triggered = ctx.triggered_id

    # Fetch new data on interval or reset
    if triggered in ("interval", "btn-reset", None):
        flights = fetch_states()
    else:
        flights = _flights_cache

    # Apply filters on btn-filter (not on reset/interval)
    filtered = flights
    if triggered == "btn-filter":
        if countries:
            filtered = [f for f in filtered if f["country"] in countries]
        lo = alt_min if alt_min is not None else 0
        hi = alt_max if alt_max is not None else 60000
        filtered = [f for f in filtered if lo <= (f["alt"] or 0) <= hi]

    fig = build_figure(filtered)

    is_demo   = all(f["country"] == "Demo" for f in flights)
    n_country = len({f["country"] for f in flights})
    avg_alt   = sum(f["alt"] or 0 for f in flights) / max(len(flights), 1)
    stats     = f"{len(flights):,} aircraft  ·  {n_country} countries  ·  avg alt {avg_alt:,.0f} ft"
    mode      = "⚠ DEMO MODE" if is_demo else "● LIVE  OpenSky Network"
    color     = "#e67e22" if is_demo else "#00d2d3"

    # Build country options from current full data
    all_countries = sorted({f["country"] for f in flights})
    options = [{"label": c, "value": c} for c in all_countries]

    return fig, stats, html.Span(mode, style={"color": color}), time.time(), options, filtered


# 2. Filter count label
@callback(
    Output("filter-count", "children"),
    Input("filtered-flights", "data"),
)
def update_filter_count(filtered):
    n = len(filtered) if filtered else len(_flights_cache)
    total = len(_flights_cache)
    if n == total:
        return ""
    return f"showing {n:,} / {total:,}"


# 3. Airport arrivals / departures
@callback(
    Output("airport-table",  "children"),
    Output("airport-status", "children"),
    Input("btn-arrivals",    "n_clicks"),
    Input("btn-departures",  "n_clicks"),
    State("airport-icao",    "value"),
    prevent_initial_call=True,
)
def show_airport(n_arr, n_dep, icao):
    from dash import ctx
    if not icao or len(icao) < 3:
        return "", html.Span("ICAOコードを入力してください（例: RJTT）", style={"color":"#e67e22"})

    mode = "arrivals" if ctx.triggered_id == "btn-arrivals" else "departures"
    label = "ARRIVALS" if mode == "arrivals" else "DEPARTURES"
    data = fetch_airport_flights(icao, mode)

    if not data:
        return "", html.Span(f"{icao.upper()} – データなし（認証エラーまたは該当便なし）",
                             style={"color":"#e67e22"})

    def ts(t):
        if not t:
            return "–"
        return time.strftime("%H:%M", time.gmtime(t))

    rows = []
    for f in data[:20]:
        dep  = f.get("estDepartureAirport") or "?"
        arr  = f.get("estArrivalAirport")   or "?"
        call = (f.get("callsign") or "").strip() or f.get("icao24","?")
        first_seen = ts(f.get("firstSeen"))
        last_seen  = ts(f.get("lastSeen"))
        rows.append(html.Tr([
            html.Td(call,       style={"padding":"4px 12px","color":"#e0f7fa","fontWeight":"bold"}),
            html.Td(dep,        style={"padding":"4px 12px","color":"#7ecfee"}),
            html.Td("→",        style={"padding":"4px 4px", "color":"#4a6fa5"}),
            html.Td(arr,        style={"padding":"4px 12px","color":"#ffd32a"}),
            html.Td(first_seen, style={"padding":"4px 12px","color":"#aaa"}),
            html.Td(last_seen,  style={"padding":"4px 12px","color":"#aaa"}),
        ]))

    table = html.Table([
        html.Thead(html.Tr([
            html.Th("CALLSIGN", style={"padding":"4px 12px","color":"#4a6fa5","fontSize":"10px","letterSpacing":"1px"}),
            html.Th("FROM",     style={"padding":"4px 12px","color":"#4a6fa5","fontSize":"10px","letterSpacing":"1px"}),
            html.Th("",         style={"padding":"4px 4px"}),
            html.Th("TO",       style={"padding":"4px 12px","color":"#4a6fa5","fontSize":"10px","letterSpacing":"1px"}),
            html.Th("FIRST",    style={"padding":"4px 12px","color":"#4a6fa5","fontSize":"10px","letterSpacing":"1px"}),
            html.Th("LAST",     style={"padding":"4px 12px","color":"#4a6fa5","fontSize":"10px","letterSpacing":"1px"}),
        ])),
        html.Tbody(rows),
    ], style={"borderCollapse":"collapse","fontSize":"12px","width":"100%"})

    status = html.Span(
        f"{icao.upper()}  {label}  – last 2h  ({len(data)} flights)",
        style={"color":"#00d2d3"}
    )
    return table, status


# 4. Countdown
@callback(
    Output("next-refresh", "children"),
    Input("countdown-tick", "n_intervals"),
    Input("last-refresh-ts", "data"),
)
def countdown(_, last_ts):
    remaining = max(0, REFRESH_MS / 1000 - (time.time() - (last_ts or time.time())))
    return f"next update in {int(remaining)}s"


if __name__ == "__main__":
    app.run(debug=True, port=8050)
