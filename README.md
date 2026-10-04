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
3. Build the Vue frontend: `cd frontend && npm ci && npm run build`, then return
   to the repository root with `cd ..`.
4. `uv run app.py`, then open http://localhost:8000

## Frontend

The interface uses Vue 3, TypeScript, Vite, and the free TailAdmin Vue theme with
Tailwind CSS v4. It includes searchable conversation history in the left sidebar,
a chat workspace, dark/light themes, responsive mobile navigation, prompt starters,
collapsed tool logs, Markdown-formatted assistant replies, and final player/team panels. Source and attribution are in
`frontend/` and `frontend/THIRD-PARTY-NOTICES.md`.

For frontend development, keep the Python server running on port 8000 and run
`npm run dev` in `frontend/`. Vite proxies the existing chat/session endpoints to
FastAPI. For the single-server app at port 8000, rebuild with `npm run build` after
frontend changes. The build also runs TypeScript checks. No Node server is needed
to serve the production build, and fonts are bundled locally.


## Project layout

```text
app.py                     # FastAPI routes and application startup
backend/
  paths.py                 # Stable repository root for persisted data/assets
  activity.py              # Streaming tool activity events
  assistant/
    tools.py               # ESPN tools and tool registration
    advanced_stats.py      # Single-game derived statistics
  data/
    nba.py                 # NBA requests, normalization and bounded SQLite cache
    honors.py              # Award categories and normalization
  debate/
    agent.py               # Debate orchestration and mandatory reply validation
    tools.py               # Structured evidence tools and numeric audits
    context.py             # Team support and player-role evidence
    research.py            # Automatic season selection and research queues
frontend/                  # Vue application
test/                     # Offline regression tests
chat_history/              # Local conversation snapshots (ignored by Git)
data_cache/                # Local NBA cache (ignored by Git)
```

The startup commands stay `uv run app.py` or `uv run uvicorn app:app`.
Backend imports now use the `backend` package; for example,
`from backend.debate.agent import run_debate`. Module organization does not move
existing history, cached NBA responses, or frontend assets. The cache remains
`data_cache/nba.sqlite3` at the repository root, regardless of the working directory.

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
text for user messages and tool logs. Assistant replies use Markdown with raw HTML
disabled; unsafe link schemes are rejected and inline Markdown images are shown
as their descriptions. Panel image URLs are restricted to HTTPS ESPN headshots.

Try these independent queries:

- "Show Paolo Banchero's photo and advanced stats against Memphis on January 15, 2026."
- "What was Orlando's estimated offensive rating against Memphis on January 15, 2026?"
- "Calculate Paolo Banchero's TS% against Memphis on January 15, 2026, and show the inputs."

## Saved conversations

Click **New conversation** to start with fresh context without deleting previous chats.
Use the **Your conversations** sidebar to search, reopen a conversation, and continue it,
including its previous answers, tool logs, photos, and metric cards. Empty new
chats are saved only after the first message. Refreshing opens a blank chat; saved
conversations remain available in the sidebar. On mobile, use the menu button to open it.
Use the trash button beside a conversation to permanently delete that saved chat.
Deleting the open conversation returns the chat area to a new conversation;
deleting another conversation leaves the current chat and draft intact.

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

## Fan Debate

Start a new conversation and choose **Fan Debate**, then select **Your player** and
**AI's player** from the current/retired player directory. There is one debate
entry point, and every debate reply can be copied. No season or competition setup
is required. New conversations use `scope_mode: auto`: only roles are locked.
The LLM selects relevant career, season, regular-season, playoff or game-log evidence
for each argument and states the actual scope and selection rationale. Explicit
scope requests in chat take precedence, including follow-ups. Comparisons still
require compatible periods and statistical bases; selected samples cannot stand
for entire careers. Start a new conversation to change player roles.

Older sparring/comeback conversations retain their original roles and configuration;
missing `scope_mode` fields default to `configured` for compatibility. Saved
conversations restore concessions, evidence snapshots and checked cards.

