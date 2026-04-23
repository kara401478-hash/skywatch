"""
✈️  Live Global Flight Tracker
Features: country/altitude filter, airport arrivals/departures, single aircraft tracking
"""

import os
import time
import requests
from collections import deque
from dotenv import load_dotenv
from dash import Dash, dcc, html, Input, Output, State, callback
import plotly.graph_objects as go
from plotly.subplots import make_subplots

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


# ── Flight data cache ──────────────────────────────────────────────────────────
_flights_cache = []

# Tracking history: icao24 -> deque of {ts, lat, lon, alt, velocity, heading}
_track_history = {}
MAX_TRACK_POINTS = 60  # keep last 60 snapshots (~1 hour at 1min interval)

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

    # Update track history for all aircraft
    ts_now = time.time()
    for f in _flights_cache:
        icao = f["icao24"]
        if icao not in _track_history:
            _track_history[icao] = deque(maxlen=MAX_TRACK_POINTS)
        _track_history[icao].append({
            "ts":       ts_now,
            "lat":      f["lat"],
            "lon":      f["lon"],
            "alt":      f["alt"],
            "velocity": f["velocity"],
            "heading":  f["heading"],
        })

    return _flights_cache


def fetch_airport_flights(icao_airport, mode="arrivals"):
    token = get_token()
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    end   = int(time.time())
    begin = end - 7200
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


_photo_cache = {}  # icao24 -> {url, photographer, thumbnail} or None

