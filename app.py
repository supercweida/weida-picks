from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from re import sub
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st


CT = ZoneInfo("America/Chicago")
HISTORY_PATH = Path("data") / "history.csv"
POINT_VALUES = list(range(1, 19))
NFL_TEAMS = [
    "Arizona Cardinals",
    "Atlanta Falcons",
    "Baltimore Ravens",
    "Buffalo Bills",
    "Carolina Panthers",
    "Chicago Bears",
    "Cincinnati Bengals",
    "Cleveland Browns",
    "Dallas Cowboys",
    "Denver Broncos",
    "Detroit Lions",
    "Green Bay Packers",
    "Houston Texans",
    "Indianapolis Colts",
    "Jacksonville Jaguars",
    "Kansas City Chiefs",
    "Las Vegas Raiders",
    "Los Angeles Chargers",
    "Los Angeles Rams",
    "Miami Dolphins",
    "Minnesota Vikings",
    "New England Patriots",
    "New Orleans Saints",
    "New York Giants",
    "New York Jets",
    "Philadelphia Eagles",
    "Pittsburgh Steelers",
    "San Francisco 49ers",
    "Seattle Seahawks",
    "Tampa Bay Buccaneers",
    "Tennessee Titans",
    "Washington Commanders",
]
HISTORY_COLUMNS = ["Season", "Week", "Player", "Team", "Point Value", "Result"]
MATCHUP_COLUMNS = ["Week", "Kickoff", "Away Team", "Home Team", "Away Spread", "Home Spread"]


def app_setting(name: str, default=None):
    return st.secrets.get("app", {}).get(name, default)


def default_participants() -> list[str]:
    names = app_setting("participants", None) or ["Weida", "Player 2", "Player 3", "Player 4"]
    return [str(name).strip() for name in names if str(name).strip()]


def format_kickoff(value) -> str:
    if pd.isna(value):
        return ""
    when = pd.to_datetime(value, utc=True).tz_convert(CT)
    return when.strftime("%a %b %d, %I:%M %p CT").replace(" 0", " ")