The agent uses a direct rival-fan voice: challenge the claim, counter with decisive
facts, and close with a pointed line. Sarcasm about the argument is allowed; factual
standards remain unchanged. A valid concession should be brief and specific.

Major honors are retrieved only when the LLM selects an honors tool in a debate
and retained as evidence snapshots. The **Trophy cabinet** section shows the comparison,
winning seasons and source records; it expands automatically when a reply cites honors.
The default table covers championships, regular-season MVP, Finals MVP, DPOY, All-NBA,
All-Defensive and All-Star selections. The dedicated tools also support individual
All-NBA/All-Defensive teams, Rookie of the Year, Sixth Man and Most Improved Player.
Exact NBA descriptions prevent All-Star MVP and media awards from inflating NBA MVP
counts. Duplicate records for the same honor and season count once. An absent record
is shown as unavailable, including awards that did not exist in a player's era.
Award counts are season honors, never per-game values or playoff/regular-season splits.

The agent is pointed but must concede a supported disadvantage. It does not invent
shortcomings to win, assign GOAT scores, or infer overall defense from steals/blocks.
Undefined “peak” requires a season or metric. It may return limited evidence rather
than a counterattack. Evidence cards show exact scope, sample sizes, sources, retrieval
time and any stale-cache or coverage limitations. Text review is not a guarantee of
correctness.

### Historical data and cache

The `nba_api` adapter uses NBA.com player directories, career/season totals,
awards, season game logs and team shooting totals. NBA and ESPN IDs are separate.
The bundled `nba_api` directory is an identity-only fallback when the live directory
is unavailable; missing playing years are displayed as unavailable. It never supplies
invented statistical evidence.

Responses and normalized data are cached in `data_cache/nba.sqlite3` (ignored by Git).
Directory, career and awards requests refresh after 24 hours. Game logs, team context and league
baselines older than the previous season refresh after 30 days; newer data uses
24 hours. A failed refresh may use an existing stale response with an explicit warning.
A first-time failure without cache returns unavailable. Requests are bounded to
12 seconds each and share a 60-second per-turn historical-research budget; failed
lookups are not immediately repeated. Historical availability varies by endpoint.

The SQLite cache defaults to a 100 MiB cap (`NBA_CACHE_MAX_MB` overrides it).
Least recently accessed queries are removed when the database exceeds the cap;
SQLite free pages are reclaimed. Oversized individual responses are used in memory
for the current turn without evicting the entire cache. Existing databases are
upgraded automatically to track access times. Temporary SQLite transaction/vacuum
files may need additional disk space during maintenance. Conversation files and
their immutable evidence snapshots are separate and are never deleted by this cap.

Statistics are aggregated from totals. Trade-season total rows take precedence over
individual team rows. Historical unrecorded statistics are null, never zero. Partial
career metrics include the covered seasons; incomplete metrics cannot support a
certified pairwise comparison. Awards are deduplicated source records, not an inference
of zero when missing, and are season honors rather than regular-season/playoff splits.
Single-season relative TS% is player TS% minus the same season/phase league TS%, in
percentage points. The league baseline requires a verified complete team count; if it
is unavailable, raw player efficiency remains available with a limitation.

### Debate tools and verification

| Tool | Purpose |
| --- | --- |
| `resolve_player` | Identify a player or return ambiguous candidates |
| `query_evidence` | Retrieve sourced stats, awards, game logs or league baseline |
| `player_awards` | Look up distinct NBA honors and winning seasons, including specific All-NBA teams |
| `compare_awards` | Compare championships, MVP, Finals MVP, DPOY, All-NBA, All-Defensive and All-Star selections |
| `query_competitive_context` | Explicit-season teammate statistics, team shares/ranks and usage |
| `compare_competitive_context` | Discover both rosters, then compare one model-selected strong teammate per side; use `focus=team_context` for broad roles/support |
| `plan_team_context_research` | Derive a stable, source-backed season/team queue for a team-outcome argument |
| `research_team_context` | Execute the next batch (at most two units per call) and return coverage |
| `compare_players` | Compare compatible scopes, preserving both sides and coverage |
| `find_counterexamples` | Search one season for games meeting an explicit numeric predicate |
| `find_comparative_edges` | Return advantages, disadvantages, ties and unavailable metrics from a fixed set |
| `audit_argument` | Validate exact values, player attribution, scope and comparison direction against server-owned evidence |

