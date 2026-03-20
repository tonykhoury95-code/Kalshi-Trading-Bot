"""
Kalshi Sports Arbitrage Terminal — Dashboard

Run with:
    uv run streamlit run dashboard.py

Then open: http://localhost:8501
"""

import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = Path("./data/ledger.db")
LOG_DIR = Path("./logs")
REFRESH_SECONDS = 15

st.set_page_config(
    page_title="Sports Arb Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# CSS — dark trading-terminal theme
# ---------------------------------------------------------------------------
st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"] {
    background-color: #0d0f14;
    color: #e2e8f0;
}
[data-testid="stSidebar"] {
    background-color: #111318;
    border-right: 1px solid #1e2330;
}

/* ── Summary bar ── */
.summary-bar { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }
.stat-card {
    background:#161b26; border:1px solid #1e2a3a; border-radius:8px;
    padding:10px 18px; min-width:110px; flex:1;
}
.stat-card .label { font-size:11px; color:#6b7a99; text-transform:uppercase; letter-spacing:.06em; margin-bottom:4px; }
.stat-card .value { font-size:22px; font-weight:700; color:#e2e8f0; line-height:1.1; }
.stat-card .value.green  { color:#22c55e; }
.stat-card .value.red    { color:#ef4444; }
.stat-card .value.yellow { color:#f59e0b; }
.stat-card .value.blue   { color:#3b82f6; }

/* ── Arb type badges ── */
.badge { display:inline-block; padding:2px 10px; border-radius:4px; font-size:12px; font-weight:700; white-space:nowrap; }
.badge-pure   { background:#2d0000; color:#f87171; border:1px solid #8b0000; }
.badge-near   { background:#2d1b00; color:#fbbf24; border:1px solid #7a5000; }
.badge-ev     { background:#0b2238; color:#38bdf8; border:1px solid #0e4a7a; }
.badge-sport  { background:#0c1a2d; color:#60a5fa; border:1px solid #1e4a80; }
.badge-yes    { background:#0d200f; color:#4ade80; border:1px solid #166534; }
.badge-no     { background:#200d0d; color:#f87171; border:1px solid #7f1d1d; }

/* ── Opportunity cards ── */
.opp-card {
    background:#161b26; border:1px solid #1e2a3a; border-radius:8px;
    padding:14px 18px; margin:6px 0;
}
.opp-card.pure-arb  { border-left:4px solid #ef4444; }
.opp-card.near-arb  { border-left:4px solid #f59e0b; }
.opp-card.ev-card   { border-left:4px solid #38bdf8; }
.opp-card-header { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; }
.opp-ticker { font-size:15px; font-weight:700; color:#e2e8f0; }
.opp-arb-pct { font-size:18px; font-weight:800; }
.opp-arb-pct.positive { color:#22c55e; }
.opp-arb-pct.negative { color:#f59e0b; }
.opp-stats { display:flex; gap:20px; margin-top:10px; flex-wrap:wrap; }
.opp-stat .s-label { font-size:10px; color:#6b7a99; text-transform:uppercase; letter-spacing:.05em; }
.opp-stat .s-val   { font-size:14px; font-weight:600; color:#e2e8f0; }

/* ── Friction bar ── */
.friction-row { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; font-size:12px; }
.friction-pill { padding:2px 8px; border-radius:4px; background:#1e2a3a; color:#94a3b8; }
.friction-total { color:#ef4444; font-weight:700; }

/* ── Scanner status ── */
.scanner-running { background:#0d2010; border:1px solid #22c55e; color:#22c55e; border-radius:6px; padding:8px 16px; font-weight:600; font-size:13px; margin-bottom:10px; }
.scanner-stopped { background:#1a1a1a; border:1px solid #4b5672; color:#6b7a99; border-radius:6px; padding:8px 16px; font-weight:600; font-size:13px; margin-bottom:10px; }

/* ── Metrics ── */
[data-testid="stMetric"] {
    background:#161b26; border:1px solid #1e2a3a; border-radius:8px; padding:12px 16px;
}
[data-testid="stMetricLabel"] { color:#6b7a99 !important; font-size:11px !important; text-transform:uppercase; letter-spacing:.05em; }
[data-testid="stMetricValue"] { color:#e2e8f0 !important; font-size:20px !important; font-weight:700 !important; }

/* ── Tabs ── */
[data-testid="stTabs"] [data-testid="stTabsList"] button {
    color:#6b7a99; font-size:13px; font-weight:500;
    background:transparent; border-bottom:2px solid transparent; padding:8px 14px;
}
[data-testid="stTabs"] [data-testid="stTabsList"] button[aria-selected="true"] {
    color:#e2e8f0; border-bottom-color:#3b82f6;
}

/* ── Misc ── */
[data-testid="stDataFrame"] { border:1px solid #1e2a3a; border-radius:8px; }
hr { border-color:#1e2330 !important; }
.stTextArea textarea {
    background:#0d0f14 !important; color:#94a3b8 !important;
    font-family:'JetBrains Mono',monospace !important; font-size:12px !important;
    border:1px solid #1e2330 !important;
}
[data-testid="stExpander"] { background:#161b26; border:1px solid #1e2330; border-radius:8px; }
.sidebar-section {
    font-size:10px; text-transform:uppercase; letter-spacing:.1em;
    color:#4b5672; font-weight:700; margin:16px 0 6px;
}
.stButton > button {
    background:#1e2330; color:#94a3b8; border:1px solid #2d3448;
    border-radius:6px; font-size:13px; padding:4px 12px; transition:all .15s;
}
.stButton > button:hover { background:#252d42; color:#e2e8f0; border-color:#3b82f6; }
.stButton > button[data-testid="baseButton-primary"] {
    background:#1e4a1e; color:#4ade80; border-color:#22c55e;
}
.stButton > button[data-testid="baseButton-primary"]:hover {
    background:#16641e; color:#86efac;
}
.config-card {
    background:#161b26; border:1px solid #1e2a3a; border-radius:8px;
    padding:14px 18px; margin:8px 0;
}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _db_connect() -> Optional[sqlite3.Connection]:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _query_df(sql: str, params: Optional[list] = None) -> pd.DataFrame:
    conn = _db_connect()
    if conn is None:
        return pd.DataFrame()
    try:
        return pd.read_sql_query(sql, conn, params=params or [])
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_arb_opps(
    latest_only: bool = False,
    scan_id: Optional[str] = None,
    sport: Optional[str] = None,
    arb_type: Optional[str] = None,
    limit: int = 500,
) -> pd.DataFrame:
    conn = _db_connect()
    if conn is None:
        return pd.DataFrame()
    try:
        clauses, params = [], []
        if latest_only and scan_id is None:
            row = conn.execute(
                "SELECT scan_id FROM arb_opportunities ORDER BY detected_at DESC LIMIT 1"
            ).fetchone()
            if row:
                scan_id = row["scan_id"]
        if scan_id:
            clauses.append("scan_id = ?"); params.append(scan_id)
        if sport:
            clauses.append("sport = ?"); params.append(sport)
        if arb_type:
            clauses.append("arb_type = ?"); params.append(arb_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        return pd.read_sql_query(
            f"SELECT * FROM arb_opportunities {where} ORDER BY arb_pct DESC LIMIT ?",
            conn, params=params,
        )
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_arb_results(
    latest_only: bool = False,
    status: Optional[str] = None,
    sport: Optional[str] = None,
    limit: int = 300,
) -> pd.DataFrame:
    conn = _db_connect()
    if conn is None:
        return pd.DataFrame()
    try:
        clauses, params = [], []
        if latest_only:
            row = conn.execute(
                "SELECT scan_id FROM arb_results ORDER BY detected_at DESC LIMIT 1"
            ).fetchone()
            if row:
                clauses.append("scan_id = ?"); params.append(row["scan_id"])
        if status:
            clauses.append("status = ?"); params.append(status)
        if sport:
            clauses.append("sport = ?"); params.append(sport)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        return pd.read_sql_query(
            f"SELECT * FROM arb_results {where} ORDER BY net_arb_pct DESC LIMIT ?",
            conn, params=params,
        )
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_ev_candidates(
    latest_only: bool = False,
    sport: Optional[str] = None,
    min_edge: float = 0.0,
    limit: int = 300,
) -> pd.DataFrame:
    conn = _db_connect()
    if conn is None:
        return pd.DataFrame()
    try:
        clauses, params = [], []
        if latest_only:
            row = conn.execute(
                "SELECT scan_id FROM ev_candidates ORDER BY detected_at DESC LIMIT 1"
            ).fetchone()
            if row:
                clauses.append("scan_id = ?"); params.append(row["scan_id"])
        if sport:
            clauses.append("sport = ?"); params.append(sport)
        if min_edge > 0:
            clauses.append("best_edge_pct >= ?"); params.append(min_edge)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        return pd.read_sql_query(
            f"SELECT * FROM ev_candidates {where} ORDER BY best_edge_pct DESC LIMIT ?",
            conn, params=params,
        )
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _load_scan_ids(limit: int = 50) -> list[str]:
    conn = _db_connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """SELECT scan_id, MAX(detected_at) as last_seen
               FROM arb_opportunities GROUP BY scan_id
               ORDER BY last_seen DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [r["scan_id"] for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _scan_summary_stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"pure": 0, "near": 0, "best_pct": 0.0, "best_ticker": "—", "sports": 0}
    pure = (df["arb_type"] == "pure_arb").sum() if "arb_type" in df.columns else 0
    near = (df["arb_type"] == "near_arb").sum() if "arb_type" in df.columns else 0
    best_row = df.iloc[0]
    return {
        "pure": int(pure),
        "near": int(near),
        "best_pct": float(best_row.get("arb_pct", 0.0)),
        "best_ticker": str(best_row.get("ticker", "—")),
        "sports": df["sport"].nunique() if "sport" in df.columns else 0,
    }


# ---------------------------------------------------------------------------
# Scanner subprocess management
# ---------------------------------------------------------------------------

def _scanner_running() -> bool:
    proc = st.session_state.get("scanner_proc")
    return proc is not None and proc.poll() is None


def _launch_scanner(sports: list[str], threshold: float, interval: int, live_only: bool) -> None:
    cmd = [sys.executable, "arbitrage_scanner.py"]
    if sports:
        cmd += ["--sports", ",".join(sports)]
    cmd += ["--near-threshold", str(threshold), "--interval", str(interval)]
    if live_only:
        cmd += ["--live-only"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=os.environ.copy())
    st.session_state["scanner_proc"] = proc
    st.session_state["scanner_started_at"] = datetime.now(timezone.utc).isoformat()
    st.session_state["scanner_cmd"] = " ".join(cmd)


def _stop_scanner() -> None:
    proc = st.session_state.get("scanner_proc")
    if proc and proc.poll() is None:
        proc.terminate()
    st.session_state["scanner_proc"] = None
    st.session_state["scanner_started_at"] = None


# ---------------------------------------------------------------------------
# Badge helpers
# ---------------------------------------------------------------------------

def _arb_badge(arb_type: str) -> str:
    if arb_type == "pure_arb":
        return '<span class="badge badge-pure">PURE ARB</span>'
    if arb_type == "near_arb":
        return '<span class="badge badge-near">NEAR ARB</span>'
    return '<span class="badge badge-near">ARB</span>'


def _status_badge(status: str) -> str:
    if status == "pure_arb":
        return '<span class="badge badge-pure">PURE ARB</span>'
    if status == "near_arb":
        return '<span class="badge badge-near">NEAR ARB</span>'
    return '<span class="badge" style="background:#1a1a2e;color:#6b7a99;border:1px solid #2d3448;">REJECTED</span>'


def _sport_badge(sport: str) -> str:
    return f'<span class="badge badge-sport">{sport}</span>'


def _side_badge(side: str) -> str:
    cls = "badge-yes" if side == "YES" else "badge-no"
    return f'<span class="badge {cls}">{side}</span>'


# ---------------------------------------------------------------------------
# Summary bar
# ---------------------------------------------------------------------------

def _render_summary_bar() -> None:
    df = _load_arb_opps(latest_only=True)
    stats = _scan_summary_stats(df)
    ev_df = _load_ev_candidates(latest_only=True)
    scanner_on = _scanner_running()
    status_label = "RUNNING" if scanner_on else "STOPPED"
    status_class = "green" if scanner_on else "red"

    st.markdown(
        f"""<div class="summary-bar">
          <div class="stat-card">
            <div class="label">Scanner</div>
            <div class="value {status_class}">{status_label}</div>
          </div>
          <div class="stat-card">
            <div class="label">Pure Arbs</div>
            <div class="value {'red' if stats['pure'] > 0 else ''}">{stats['pure']}</div>
          </div>
          <div class="stat-card">
            <div class="label">Near-Arbs</div>
            <div class="value {'yellow' if stats['near'] > 0 else ''}">{stats['near']}</div>
          </div>
          <div class="stat-card">
            <div class="label">EV Candidates</div>
            <div class="value {'blue' if len(ev_df) > 0 else ''}">{len(ev_df)}</div>
          </div>
          <div class="stat-card">
            <div class="label">Best Edge</div>
            <div class="value {'green' if stats['best_pct'] > 0 else 'yellow'}">{stats['best_pct']:+.2f}%</div>
          </div>
          <div class="stat-card">
            <div class="label">Best Ticker</div>
            <div class="value blue" style="font-size:14px;">{stats['best_ticker'][:22]}</div>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def _render_sidebar() -> dict:
    with st.sidebar:
        st.markdown("## ⚡ Sports Arb Terminal")
        st.markdown("---")

        st.markdown('<div class="sidebar-section">Scanner Settings</div>', unsafe_allow_html=True)

        sports_options = ["NBA", "NFL", "MLB", "NHL", "UFC", "SOC", "TEN"]
        selected_sports = st.multiselect(
            "Sports to scan", options=sports_options,
            default=st.session_state.get("cfg_sports", sports_options), key="cfg_sports",
        )
        threshold = st.slider(
            "Near-arb threshold %", min_value=0.5, max_value=10.0, step=0.5,
            value=st.session_state.get("cfg_threshold", 2.0), key="cfg_threshold",
            help="Flag markets within this % of breakeven (100¢ total cost).",
        )
        interval = st.slider(
            "Scan interval (seconds)", min_value=10, max_value=300, step=10,
            value=st.session_state.get("cfg_interval", 30), key="cfg_interval",
        )
        live_only = st.toggle(
            "Live / in-play markets only",
            value=st.session_state.get("cfg_live_only", False), key="cfg_live_only",
        )

        st.markdown("---")

        if _scanner_running():
            st.markdown('<div class="scanner-running">● Scanner running</div>', unsafe_allow_html=True)
            started = st.session_state.get("scanner_started_at", "")
            if started:
                st.caption(f"Started: {started[:19].replace('T', ' ')} UTC")
            if st.button("Stop Scanner", use_container_width=True):
                _stop_scanner(); st.rerun()
        else:
            st.markdown('<div class="scanner-stopped">○ Scanner stopped</div>', unsafe_allow_html=True)
            if st.button("Start Scanner", type="primary", use_container_width=True):
                _launch_scanner(sports=selected_sports, threshold=threshold,
                                interval=interval, live_only=live_only)
                st.rerun()

        st.markdown("---")
        st.markdown('<div class="sidebar-section">Arb Filters</div>', unsafe_allow_html=True)

        filter_sport = st.selectbox(
            "Sport", options=["All"] + sports_options, index=0, key="filter_sport"
        )
        filter_type = st.selectbox(
            "Arb type", options=["All", "Pure Arb only", "Near Arb only"],
            index=0, key="filter_type"
        )
        min_vol = st.number_input(
            "Min volume", min_value=0, max_value=100_000, step=100,
            value=st.session_state.get("filter_min_vol", 0), key="filter_min_vol",
        )

        st.markdown('<div class="sidebar-section">EV Filters</div>', unsafe_allow_html=True)

        ev_min_edge = st.slider(
            "Min EV edge %", min_value=0.0, max_value=20.0, step=0.5,
            value=st.session_state.get("ev_min_edge", 1.0), key="ev_min_edge",
            help="Minimum edge (vig-free sportsbook prob minus Kalshi implied prob) to show.",
        )
        ev_sport = st.selectbox(
            "EV sport", options=["All"] + sports_options, index=0, key="ev_sport"
        )

        st.markdown("---")
        st.caption("Auto-refreshes every 15s when scanner is running.")

    return {
        "sport": None if filter_sport == "All" else filter_sport,
        "arb_type": None if filter_type == "All" else (
            "pure_arb" if "Pure" in filter_type else "near_arb"
        ),
        "min_vol": int(min_vol),
        "ev_min_edge": float(ev_min_edge),
        "ev_sport": None if ev_sport == "All" else ev_sport,
    }


# ---------------------------------------------------------------------------
# Tab 1: Live Opportunities (legacy arb_opportunities)
# ---------------------------------------------------------------------------

def tab_live_opportunities(filters: dict) -> None:
    df = _load_arb_opps(latest_only=True, sport=filters["sport"], arb_type=filters["arb_type"])
    if filters["min_vol"] > 0 and not df.empty and "volume" in df.columns:
        df = df[df["volume"] >= filters["min_vol"]]

    if df.empty:
        if not DB_PATH.exists():
            st.info("No database found. Start the scanner to begin collecting data.")
        else:
            st.info("No opportunities in the latest scan cycle. Start the scanner to collect data.")
        return

    scan_id = df.iloc[0].get("scan_id", "unknown")
    detected_at = df.iloc[0].get("detected_at", "")
    st.caption(f"Scan `{scan_id[:8]}…` · {detected_at[:19].replace('T', ' ')} UTC · {len(df)} opportunities")

    stats = _scan_summary_stats(df)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Pure Arbs", stats["pure"])
    c2.metric("Near-Arbs", stats["near"])
    c3.metric("Best Edge", f"{stats['best_pct']:+.2f}%")
    c4.metric("Unique Sports", stats["sports"])
    c5.metric("Total", len(df))
    st.markdown("---")

    for _, row in df.iterrows():
        arb_pct = float(row.get("arb_pct", 0))
        arb_type = str(row.get("arb_type", ""))
        sport = str(row.get("sport", "?"))
        ticker = str(row.get("ticker", ""))
        title = str(row.get("market_title", ""))
        yes_ask = int(row.get("yes_ask", 0))
        no_ask = int(row.get("no_ask", 0))
        total_cost = int(row.get("total_cost", yes_ask + no_ask))
        profit = float(row.get("expected_profit_at_100", 0))
        volume = int(row.get("volume", 0))
        hrs = float(row.get("hours_to_close", 0))
        pct_class = "positive" if arb_pct > 0 else "negative"
        card_class = "pure-arb" if arb_type == "pure_arb" else "near-arb"
        explanation = str(row.get("explanation") or "")

        st.markdown(
            f"""<div class="opp-card {card_class}">
              <div class="opp-card-header">
                <div>
                  {_arb_badge(arb_type)}&nbsp;&nbsp;{_sport_badge(sport)}&nbsp;&nbsp;
                  <span class="opp-ticker">{ticker}</span>
                  <div style="font-size:12px;color:#6b7a99;margin-top:2px;">{title[:60]}</div>
                </div>
                <div class="opp-arb-pct {pct_class}">{arb_pct:+.2f}%</div>
              </div>
              <div class="opp-stats">
                <div class="opp-stat"><div class="s-label">YES ask</div><div class="s-val">{yes_ask}¢</div></div>
                <div class="opp-stat"><div class="s-label">NO ask</div><div class="s-val">{no_ask}¢</div></div>
                <div class="opp-stat"><div class="s-label">Total cost</div><div class="s-val">{total_cost}¢</div></div>
                <div class="opp-stat"><div class="s-label">Profit / $100</div><div class="s-val" style="color:{'#22c55e' if profit >= 0 else '#f59e0b'};">${profit:+.2f}</div></div>
                <div class="opp-stat"><div class="s-label">Volume</div><div class="s-val">{volume:,}</div></div>
                <div class="opp-stat"><div class="s-label">Hrs to close</div><div class="s-val">{hrs:.1f}h</div></div>
              </div>
            </div>""",
            unsafe_allow_html=True,
        )
        if explanation:
            with st.expander("📖 Explanation"):
                st.text(explanation)


# ---------------------------------------------------------------------------
# Tab 2: Arbitrage Finder (new engine with friction model)
# ---------------------------------------------------------------------------

def tab_arbitrage_finder(filters: dict) -> None:
    df = _load_arb_results(
        latest_only=True,
        sport=filters["sport"],
    )
    if filters["min_vol"] > 0 and not df.empty and "volume" in df.columns:
        df = df[df["volume"] >= filters["min_vol"]]

    # Status filter
    if filters["arb_type"] == "pure_arb":
        if not df.empty and "status" in df.columns:
            df = df[df["status"] == "pure_arb"]
    elif filters["arb_type"] == "near_arb":
        if not df.empty and "status" in df.columns:
            df = df[df["status"] == "near_arb"]

    if df.empty:
        if not DB_PATH.exists():
            st.info("No database found. Start the scanner to collect data.")
        else:
            st.info(
                "No arb results yet. This tab uses the new arb engine with friction modeling. "
                "Run the scanner to populate data."
            )
        return

    pure_count = (df["status"] == "pure_arb").sum() if "status" in df.columns else 0
    near_count = (df["status"] == "near_arb").sum() if "status" in df.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pure Arb (net > 0)", int(pure_count))
    c2.metric("Near Arb", int(near_count))
    c3.metric("Best Net Arb %", f"{df['net_arb_pct'].max():+.2f}%" if "net_arb_pct" in df.columns else "—")
    c4.metric("Best Gross Arb %", f"{df['gross_arb_pct'].max():+.2f}%" if "gross_arb_pct" in df.columns else "—")
    st.markdown("---")

    for _, row in df.iterrows():
        status = str(row.get("status", ""))
        sport = str(row.get("sport", "?"))
        ticker = str(row.get("ticker", ""))
        title = str(row.get("market_title", ""))
        yes_ask = int(row.get("yes_ask", 0))
        no_ask = int(row.get("no_ask", 0))
        gross_pct = float(row.get("gross_arb_pct", 0))
        net_pct = float(row.get("net_arb_pct", 0))
        total_fric = float(row.get("total_friction_pct", 0))
        spread_pen = float(row.get("spread_penalty_pct", 0))
        slippage = float(row.get("slippage_pct", 0))
        liquidity = float(row.get("liquidity_penalty_pct", 0))
        stale = float(row.get("stale_quote_penalty_pct", 0))
        volume = int(row.get("volume", 0))
        hrs = float(row.get("hours_to_close", 0))
        rejection = str(row.get("rejection_reason") or "")
        explanation = str(row.get("explanation") or "")

        net_class = "positive" if net_pct > 0 else "negative"
        card_class = "pure-arb" if status == "pure_arb" else "near-arb"

        st.markdown(
            f"""<div class="opp-card {card_class}">
              <div class="opp-card-header">
                <div>
                  {_status_badge(status)}&nbsp;&nbsp;{_sport_badge(sport)}&nbsp;&nbsp;
                  <span class="opp-ticker">{ticker}</span>
                  <div style="font-size:12px;color:#6b7a99;margin-top:2px;">{title[:60]}</div>
                </div>
                <div>
                  <span style="font-size:12px;color:#6b7a99;">Gross: </span>
                  <span style="font-size:14px;font-weight:700;color:#f59e0b;">{gross_pct:+.2f}%</span>
                  &nbsp;→&nbsp;
                  <span style="font-size:12px;color:#6b7a99;">Net: </span>
                  <span class="opp-arb-pct {net_class}" style="font-size:18px;">{net_pct:+.2f}%</span>
                </div>
              </div>
              <div class="opp-stats">
                <div class="opp-stat"><div class="s-label">YES ask</div><div class="s-val">{yes_ask}¢</div></div>
                <div class="opp-stat"><div class="s-label">NO ask</div><div class="s-val">{no_ask}¢</div></div>
                <div class="opp-stat"><div class="s-label">Total cost</div><div class="s-val">{yes_ask + no_ask}¢</div></div>
                <div class="opp-stat"><div class="s-label">Volume</div><div class="s-val">{volume:,}</div></div>
                <div class="opp-stat"><div class="s-label">Hrs to close</div><div class="s-val">{hrs:.1f}h</div></div>
              </div>
              <div class="friction-row">
                <span class="friction-pill">Spread: {spread_pen:+.2f}%</span>
                <span class="friction-pill">Slippage: {slippage:+.2f}%</span>
                <span class="friction-pill">Liquidity: {liquidity:+.2f}%</span>
                <span class="friction-pill">Stale quote: {stale:+.2f}%</span>
                <span class="friction-pill friction-total">Total friction: {total_fric:+.2f}%</span>
              </div>
              {f'<div style="font-size:12px;color:#94a3b8;margin-top:6px;">ℹ️ {rejection}</div>' if rejection else ''}
            </div>""",
            unsafe_allow_html=True,
        )
        if explanation:
            with st.expander("📖 Full explanation"):
                st.text(explanation)

    st.markdown("---")
    st.markdown(
        "**How to read this:** Gross arb % is the raw edge before transaction costs. "
        "Friction (spread + slippage + liquidity + stale quote) is subtracted to get **Net arb %**. "
        "Only markets with Net > 0 are truly risk-free."
    )


# ---------------------------------------------------------------------------
# Tab 3: EV Finder
# ---------------------------------------------------------------------------

def tab_ev_finder(filters: dict) -> None:
    df = _load_ev_candidates(
        latest_only=True,
        sport=filters["ev_sport"],
        min_edge=filters["ev_min_edge"],
    )

    if df.empty:
        if not DB_PATH.exists():
            st.info("No database found. Start the scanner to collect data.")
        else:
            st.info(
                "No EV candidates found. This requires a sportsbook API key (The Odds API) "
                "to compare Kalshi prices against real sportsbook lines. "
                "Set THE_ODDS_API_KEY in .env and SPORTSBOOK_PROVIDER=the_odds_api."
            )
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("EV Candidates", len(df))
    if "best_edge_pct" in df.columns:
        c2.metric("Best Edge", f"{df['best_edge_pct'].max():+.2f}%")
        c3.metric("Avg Edge", f"{df['best_edge_pct'].mean():+.2f}%")
    st.markdown("---")

    for _, row in df.iterrows():
        sport = str(row.get("sport", "?"))
        ticker = str(row.get("ticker", ""))
        title = str(row.get("market_title", ""))
        yes_team = str(row.get("yes_team", "YES"))
        no_team = str(row.get("no_team", "NO"))
        yes_ask = int(row.get("yes_ask", 0))
        no_ask = int(row.get("no_ask", 0))
        best_side = str(row.get("best_side", "NONE"))
        best_edge = float(row.get("best_edge_pct", 0))
        best_kelly = float(row.get("best_kelly_fraction", 0))
        bookmaker = str(row.get("sportsbook_bookmaker", "?")).upper()
        match_conf = float(row.get("match_confidence", 0))
        spread_fric = float(row.get("spread_friction_pct", 0))
        fill_risk = float(row.get("fill_risk_pct", 0))
        hrs = float(row.get("hours_to_close", 0))
        volume = int(row.get("volume", 0))
        explanation = str(row.get("explanation") or "")

        # Per-side fields
        yes_edge = row.get("yes_edge_pct")
        no_edge = row.get("no_edge_pct")
        yes_vig = row.get("yes_vig_free_prob")
        no_vig = row.get("no_vig_free_prob")
        yes_kelly = row.get("yes_kelly_fraction")
        no_kelly = row.get("no_kelly_fraction")

        yes_edge_str = f"{float(yes_edge):+.2f}%" if yes_edge is not None else "—"
        no_edge_str = f"{float(no_edge):+.2f}%" if no_edge is not None else "—"
        yes_vig_str = f"{float(yes_vig):.1%}" if yes_vig is not None else "—"
        no_vig_str = f"{float(no_vig):.1%}" if no_vig is not None else "—"
        yes_kelly_str = f"{float(yes_kelly):.1%}" if yes_kelly is not None else "—"
        no_kelly_str = f"{float(no_kelly):.1%}" if no_kelly is not None else "—"

        edge_color = "#22c55e" if best_edge > 0 else "#f59e0b"

        st.markdown(
            f"""<div class="opp-card ev-card">
              <div class="opp-card-header">
                <div>
                  <span class="badge badge-ev">EV BET</span>&nbsp;&nbsp;{_sport_badge(sport)}&nbsp;&nbsp;
                  <span class="opp-ticker">{ticker}</span>
                  <div style="font-size:12px;color:#6b7a99;margin-top:2px;">{title[:60]}</div>
                </div>
                <div>
                  {_side_badge(best_side)}&nbsp;
                  <span style="font-size:20px;font-weight:800;color:{edge_color};">{best_edge:+.2f}%</span>
                  <span style="font-size:12px;color:#6b7a99;"> edge&nbsp;|&nbsp;Kelly </span>
                  <span style="font-size:14px;font-weight:700;color:#94a3b8;">{best_kelly:.1%}</span>
                </div>
              </div>
              <div class="opp-stats" style="margin-top:10px;">
                <div class="opp-stat">
                  <div class="s-label">YES ({yes_team[:20]})</div>
                  <div class="s-val">{yes_ask}¢ &nbsp;edge {yes_edge_str}</div>
                  <div style="font-size:11px;color:#6b7a99;">Vig-free: {yes_vig_str} | Kelly: {yes_kelly_str}</div>
                </div>
                <div class="opp-stat">
                  <div class="s-label">NO ({no_team[:20]})</div>
                  <div class="s-val">{no_ask}¢ &nbsp;edge {no_edge_str}</div>
                  <div style="font-size:11px;color:#6b7a99;">Vig-free: {no_vig_str} | Kelly: {no_kelly_str}</div>
                </div>
                <div class="opp-stat"><div class="s-label">Bookmaker</div><div class="s-val">{bookmaker}</div></div>
                <div class="opp-stat"><div class="s-label">Match conf</div><div class="s-val">{match_conf:.0%}</div></div>
                <div class="opp-stat"><div class="s-label">Spread friction</div><div class="s-val">{spread_fric:.2f}%</div></div>
                <div class="opp-stat"><div class="s-label">Fill risk</div><div class="s-val">{fill_risk:.2f}%</div></div>
                <div class="opp-stat"><div class="s-label">Volume</div><div class="s-val">{volume:,}</div></div>
                <div class="opp-stat"><div class="s-label">Hrs to close</div><div class="s-val">{hrs:.1f}h</div></div>
              </div>
            </div>""",
            unsafe_allow_html=True,
        )
        if explanation:
            with st.expander("📖 Full explanation"):
                st.text(explanation)

    st.markdown("---")
    st.markdown(
        "**How to read this:** The EV engine removes the sportsbook's vig (built-in profit margin) "
        "from moneyline odds to estimate the 'true' probability of each outcome. "
        "When that estimate is higher than Kalshi's implied price, you have positive expected value."
    )


# ---------------------------------------------------------------------------
# Tab 4: Arb History
# ---------------------------------------------------------------------------

def tab_arb_history(filters: dict) -> None:
    scan_ids = _load_scan_ids(limit=50)
    if not scan_ids:
        st.info("No scan history yet. Run the scanner to collect data.")
        return

    labels = [f"{sid[:8]}…" for sid in scan_ids]
    selected_label = st.selectbox("Select scan cycle", options=labels, index=0)
    selected_scan_id = scan_ids[labels.index(selected_label)]

    df = _load_arb_opps(scan_id=selected_scan_id, sport=filters["sport"], arb_type=filters["arb_type"])
    if filters["min_vol"] > 0 and not df.empty and "volume" in df.columns:
        df = df[df["volume"] >= filters["min_vol"]]

    if df.empty:
        st.info("No opportunities match the current filters for this scan.")
        return

    st.caption(f"Scan `{selected_scan_id[:8]}…` · {len(df)} opportunities")
    stats = _scan_summary_stats(df)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pure Arbs", stats["pure"])
    c2.metric("Near-Arbs", stats["near"])
    c3.metric("Best Edge", f"{stats['best_pct']:+.2f}%")
    c4.metric("Sports", stats["sports"])
    st.markdown("---")

    display_cols = ["ticker", "sport", "arb_type", "arb_pct", "yes_ask", "no_ask",
                    "total_cost", "expected_profit_at_100", "volume", "hours_to_close", "alerted"]
    available = [c for c in display_cols if c in df.columns]
    view = df[available].copy()
    view.rename(columns={
        "arb_pct": "Arb %", "yes_ask": "YES ask", "no_ask": "NO ask",
        "total_cost": "Total ¢", "expected_profit_at_100": "Profit/$100",
        "hours_to_close": "Hrs left", "alerted": "Alerted",
    }, inplace=True)
    if "Arb %" in view.columns:
        view["Arb %"] = view["Arb %"].map(lambda x: f"{x:+.2f}%")
    if "Profit/$100" in view.columns:
        view["Profit/$100"] = view["Profit/$100"].map(lambda x: f"${x:+.2f}")

    st.dataframe(view, use_container_width=True, hide_index=True)
    csv = df.to_csv(index=False)
    st.download_button("Download CSV", data=csv,
                       file_name=f"arb_scan_{selected_scan_id[:8]}.csv", mime="text/csv")


# ---------------------------------------------------------------------------
# Tab 5: Scanner Config
# ---------------------------------------------------------------------------

def tab_scanner_config() -> None:
    st.markdown("### Scanner Configuration")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Same-Market Arb (Kalshi internal)**")
        st.markdown("""
**Pure Arb** — `YES ask + NO ask < 100¢`

Buy both sides for guaranteed profit regardless of outcome.
Example: YES=48¢, NO=50¢ → cost 98¢ → guaranteed +$2.04 per $100.

**Near-Arb** — total within threshold of 100¢

Close to breakeven — worth monitoring for price tightening.

**New friction model:**
- Spread penalty (YES spread + NO spread)
- Slippage (volume-tier estimate)
- Liquidity penalty (total volume)
- Stale quote penalty (quote age)

Net Arb = Gross Arb − Total Friction
""")

    with c2:
        st.markdown("**EV Betting (cross-exchange)**")
        st.markdown("""
Compares Kalshi binary prices against sportsbook moneylines.

1. Fetch moneyline from The Odds API (FanDuel, DraftKings, etc.)
2. Remove vig: `vig_free_prob = raw_implied / total_vig`
3. Compute edge: `edge = (vig_free_prob − Kalshi_implied) × 100`
4. Kelly fraction: `b = (100 − ask) / ask`, then `Kelly = (p×b − (1−p)) / b`

Positive edge → you're buying at less than fair value.
""")

    st.markdown("---")
    st.markdown("### Sports Covered")
    for code, desc in {
        "NBA": "Basketball — game-result markets",
        "NFL": "Football — game-result & prop markets",
        "MLB": "Baseball — game-result markets",
        "NHL": "Hockey — game-result markets",
        "UFC": "MMA — fight outcome markets",
        "SOC": "Soccer — MLS and international",
        "TEN": "Tennis — match outcome markets",
    }.items():
        st.markdown(f"- **{code}** — {desc}")

    st.markdown("---")
    st.markdown("### Environment Variables")
    st.code("""
# Arb scanner
ARB_NEAR_THRESHOLD_PCT=2.0       # flag markets within 2% of breakeven
ARB_SCAN_INTERVAL_SECONDS=30     # seconds between scans
ARB_MIN_VOLUME=100               # minimum market volume
ARB_SPORTS=NBA,NFL,MLB,NHL,UFC   # comma-separated sport codes

# Sportsbook / EV engine
SPORTSBOOK_PROVIDER=the_odds_api # or: mock, none
THE_ODDS_API_KEY=your_key_here
PREFERRED_BOOKMAKER=fanduel      # bookmaker to use for comparison
MATCH_MIN_NET_EDGE_PCT=1.0       # minimum EV edge % to surface candidate
""", language="bash")

    st.markdown("---")
    st.markdown("### CLI Usage")
    st.code("""
uv run python arbitrage_scanner.py          # continuous scan
uv run python arbitrage_scanner.py --once   # single scan then exit
uv run python arbitrage_scanner.py --live-only  # closing within 4h only
uv run streamlit run dashboard.py           # this dashboard
""", language="bash")


# ---------------------------------------------------------------------------
# Tab 6: System Health
# ---------------------------------------------------------------------------

def tab_system_health() -> None:
    st.markdown("### System Health")

    c1, c2, c3 = st.columns(3)
    c1.metric("DB exists", "Yes" if DB_PATH.exists() else "No")
    c2.metric("Scanner", "Running" if _scanner_running() else "Stopped")
    if DB_PATH.exists():
        size_kb = DB_PATH.stat().st_size / 1024
        c3.metric("DB size", f"{size_kb:.1f} KB")

    conn = _db_connect()
    if conn:
        try:
            total_opps = conn.execute("SELECT COUNT(*) FROM arb_opportunities").fetchone()[0]
            total_scans = conn.execute(
                "SELECT COUNT(DISTINCT scan_id) FROM arb_opportunities"
            ).fetchone()[0]
            total_arb_results = conn.execute("SELECT COUNT(*) FROM arb_results").fetchone()[0]
            total_ev = conn.execute("SELECT COUNT(*) FROM ev_candidates").fetchone()[0]
        except Exception:
            total_opps = total_scans = total_arb_results = total_ev = 0
        finally:
            conn.close()

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Arb opportunities logged", f"{total_opps:,}")
        d2.metric("Scan cycles", f"{total_scans:,}")
        d3.metric("Arb results (new engine)", f"{total_arb_results:,}")
        d4.metric("EV candidates", f"{total_ev:,}")

    st.markdown("---")
    st.markdown("### Recent Log Files")

    if LOG_DIR.exists():
        log_files = sorted(LOG_DIR.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        if log_files:
            selected_log = st.selectbox("Log file", options=[f.name for f in log_files[:10]])
            log_path = LOG_DIR / selected_log
            try:
                content = log_path.read_text(errors="replace")
                lines = content.splitlines()[-200:]
                st.text_area("Log output (last 200 lines)", value="\n".join(lines),
                             height=400, disabled=True)
            except Exception as exc:
                st.error(f"Could not read log: {exc}")
        else:
            st.info("No log files found in ./logs/")
    else:
        st.info("Log directory ./logs/ does not exist yet.")

    st.markdown("---")
    st.markdown("### Scanner Process")
    if _scanner_running():
        proc = st.session_state["scanner_proc"]
        st.success(f"Scanner PID: {proc.pid}")
        cmd = st.session_state.get("scanner_cmd", "")
        if cmd:
            st.code(cmd, language="bash")
        if st.button("Force Stop Scanner"):
            _stop_scanner(); st.rerun()
    else:
        st.info("Scanner is not running. Use the sidebar to start it.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    for key, default in [
        ("scanner_proc", None),
        ("scanner_started_at", None),
        ("scanner_cmd", ""),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    filters = _render_sidebar()
    _render_summary_bar()

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "⚡ Live Opportunities",
        "📊 Arbitrage Finder",
        "💰 EV Finder",
        "📋 Arb History",
        "⚙️ Scanner Config",
        "🖥️ System Health",
    ])

    with tab1:
        tab_live_opportunities(filters)
    with tab2:
        tab_arbitrage_finder(filters)
    with tab3:
        tab_ev_finder(filters)
    with tab4:
        tab_arb_history(filters)
    with tab5:
        tab_scanner_config()
    with tab6:
        tab_system_health()

    if _scanner_running():
        time.sleep(REFRESH_SECONDS)
        st.cache_data.clear()
        st.rerun()


if __name__ == "__main__":
    main()