def week_for_kickoff(kickoff: datetime) -> int:
    start = datetime.fromisoformat(str(app_setting("week_1_start", "2026-09-08T00:01:00-05:00")))
    return ((kickoff.astimezone(CT) - start).days // 7) + 1


def safe_sheet_name(name: str, existing: set[str]) -> str:
    cleaned = sub(r"[\[\]:*?/\\]", "-", name)[:31].strip() or "Sheet"
    candidate = cleaned
    suffix = 2
    while candidate in existing:
        tail = f" {suffix}"
        candidate = f"{cleaned[:31 - len(tail)]}{tail}"
        suffix += 1
    existing.add(candidate)
    return candidate


def normalize_history(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    normalized = history.copy()
    for column in HISTORY_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    normalized = normalized[HISTORY_COLUMNS]
    normalized["Season"] = pd.to_numeric(normalized["Season"], errors="coerce").astype("Int64")
    normalized["Week"] = pd.to_numeric(normalized["Week"], errors="coerce").astype("Int64")
    normalized["Point Value"] = pd.to_numeric(normalized["Point Value"], errors="coerce").astype("Int64")
    for column in ["Player", "Team", "Result"]:
        normalized[column] = normalized[column].fillna("").astype(str).str.strip()
    return normalized.dropna(subset=["Season", "Week"])


def load_local_history() -> pd.DataFrame:
    if not HISTORY_PATH.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        return normalize_history(pd.read_csv(HISTORY_PATH))
    except Exception as exc:
        st.warning(f"Could not read saved history from {HISTORY_PATH}: {exc}")
        return pd.DataFrame(columns=HISTORY_COLUMNS)


def save_local_history(history: pd.DataFrame) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    normalize_history(history).to_csv(HISTORY_PATH, index=False)


def load_workbook_history(uploaded_file) -> pd.DataFrame:
    if uploaded_file is None:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        return normalize_history(pd.read_excel(uploaded_file, sheet_name="History"))
    except Exception as exc:
        st.warning(f"Could not read a History tab from that workbook: {exc}")
        return pd.DataFrame(columns=HISTORY_COLUMNS)


@st.cache_data(ttl=900, show_spinner=False)
def fetch_matchups(season: int) -> tuple[pd.DataFrame, str | None]:
    api_key = st.secrets.get("odds_api", {}).get("api_key")
    if not api_key:
        return pd.DataFrame(columns=MATCHUP_COLUMNS), "Missing [odds_api].api_key in Streamlit secrets."

    try:
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
                "dateFormat": "iso",
            },
            timeout=20,
        )
        odds_response.raise_for_status()
        odds_by_id = {game["id"]: game for game in odds_response.json()}

        rows = []
        for game in events_response.json():
            kickoff = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
            week = week_for_kickoff(kickoff)
            if not 1 <= week <= 18:
                continue

            row = {
                "Week": week,
                "Kickoff": kickoff.isoformat(),
                "Away Team": game["away_team"],
                "Home Team": game["home_team"],
                "Away Spread": None,
                "Home Spread": None,
            }
            fanduel = next(
                (
                    book
                    for book in odds_by_id.get(game["id"], {}).get("bookmakers", [])
                    if book.get("key") == "fanduel"
                ),
                None,
            )
            if fanduel:
                spreads = next(
                    (market for market in fanduel.get("markets", []) if market.get("key") == "spreads"),
                    None,
                )
                if spreads:
                    points = {
                        outcome["name"]: outcome.get("point")
                        for outcome in spreads.get("outcomes", [])
                    }
                    row["Away Spread"] = points.get(row["Away Team"])
                    row["Home Spread"] = points.get(row["Home Team"])
            rows.append(row)

        matchups = pd.DataFrame(rows, columns=MATCHUP_COLUMNS)
        matchups["Season"] = season
        return matchups.sort_values(["Week", "Kickoff"]), None
    except Exception as exc:
        return pd.DataFrame(columns=MATCHUP_COLUMNS), str(exc)


