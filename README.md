# NBA Game Lab

An English-language NBA assistant with focused ESPN tools, player photos, and
transparent single-game advanced statistics. Built with FastAPI, LiteLLM, and Gemini.

- The harness loop is the same one from `qwen-tool-calling`, wrapped in `run_agent()`.
- `/chat` preserves the starter response format; sessions are stored locally as JSON files.
- The model is: `vertex_ai/gemini-3.5-flash-lite` in the `global` location.
- `/chat` also returns the tool calls the harness made, and the page shows them
  above the assistant's answer.

## Setup

1. A GCP project with billing and the Agent Platform API enabled
   (older docs and the endpoint itself still call it Vertex AI)
2. `gcloud auth application-default login`. The app uses your gcloud default
   project, so run `gemini-hello-world` first to check it.
3. `uv run app.py`, then open http://localhost:8000

## Tools

Each tool returns only its own information category. Team-specific tools require
`team_name`; player-specific tools require `player_name` and optionally accept
`team_name` to narrow the search. Names and abbreviations are matched without
case sensitivity. Ambiguous names or games return a clarification message.

| Tool | Result | Additional parameters |
| --- | --- | --- |
| `display_panel` | Prepare a final visual panel from supplied data, without lookup or calculation | Required structured `panel` |
| `game_players` | Player IDs, names, teams, and did-not-play status in one game | Optional `team_name` |
| `find_games` | Game IDs, matchups, and start times | Optional `team_name` |
| `game_info` | Start time and participating teams | Optional `team_name` |
| `game_status` | State, completion, period, and clock | Optional `team_name` |
| `game_score` | Team names and full-game or period scores | `quarter=0`; optional `team_name` |
| `team_game_info` | Home/away designation and final winner flag | Required `team_name` |
| `team_game_stats` | One team's single-game statistics | Required `team_name`; optional `stat` |
| `team_game_leader` | ESPN's team leaders for one category | Required `team_name`, `category` |
| `player_info` | Name, ID, position, jersey, and headshot | Required `player_name` |
| `player_game_status` | Starter, did-not-play, ejection, and applicable absence reason | Required `player_name` |
| `player_game_stats` | One player's single-game statistics | Required `player_name`; optional `stat` |
| `game_plays` | Paginated play-by-play events | Optional `team_name`, `period`, `event_type`, `offset`, `limit` |
| `player_shots` | A player's shot attempts, outcomes, and available coordinates | Required `player_name`; optional `period`, `offset`, `limit` |
| `game_venue` | Venue name and location | Optional `team_name` |
| `game_officials` | Officials and roles | Optional `team_name` |
| `team_injuries` | Team injury reports and supplied report dates | Required `team_name` |
| `game_recap` | Recap headline, summary, publication time, and link | Optional `team_name` |
| `game_videos` | Video titles, descriptions, and links | Optional `team_name` |
| `player_advanced_stats` | Player efficiency metrics, formulas, inputs, and headshot | Required `player_name`; optional `team_name`, `metric` |
| `team_advanced_stats` | Team efficiency metrics and estimated possession-based ratings | Required `team_name`; optional `metric` |
| `team_standings` | Team standings statistics and supplied season label | Required `team_name` |

### Locating a game

All tools accept `date` in YYYY-MM-DD format. It defaults to today in
America/New_York when no game ID is supplied. All tools except `find_games`
also accept `event_id`. Use `find_games` to discover the ID and reuse it across
queries. An ID works without a date; if both are supplied, they must agree.
Player names select players within a game, not games across the schedule:
provide an event ID or a date and team when multiple games are scheduled.

### Scores and statistics

`game_score` uses `quarter=0` for the full-game total (including overtime),
`1` through `4` for an individual quarter, `5` for the first overtime, `6` for
the second overtime, and so on. Period scores are not cumulative. For live games,
returned scores are the current totals. Unstarted or unavailable periods are
reported as unavailable rather than fabricated as zero.

Statistics can be selected by their ESPN name or label, such as `points`,
`PTS`, `rebounds`, or `REB`. Unsupported statistics return the available names.
Leader categories depend on ESPN's response, commonly `points`, `assists`, and
`rebounds`. No overall best-player calculation or award selection is performed.

Play and shot tools default to 50 events per page, with a maximum of 100.
Use `next_offset` to request subsequent pages. A period of `0` selects all periods.

### Data availability

The tools use ESPN's scoreboard and game-summary responses. Missing fields,
empty media, unavailable player data, and network failures are reported explicitly.
A player profile is sourced from the selected game's box score, so a player not
listed there cannot be retrieved through that game.

Injury reports and standings attached to historical games may describe the current
date or season. Their outputs preserve report dates or season labels and explain
this limitation. An empty injury list does not establish that every player is
healthy. A ranking is not inferred from the order of standings entries.

### Examples

- "Find Orlando's game on January 15, 2026."
- "What was the score in the third quarter?"
- "How many rebounds did Paolo Banchero have in that game?"
- "Who led Orlando in assists?"
- "Where was the game played, and who were the officials?"

