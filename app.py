from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from supabase import Client, create_client


CT = ZoneInfo("America/Chicago")


def setting(name: str, default=None):
    return st.secrets.get("app", {}).get(name, default)


def secret(name: str) -> str:
    value = st.secrets.get("supabase", {}).get(name)
    if not value:
        st.error(f"Missing [supabase].{name} in Streamlit secrets.")
        st.stop()
    return value


def user_client() -> Client:
    if "supabase" not in st.session_state:
        st.session_state.supabase = create_client(secret("url"), secret("anon_key"))
    return st.session_state.supabase


def admin_client() -> Client:
    return create_client(secret("url"), secret("service_role_key"))


def current_user():
    return st.session_state.get("user")


def is_admin() -> bool:
    email = (current_user().email or "").lower() if current_user() else ""
    admins = [str(value).lower() for value in setting("admin_emails", [])]
    return email in admins


def login() -> None:
    st.title("WeidaPicks")
    st.caption("Sign in with the account created for you by the pool administrator.")
    with st.form("login"):
        email = st.text_input("Email").strip()
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)
    if submitted:
        try:
            response = user_client().auth.sign_in_with_password(
                {"email": email, "password": password}
            )
            st.session_state.user = response.user
            st.rerun()
        except Exception:
            st.error("Sign-in failed. Check your email and password.")


def logout() -> None:
    try:
        user_client().auth.sign_out()
    finally:
        for key in ("user", "supabase"):
            st.session_state.pop(key, None)
        st.rerun()


def get_rows(table: str, columns: str = "*") -> list[dict]:
    return user_client().table(table).select(columns).execute().data or []


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    games = pd.DataFrame(get_rows("games"))
    picks = pd.DataFrame(get_rows("picks"))
    profiles = pd.DataFrame(get_rows("profiles"))
    if not games.empty:
        games["kickoff"] = pd.to_datetime(games["kickoff"], utc=True)
    return games, picks, profiles


def display_name(user_id: str, profiles: pd.DataFrame) -> str:
    if not profiles.empty:
        match = profiles[profiles["id"] == user_id]
        if not match.empty:
            return str(match.iloc[0]["display_name"])
    return "Player"


def season_and_week(games: pd.DataFrame) -> tuple[int, int]:
    default_season = int(setting("season", datetime.now(CT).year))
    if games.empty:
        return default_season, 1

    seasons = sorted(games["season"].dropna().astype(int).unique(), reverse=True)
    season = st.sidebar.selectbox("Season", seasons, index=0)
    season_games = games[games["season"] == season]
    weeks = sorted(season_games["week"].dropna().astype(int).unique())
    now = pd.Timestamp.now(tz="UTC")
    upcoming = season_games[season_games["kickoff"] >= now]
    suggested = int(upcoming["week"].min()) if not upcoming.empty else max(weeks)
    week = st.sidebar.selectbox("Week", weeks, index=weeks.index(suggested))
    return int(season), int(week)