The writing LLM decides what to query, when to query and whether to continue. There
is no keyword-triggered investigation or automatic honors preload. All debate
tools and saved evidence remain available throughout the turn, including after a
failed draft. Planning a queue does not execute it. The model can continue a queue
or call a targeted context tool with a required `selection_reason`; valid selected
seasons/phases are preserved even if the user did not mention them. Directed
lookups are recorded separately and do not alter the queue's completion state.

`plan_team_context_research` uses both players' career participation and verified
championship award seasons to build the range. Missing awards are unknown, not zero.
Contemporary careers align by calendar season; careers with no overlap align by
Nth season with NBA appearances. Explicit user season pairs take priority. Traded
seasons split into separate team stints. TeamYearByYearStats verifies team W/L and
postseason results; championship stints must match a league-champion team result.
Team qualification is never counted as individual postseason participation from
season roster membership alone. NBA team dashboards may be unavailable for early
historical seasons; those units remain explicitly unavailable.

Each batch call starts at most two season/team-pair-and-phase units, in chronological
order then stable team-ID order, irrespective of stance, results or cache hits.
Both base rosters precede optional advanced statistics. No postseason row
means no same-phase comparison; a separate two-sided regular-season supplement
may be queued. Additional batches are allowed within the 20 tool-interaction rounds.
The historical budget counts cumulative network-request time (60 seconds), with a
12-second cap per request. Model/reviewer latency and cache reads do not consume it.
Failed lookups are not retried in the same turn. The 100 MiB SQLite cache cap remains.
No background work runs. The LLM may continue saved queues on follow-up; an explicit-year detour preserves its parent
queue. Pending, completed and unavailable scopes are separate. Failed candidate
selection can be refreshed on a later turn without repeating completed units.

TeamPlayerDashboard supplies team-scoped totals and advanced usage. Ranks are based
on totals (ties share rank); scoring, assist and shot shares use full team-season
totals, including games the focal player missed. Cards show the team sample
separately from the player sample. These are descriptive comparisons, not a team
strength score or causal explanation of wins.

`compare_competitive_context` defaults to `focus=key_teammates`. With no teammate
IDs it returns both complete non-focal roster candidate lists, with compact rounded
GP, minutes, scoring, assists, rebounds, steals, blocks and TS% for selection.
Candidates are sorted by player ID, not an ability score. Discovery does not fetch
all-player awards or present focal-star citations as a completed teammate comparison.
The LLM selects one representative strong co-star per side and calls again with
`left_teammate_id`, `right_teammate_id` and `teammate_selection_reason`, explaining
both selections against named alternatives using comparable criteria.

The tool verifies each selected player's actual team stint, excludes each focal
player, and supplies exact head-to-head statistics. Teammate research does not
fetch awards or use honors to choose or compare supporting players. The comparison
panel names the focal stars, selected teammates, team/season scopes and selection
rationale, and shows statistical production, efficiency and availability only.
Older teammate-honor tables are no longer rendered, and cached teammate award
records are omitted from new citation catalogs. Selected statistics remain
numerically auditable and citable using the teammate IDs, never assigned to the
focal stars. Dedicated focal-player honors and championship queries remain available.

