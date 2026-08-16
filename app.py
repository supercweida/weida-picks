from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from supabase import Client, create_client


CT = ZoneInfo("America/Chicago")
STARTING_POINTS = 30.0


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


@st.cache_data(ttl=900, show_spinner=False)
def sync_completed_scores() -> dict:
    """Update recently completed games, at most once per app process per 15 minutes."""
    api_key = st.secrets.get("odds_api", {}).get("api_key")
    if not api_key:
        return {"updated": 0, "error": "The Odds API key is not configured."}

    try:
        client = admin_client()
        unfinished = (
            client.table("games")
            .select("id,kickoff")
            .eq("completed", False)
            .execute()
            .data
            or []
        )
        now = datetime.now(CT)
        earliest = now - timedelta(days=4)
        candidate_ids = {
            row["id"]
            for row in unfinished
            if earliest
            <= datetime.fromisoformat(row["kickoff"].replace("Z", "+00:00")).astimezone(CT)
            <= now
        }
        if not candidate_ids:
            return {"updated": 0, "error": None}

        response = requests.get(
            "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/scores",
            params={"apiKey": api_key, "daysFrom": 3, "dateFormat": "iso"},
            timeout=20,
        )
        response.raise_for_status()

        updated = 0
        for game in response.json():
            if game.get("id") not in candidate_ids or not game.get("completed"):
                continue
            scores = {
                score["name"]: int(score["score"])
                for score in (game.get("scores") or [])
                if score.get("score") is not None
            }
            home = game.get("home_team")
            away = game.get("away_team")
            if home not in scores or away not in scores:
                continue
            winner = None
            if scores[home] > scores[away]:
                winner = home
            elif scores[away] > scores[home]:
                winner = away
            client.table("games").update(
                {"winner_team": winner, "completed": True}
            ).eq("id", game["id"]).execute()
            updated += 1

        return {
            "updated": updated,
            "error": None,
            "remaining": response.headers.get("x-requests-remaining"),
        }
    except Exception as exc:
        return {"updated": 0, "error": str(exc)}


def display_name(user_id: str, profiles: pd.DataFrame) -> str:
    if not profiles.empty:
        match = profiles[profiles["id"] == user_id]
        if not match.empty:
            return str(match.iloc[0]["display_name"])
    return "Player"




def numeric_or_none(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def format_points(value) -> str:
    number = numeric_or_none(value)
    if number is None:
        return "TBD"
    if number == 0:
        return "0"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:g}"


def format_points_label(value) -> str:
    formatted = format_points(value)
    return formatted if formatted == "TBD" else f"{formatted} pts"


def format_spread(value) -> str:
    number = numeric_or_none(value)
    return "TBD" if number is None else format_points(number)


def format_kickoff(value) -> str:
    when = value.tz_convert(CT) if hasattr(value, "tz_convert") else value.astimezone(CT)
    return when.strftime("%a %b %d, %I:%M %p CT").replace(" 0", " ")


def pick_spread(game: pd.Series, team: str) -> float | None:
    if team == game["away_team"]:
        return numeric_or_none(game.get("away_spread"))
    if team == game["home_team"]:
        return numeric_or_none(game.get("home_spread"))
    return None


def movement_for_result(spread: float | None, won: bool) -> float | None:
    if spread is None:
        return None
    if won:
        return spread if spread > 0 else 0.0
    return -5.0 if spread >= 0 else -5.0 + spread


def pick_movement(game: pd.Series, team: str) -> float | None:
    if not bool(game.get("completed")):
        return None
    if pd.isna(game.get("winner_team")):
        return 0.0
    spread = pick_spread(game, team)
    return movement_for_result(spread, team == game["winner_team"])


def pick_is_autopick(pick: pd.Series) -> bool:
    return bool(pick.get("is_autopick", False)) if "is_autopick" in pick.index else False