def make_pick_page(
    games: pd.DataFrame, picks: pd.DataFrame, profiles: pd.DataFrame, season: int, week: int
) -> None:
    st.header(f"Make a pick — Week {week}")
    week_games = games[(games["season"] == season) & (games["week"] == week)].copy()
    if week_games.empty:
        st.info("No games have been imported for this week yet.")
        return

    week_games = week_games.sort_values("kickoff")
    now = pd.Timestamp.now(tz="UTC")
    available = week_games[(week_games["kickoff"] > now) & ~week_games["completed"]]
    mine = pd.DataFrame()
    if not picks.empty:
        mine = picks[
            (picks["user_id"] == current_user().id)
            & (picks["season"] == season)
            & (picks["week"] == week)
        ]

    if not mine.empty:
        row = mine.iloc[0]
        st.success(f"Your current pick: **{row['picked_team']}**")

    if available.empty:
        st.warning("All games for this week have started; picks are locked.")
        return

    choices: dict[str, tuple[str, str]] = {}
    for _, game in available.iterrows():
        kickoff = game["kickoff"].tz_convert(CT).strftime("%a %b %-d, %-I:%M %p CT")
        for team in (game["away_team"], game["home_team"]):
            choices[f"{team} — {game['away_team']} at {game['home_team']} ({kickoff})"] = (
                game["id"],
                team,
            )

    existing_label = None
    if not mine.empty:
        selected_game, selected_team = mine.iloc[0][["game_id", "picked_team"]]
        existing_label = next(
            (label for label, value in choices.items() if value == (selected_game, selected_team)),
            None,
        )

    with st.form("pick_form"):
        labels = list(choices)
        selected = st.selectbox(
            "Team",
            labels,
            index=labels.index(existing_label) if existing_label in labels else 0,
        )
        submitted = st.form_submit_button("Save pick", type="primary")

    if submitted:
        game_id, team = choices[selected]
        payload = {
            "user_id": current_user().id,
            "game_id": game_id,
            "season": season,
            "week": week,
            "picked_team": team,
        }
        try:
            user_client().table("picks").upsert(
                payload, on_conflict="user_id,season,week"
            ).execute()
            st.success(f"Saved {team} for Week {week}.")
            st.rerun()
        except Exception as exc:
            st.error(f"The pick could not be saved: {exc}")


