"""
✈️  Live Global Flight Tracker
Real-time aircraft positions via OpenSky Network API
"""

import os
import time
import requests
from dotenv import load_dotenv
from dash import Dash, dcc, html, Input, Output, callback
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


def fetch_states():
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
            "alt":      round(s[7] / 0.3048) if s[7] else None,
            "velocity": round(s[9] * 1.94384) if s[9] else None,
            "heading":  round(s[10]) if s[10] else None,
        })

    return flights if flights else _demo_flights()


def _demo_flights():
    import random, math
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
        alt_str = f"{f['alt']:,} ft"   if f["alt"]      else "N/A"
        spd_str = f"{f['velocity']} kts" if f["velocity"] else "N/A"
        hdg_str = f"{f['heading']}°"   if f["heading"]   else "N/A"
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
        height=640,
        font=dict(family="'Courier New', monospace", color="#aaa"),
    )
    return fig


# ── Dash app ───────────────────────────────────────────────────────────────────
app = Dash(__name__, title="✈ Live Flight Tracker")
REFRESH_MS = 30_000

app.layout = html.Div(
    style={"backgroundColor":"#060d17","minHeight":"100vh",
           "fontFamily":"'Courier New', monospace","color":"#ccc"},
    children=[
        # Header
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

        # Legend
        html.Div(style={
            "padding":"8px 32px","backgroundColor":"#0a1628",
            "borderBottom":"1px solid rgba(255,255,255,0.05)",
            "display":"flex","alignItems":"center","gap":"16px","fontSize":"11px","color":"#4a6fa5",
        }, children=[
            html.Span("ALTITUDE COLOR", style={"letterSpacing":"2px"}),
            html.Div(style={
                "width":"180px","height":"8px","borderRadius":"4px",
                "background":"linear-gradient(90deg,rgb(0,0,255),rgb(128,80,128),rgb(255,160,0))",
            }),
            html.Span("LOW → HIGH"),
            html.Span("▲ = aircraft", style={"marginLeft":"auto","color":"#888"}),
        ]),

        # Map
        dcc.Graph(
            id="live-map",
            figure=build_figure(_demo_flights()),
            config={"scrollZoom":True,"displayModeBar":True,"displaylogo":False,
                    "modeBarButtonsToRemove":["select2d","lasso2d"]},
            style={"height":"640px"},
        ),

        # Status bar
        html.Div(style={
            "padding":"10px 32px","backgroundColor":"#0a1628",
            "borderTop":"1px solid rgba(0,200,255,0.1)",
            "display":"flex","alignItems":"center","justifyContent":"space-between",
        }, children=[
            html.Div(id="status-bar", style={"fontSize":"12px","color":"#4a9fd4"}),
            html.Div(id="next-refresh", style={"fontSize":"11px","color":"#4a6fa5"}),
        ]),

        dcc.Interval(id="interval",       interval=REFRESH_MS, n_intervals=0),
        dcc.Interval(id="countdown-tick", interval=1_000,      n_intervals=0),
        dcc.Store(id="last-refresh-ts", data=time.time()),
    ]
)


@callback(
    Output("live-map",         "figure"),
    Output("header-stats",     "children"),
    Output("status-bar",       "children"),
    Output("last-refresh-ts",  "data"),
    Input("interval", "n_intervals"),
)
def refresh_map(_):
    flights  = fetch_states()
    is_demo  = all(f["country"] == "Demo" for f in flights)
    fig      = build_figure(flights)
    countries = len({f["country"] for f in flights})
    avg_alt   = sum(f["alt"] or 0 for f in flights) / max(len(flights), 1)
    stats    = f"{len(flights):,} aircraft  ·  {countries} countries  ·  avg alt {avg_alt:,.0f} ft"
    mode     = "⚠ DEMO MODE – add credentials to .env" if is_demo else "● LIVE  OpenSky Network"
    color    = "#e67e22" if is_demo else "#00d2d3"
    return fig, stats, html.Span(mode, style={"color": color}), time.time()


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