def current_week_entries(
    participants: list[str],
    season: int,
    week: int,
    saved_history: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    st.subheader("Enter completed-week picks")
    st.caption("These entries can be saved locally, then used to generate every future workbook.")
    week_history = saved_history[
        (saved_history["Season"].astype("Int64") == season)
        & (saved_history["Week"].astype("Int64") == week)
    ] if not saved_history.empty else saved_history
    for player in participants:
        existing = week_history[week_history["Player"] == player] if not week_history.empty else week_history
        existing_team = str(existing.iloc[0]["Team"]) if not existing.empty else ""
        existing_points = (
            int(existing.iloc[0]["Point Value"])
            if not existing.empty and pd.notna(existing.iloc[0]["Point Value"])
            else ""
        )
        existing_result = str(existing.iloc[0]["Result"]) if not existing.empty else "Pending"
        cols = st.columns([2, 3, 2, 2])
        cols[0].markdown(f"**{player}**")
        team_options = [""] + NFL_TEAMS
        point_options = [""] + POINT_VALUES
        result_options = ["Pending", "Win", "Loss", "Tie"]
        team = cols[1].selectbox(
            "Team",
            team_options,
            index=team_options.index(existing_team) if existing_team in team_options else 0,
            key=f"team_{player}_{season}_{week}",
            label_visibility="collapsed",
        )
        points = cols[2].selectbox(
            "Points",
            point_options,
            index=point_options.index(existing_points) if existing_points in point_options else 0,
            key=f"points_{player}_{season}_{week}",
            label_visibility="collapsed",
        )
        result = cols[3].selectbox(
            "Result",
            result_options,
            index=result_options.index(existing_result) if existing_result in result_options else 0,
            key=f"result_{player}_{season}_{week}",
            label_visibility="collapsed",
        )
        if team and points:
            rows.append(
                {
                    "Season": season,
                    "Week": week,
                    "Player": player,
                    "Team": team,
                    "Point Value": int(points),
                    "Result": result,
                }
            )
    return normalize_history(pd.DataFrame(rows))


def combine_history(history: pd.DataFrame, entries: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    history = normalize_history(history)
    if entries.empty:
        return history
    keep = ~(
        (history["Season"].astype("Int64") == season)
        & (history["Week"].astype("Int64") == week)
        & (history["Player"].isin(entries["Player"]))
    )
    return normalize_history(pd.concat([history[keep], entries], ignore_index=True))


def availability_rows(history: pd.DataFrame, participants: list[str], season: int) -> pd.DataFrame:
    rows = []
    season_history = history[history["Season"].astype("Int64") == season] if not history.empty else history
    for player in participants:
        player_history = season_history[season_history["Player"] == player] if not season_history.empty else season_history
        used_teams = sorted(set(player_history["Team"].dropna()) - {""})
        used_points = sorted(int(value) for value in player_history["Point Value"].dropna())
        rows.append(
            {
                "Player": player,
                "Available Teams": ", ".join(team for team in NFL_TEAMS if team not in used_teams),
                "Used Teams": ", ".join(used_teams),
                "Available Point Values": ", ".join(str(value) for value in POINT_VALUES if value not in used_points),
                "Used Point Values": ", ".join(str(value) for value in used_points),
            }
        )
    return pd.DataFrame(rows)


def validation_messages(history: pd.DataFrame, season: int) -> list[str]:
    messages = []
    season_history = history[history["Season"].astype("Int64") == season] if not history.empty else history
    if season_history.empty:
        return messages
    for player, player_history in season_history.groupby("Player"):
        team_repeats = player_history[player_history.duplicated("Team", keep=False) & (player_history["Team"] != "")]
        point_history = player_history[player_history["Point Value"].notna()]
        point_repeats = point_history[point_history.duplicated("Point Value", keep=False)]
        if not team_repeats.empty:
            messages.append(f"{player} has a reused team: {', '.join(sorted(team_repeats['Team'].unique()))}.")
        if not point_repeats.empty:
            points = [str(int(value)) for value in sorted(point_repeats["Point Value"].dropna().unique())]
            messages.append(f"{player} has a reused point value: {', '.join(points)}.")
    return messages


def matchup_tab(matchup: pd.Series, availability: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    away = matchup["Away Team"]
    home = matchup["Home Team"]
    rows = []
    for _, player in availability.iterrows():
        player_history = history[history["Player"] == player["Player"]] if not history.empty else history
        used_teams = set(player_history["Team"].dropna())
        rows.append(
            {
                "Player": player["Player"],
                f"{away} Available": "Yes" if away not in used_teams else "No",
                f"{home} Available": "Yes" if home not in used_teams else "No",
                "Available Point Values": player["Available Point Values"],
            }
        )
    return pd.DataFrame(rows)


def build_workbook(
    history: pd.DataFrame,
    matchups: pd.DataFrame,
    participants: list[str],
    season: int,
    distribution_week: int,
) -> bytes:
    availability = availability_rows(history, participants, season)
    week_matchups = matchups[matchups["Week"].astype(int) == distribution_week].copy()
    week_matchups["Kickoff"] = week_matchups["Kickoff"].map(format_kickoff)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        availability.to_excel(writer, sheet_name="Availability", index=False)
        week_matchups[MATCHUP_COLUMNS].to_excel(writer, sheet_name=f"Week {distribution_week} Matchups", index=False)
        history.sort_values(["Week", "Player"]).to_excel(writer, sheet_name="History", index=False)

        pick_sheet = availability[["Player", "Available Teams", "Available Point Values"]].copy()
        pick_sheet.insert(1, "Week", distribution_week)
        pick_sheet["Pick"] = ""
        pick_sheet["Point Value"] = ""
        pick_sheet.to_excel(writer, sheet_name="Pick Sheet", index=False)

        used_names = set(writer.book.sheetnames)
        for _, matchup in week_matchups.iterrows():
            label = safe_sheet_name(
                f"{matchup['Away Team'].split()[-1]} at {matchup['Home Team'].split()[-1]}",
                used_names,
            )
            tab = matchup_tab(matchup, availability, history)
            tab.to_excel(writer, sheet_name=label, index=False, startrow=3)
            sheet = writer.book[label]
            sheet["A1"] = f"{matchup['Away Team']} at {matchup['Home Team']}"
            sheet["A2"] = matchup["Kickoff"]
            sheet["D1"] = "Spread"
            sheet["D2"] = f"{matchup['Away Spread']} / {matchup['Home Spread']}"

        for sheet in writer.book.worksheets:
            sheet.freeze_panes = "A2"
            for column_cells in sheet.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 55)

    return output.getvalue()


def main() -> None:
    st.set_page_config(page_title="WeidaPicks Workbook", layout="wide")
    st.title("WeidaPicks Workbook Generator")
    st.caption("Generate the distributable confidence survivor spreadsheet. No login, no database.")

    with st.sidebar:
        season = st.number_input("Season", min_value=2024, max_value=2035, value=int(app_setting("season", 2026)))
        completed_week = st.number_input("Completed week to record", min_value=1, max_value=18, value=1)
        distribution_week = st.number_input("Workbook week to distribute", min_value=1, max_value=18, value=min(int(completed_week) + 1, 18))
        participant_text = st.text_area("Participants", "\n".join(default_participants()), height=120)
        participants = [name.strip() for name in participant_text.splitlines() if name.strip()]

    saved_history = load_local_history()

    with st.expander("Import history from an older workbook"):
        uploaded = st.file_uploader("Optional workbook import", type=["xlsx"])
        imported_history = load_workbook_history(uploaded)
        if uploaded is not None and st.button("Save imported history locally"):
            save_local_history(imported_history)
            st.success(f"Imported history saved to {HISTORY_PATH}.")
            st.rerun()

    current_entries = current_week_entries(participants, int(season), int(completed_week), saved_history)
    history = combine_history(saved_history, current_entries, int(season), int(completed_week))

    cols = st.columns([1, 3])
    if cols[0].button("Save weekly entries", type="primary", use_container_width=True):
        save_local_history(history)
        st.success(f"Saved season history to {HISTORY_PATH}.")
        st.rerun()
    cols[1].caption(f"Saved history file: {HISTORY_PATH}")

    st.subheader("Season availability")
    availability = availability_rows(history, participants, int(season))
    st.dataframe(availability, hide_index=True, use_container_width=True)

    for message in validation_messages(history, int(season)):
        st.error(message)

    with st.spinner("Fetching NFL matchups from The Odds API..."):
        matchups, error = fetch_matchups(int(season))
    if error:
        st.warning(f"Odds API schedule could not be loaded: {error}")

    week_matchups = matchups[matchups["Week"].astype(str) == str(int(distribution_week))] if not matchups.empty else matchups
    if week_matchups.empty:
        st.info("No matchup data is available for the distribution week yet.")
    else:
        preview = week_matchups.copy()
        preview["Kickoff"] = preview["Kickoff"].map(format_kickoff)
        st.subheader(f"Week {int(distribution_week)} matchups")
        st.dataframe(preview[MATCHUP_COLUMNS], hide_index=True, use_container_width=True)

    workbook = build_workbook(history, matchups, participants, int(season), int(distribution_week))
    st.download_button(
        "Download distributable workbook",
        data=workbook,
        file_name=f"WeidaPicks_{int(season)}_Week_{int(distribution_week)}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )


if __name__ == "__main__":
    main()