def option_summary(game: pd.Series, team: str) -> dict[str, str]:
    spread = pick_spread(game, team)
    win = movement_for_result(spread, True)
    loss = movement_for_result(spread, False)
    return {
        "Pick": f"{team} {format_spread(spread)}",
        "Game": f"{game['away_team']} at {game['home_team']}",
        "Kickoff": format_kickoff(game["kickoff"]),
        "If Pick Wins": format_points_label(win),
        "If Pick Loses": format_points_label(loss),
    }


def option_choice_label(summary: dict[str, str]) -> str:
    return (
        f"{summary['Pick']} - win {summary['If Pick Wins']}, "
        f"lose {summary['If Pick Loses']}"
    )


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


def sidebar_page_nav(page_names: list[str]) -> str:
    current = st.session_state.get("page", page_names[0])
    if current not in page_names:
        current = page_names[0]
        st.session_state.page = current

    st.sidebar.caption("Navigation")
    for name in page_names:
        is_current = name == current
        clicked = st.sidebar.button(
            name,
            key=f"nav_{name.lower().replace(' ', '_')}",
            type="primary" if is_current else "secondary",
            use_container_width=True,
        )
        if clicked and not is_current:
            st.session_state.page = name
            st.rerun()
    return current


def make_pick_page(
    games: pd.DataFrame, picks: pd.DataFrame, profiles: pd.DataFrame, season: int, week: int
) -> None:
    st.header(f"Make a pick - Week {week}")
    if games.empty:
        st.info("No games have been imported yet. Ask an administrator to refresh games.")
        return
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
        current_game = week_games[week_games["id"] == row["game_id"]]
        if current_game.empty:
            st.success(f"Your current pick: **{row['picked_team']}**")
        else:
            summary = option_summary(current_game.iloc[0], row["picked_team"])
            source = "Autopick" if pick_is_autopick(row) else "Manual pick"
            st.success(
                f"Your current pick: **{summary['Pick']}** ({source}) - "
                f"win: {summary['If Pick Wins']}, lose: {summary['If Pick Loses']}"
            )

    if available.empty:
        st.warning("All games for this week have started; picks are locked.")
        return

    choices: dict[str, tuple[str, str]] = {}
    option_rows = []
    for _, game in available.iterrows():
        for team in (game["away_team"], game["home_team"]):
            summary = option_summary(game, team)
            label = option_choice_label(summary)
            option_rows.append({"Select": label, **summary})
            choices[label] = (game["id"], team)

    st.caption(
        "Each option uses the FanDuel spread and shows exactly what your score moves "
        "if that pick wins or loses."
    )
    st.dataframe(
        pd.DataFrame(option_rows)[["Game", "Pick", "Kickoff", "If Pick Wins", "If Pick Loses"]],
        hide_index=True,
        use_container_width=True,
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
        selected = st.radio(
            "Pick option",
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
            "is_autopick": False,
        }
        try:
            try:
                user_client().table("picks").upsert(
                    payload, on_conflict="user_id,season,week"
                ).execute()
            except Exception as exc:
                if "is_autopick" not in str(exc):
                    raise
                payload.pop("is_autopick", None)
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
    st.header(f"Weekly picks - Week {week}")
    if profiles.empty:
        st.info("No player profiles have been created yet.")
        return
    week_games = games[(games["season"] == season) & (games["week"] == week)] if not games.empty else games
    rows = []
    for _, profile in profiles.sort_values("display_name").iterrows():
        pick = pd.DataFrame()
        if not picks.empty:
            pick = picks[
                (picks["user_id"] == profile["id"])
                & (picks["season"] == season)
                & (picks["week"] == week)
            ]
        if pick.empty:
            rows.append(
                {
                    "Player": profile["display_name"],
                    "Pick": "Not submitted",
                    "Source": "",
                    "Points if Win": "",
                    "Points if Loss": "",
                    "Status": "",
                }
            )
            continue
        pick_row = pick.iloc[0]
        game = week_games[week_games["id"] == pick_row["game_id"]]
        if game.empty:
            pick_label = pick_row["picked_team"]
            win_points = loss_points = "TBD"
            status = "Game not found"
        else:
            game_row = game.iloc[0]
            summary = option_summary(game_row, pick_row["picked_team"])
            pick_label = summary["Pick"]
            win_points = summary["If Pick Wins"]
            loss_points = summary["If Pick Loses"]
            movement = pick_movement(game_row, pick_row["picked_team"])
            if not bool(game_row.get("completed")):
                status = "Pending"
            elif pd.isna(game_row.get("winner_team")):
                status = "Tie, 0 pts"
            else:
                result = "Win" if game_row["winner_team"] == pick_row["picked_team"] else "Loss"
                status = f"{result}, {format_points_label(movement)}"
        rows.append(
            {
                "Player": profile["display_name"],
                "Pick": pick_label,
                "Source": "Autopick" if pick_is_autopick(pick_row) else "Manual",
                "Points if Win": win_points,
                "Points if Loss": loss_points,
                "Status": status,
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

def history_page(games: pd.DataFrame, picks: pd.DataFrame, profiles: pd.DataFrame) -> None:
    st.header("Pick history")
    if picks.empty:
        st.info("No picks have been submitted.")
        return
    game_columns = games[
        ["id", "away_team", "home_team", "away_spread", "home_spread", "winner_team", "completed"]
    ].rename(columns={"id": "game_id"})
    history = picks.merge(game_columns, on="game_id", how="left")
    history["Player"] = history["user_id"].map(lambda value: display_name(value, profiles))
    history["Spread"] = history.apply(
        lambda row: format_spread(pick_spread(row, row["picked_team"])), axis=1
    )
    history["Source"] = history.apply(
        lambda row: "Autopick" if pick_is_autopick(row) else "Manual", axis=1
    )
    history["Result"] = history.apply(
        lambda row: "Pending"
        if not row.get("completed", False)
        else (
            "Tie"
            if pd.isna(row["winner_team"])
            else ("Win" if row["picked_team"] == row["winner_team"] else "Loss")
        ),
        axis=1,
    )
    history["Points"] = history.apply(
        lambda row: "Pending"
        if not row.get("completed", False)
        else format_points(pick_movement(row, row["picked_team"])),
        axis=1,
    )
    shown = history.rename(
        columns={"season": "Season", "week": "Week", "picked_team": "Pick"}
    )[["Season", "Week", "Player", "Pick", "Spread", "Source", "Result", "Points"]]
    st.dataframe(
        shown.sort_values(["Season", "Week", "Player"], ascending=[False, False, True]),
        hide_index=True,
        use_container_width=True,
    )

def standings_page(
    games: pd.DataFrame, picks: pd.DataFrame, profiles: pd.DataFrame, season: int
) -> None:
    st.header(f"{season} standings")
    st.caption(f"Everyone starts at {STARTING_POINTS:g} points. Completed picks add the spread-based movement from the pool rules.")
    season_picks = picks[picks["season"] == season] if not picks.empty else picks
    season_games = games[games["season"] == season] if not games.empty else games
    rows = []
    for _, profile in profiles.iterrows():
        player_picks = (
            season_picks[season_picks["user_id"] == profile["id"]]
            if not season_picks.empty
            else season_picks
        )
        wins = losses = ties = pending = needs_spread = autopicks = 0
        movement_total = 0.0
        for _, pick in player_picks.iterrows():
            if pick_is_autopick(pick):
                autopicks += 1
            game = season_games[season_games["id"] == pick["game_id"]]
            if game.empty or not bool(game.iloc[0]["completed"]):
                pending += 1
                continue
            game_row = game.iloc[0]
            movement = pick_movement(game_row, pick["picked_team"])
            if pd.isna(game_row["winner_team"]):
                ties += 1
                movement_total += 0.0
            elif game_row["winner_team"] == pick["picked_team"]:
                wins += 1
                if movement is None:
                    needs_spread += 1
                else:
                    movement_total += movement
            else:
                losses += 1
                if movement is None:
                    needs_spread += 1
                else:
                    movement_total += movement
        completed = wins + losses + ties
        rows.append(
            {
                "Player": profile["display_name"],
                "Points": STARTING_POINTS + movement_total,
                "Movement": movement_total,
                "Wins": wins,
                "Losses": losses,
                "Ties": ties,
                "Pending": pending,
                "Needs Spread": needs_spread,
                "Autopicks": autopicks,
                "Win %": round(100 * (wins + 0.5 * ties) / completed, 1) if completed else 0.0,
            }
        )
    standings = pd.DataFrame(rows)
    if standings.empty:
        st.info("No player profiles have been created yet.")
    else:
        standings["Points"] = standings["Points"].map(lambda value: round(value, 1))
        standings["Movement"] = standings["Movement"].map(lambda value: format_points(value))
        st.dataframe(
            standings.sort_values(["Points", "Wins", "Losses"], ascending=[False, False, True]),
            hide_index=True,
            use_container_width=True,
        )

def week_for_kickoff(kickoff: datetime) -> int:
    season_start = datetime.fromisoformat(str(setting("week_1_start", "2026-09-08T00:01:00-05:00")))
    return ((kickoff.astimezone(CT) - season_start).days // 7) + 1


def import_games_and_odds() -> dict:
    api_key = st.secrets.get("odds_api", {}).get("api_key")
    if not api_key:
        raise ValueError("Missing [odds_api].api_key in Streamlit secrets.")

    events_response = requests.get(
        "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events",
        params={"apiKey": api_key, "dateFormat": "iso"},
        timeout=20,
    )
    events_response.raise_for_status()

    odds_response = requests.get(
        "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
        params={
            "apiKey": api_key,
            "regions": "us",
            "markets": "spreads",
            "oddsFormat": "american",
        },
        timeout=20,
    )
    odds_response.raise_for_status()
    odds_by_id = {game["id"]: game for game in odds_response.json()}

    rows = []
    priced_games = 0
    for game in events_response.json():
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
            "away_spread": None,
            "home_spread": None,
        }
        odds_game = odds_by_id.get(game["id"], {})
        fanduel = next(
            (book for book in odds_game.get("bookmakers", []) if book["key"] == "fanduel"),
            None,
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
                priced_games += 1
        rows.append(row)
    if rows:
        admin_client().table("games").upsert(rows).execute()
    return {"games": len(rows), "priced_games": priced_games}


def admin_page(games: pd.DataFrame) -> None:
    st.header("Admin")
    st.caption("Import the upcoming schedule and FanDuel odds, and record final winners.")
    if message := st.session_state.pop("admin_message", None):
        st.success(message)
    if st.button("Refresh games and odds"):
        try:
            result = import_games_and_odds()
            st.session_state.admin_message = (
                f"Imported or updated {result['games']} games; "
                f"{result['priced_games']} currently have FanDuel spreads."
            )
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
        winner = st.radio(
            "Winner", [game["away_team"], game["home_team"], "Tie"], horizontal=True
        )
        submitted = st.form_submit_button("Record final result")
    if submitted:
        admin_client().table("games").update(
            {"winner_team": None if winner == "Tie" else winner, "completed": True}
        ).eq("id", game["id"]).execute()
        st.success("Result recorded.")
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="WeidaPicks", layout="wide")
    if not current_user():
        login()
        return

    score_sync = sync_completed_scores()

    with st.sidebar:
        st.title("WeidaPicks")
        st.caption(current_user().email)
        if score_sync.get("updated"):
            st.success(f"Updated {score_sync['updated']} final score(s).")
        if score_sync.get("error") and is_admin():
            st.warning(f"Automatic score update failed: {score_sync['error']}")
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
    page = sidebar_page_nav(page_names)
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
