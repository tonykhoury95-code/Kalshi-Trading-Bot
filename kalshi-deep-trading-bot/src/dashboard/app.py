"""
Streamlit dashboard for monitoring the Kalshi trading bot.

Run with:
    streamlit run src/dashboard/app.py
"""

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st


DEFAULT_DB_PATH = Path("./data/ledger.db")


@st.cache_data(ttl=30)
def load_runs(db_path: str) -> pd.DataFrame:
    """Load all runs from the ledger."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM runs ORDER BY timestamp DESC", conn)
    conn.close()
    return df


@st.cache_data(ttl=30)
def load_orders(db_path: str, run_id: str) -> pd.DataFrame:
    """Load orders for a specific run."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM orders WHERE run_id = ? ORDER BY timestamp",
        conn,
        params=(run_id,),
    )
    conn.close()
    return df


@st.cache_data(ttl=30)
def load_decisions(db_path: str, run_id: str) -> pd.DataFrame:
    """Load decisions for a specific run."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM decisions WHERE run_id = ? ORDER BY id",
        conn,
        params=(run_id,),
    )
    conn.close()
    return df


@st.cache_data(ttl=30)
def load_scanned_markets(db_path: str, run_id: str) -> pd.DataFrame:
    """Load scanned markets for a specific run."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM scanned_markets WHERE run_id = ? ORDER BY id",
        conn,
        params=(run_id,),
    )
    conn.close()
    return df


@st.cache_data(ttl=30)
def load_edge_calculations(db_path: str, run_id: str) -> pd.DataFrame:
    """Load edge calculations for a specific run."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM edge_calculations WHERE run_id = ? ORDER BY id",
        conn,
        params=(run_id,),
    )
    conn.close()
    return df


@st.cache_data(ttl=30)
def load_outcomes(db_path: str) -> pd.DataFrame:
    """Load all outcomes."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM outcomes ORDER BY resolved_at DESC", conn)
    conn.close()
    return df