### Verification

From the repository root, run `.venv/bin/python -m unittest discover -s test -t . -v`
for offline tests in `test/` covering all
22 tools, output isolation, name and game ambiguity, quarter/overtime scoring,
statistic selection, shot attribution, pagination, missing data, and errors.

## Advanced statistics and photos

`player_advanced_stats` computes six metrics: `efg_pct`, `ts_pct`, `game_score`,
`ast_to_ratio`, `three_point_attempt_rate`, and `free_throw_rate`.
`team_advanced_stats` computes ten: `efg_pct`, `ts_pct`,
`three_point_attempt_rate`, `free_throw_rate`, `tov_pct`, `oreb_pct`,
`estimated_possessions`, `offensive_rating`, `defensive_rating`, and `net_rating`.
Both accept `metric="all"` or one metric name. Common aliases such as `TS%`,
`eFG%`, `GmSc`, `AST/TO`, `3PAr`, `FTr`, `ORtg`, and `DRtg` are accepted.

Every metric includes its value, units, formula, actual inputs, and whether it
is an estimate. Percentages are returned on a 0-100 scale (and can legitimately
exceed 100 for eFG% or TS%); FTr is the ratio FTA/FGA. Values are rounded only
at the end. Missing inputs and zero denominators yield `null` with an explanation,
not zero or infinity. Live-game outputs are marked provisional. Pregame and
DNP player requests return availability messages.

Team possessions use a simple local box-score estimate:
`0.5 * ((FGA + 0.44*FTA - OREB + TO) + (opponent FGA + 0.44*opponent FTA - opponent OREB + opponent TO))`.
Ratings use this same unrounded estimate for both teams. They include overtime
and are not pace per 48 minutes. They may differ from official NBA ratings based
on counted possessions. Total team turnovers are used when available; otherwise
the supplied turnovers field is used. Missing opponent data does not suppress
independent shooting metrics. These tools do not compute player on/off ratings,
USG%, PER, BPM, or an overall award winner.

Formula references: [Basketball Reference glossary](https://www.basketball-reference.com/about/glossary.html),
[Four Factors](https://www.basketball-reference.com/about/factors.html), and
[NBA glossary](https://www.nba.com/stats/help/glossary).
The simplified possession estimate is explicitly the application's chosen method.

Data lookup tools return data only. The separate `display_panel(panel)` tool takes
previously retrieved player/team data and selected metrics as structured parameters;
it does not fetch data, compute statistics, or rank players. The model requests a
panel only after research is complete when a visual is useful or explicitly requested.
The frontend displays these panels after the final answer, never for intermediate
`player_info` or advanced-stat queries. Saved conversations use the same rule.

Panels support ESPN headshots with a missing-image fallback and expandable metric
formulas/inputs. Raw tool calls remain in collapsed logs. Text is rendered safely as
text, and image URLs are restricted to HTTPS ESPN headshots.

Try these independent queries:

- "Show Paolo Banchero's photo and advanced stats against Memphis on January 15, 2026."
- "What was Orlando's estimated offensive rating against Memphis on January 15, 2026?"
- "Calculate Paolo Banchero's TS% against Memphis on January 15, 2026, and show the inputs."

## Saved conversations

Click **New chat** to start with fresh context without deleting previous chats.
Use the **Saved conversations** dropdown to reopen a conversation and continue it,
including its previous answers, tool logs, photos, and metric cards. Empty new
chats are saved only after the first message. Refreshing opens a blank chat; saved
conversations remain available in the dropdown.

History is stored on the machine running the server in
`chat_history/<browser-id>/<session-id>.json` and is excluded from Git. Each file
contains model context and displayable turns. A persistent HTTP-only browser cookie
scopes the history list and access to that browser; different chats have separate
context. This is anonymous browser isolation, not an account/login system. Clearing
cookies loses access to that browser's saved history. Local server restarts retain
history as long as the files and browser cookie remain.

The file store uses atomic replacement and a single-process lock. It is intended
for local, single-worker use. Multi-instance deployment needs shared persistent
storage; container-local files should not be relied upon for durable cloud history.
Session tests use temporary folders and a mocked model, without saving real chats.

## Composing tools for comparisons

The model combines basic tools instead of calling a ranking tool: `find_games`
identifies the games, `game_players` lists each game's candidates, and repeated
`player_advanced_stats` calls provide the selected metric for each player. The
model compares the returned values, reports ties, and writes the answer. A daily
comparison repeats this process across every game on the requested date.

The harness allows 20 tool rounds (multiple calls per round), followed by one
final tools-disabled summary request if the limit is reached. Partial coverage
must be stated explicitly; an unchecked candidate prevents a definitive winner
claim. No minimum shot-attempt threshold is imposed unless requested.

Try: "Who had the highest eFG% across all games on March 31, 2026?"
Large comparisons require more API calls and may reach the round limit.
