# WeidaPicks

A small Streamlit tool for generating a distributable NFL confidence survivor
workbook.

The app keeps the season record locally in `data/history.csv`. Each week, enter
the four players' completed-week picks and results, save them, then download the
next workbook for distribution. Uploading an older workbook is only needed if
you want to import or recover history.

## Pool Format

- Four participants.
- Each week, each participant picks one NFL team to win.
- Each pick also uses one confidence value from 1 through 18.
- A participant cannot reuse a team during the season.
- A participant cannot reuse a confidence value during the season.

## Workbook Tabs

Each generated workbook includes:

- `Availability`: each player's remaining teams and point values.
- `Week N Matchups`: the upcoming week's NFL schedule from The Odds API.
- `Pick Sheet`: a simple sheet participants can use for the next pick.
- `History`: a copy of the season record included for audit/recovery.
- One tab per matchup showing which players still have each team available.

## Secrets

Only The Odds API key is required:

```toml
[odds_api]
api_key = "your-key"

[app]
season = 2026
week_1_start = "2026-09-08T00:01:00-05:00"
participants = ["Weida", "Player 2", "Player 3", "Player 4"]
```

`participants` is optional; you can also edit the names in the sidebar.

## Run Locally

Use Python 3.11 or newer:

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```
