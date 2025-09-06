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


# --- Fetch ALL spreads at once ---
def fetch_all_spreads(api_key: str):
    odds_url = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "spreads",
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

    games_list = []
    for game in odds_data:
        game_time_utc = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
        game_time_ct = game_time_utc.astimezone(ct)
        game_time_fmt = game_time_ct.strftime("%A %I:%M %p")  # Monday 07:15 PM

        # Figure out week_number for this game
        delta_days = (game_time_ct - season_start_ct).days
        week_number = (delta_days // 7) + 1

        home_team = game["home_team"]

        fanduel = next((b for b in game.get("bookmakers", []) if b["key"] == "fanduel"), None)
        if not fanduel:
            continue

        spread_data = fanduel.get("markets", [])
        if spread_data:
            outcomes = spread_data[0].get("outcomes", [])
            if len(outcomes) == 2:
                team1, team2 = outcomes
                if team1.get("point") < team2.get("point"):
                    favorite, underdog = team1, team2
                elif team2.get("point") < team1.get("point"):
                    favorite, underdog = team2, team1
                else:
                    favorite, underdog = None, None

                games_list.append({
                    "week_number": week_number,
                    "favorite_team": favorite["name"] if favorite else None,
                    "underdog_team": underdog["name"] if underdog else None,
                    "home_team": home_team,
                    "favorite_spread": favorite.get("point") if favorite else None,
                    "underdog_spread": underdog.get("point") if underdog else None,
                    "commence_time": game_time_fmt
                })

    df = pd.DataFrame(games_list)

    if not df.empty:
        # Add Autopick (biggest favorite per week)
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

st.title("WeidaPicks - NFL Weekly Spreads (FanDuel)")

st.set_page_config(layout="wide")

# Refresh button triggers API call
if st.button("🔄 Refresh Spreads"):
    st.session_state["all_spreads"] = fetch_all_spreads(api_key)

# Week selection (1–18)
week_selected = st.selectbox("Select Week", list(range(1, 19)))

if "all_spreads" not in st.session_state or st.session_state["all_spreads"].empty:
    st.info("Click 'Refresh Spreads' to load the latest odds.")
else:
    df = filter_week(st.session_state["all_spreads"], week_selected)
    if df.empty:
        st.warning(f"No games found for Week {week_selected}.")
    else:
        st.dataframe(df)