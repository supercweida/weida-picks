import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta
import requests

def get_nfl_spreads(api_key):
    """
    Fetch FanDuel NFL spreads for the upcoming week's games (Tue–Mon window),
    with one row per game, including week_number, favorite, underdog, and autopick.
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

    now = datetime.now(timezone.utc)

    # --- Step 1: find the next game (closest future commence_time) ---
    future_games = [
        datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
        for game in odds_data
        if datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00")) >= now
    ]
    if not future_games:
        return pd.DataFrame()

    next_game_time = min(future_games)

    # --- Step 2: compute the Tue–Mon window containing that game ---
    days_since_tuesday = (next_game_time.weekday() - 1) % 7
    week_start = (next_game_time - timedelta(days=days_since_tuesday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)

    # --- Step 3: compute week_number based on NFL season start ---
    season_start = datetime(2025, 9, 2, tzinfo=timezone.utc)  # Week 1 Tuesday
    week_number = ((week_start - season_start).days // 7) + 1

    # --- Step 4: collect spreads only for FanDuel in that window ---
    games_list = []
    for game in odds_data:
        game_time = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
        if not (week_start <= game_time <= week_end):
            continue

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
                    "home_team": home_team,
                    "away_team": away_team,
                    "favorite_team": favorite["name"] if favorite else None,
                    "favorite_spread": favorite.get("point") if favorite else None,
                    "favorite_price": favorite.get("price") if favorite else None,
                    "underdog_team": underdog["name"] if underdog else None,
                    "underdog_spread": underdog.get("point") if underdog else None,
                    "underdog_price": underdog.get("price") if underdog else None,
                    "commence_time": game_time
                })

    df = pd.DataFrame(games_list)
    if df.empty:
        return df

    # --- Step 5: determine Autopick ---
    favorites_df = df.dropna(subset=["favorite_spread"]).copy()
    if not favorites_df.empty:
        best_fav = favorites_df.sort_values(
            by=["favorite_spread", "commence_time"], ascending=[True, True]
        ).iloc[0]
        df["autopick"] = df.apply(
            lambda row: "Yes" if row["favorite_team"] == best_fav["favorite_team"] and 
                                   row["commence_time"] == best_fav["commence_time"]
                        else "No",
            axis=1
        )
    else:
        df["autopick"] = "No"

    return df

api_key = "41e45f964d812e7102f96fb8fe7def65"

st.title("NFL Weekly Spreads (FanDuel)")
df = get_nfl_spreads(api_key)

if df.empty:
    st.warning("No games found.")
else:
    week_options = sorted(df["week_number"].unique())
    week_selected = st.selectbox("Select Week", week_options)
    st.dataframe(df[df["week_number"] == week_selected])