def weekly_picks_page(
    games: pd.DataFrame, picks: pd.DataFrame, profiles: pd.DataFrame, season: int, week: int
) -> None:
    st.header(f"Weekly picks — Week {week}")
    if profiles.empty:
        st.info("No player profiles have been created yet.")
        return
    rows = []
    for _, profile in profiles.sort_values("display_name").iterrows():
        pick = pd.DataFrame()
        if not picks.empty:
            pick = picks[
                (picks["user_id"] == profile["id"])
                & (picks["season"] == season)
                & (picks["week"] == week)
            ]
        rows.append(
            {
                "Player": profile["display_name"],
                "Pick": "Not submitted" if pick.empty else pick.iloc[0]["picked_team"],
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def history_page(games: pd.DataFrame, picks: pd.DataFrame, profiles: pd.DataFrame) -> None:
    st.header("Pick history")
    if picks.empty:
        st.info("No picks have been submitted.")
        return
    game_columns = games[["id", "winner_team", "completed"]].rename(columns={"id": "game_id"})
    history = picks.merge(game_columns, on="game_id", how="left")
    history["Player"] = history["user_id"].map(lambda value: display_name(value, profiles))
    history["Result"] = history.apply(
        lambda row: "Pending"
        if not row.get("completed", False)
        else ("Win" if row["picked_team"] == row["winner_team"] else "Loss"),
        axis=1,
    )
    shown = history.rename(
        columns={"season": "Season", "week": "Week", "picked_team": "Pick"}
    )[["Season", "Week", "Player", "Pick", "Result"]]
    st.dataframe(
        shown.sort_values(["Season", "Week", "Player"], ascending=[False, False, True]),
        hide_index=True,
        use_container_width=True,
    )


def standings_page(
    games: pd.DataFrame, picks: pd.DataFrame, profiles: pd.DataFrame, season: int
) -> None:
    st.header(f"{season} standings")
    season_picks = picks[picks["season"] == season] if not picks.empty else picks
    season_games = games[games["season"] == season] if not games.empty else games
    rows = []
    for _, profile in profiles.iterrows():
        player_picks = (
            season_picks[season_picks["user_id"] == profile["id"]]
            if not season_picks.empty
            else season_picks
        )
        wins = losses = pending = 0
        for _, pick in player_picks.iterrows():
            game = season_games[season_games["id"] == pick["game_id"]]
            if game.empty or not bool(game.iloc[0]["completed"]):
                pending += 1
            elif game.iloc[0]["winner_team"] == pick["picked_team"]:
                wins += 1
            else:
                losses += 1
        rows.append(
            {
                "Player": profile["display_name"],
                "Wins": wins,
                "Losses": losses,
                "Pending": pending,
                "Win %": round(100 * wins / (wins + losses), 1) if wins + losses else 0.0,
            }
        )
    standings = pd.DataFrame(rows)
    if standings.empty:
        st.info("No player profiles have been created yet.")
    else:
        st.dataframe(
            standings.sort_values(["Wins", "Losses"], ascending=[False, True]),
            hide_index=True,
            use_container_width=True,
        )


def week_for_kickoff(kickoff: datetime) -> int:
    season_start = datetime.fromisoformat(str(setting("week_1_start", "2026-09-08T00:01:00-05:00")))
    return ((kickoff.astimezone(CT) - season_start).days // 7) + 1


def import_odds() -> int:
    api_key = st.secrets.get("odds_api", {}).get("api_key")
    if not api_key:
        raise ValueError("Missing [odds_api].api_key in Streamlit secrets.")
    response = requests.get(
        "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
        params={
            "apiKey": api_key,
            "regions": "us",
            "markets": "spreads,h2h",
            "oddsFormat": "american",
        },
        timeout=20,
    )
    response.raise_for_status()
    rows = []
    for game in response.json():
        kickoff = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
        week = week_for_kickoff(kickoff)
        if not 1 <= week <= 22:
            continue
        row = {
            "id": game["id"],
            "season": int(setting("season", kickoff.year)),
            "week": week,
            "away_team": game["away_team"],
            "home_team": game["home_team"],
            "kickoff": kickoff.isoformat(),
        }
        fanduel = next(
            (book for book in game.get("bookmakers", []) if book["key"] == "fanduel"), None
        )
        if fanduel:
            spreads = next(
                (market for market in fanduel.get("markets", []) if market["key"] == "spreads"),
                None,
            )
            if spreads:
                points = {outcome["name"]: outcome.get("point") for outcome in spreads["outcomes"]}
                row["away_spread"] = points.get(game["away_team"])
                row["home_spread"] = points.get(game["home_team"])
        rows.append(row)
    if rows:
        admin_client().table("games").upsert(rows).execute()
    return len(rows)


def admin_page(games: pd.DataFrame) -> None:
    st.header("Admin")
    st.caption("Import upcoming games and record final winners.")
    if st.button("Refresh games from The Odds API"):
        try:
            count = import_odds()
            st.success(f"Imported or updated {count} games.")
            st.rerun()
        except Exception as exc:
            st.error(f"Import failed: {exc}")

    if games.empty:
        return
    unfinished = games[~games["completed"]].sort_values("kickoff")
    if unfinished.empty:
        st.info("There are no unfinished games.")
        return
    labels = {
        f"Week {row['week']}: {row['away_team']} at {row['home_team']}": row
        for _, row in unfinished.iterrows()
    }
    with st.form("result_form"):
        selected = st.selectbox("Game", list(labels))
        game = labels[selected]
        winner = st.radio("Winner", [game["away_team"], game["home_team"]], horizontal=True)
        submitted = st.form_submit_button("Record final result")
    if submitted:
        admin_client().table("games").update(
            {"winner_team": winner, "completed": True}
        ).eq("id", game["id"]).execute()
        st.success("Result recorded.")
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="WeidaPicks", page_icon="🏈", layout="wide")
    if not current_user():
        login()
        return

    with st.sidebar:
        st.title("🏈 WeidaPicks")
        st.caption(current_user().email)
        if st.button("Sign out"):
            logout()

    try:
        games, picks, profiles = load_data()
    except Exception as exc:
        st.error(f"Could not load pool data. Has supabase_schema.sql been installed?\n\n{exc}")
        return

    page_names = ["Make Pick", "Weekly Picks", "History", "Standings"]
    if is_admin():
        page_names.append("Admin")
    page = st.sidebar.radio("Page", page_names)
    season, week = season_and_week(games)

    if page == "Make Pick":
        make_pick_page(games, picks, profiles, season, week)
    elif page == "Weekly Picks":
        weekly_picks_page(games, picks, profiles, season, week)
    elif page == "History":
        history_page(games, picks, profiles)
    elif page == "Standings":
        standings_page(games, picks, profiles, season)
    else:
        admin_page(games)


if __name__ == "__main__":
    main()
