import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta
import requests
from zoneinfo import ZoneInfo

# --- Your function (unchanged) ---
def get_nfl_spreads(api_key, week_number: int = None):
    """
    Fetch FanDuel NFL spreads for a given NFL week (Tue–Mon window).
    
    Output columns: week_number, favorite_team, underdog_team, home_team,
    favorite_spread, underdog_spread, commence_time (CST, AM/PM), autopick.
    """
    odds_url = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "spreads",
        "oddsFormat": "american"
    }

    response = requests.get(odds_url, params=params)
    if response.status_code != 200:
        print(f"Error fetching odds: {response.status_code} - {response.text}")
        return pd.DataFrame()

    odds_data = response.json()
    if not odds_data:
        return pd.DataFrame()

    # --- Anchoring season start at 12:01 AM CT, Tuesday Sept 2, 2025 ---
    ct = ZoneInfo("America/Chicago")
    season_start_ct = datetime(2025, 9, 2, 0, 1, tzinfo=ct)

    # --- Step 1: Determine week_number ---
    now_ct = datetime.now(ct)
    if week_number is None:
        week_number = ((now_ct - season_start_ct).days // 7) + 1

    # --- Step 2: Compute window for that week ---
    week_start_ct = season_start_ct + timedelta(weeks=week_number - 1)
    week_end_ct = week_start_ct + timedelta(days=6, hours=23, minutes=58, seconds=59)

    # Convert boundaries to UTC for API comparisons
    week_start = week_start_ct.astimezone(timezone.utc)
    week_end = week_end_ct.astimezone(timezone.utc)

    # --- Step 3: Collect spreads only for FanDuel within that week ---
    games_list = []
    for game in odds_data:
        game_time_utc = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
        if not (week_start <= game_time_utc <= week_end):
            continue

        # Convert kickoff time to Central Time for display
        game_time_ct = game_time_utc.astimezone(ct)
        game_time_fmt = game_time_ct.strftime("%A %I:%M %p")  # Monday 07:15 PM

        home_team = game["home_team"]
        away_team = game["away_team"]

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
                    favorite, underdog = None, None  # Pick'em

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
    if df.empty:
        return df

    # --- Step 4: Determine Autopick (biggest favorite) ---
    favorites_df = df.dropna(subset=["favorite_spread"]).copy()
    if not favorites_df.empty:
        best_fav = favorites_df.sort_values(
            by=["favorite_spread", "commence_time"], ascending=[True, True]
        ).iloc[0]
        df["autopick"] = df.apply(
            lambda row: "Yes" if row["favorite_team"] == best_fav["favorite_team"]
                        and row["commence_time"] == best_fav["commence_time"]
                        else "No",
            axis=1
        )
    else:
        df["autopick"] = "No"

    # Reorder columns for clarity
    df = df[[
        "week_number",
        "favorite_team",
        "underdog_team",
        "favorite_spread",
        "underdog_spread",
        "home_team",
        "commence_time",
        "autopick"
    ]]

    return df

api_key = "41e45f964d812e7102f96fb8fe7def65"

# --- Streamlit App ---

st.title("NFL Weekly Spreads (FanDuel)")

# Week selector 1–18
week_selected = st.selectbox("Select Week", list(range(1, 19)))

df = get_nfl_spreads(api_key, week_selected)

if df.empty:
    st.warning(f"No games found for Week {week_selected}.")
else:
    st.dataframe(df)