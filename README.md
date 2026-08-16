# WeidaPicks

A four-player NFL pick pool built with Streamlit and Supabase. Players sign in,
make one pick per week, review pick history, and see standings. An administrator
imports upcoming games from The Odds API and records final winners.

## Scoring

Everyone starts each season with 30 points. Scores move after completed games
using the picked team's FanDuel spread:

- Winning underdog: gain the spread, such as `+3.5`.
- Winning favorite: no point movement.
- Losing underdog: lose 5 points.
- Losing favorite: lose 5 points plus the favorite spread, such as `-8.5` for
  a `-3.5` favorite.

Moneyline odds are not used for scoring.

## Supabase setup

1. Create a free project at <https://supabase.com>.
2. Open **SQL Editor**, paste `supabase_schema.sql`, and run it once.
3. In **Authentication > Users**, create the four users with email/password.
   Turn on **Auto Confirm User** when creating them so no confirmation email is
   needed.
4. Add one profile row for each user in SQL Editor:

   ```sql
   insert into public.profiles (id, display_name)
   select id, 'Weida' from auth.users where email = 'weida@example.com';
   ```

   Repeat that statement with the other three names and emails.
5. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill
   in the project URL, publishable/anon key, service-role key, a newly rotated
   Odds API key, and the administrator's email.

The service-role key bypasses database security. Never commit
`.streamlit/secrets.toml` or expose that key in browser-side code.

## Run locally

Use Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Sign in as the administrator and use **Admin > Refresh games and odds** before
the week's picks. The schedule comes from the Events endpoint, so games can be
shown before FanDuel publishes betting markets. When any signed-in player opens
the app, it checks for recently completed games and updates results
automatically. Score checks are shared and limited to once every 15 minutes, and
no API call is made when there are no started, unfinished games. The API only
returns finals from the prior three days, so the administrator can still record
a winner manually as a fallback. Results immediately flow into pick history and
standings.

## Deploy free on Streamlit Community Cloud

1. Push this repository to GitHub.
2. Create an app at <https://share.streamlit.io> with `app.py` as the
   entrypoint.
3. In the app's **Settings > Secrets**, paste the contents of your local
   `.streamlit/secrets.toml`.
4. Deploy and share the resulting URL with the four players.

The database enforces one pick per player per week and rejects new or changed
picks after the selected game's kickoff. Streamlit may hibernate after a period
without traffic, but Supabase keeps the pool data independently of the app.