This reasoning angle is guidance to the writer, not a keyword-triggered lookup,
fixed nominee, weighted strength score or predetermined winner. Stronger co-star
evidence may qualify attributing all team success to an individual, but cannot
prove wins fake or establish causation. Opposite findings must be preserved.
The writer is instructed to begin each turn with `plan_argument`: a short public
summary of the disputed inference, proposed counterpoint, evidence questions and
scope rationale. Plans appear in tool activity and `debate_result.argument_plan`,
and reach the reviewer separately from verified facts. They fetch no data, grant
no factual approval and can be revised when findings change the approach.
A separate short LLM assessment checks the plan's relevance and selects at most
one primary investigative tool; auxiliary lookups remain available. The execution
loop requires that model-selected investigation to be attempted before submission,
and roster discovery alone does not complete a teammate comparison. Premature
submissions return `needs_evidence` without spending a draft-review attempt. Failed
lookups allow an explicitly limited conclusion rather than an endless retry.
The plan reviewer classifies bare count questions separately from using team
achievements as individual credit. The latter and overall-support questions
require two teammate pairs; a strongest-co-star question requires one. Both pairs
must be registered after roster discovery and before the first exact comparison,
with distinct non-focal teammates and consistent samples and selection criteria.
The first result never cancels the second task. Failed queries are cached, missing
pairs require an explicit limitation, and both results remain in the comparison
panel. Four selected teammates do not establish a whole-roster ranking. Tasks,
next actions and replacement reasons appear in `support_research`; only published
citations consume facts. No keyword router or automatic winner is introduced.
The required `team_success_argument` finding is resolved from ordered dialogue:
"but X has a championship" after a superiority claim is a team-credit argument,
not a bare count lookup. A positive finding overrides contradictory count-only or
no-research labels and creates the required tasks. Final debate review independently
returns `required_support_scope`; missed obligations become tracked tasks through
`require_support_research` activity and block resubmission until attempted. Labeling
a clause `general_comment` does not waive this argument-level investigation.
If broader requirements are discovered after a selected pair's result, additional
unplanned pairs are marked unavailable instead of selected after seeing the outcome;
the response must explicitly limit its coverage. Roast cannot start new research.
`focus=team_context` retains the broad role/support comparison, and
`query_competitive_context` accepts an optional verified `teammate_id` for closer
inspection. Its legacy broad shortlist combines the top three by total minutes
and top two by total points, up to five players; it is not an ability ranking.

Teammate production, usage and team shares can challenge an argument about credit,
but cannot establish shared minutes, injury causes, who was carried, or hypothetical
replacement wins. On/off and public article search are not part of this version.
Opponent evidence is limited to the explicitly defined defensive-rating samples below.

`submit_argument` is the only final-reply entry point. It accepts an English draft,
up to eight fact IDs or structured claims, a topic and concession indexes. Exact
server-owned facts populate evidence cards. The mandatory `audit_argument` checks
values, ownership, seasons/phases, scopes and comparison direction; it does not
scan prose for award keywords or numeric strings.

Fan Debate has a composer-level reply-tone switch: `reasoned` (default) and
`roast` (sharp basketball sarcasm). Send `reply_tone` with a chat request to change
the next reply's voice without changing the locked players or scopes. The session
remembers the latest choice; each saved turn records its own tone. Legacy sessions
default to reasoned. Both voices use the same evidence and review requirements.
Both modes first prepare and verify a measured argument. Full roast then rewrites
its expression while preserving the original facts, scope and concessions.
The tone check verifies added assertions and implications against that baseline
and the same citations, rather than reopening unchanged approved claims.
After a rejected or malformed rewrite, up to two repairs receive the original approved
reply, rejected version and exact review issues. It cannot fetch evidence for new
implications. After three failed rewrites, or immediately on any provider exception, return the reasoned
reply (`style_status=reasoned_fallback`), without added network retries. Optional
`style_attempts` and paired activity IDs record these separate attempts without
spending research or content-review budgets. Unsupported
roast text is never used as the fallback. Ordinary scoped descriptions of teammate
support are distinguished from causal win attribution or whole-roster rankings.