@st.cache_data(ttl=30)
def load_all_orders(db_path: str) -> pd.DataFrame:
    """Load all orders across all runs."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM orders ORDER BY timestamp DESC", conn)
    conn.close()
    return df


def main() -> None:
    """Main dashboard entry point."""
    st.set_page_config(page_title="Kalshi Trading Bot Dashboard", layout="wide")
    st.title("Kalshi Trading Bot Dashboard")

    # Sidebar - DB path and run selection
    st.sidebar.header("Configuration")
    db_path = st.sidebar.text_input("Database Path", value=str(DEFAULT_DB_PATH))

    if not Path(db_path).exists():
        st.warning(f"Database not found at {db_path}. Run the bot first to create data.")
        return

    runs_df = load_runs(db_path)
    if runs_df.empty:
        st.info("No runs found in the database.")
        return

    run_options = ["latest"] + runs_df["run_id"].tolist()
    selected = st.sidebar.selectbox("Select Run", run_options)

    if selected == "latest":
        run_id = runs_df.iloc[0]["run_id"]
    else:
        run_id = selected

    run_row = runs_df[runs_df["run_id"] == run_id].iloc[0]
    is_dry_run = bool(run_row.get("dry_run", 1))

    if not is_dry_run:
        st.warning("You are viewing LIVE trading data. Proceed with caution.")

    st.sidebar.markdown(f"**Run:** `{run_id[:8]}...`")
    st.sidebar.markdown(f"**Time:** {run_row.get('timestamp', 'N/A')}")
    st.sidebar.markdown(f"**Mode:** {'DRY RUN' if is_dry_run else 'LIVE'}")

    # Main tabs
    tab_overview, tab_positions, tab_history, tab_candidates, tab_edge, tab_exposure, tab_health = st.tabs(
        ["Overview", "Positions", "Trade History", "Candidates", "Edge Distribution", "Exposure", "System Health"]
    )

    # -- Overview tab ---------------------------------------------------------
    with tab_overview:
        st.header("Run Overview")
        orders_df = load_orders(db_path, run_id)
        decisions_df = load_decisions(db_path, run_id)
        scanned_df = load_scanned_markets(db_path, run_id)
        outcomes_df = load_outcomes(db_path)

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Markets Scanned", len(scanned_df))
        with col2:
            st.metric("Decisions Made", len(decisions_df))
        with col3:
            actionable = decisions_df[decisions_df["action"] != "skip"] if not decisions_df.empty else pd.DataFrame()
            st.metric("Orders Placed", len(orders_df))
        with col4:
            total_wagered = orders_df["amount"].sum() if not orders_df.empty else 0
            st.metric("Total Wagered", f"${total_wagered:.2f}")

        if not outcomes_df.empty:
            wins = len(outcomes_df[outcomes_df["pnl"] > 0])
            total = len(outcomes_df)
            win_rate = wins / total if total > 0 else 0
            st.metric("Win Rate", f"{win_rate:.1%} ({wins}/{total})")
            st.metric("Total P&L", f"${outcomes_df['pnl'].sum():.2f}")

    # -- Positions tab --------------------------------------------------------
    with tab_positions:
        st.header("Current Positions")
        all_orders = load_all_orders(db_path)
        if not all_orders.empty:
            position_summary = (
                all_orders.groupby("ticker")
                .agg({"amount": "sum", "action": "last", "timestamp": "last"})
                .reset_index()
            )
            st.dataframe(position_summary, use_container_width=True)
        else:
            st.info("No positions found.")

    # -- Trade History tab ----------------------------------------------------
    with tab_history:
        st.header("Trade History")
        orders_df = load_orders(db_path, run_id)
        if not orders_df.empty:
            st.dataframe(orders_df, use_container_width=True)
        else:
            st.info("No orders in this run.")

        decisions_df = load_decisions(db_path, run_id)
        if not decisions_df.empty:
            st.subheader("Decision Details")
            st.dataframe(decisions_df, use_container_width=True)

    # -- Candidates tab -------------------------------------------------------
    with tab_candidates:
        st.header("Market Candidates")
        scanned_df = load_scanned_markets(db_path, run_id)
        if not scanned_df.empty:
            passed = scanned_df[scanned_df["passed_filters"] == 1]
            rejected = scanned_df[scanned_df["passed_filters"] == 0]

            st.subheader(f"Passed Filters ({len(passed)})")
            if not passed.empty:
                st.dataframe(passed, use_container_width=True)

            st.subheader(f"Rejected ({len(rejected)})")
            if not rejected.empty:
                st.dataframe(rejected[["ticker", "event_ticker", "volume", "filter_rejection"]], use_container_width=True)
        else:
            st.info("No scanned markets in this run.")

    # -- Edge Distribution tab ------------------------------------------------
    with tab_edge:
        st.header("Edge Distribution")
        edge_df = load_edge_calculations(db_path, run_id)
        if not edge_df.empty:
            import plotly.express as px

            fig = px.histogram(
                edge_df,
                x="edge",
                nbins=30,
                title="Edge Distribution",
                labels={"edge": "Edge (research - implied)"},
            )
            st.plotly_chart(fig, use_container_width=True)

            fig2 = px.histogram(
                edge_df,
                x="r_score",
                nbins=30,
                title="R-Score Distribution",
                labels={"r_score": "R-Score (z-score)"},
            )
            st.plotly_chart(fig2, use_container_width=True)

            st.dataframe(edge_df, use_container_width=True)
        else:
            st.info("No edge calculations in this run.")

    # -- Exposure tab ---------------------------------------------------------
    with tab_exposure:
        st.header("Exposure by Event")
        orders_df = load_orders(db_path, run_id)
        scanned_df = load_scanned_markets(db_path, run_id)

        if not orders_df.empty and not scanned_df.empty:
            # Join orders with scanned markets to get event_ticker
            merged = orders_df.merge(
                scanned_df[["ticker", "event_ticker"]].drop_duplicates(),
                on="ticker",
                how="left",
            )
            if "event_ticker" in merged.columns:
                exposure = merged.groupby("event_ticker")["amount"].sum().reset_index()
                exposure.columns = ["Event", "Exposure ($)"]

                import plotly.express as px

                fig = px.bar(
                    exposure,
                    x="Event",
                    y="Exposure ($)",
                    title="Exposure by Event",
                )
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No exposure data available.")

    # -- System Health tab ----------------------------------------------------
    with tab_health:
        st.header("System Health")

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Recent Runs")
            st.dataframe(
                runs_df[["run_id", "timestamp", "dry_run"]].head(10),
                use_container_width=True,
            )

        with col2:
            st.subheader("Database Stats")
            conn = sqlite3.connect(db_path)
            for table in ["runs", "scanned_markets", "decisions", "orders", "outcomes"]:
                try:
                    count = pd.read_sql_query(f"SELECT COUNT(*) as cnt FROM {table}", conn).iloc[0]["cnt"]
                    st.metric(f"{table} rows", count)
                except Exception:
                    st.metric(f"{table} rows", "N/A")
            conn.close()


if __name__ == "__main__":
    main()