def fetch_aircraft_photo(icao24):
    """Fetch aircraft photo from Planespotters.net by ICAO24 hex."""
    if icao24 in _photo_cache:
        return _photo_cache[icao24]
    try:
        resp = requests.get(
            f"https://api.planespotters.net/pub/photos/hex/{icao24}",
            timeout=8,
            headers={"User-Agent": "skywatch-flight-tracker/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        photos = data.get("photos", [])
        if photos:
            p = photos[0]
            result = {
                "url":          p.get("link", ""),
                "thumbnail":    p.get("thumbnail", {}).get("src", ""),
                "large":        p.get("thumbnail_large", {}).get("src", "") or p.get("thumbnail", {}).get("src", ""),
                "photographer": p.get("photographer", "Unknown"),
                "aircraft":     p.get("aircraft", {}).get("model", ""),
                "airline":      p.get("airline", {}).get("name", ""),
            }
        else:
            result = None
        _photo_cache[icao24] = result
        return result
    except Exception as e:
        print(f"[Photo error] {e}")
        _photo_cache[icao24] = None
        return None


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


def build_main_figure(flights, tracked_icao=None):
    if not flights:
        flights = [{"lon":0,"lat":0,"alt":0,"callsign":"","country":"","velocity":None,"heading":None,"icao24":""}]

    lons  = [f["lon"] for f in flights]
    lats  = [f["lat"] for f in flights]
    alts  = [f["alt"] or 0 for f in flights]
    max_a = max(alts) if alts else 45000

    def alt_color(a):
        n = max(0.0, min(1.0, a / max_a))
        return f"rgb({int(n*255)},{int(n*160)},{int((1-n)*255)})"

    colors = [alt_color(a) for a in alts]
    sizes  = [9 if f.get("icao24") == tracked_icao else 5 for f in flights]

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

    # Draw track trail if tracking
    if tracked_icao and tracked_icao in _track_history:
        history = list(_track_history[tracked_icao])
        if len(history) > 1:
            fig.add_trace(go.Scattergeo(
                lon=[p["lon"] for p in history],
                lat=[p["lat"] for p in history],
                mode="lines",
                line=dict(width=2, color="rgba(255, 215, 0, 0.6)"),
                hoverinfo="skip",
                showlegend=False,
            ))

    # All aircraft
    fig.add_trace(go.Scattergeo(
        lon=lons, lat=lats,
        mode="markers",
        marker=dict(
            size=sizes,
            color=["#ffd700" if f.get("icao24") == tracked_icao else alt_color(f["alt"] or 0) for f in flights],
            opacity=0.85,
            symbol="triangle-up",
            line=dict(width=0.3, color="rgba(255,255,255,0.2)"),
        ),
        text=hover_texts,
        customdata=[f.get("icao24","") for f in flights],
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
        height=520,
        font=dict(family="'Courier New', monospace", color="#aaa"),
        clickmode="event",
    )
    return fig


def build_track_graphs(icao24):
    """Build altitude + speed time-series charts for a tracked aircraft."""
    history = list(_track_history.get(icao24, []))
    if len(history) < 2:
        return go.Figure().update_layout(
            paper_bgcolor="#0a1628", plot_bgcolor="#0a1628",
            margin=dict(l=40,r=20,t=30,b=20), height=180,
            annotations=[dict(text="データ収集中… (1分ごとに更新)",
                              x=0.5, y=0.5, xref="paper", yref="paper",
                              showarrow=False, font=dict(color="#4a6fa5", size=12))],
        )

    ts_labels = [time.strftime("%H:%M", time.gmtime(p["ts"])) for p in history]
    alts      = [p["alt"]      or 0 for p in history]
    speeds    = [p["velocity"] or 0 for p in history]

    fig = make_subplots(rows=1, cols=2,
                        subplot_titles=("ALTITUDE (ft)", "SPEED (kts)"))

    fig.add_trace(go.Scatter(
        x=ts_labels, y=alts,
        mode="lines+markers",
        line=dict(color="#00d2d3", width=2),
        marker=dict(size=4),
        fill="tozeroy",
        fillcolor="rgba(0,210,211,0.08)",
        name="Alt",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=ts_labels, y=speeds,
        mode="lines+markers",
        line=dict(color="#ffd32a", width=2),
        marker=dict(size=4),
        fill="tozeroy",
        fillcolor="rgba(255,211,42,0.08)",
        name="Speed",
    ), row=1, col=2)

    fig.update_layout(
        paper_bgcolor="#0a1628",
        plot_bgcolor="#0a1628",
        margin=dict(l=50, r=20, t=30, b=30),
        height=180,
        showlegend=False,
        font=dict(family="'Courier New', monospace", color="#888", size=10),
    )
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.05)", tickfont=dict(size=9))
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.05)", tickfont=dict(size=9))
    for ann in fig.layout.annotations:
        ann.font.color = "#4a6fa5"
        ann.font.size  = 10

    return fig


# ── Dash app ───────────────────────────────────────────────────────────────────
app = Dash(__name__, title="✈ Live Flight Tracker")
REFRESH_MS = 60_000

fetch_states()

app.layout = html.Div(
    style={"backgroundColor":"#060d17","minHeight":"100vh",
           "fontFamily":"'Courier New', monospace","color":"#ccc"},
    children=[

        # ── Header ────────────────────────────────────────────────────────────
        html.Div(style={
            "background":"linear-gradient(90deg,#060d17 0%,#0a2744 50%,#060d17 100%)",
            "borderBottom":"1px solid rgba(0,200,255,0.2)",
            "padding":"14px 32px","display":"flex","alignItems":"center","justifyContent":"space-between",
        }, children=[
            html.Div([
                html.Span("✈", style={"fontSize":"24px","marginRight":"10px","color":"#00d2d3"}),
                html.Span("LIVE FLIGHT TRACKER", style={
                    "fontSize":"17px","fontWeight":"bold","letterSpacing":"4px","color":"#e0f7fa"}),
            ]),
            html.Div(id="header-stats", style={"fontSize":"11px","color":"#4a6fa5","letterSpacing":"1px"}),
        ]),

        # ── Filter bar ────────────────────────────────────────────────────────
        html.Div(style={
            "padding":"8px 32px","backgroundColor":"#0a1628",
            "borderBottom":"1px solid rgba(255,255,255,0.05)",
            "display":"flex","alignItems":"center","gap":"20px","flexWrap":"wrap",
        }, children=[
            html.Div([
                html.Label("COUNTRY", style={"fontSize":"10px","letterSpacing":"2px","color":"#4a6fa5","display":"block","marginBottom":"3px"}),
                dcc.Dropdown(id="filter-country", options=[], placeholder="All…", multi=True, clearable=True,
                    style={"width":"240px","backgroundColor":"#0d1b2a","fontSize":"12px",
                           "border":"1px solid rgba(0,200,255,0.2)","borderRadius":"4px"}),
            ]),
            html.Div([
                html.Label("ALTITUDE (ft)", style={"fontSize":"10px","letterSpacing":"2px","color":"#4a6fa5","display":"block","marginBottom":"3px"}),
                html.Div(style={"display":"flex","alignItems":"center","gap":"6px"}, children=[
                    dcc.Input(id="alt-min", type="number", placeholder="Min", value=0,
                        style={"width":"75px","backgroundColor":"#0d1b2a","color":"#ccc",
                               "border":"1px solid rgba(0,200,255,0.2)","borderRadius":"4px","padding":"4px 6px","fontSize":"12px"}),
                    html.Span("–", style={"color":"#4a6fa5"}),
                    dcc.Input(id="alt-max", type="number", placeholder="Max", value=60000,
                        style={"width":"75px","backgroundColor":"#0d1b2a","color":"#ccc",
                               "border":"1px solid rgba(0,200,255,0.2)","borderRadius":"4px","padding":"4px 6px","fontSize":"12px"}),
                ]),
            ]),
            html.Button("APPLY", id="btn-filter",
                style={"marginTop":"14px","backgroundColor":"rgba(0,200,255,0.1)","color":"#00d2d3",
                       "border":"1px solid rgba(0,200,255,0.3)","borderRadius":"4px",
                       "padding":"5px 14px","fontSize":"11px","letterSpacing":"2px","cursor":"pointer"}),
            html.Button("RESET", id="btn-reset",
                style={"marginTop":"14px","backgroundColor":"transparent","color":"#4a6fa5",
                       "border":"1px solid rgba(255,255,255,0.1)","borderRadius":"4px",
                       "padding":"5px 10px","fontSize":"11px","cursor":"pointer"}),
            html.Div(id="filter-count", style={"marginTop":"14px","fontSize":"11px","color":"#4a9fd4"}),
        ]),

        # ── Altitude legend ───────────────────────────────────────────────────
        html.Div(style={
            "padding":"5px 32px","backgroundColor":"#080f1c",
            "borderBottom":"1px solid rgba(255,255,255,0.04)",
            "display":"flex","alignItems":"center","gap":"10px","fontSize":"10px","color":"#4a6fa5",
        }, children=[
            html.Span("ALTITUDE", style={"letterSpacing":"2px"}),
            html.Div(style={"width":"120px","height":"5px","borderRadius":"3px",
                "background":"linear-gradient(90deg,rgb(0,0,255),rgb(128,80,128),rgb(255,160,0))"}),
            html.Span("LOW → HIGH"),
            html.Span("🟡 = tracking", style={"marginLeft":"auto","color":"#ffd700","fontSize":"10px"}),
            html.Span("▲ = aircraft", style={"marginLeft":"12px","color":"#555"}),
        ]),

        # ── Map ───────────────────────────────────────────────────────────────
        dcc.Graph(
            id="live-map",
            figure=build_main_figure(_flights_cache),
            config={"scrollZoom":True,"displayModeBar":True,"displaylogo":False,
                    "modeBarButtonsToRemove":["select2d","lasso2d"]},
            style={"height":"520px"},
        ),

        # ── Tracking panel ────────────────────────────────────────────────────
        html.Div(style={
            "backgroundColor":"#0a1628",
            "borderTop":"1px solid rgba(255,215,0,0.2)",
            "padding":"10px 32px",
        }, children=[
            html.Div(style={"display":"flex","alignItems":"center","justifyContent":"space-between","marginBottom":"6px"}, children=[
                html.Div(id="track-header", style={"fontSize":"12px","color":"#ffd700","letterSpacing":"1px"},
                         children="▲ 地図の飛行機をクリックすると追跡開始"),
                html.Button("× STOP TRACKING", id="btn-stop-track",
                    style={"backgroundColor":"transparent","color":"#4a6fa5","border":"1px solid rgba(255,255,255,0.1)",
                           "borderRadius":"4px","padding":"3px 10px","fontSize":"10px","cursor":"pointer"}),
            ]),
            # グラフ + 写真 横並び
            html.Div(style={"display":"flex","gap":"20px","alignItems":"flex-start"}, children=[
                html.Div(style={"flex":"1"}, children=[
                    dcc.Graph(id="track-graphs",
                        figure=go.Figure().update_layout(
                            paper_bgcolor="#0a1628", plot_bgcolor="#0a1628",
                            margin=dict(l=40,r=20,t=10,b=20), height=180,
                            annotations=[dict(text="飛行機をクリックして追跡開始",
                                              x=0.5,y=0.5,xref="paper",yref="paper",
                                              showarrow=False,font=dict(color="#4a6fa5",size=12))],
                        ),
                        config={"displayModeBar":False},
                        style={"height":"180px"}),
                ]),
                # 写真パネル
                html.Div(id="aircraft-photo-panel", style={
                    "width":"260px","minWidth":"260px",
                    "backgroundColor":"#080f1c",
                    "border":"1px solid rgba(255,215,0,0.15)",
                    "borderRadius":"6px",
                    "overflow":"hidden",
                    "display":"flex","flexDirection":"column",
                }),
            ]),
        ]),

        # ── Airport panel ─────────────────────────────────────────────────────
        html.Div(style={"padding":"10px 32px","backgroundColor":"#080f1c",
                        "borderTop":"1px solid rgba(0,200,255,0.08)"}, children=[
            html.Div(style={"display":"flex","alignItems":"center","gap":"14px","flexWrap":"wrap"}, children=[
                html.Label("AIRPORT ICAO", style={"fontSize":"10px","letterSpacing":"2px","color":"#4a6fa5"}),
                dcc.Input(id="airport-icao", type="text", placeholder="例: RJTT  KJFK  EGLL", maxLength=4,
                    style={"width":"200px","backgroundColor":"#0d1b2a","color":"#e0f7fa",
                           "border":"1px solid rgba(0,200,255,0.25)","borderRadius":"4px",
                           "padding":"4px 10px","fontSize":"13px","letterSpacing":"2px"}),
                html.Button("ARRIVALS",   id="btn-arrivals",
                    style={"backgroundColor":"rgba(0,200,255,0.12)","color":"#00d2d3",
                           "border":"1px solid rgba(0,200,255,0.3)","borderRadius":"4px",
                           "padding":"4px 12px","fontSize":"11px","cursor":"pointer"}),
                html.Button("DEPARTURES", id="btn-departures",
                    style={"backgroundColor":"rgba(255,200,0,0.1)","color":"#ffd32a",
                           "border":"1px solid rgba(255,200,0,0.3)","borderRadius":"4px",
                           "padding":"4px 12px","fontSize":"11px","cursor":"pointer"}),
                html.Div(id="airport-status", style={"fontSize":"11px","color":"#4a6fa5"}),
            ]),
            html.Div(id="airport-table", style={"marginTop":"8px"}),
        ]),

        # ── Status bar ────────────────────────────────────────────────────────
        html.Div(style={
            "padding":"7px 32px","backgroundColor":"#060d17",
            "borderTop":"1px solid rgba(255,255,255,0.04)",
            "display":"flex","alignItems":"center","justifyContent":"space-between",
        }, children=[
            html.Div(id="status-bar",   style={"fontSize":"11px","color":"#4a9fd4"}),
            html.Div(id="next-refresh", style={"fontSize":"11px","color":"#4a6fa5"}),
        ]),

        dcc.Interval(id="interval",       interval=REFRESH_MS, n_intervals=0),
        dcc.Interval(id="countdown-tick", interval=1_000,      n_intervals=0),
        dcc.Store(id="last-refresh-ts",   data=time.time()),
        dcc.Store(id="filtered-flights",  data=[]),
        dcc.Store(id="tracked-icao",      data=None),
    ]
)


# ── Callbacks ──────────────────────────────────────────────────────────────────

# 1. Click on map → store tracked icao
@callback(
    Output("tracked-icao", "data"),
    Input("live-map",      "clickData"),
    Input("btn-stop-track","n_clicks"),
    State("tracked-icao",  "data"),
    prevent_initial_call=True,
)
def handle_click(click_data, stop_clicks, current_icao):
    from dash import ctx
    if ctx.triggered_id == "btn-stop-track":
        return None
    if click_data:
        pts = click_data.get("points", [])
        for pt in pts:
            cd = pt.get("customdata")
            if cd:
                return cd
    return current_icao


# 2. Main map + stats refresh
@callback(
    Output("live-map",         "figure"),
    Output("header-stats",     "children"),
    Output("status-bar",       "children"),
    Output("last-refresh-ts",  "data"),
    Output("filter-country",   "options"),
    Output("filtered-flights", "data"),
    Input("interval",          "n_intervals"),
    Input("btn-filter",        "n_clicks"),
    Input("btn-reset",         "n_clicks"),
    Input("tracked-icao",      "data"),
    State("filter-country",    "value"),
    State("alt-min",           "value"),
    State("alt-max",           "value"),
    prevent_initial_call=False,
)
def refresh_map(n_intervals, n_filter, n_reset, tracked_icao, countries, alt_min, alt_max):
    from dash import ctx
    triggered = ctx.triggered_id

    if triggered in ("interval", "btn-reset", None):
        flights = fetch_states()
    else:
        flights = _flights_cache

    filtered = flights
    if triggered == "btn-filter":
        if countries:
            filtered = [f for f in filtered if f["country"] in countries]
        lo = alt_min if alt_min is not None else 0
        hi = alt_max if alt_max is not None else 60000
        filtered = [f for f in filtered if lo <= (f["alt"] or 0) <= hi]

    fig = build_main_figure(filtered, tracked_icao=tracked_icao)

    is_demo   = all(f["country"] == "Demo" for f in flights)
    n_country = len({f["country"] for f in flights})
    avg_alt   = sum(f["alt"] or 0 for f in flights) / max(len(flights), 1)
    stats     = f"{len(flights):,} aircraft  ·  {n_country} countries  ·  avg alt {avg_alt:,.0f} ft"
    mode      = "⚠ DEMO MODE" if is_demo else "● LIVE  OpenSky Network"
    color     = "#e67e22" if is_demo else "#00d2d3"
    options   = [{"label": c, "value": c} for c in sorted({f["country"] for f in flights})]

    return fig, stats, html.Span(mode, style={"color":color}), time.time(), options, filtered


# 3. Tracking panel update
@callback(
    Output("track-graphs",  "figure"),
    Output("track-header",  "children"),
    Input("tracked-icao",   "data"),
    Input("interval",       "n_intervals"),
)
def update_tracking(tracked_icao, _):
    if not tracked_icao:
        empty = go.Figure().update_layout(
            paper_bgcolor="#0a1628", plot_bgcolor="#0a1628",
            margin=dict(l=40,r=20,t=10,b=20), height=180,
            annotations=[dict(text="飛行機をクリックして追跡開始",
                              x=0.5,y=0.5,xref="paper",yref="paper",
                              showarrow=False,font=dict(color="#4a6fa5",size=12))],
        )
        return empty, "▲ 地図の飛行機をクリックすると追跡開始"

    # Find current data for this aircraft
    current = next((f for f in _flights_cache if f["icao24"] == tracked_icao), None)
    if current:
        arr     = heading_to_arrow(current["heading"])
        alt_str = f"{current['alt']:,} ft"     if current["alt"]      else "N/A"
        spd_str = f"{current['velocity']} kts" if current["velocity"] else "N/A"
        hdg_str = f"{current['heading']}°"     if current["heading"]  else "N/A"
        header  = (
            f"🟡 TRACKING  {current['callsign']}  ({tracked_icao.upper()})  "
            f"{arr}  |  Alt: {alt_str}  Speed: {spd_str}  Hdg: {hdg_str}  |  {current['country']}"
        )
    else:
        header = f"🟡 TRACKING  {tracked_icao.upper()}  (圏外 or 着陸済み)"

    fig = build_track_graphs(tracked_icao)
    return fig, header


# 3b. Aircraft photo panel
@callback(
    Output("aircraft-photo-panel", "children"),
    Input("tracked-icao",          "data"),
)
def update_photo_panel(tracked_icao):
    if not tracked_icao or tracked_icao.startswith("demo"):
        return html.Div(
            "📷 クリックで写真を表示",
            style={"padding":"16px","fontSize":"11px","color":"#4a6fa5",
                   "textAlign":"center","lineHeight":"180px"},
        )

    photo = fetch_aircraft_photo(tracked_icao)

    if not photo:
        return html.Div([
            html.Div("📷", style={"fontSize":"32px","textAlign":"center","padding":"20px 0 8px"}),
            html.Div("写真なし", style={"textAlign":"center","fontSize":"11px","color":"#4a6fa5","paddingBottom":"20px"}),
        ])

    return html.Div([
        # 写真
        html.A(
            html.Img(
                src=photo["large"],
                style={"width":"100%","display":"block","objectFit":"cover","maxHeight":"160px"},
            ),
            href=photo["url"],
            target="_blank",
        ),
        # メタ情報
        html.Div(style={"padding":"8px 10px"}, children=[
            html.Div(photo["aircraft"] or "Unknown type",
                     style={"fontSize":"12px","color":"#e0f7fa","fontWeight":"bold","marginBottom":"2px"}),
            html.Div(photo["airline"] or "",
                     style={"fontSize":"11px","color":"#7ecfee","marginBottom":"4px"}),
            html.Div(f"📸 {photo['photographer']}",
                     style={"fontSize":"10px","color":"#4a6fa5"}),
        ]),
    ])



@callback(
    Output("filter-count","children"),
    Input("filtered-flights","data"),
)
def update_filter_count(filtered):
    n = len(filtered) if filtered else len(_flights_cache)
    total = len(_flights_cache)
    if n == total:
        return ""
    return f"showing {n:,} / {total:,}"


# 5. Airport arrivals/departures
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
    mode  = "arrivals" if ctx.triggered_id == "btn-arrivals" else "departures"
    label = "ARRIVALS" if mode == "arrivals" else "DEPARTURES"
    data  = fetch_airport_flights(icao, mode)
    if not data:
        return "", html.Span(f"{icao.upper()} – データなし", style={"color":"#e67e22"})

    def ts(t):
        return time.strftime("%H:%M", time.gmtime(t)) if t else "–"

    rows = [html.Tr([
        html.Td((f.get("callsign") or f.get("icao24","?")).strip(), style={"padding":"3px 10px","color":"#e0f7fa","fontWeight":"bold"}),
        html.Td(f.get("estDepartureAirport") or "?",                style={"padding":"3px 10px","color":"#7ecfee"}),
        html.Td("→",                                                 style={"padding":"3px 4px","color":"#4a6fa5"}),
        html.Td(f.get("estArrivalAirport")   or "?",                style={"padding":"3px 10px","color":"#ffd32a"}),
        html.Td(ts(f.get("firstSeen")),                              style={"padding":"3px 10px","color":"#aaa"}),
        html.Td(ts(f.get("lastSeen")),                               style={"padding":"3px 10px","color":"#aaa"}),
    ]) for f in data[:20]]

    table = html.Table([
        html.Thead(html.Tr([
            html.Th(h, style={"padding":"3px 10px","color":"#4a6fa5","fontSize":"10px","letterSpacing":"1px"})
            for h in ["CALLSIGN","FROM","","TO","FIRST","LAST"]
        ])),
        html.Tbody(rows),
    ], style={"borderCollapse":"collapse","fontSize":"12px","width":"100%"})

    return table, html.Span(f"{icao.upper()}  {label}  – last 2h  ({len(data)} flights)", style={"color":"#00d2d3"})


# 6. Countdown
@callback(
    Output("next-refresh","children"),
    Input("countdown-tick","n_intervals"),
    Input("last-refresh-ts","data"),
)
def countdown(_, last_ts):
    remaining = max(0, REFRESH_MS/1000 - (time.time() - (last_ts or time.time())))
    return f"next update in {int(remaining)}s"


if __name__ == "__main__":
    app.run(debug=True, port=8050)