Three content-review attempts and three citation/format repair attempts have
separate limits, within the existing tool-round budget. Bad fact IDs return the
available catalog and do not consume content-review slots. Unapproved replies are
visibly labeled as such rather than displayed as normal comebacks; fallback facts
deduplicate reverse comparisons and covered single-player entries.

Broad player comparisons should call `compare_players` in compatible scopes relevant to the argument.
Once the writer selects or cites a single-player statistics snapshot, the harness
also retrieves the other locked player's matching scope, using the configured
season pair when applicable and otherwise the same season range/phase/basis.
This follow-up is visible in tool activity, shares the existing network budget,
and does not run for unrelated honors-only queries or invalid numeric claims.
Each scope pair is attempted once per turn. Missing opponents remain unavailable.
Valid single-player stat citations are expanded to the exact sourced comparison
when available; this never fixes an incorrect submitted number.

`debate_result.comparisons` supplies a two-column panel independently of which
facts the writer chose to cite: GP, PTS, AST, REB, TOV, TS%, eFG% and 3P%.
The writer and reviewer both receive this table, including relevant disadvantages,
source timestamps and incomplete coverage. No overall winner is computed; TS%
and eFG% are shooting-efficiency measures, not comprehensive impact ratings.

After that check passes, an independent LLM review receives the question, locked
stance, draft, cited cards, research scope and limitations. It returns `pass`,
`revise` or `needs_evidence`, with quoted clauses, reasons and missing evidence.
Generic team-credit commentary does not require a championship count. Specific
honors, roster comparisons and claims about offensive burden require appropriate
support. Tone alone is not a review failure. This semantic assessment is not a
guarantee of correctness.
Review separates mixed clauses (e.g. offensive workload, MVP and All-Star count)
so awards cannot substantiate workload. It also assesses relevance and implied
attribution: unrelated honors plus a generic team-credit slogan do not constitute
an evidenced rebuttal. Specific support/stability explanations need evidence;
an explicit count-only or personal-performance question can still pass without
teammate research. A team-credit argument needs its required investigation attempted
or unavailable before an explicitly limited concession can pass. The plan never
overrides the actual reply.
The structured review explicitly assesses relevance and evidence follow-through;
an evasive or unfinished assessment cannot pass validation. Previous review issues
and tool activity are supplied on resubmission, so replacing a rejected explanation
with unrelated cached facts cannot silently count as resolving its evidence gap.
Debate reviews also require `conclusion_addresses_objection`: the actual takeaway
must answer the user's comparison or latest requested dimension. Accurate stats
followed by generic balance or rival praise cannot substitute for that answer.
An explicit scoped concession or uncertainty can pass without declaring a winner.
A failed takeaway check returns `revise` through the existing budget, without a
new model call. Roast preserves this takeaway and is checked under the same rule;
failed repairs retain the approved reasoned reply. Old saved assessments may omit
the field, but new live debate reviews must supply a boolean.
Before approving reviewed assertions, a focused inference check independently
compares the draft with only its cited records. It can reject an unsupported
workload/roster description attached to otherwise correct numbers or awards.
Its issues return through the same evidence-first revision loop. Planning adds
one model call; this final check adds a call only to provisionally passing reviews.

Failed submissions return feedback to the writer with tools still available.
The writer first seeks/cites evidence for each missing-evidence issue, preserving
supported evaluations and narrowing them to the verified season, phase and sample.
Only unsupported, unavailable or contradicted conclusions are narrowed/removed.
`query_performance_context` provides three targeted follow-up dimensions:

- `player_ranks`: PTS/AST per game and ranks among returned players meeting an
  explicit minimum-games threshold (default 20, not official award qualification).
- `team_offense`: verified team offensive rating and rank, paired with existing
  `query_competitive_context` individual-role evidence for scoring/assist roles.
- `opponent_splits`: GP/PTS/AST/TS% against teams with the lowest regular-season
  defensive ratings (default top five, ties included), with opponents and game IDs.

