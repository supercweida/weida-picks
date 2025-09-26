import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta
import requests
from zoneinfo import ZoneInfo

# --- Helper to compute week boundaries ---
def get_week_bounds(week_number: int):
    ct = ZoneInfo("America/Chicago")
    season_start_ct = datetime(2025, 9, 2, 0, 1, tzinfo=ct)
    week_start_ct = season_start_ct + timedelta(weeks=week_number - 1)
    week_end_ct = week_start_ct + timedelta(days=6, hours=23, minutes=58, seconds=59)
    return week_start_ct.astimezone(timezone.utc), week_end_ct.astimezone(timezone.utc)


# --- Helper: convert moneyline odds to points ---
def moneyline_to_points(odds: int) -> float:
    if odds is None:
        return None
    if odds > 0:
        return round(odds / 10, 2)
    else:
        return round(-10 / odds, 2)


# --- Fetch ALL odds (Spreads + Moneyline) ---
def fetch_all_odds(api_key: str):
    odds_url = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "spreads,h2h",  # include moneyline
        "oddsFormat": "american"
    }

    response = requests.get(odds_url, params=params)
    if response.status_code != 200:
        st.error(f"Error fetching odds: {response.status_code} - {response.text}")
        return pd.DataFrame()

    odds_data = response.json()
    if not odds_data:
        return pd.DataFrame()

    ct = ZoneInfo("America/Chicago")
    season_start_ct = datetime(2025, 9, 2, 0, 1, tzinfo=ct)

    spreads_list = []
    moneyline_list = []

    for game in odds_data:
        game_time_utc = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
        game_time_ct = game_time_utc.astimezone(ct)
        game_time_fmt = game_time_ct.strftime("%A %I:%M %p")

        delta_days = (game_time_ct - season_start_ct).days
        week_number = (delta_days // 7) + 1

        home_team = game["home_team"]

        fanduel = next((b for b in game.get("bookmakers", []) if b["key"] == "fanduel"), None)
        if not fanduel:
            continue

        # --- Spreads ---
        spread_market = next((m for m in fanduel.get("markets", []) if m["key"] == "spreads"), None)
        if spread_market:
            outcomes = spread_market.get("outcomes", [])
            if len(outcomes) == 2:
                t1, t2 = outcomes
                if t1.get("point") < t2.get("point"):
                    favorite, underdog = t1, t2
                elif t2.get("point") < t1.get("point"):
                    favorite, underdog = t2, t1
                else:
                    favorite, underdog = None, None

                spreads_list.append({
                    "week_number": week_number,
                    "home_team": home_team,
                    "commence_time": game_time_fmt,
                    "favorite_team": favorite["name"] if favorite else None,
                    "underdog_team": underdog["name"] if underdog else None,
                    "favorite_spread": favorite.get("point") if favorite else None,
                    "underdog_spread": underdog.get("point") if underdog else None
                })

        # --- Moneyline ---
        moneyline_market = next((m for m in fanduel.get("markets", []) if m["key"] in ["h2h", "moneyline"]), None)
        if moneyline_market:
            ml_outcomes = {o["name"]: o["price"] for o in moneyline_market.get("outcomes", [])}
            away_team = next((t for t in ml_outcomes.keys() if t != home_team), None)
            ml_home = ml_outcomes.get(home_team)
            ml_away = ml_outcomes.get(away_team)

            moneyline_list.append({
                "week_number": week_number,
                "home_team": home_team,
                "away_team": away_team,
                "commence_time": game_time_fmt,
                "moneyline_home": ml_home,
                "moneyline_away": ml_away,
                "moneyline_home_points": moneyline_to_points(ml_home),
                "moneyline_away_points": moneyline_to_points(ml_away)
            })

    df_spreads = pd.DataFrame(spreads_list)
    df_moneyline = pd.DataFrame(moneyline_list)

    # Merge spreads + moneyline into one DataFrame
    df = pd.merge(
        df_spreads,
        df_moneyline,
        on=["week_number", "home_team", "commence_time"],
        how="outer"
    )

    # Add autopick column
    if not df.empty:
        df["autopick"] = "No"
        for week in df["week_number"].unique():
            week_df = df[df["week_number"] == week].dropna(subset=["favorite_spread"]).copy()
            if not week_df.empty:
                best_fav = week_df.sort_values(
                    by=["favorite_spread", "commence_time"], ascending=[True, True]
                ).iloc[0]
                mask = (df["week_number"] == week) & \
                       (df["favorite_team"] == best_fav["favorite_team"]) & \
                       (df["commence_time"] == best_fav["commence_time"])
                df.loc[mask, "autopick"] = "Yes"

    return df


# --- Filter helper ---
def filter_week(df, week_number: int):
    return df[df["week_number"] == week_number]


# --- Streamlit App ---
api_key = "41e45f964d812e7102f96fb8fe7def65"

st.set_page_config(layout="wide")
st.title("WeidaPicks - NFL Weekly Odds (FanDuel)")

# Refresh button triggers API call
if st.button("🔄 Refresh Odds"):
    st.session_state["all_odds"] = fetch_all_odds(api_key)

# Week selection (1–18)
week_selected = st.selectbox("Select Week", list(range(1, 19)))

if "all_odds" not in st.session_state or st.session_state["all_odds"].empty:
    st.info("Click 'Refresh Odds' to load the latest data.")
else:
    df = filter_week(st.session_state["all_odds"], week_selected)

    if df.empty:
        st.warning(f"No odds found for Week {week_selected}.")
    else:
        st.subheader("🏈 Combined Spreads + Moneyline (FanDuel)")
        st.dataframe(df)