Each lookup requires one season, phase and selection reason; ratings require
1996-97 or later. Sources, sample definitions and limitations are carried into the
citation cards. Missing or ambiguous inputs do not become zeroes. Same-season ranks
cannot prove historic superiority, team offense cannot prove individual causation,
and a top-defense sample is not evidence about all elite competition.

There are at most three submissions (including the first), sharing the original
20 tool-interaction rounds. Plain assistant text is never published without a
submission. At exhaustion, the reply contains only a labeled canonical summary
of checked facts from submitted claims or model-selected lookups; if none exist,
it reports the gap. Provider exceptions retain the existing failed-turn behavior.
Only final replies and successful turn state are committed; failed provider calls
do not commit partial research or concessions, though public data stays cached.

### API additions

`POST /chat/stream` accepts the same body and browser ownership cookie as `/chat`.
It sends SSE JSON events: `phase` (short execution stage), `session` (conversation
ID), `tool_start`/`tool_end` (stable per-turn call ID, name, arguments, status and
result), then `done` with the existing chat response shape. Validation failures use
an `error` event; keepalive comments prevent idle stream timeouts. The original
non-streaming `/chat` remains available.

The UI displays live activity inside a collapsible **Thinking** section, with
individually expandable inputs/results. This shows public execution steps, not
model reasoning text. Completed and failed tool records survive a later model
failure and are retained in history. A dropped connection stops delivery but an
already-started server turn may still finish saving; check history before retrying.

`GET /players?query=Jordan` returns `players`, `complete` and `resolved`. Candidates
include the NBA ID, name, playing years when available, active status and directory
provenance.

`POST /chat` retains its existing inputs and additionally accepts:

```json
{
  "message": "Jordan was just a volume scorer. Defend him.",
  "mode": "debate",
  "debate_config": {
    "supported_player": 2544,
    "opponent_player": 893,
    "supported_season": null,
    "opponent_season": null,
    "phase": "Regular Season"
  }
}
```

Modes are `assistant`, `debate` and `rebuttal`. Continuing a saved session may omit
mode/config; supplied conflicting values return HTTP 409. New debate sessions require
two distinct valid NBA players. Responses retain `response`, `session_id`, and
`tool_calls`, adding `failed` and optional `debate_result` (`claims`, `cards`,
`review_status`, `review_note`, optional server-generated `honors`, `research` and `validation` summaries). Session summaries include mode; session details also
include `debate_config` and `player_names`.

Run the existing offline unittest suite to exercise cache failure handling, historical
coverage, comparisons, numeric audits, semantic-review fallback, three-turn state,
mode locking and rollback. Frontend validation remains `npm run build` in `frontend/`.
Live NBA and model calls are separate integration checks and require network access
and the existing GCP credentials; mocked tests do not establish live availability.

Research progress is stored in `debate_state.research_plans` and
`debate_state.active_research`; targeted lookups are saved in
`debate_state.directed_queries` with selection reasons, requests, sources, status
and evidence IDs. Replies carry an immutable `debate_result.research`
snapshot with the argument, selection/pairing rules, completed/pending/unavailable
scopes, gaps and source timestamps. The collapsible **Research coverage** panel
restores these snapshots in history; older sessions need no migration. Thinking
emits the actual selected tool calls, submissions, data checks and wording reviews.
`debate_result.validation` records submission count, separate numeric/semantic
results, each attempt’s feedback, stop reason and retrieval gaps. It is optional
for old history; existing replies are not rewritten. Model exceptions
roll back research state; already fetched public responses can remain cached.
Recoverable review outcomes display as “Needs evidence” or “Needs revision”,
separate from execution failures. The UI also interprets these outcomes in older
saved logs and shows the final approved submission count.

For this Vertex model, tool-enabled calls and constrained final-response schemas
are sent separately: their combination was observed to return HTTP 400. Numerical
checks remain deterministic; the separate prose review is not a guarantee of
correct inference. Team evaluations must cite the relevant supporting-cast/role
records, and partial seasons must not be represented as career conclusions.
