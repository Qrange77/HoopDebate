"""Focused NBA tools backed by ESPN's game summary and scoreboard endpoints."""

import inspect
import json
import logging
import re
import unicodedata
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from backend.tool_errors import tool_error, validation_error, request_error

from backend.assistant.advanced_stats import PLAYER_METRICS, TEAM_METRICS, calculate, shooting_inputs, normalize_metric

NBA_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
TOOLS = []
TOOL_MAP = {}
logger = logging.getLogger(__name__)
PARAMETERS = {
    "panel": "Final display data copied from previous tool results: player/team, optional headshot and metrics. Each metric must be an object, e.g. PTS: {label: 'PTS', value: 26, unit: ''}, not a raw stats string. Do not invent values; include formulas, inputs, caveats and provisional status. No fetching or calculation occurs.",
    "metric": "Metric name or all (default). Player: efg_pct, ts_pct, game_score, ast_to_ratio, three_point_attempt_rate, free_throw_rate. Team additionally supports tov_pct, oreb_pct, estimated_possessions, offensive_rating, defensive_rating, net_rating, but not game_score or ast_to_ratio.",
    "date": "Game date YYYY-MM-DD. Defaults to today in America/New_York when event_id is omitted.",
    "event_id": "ESPN game ID returned by find_games. Can be used without date.",
    "team_name": "Team name or abbreviation, e.g. Orlando Magic or ORL. Used to select a team or find its game.",
    "player_name": "Player's name, preferably the full name. Ambiguous names require clarification.",
    "quarter": "0 = full game (default); 1-4 = individual quarter; 5 = first overtime, 6 = second overtime, etc. Period scores are not cumulative.",
    "stat": "Optional exact statistic name or label, e.g. points, PTS, rebounds, REB. Omit for all statistics.",
    "category": "Single leader category supplied by ESPN, e.g. points, rebounds, assists.",
    "period": "0 = all periods; 1-4 = quarters; 5 and above = overtime periods.",
    "event_type": "Optional event type text filter, e.g. Jump Shot, Foul, or Substitution.",
    "offset": "Number of matching events to skip for pagination.",
    "limit": "Maximum number of events to return, from 1 to 100.",
}


class LookupIssue(Exception):
    def __init__(self, message, **details):
        action = details.pop('next_action', None)
        if action is None:
            if details.get('candidates'):
                action = 'Use one of the returned full names; ask the user to clarify if the intended person/team is ambiguous.'
            elif details.get('games'):
                action = 'Use an event_id from games; clarify the intended game for a single-game request.'
            elif details.get('available_stats') or details.get('available_categories'):
                action = 'Choose a returned available statistic/category matching the request and call again.'
            else:
                action = 'Check the requested game, name and period with find_games, game_players or game_status as appropriate. If coverage is absent, report it as unavailable, not zero.'
        self.result = {**tool_error('lookup_unavailable', message, action, **details), 'message': message}
        super().__init__(message)


class PanelMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    label: str = Field(description='Human-readable metric label copied from lookup results, e.g. PTS or True shooting percentage.')
    value: float | None = Field(description='Numeric value copied from tool results without recalculation; null for unavailable values or when display_value carries a shooting/minutes string.')
    display_value: str | None = Field(default=None, pattern=r"^\d+(?:-\d+|:\d{2}|(?:\.\d+)?%)$", description='Exact supplied box-score string such as 9-16, 37:12 or 50%. Use value=null; omit for ordinary numeric values.')
    unit: str = Field(description='Unit copied from the source metric, e.g. %, ratio or points; use an empty string for a unitless box-score field.')
    formula: str = Field(default="", description='Formula returned by the calculation tool; omit for raw stats. Do not invent a formula.')
    inputs: dict[str, float | None] = Field(default_factory=dict, description='Named numeric calculation inputs copied from the same tool result, preserving nulls.')
    estimated: bool = Field(default=False, description='Copy whether the source metric is an estimate; default false for raw statistics.')
    unavailable_reason: str | None = Field(default=None, description='Reason given by the source for a null metric; omit when the value is available.')


class PanelData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    player: str = Field(default="", description='Full player name from prior lookup results. At least player or team must be nonempty.')
    team: str = Field(default="", description='Team name from prior lookup results. At least player or team must be nonempty.')
    headshot: str | None = Field(default=None, description='Exact image URL supplied by the player lookup; omit if unavailable.')
    position: str | None = Field(default=None, description='Player position copied from lookup results; omit if unavailable.')
    jersey: str | None = Field(default=None, description='Player jersey number as a string copied from lookup results; omit if unavailable.')
    metrics: dict[str, PanelMetric] = Field(default_factory=dict, description='Requested statistics keyed by metric code. Copy values and metadata from prior tool results; omit for a profile/photo-only request.')
    provisional: bool = Field(default=False, description='True if the source marks these statistics as provisional, e.g. an unfinished game.')
    note: str = Field(default="", description='Relevant source caveats for this panel, including estimates or missing coverage.')


def nba_tool(fn):
    """Register a typed function and give every public tool a JSON/error boundary."""
    signature = inspect.signature(fn)
    properties, required = {}, []
    for name, param in signature.parameters.items():
        spec = {"type": "integer" if param.annotation is int else "string",
                "description": PARAMETERS[name]}
        if param.annotation is dict:
            spec = PanelData.model_json_schema()
            definitions = spec.pop("$defs", {})
            spec["properties"]["metrics"]["additionalProperties"] = definitions["PanelMetric"]
            spec["description"] = PARAMETERS[name]
        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            spec["default"] = param.default
        if param.annotation is int:
            spec["minimum"] = 1 if name == "limit" else 0
            if name == "limit":
                spec["maximum"] = 100
        if name == 'metric':
            metrics = PLAYER_METRICS if fn.__name__ == 'player_advanced_stats' else TEAM_METRICS
            spec['enum'] = ['all'] + [m[0] for m in metrics]
        properties[name] = spec

    @wraps(fn)
    def wrapped(*args, **kwargs):
        prefix = "Display panel validation failed" if fn.__name__ == "display_panel" else "Tool argument validation failed"
        try:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            for name, value in bound.arguments.items():
                expected = signature.parameters[name].annotation
                if type(value) is not expected:
                    raise ValueError(f"{name} must be {expected.__name__}.")
                if name in required and isinstance(value, str) and not value.strip():
                    raise ValueError(f"{name} must not be empty.")
                if expected is int and (value < 0 or (name == "limit" and not 1 <= value <= 100)):
                    allowed = 'from 1 to 100' if name == 'limit' else 'greater than or equal to 0'
                    raise ValueError(f"{name} must be an integer {allowed}; received {value}.")
                if name == 'date' and value:
                    _day(value)
                if name == 'metric':
                    value = bound.arguments[name] = normalize_metric(value)
                if 'enum' in properties[name] and value not in properties[name]['enum']:
                    raise ValueError(f"{name} must be one of: {', '.join(properties[name]['enum'])}.")
        except (ValueError, TypeError) as exc:
            return json.dumps(tool_error('invalid_arguments', f'{prefix}: {exc}',
                'Correct the named argument using the tool schema and call again.', status='revise'))
        try:
            return json.dumps(fn(*bound.args, **bound.kwargs), allow_nan=False)
        except LookupIssue as exc:
            result = exc.result
        except ValidationError as exc:
            result = validation_error(exc, prefix)
        except requests.RequestException as exc:
            result = request_error(exc, 'ESPN')
        except ValueError as exc:
            if fn.__name__ == 'display_panel':
                result = tool_error('invalid_arguments', f'{prefix}: {exc}',
                    'Copy a player or team name and valid metric objects from prior results.', status='revise')
            else:
                result = tool_error('invalid_response', 'ESPN returned data that could not be interpreted.',
                    'Retry at most once; if the response remains invalid, report unavailable data.')
        except (KeyError, IndexError, TypeError, AttributeError):
            logger.exception('Unexpected data structure in %s', fn.__name__)
            result = tool_error('invalid_response', 'ESPN returned missing or malformed data.',
                'Retry at most once; if the response remains invalid, report unavailable data rather than zero.')
        except Exception:
            logger.exception('Unexpected tool failure in %s', fn.__name__)
            result = tool_error('internal_error', 'The tool could not complete because of an internal error.',
                'Do not repeat this call unchanged. Explain that the lookup failed and use other verified evidence if available.')
        return json.dumps(result)

    TOOL_MAP[fn.__name__] = wrapped
    TOOLS.append({"type": "function", "function": {
        "name": fn.__name__, "description": inspect.getdoc(fn),
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False},
    }})
    return wrapped


def _fetch(path, **params):
    response = requests.get(f"{NBA_URL}/{path}", params=params, timeout=15)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError:
        raise LookupIssue('ESPN returned invalid JSON.', next_action='Retry at most once; if it fails again, report unavailable data.') from None
    if not isinstance(data, dict):
        raise LookupIssue('ESPN returned an unexpected response format.', next_action='Retry at most once; if it fails again, report unavailable data.')
    return data


def _normalize(value):
    return "".join(c for c in unicodedata.normalize("NFKD", str(value)).casefold()
                   if c.isalnum())


def _matches(query, entity):
    values = [entity.get(k, "") for k in
              ("displayName", "fullName", "shortName", "shortDisplayName", "name", "abbreviation")]
    q = _normalize(query)
    return any(q and q == _normalize(v) for v in values)


def _choose(items, query, entity_key):
    exact = [item for item in items if _matches(query, item.get(entity_key, {}))]
    matches = exact or [item for item in items if _normalize(query) in
                       _normalize(item.get(entity_key, {}).get("displayName", ""))]
    if len(matches) != 1:
        raise LookupIssue("No matching name found." if not matches else
                          "Name is ambiguous. Please choose a full name.",
                          candidates=[i.get(entity_key, {}).get("displayName") for i in matches])
    return matches[0]


def _day(date):
    if not date:
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            raise ValueError
        return datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError('date must be a real calendar date in YYYY-MM-DD format, e.g. 2026-01-15.') from None


def _events(date, team_name):
    day = _day(date)
    events = _fetch("scoreboard", dates=day.replace("-", ""), limit=100).get("events")
    if not isinstance(events, list):
        raise LookupIssue('ESPN did not return a valid game list.',
            next_action='Retry at most once; if unavailable, report that the schedule could not be verified. Do not claim there were no games.')
    if team_name:
        teams = {}
        for event in events:
            for c in event.get("competitions", [{}])[0].get("competitors", []):
                teams[c["team"]["id"]] = c
        if not teams:
            return day, []
        selected = _choose(list(teams.values()), team_name, "team")["team"]["id"]
        events = [e for e in events if any(c["team"]["id"] == selected for c in
                                         e["competitions"][0].get("competitors", []))]
    return day, events


def _game(date, event_id, team_name=""):
    if not event_id:
        day, events = _events(date, team_name)
        if len(events) != 1:
            raise LookupIssue("No matching game found." if not events else
                              "Multiple games found. Choose an event_id.", date=day,
                              games=[{"event_id": e["id"], "matchup": e["name"],
                                      "start_time": e.get("date")} for e in events])
        event_id = events[0]["id"]
    data = _fetch("summary", event=event_id)
    header = data.get('header')
    competitions = header.get('competitions') if isinstance(header, dict) else None
    if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict):
        raise LookupIssue("Game data is unavailable.")
    competition = competitions[0]
    competitors = competition.get('competitors')
    if not isinstance(competitors, list) or len(competitors) != 2 or any(
            not isinstance(c, dict) or not isinstance(c.get('team'), dict)
            or not c['team'].get('id') or not c['team'].get('displayName') for c in competitors):
        raise LookupIssue('Game participants are missing or malformed.',
            next_action='Verify the event_id using find_games. If the summary remains incomplete, report unavailable game data.')
    if date:
        expected = _day(date)
        actual = datetime.fromisoformat(competition["date"].replace("Z", "+00:00"))
        if actual.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d") != expected:
            raise LookupIssue("The event_id does not match the requested date.",
                next_action='Call find_games with the requested date and use an event_id from that result.')
    if team_name:
        _choose(competition.get("competitors", []), team_name, "team")
    return data, competition


def _team(data, competition, team_name, section=None):
    chosen = _choose(competition.get("competitors", []), team_name, "team")
    if section is None:
        return chosen
    for item in data.get("boxscore", {}).get(section, []):
        if item.get("team", {}).get("id") == chosen["team"]["id"]:
            return item
    raise LookupIssue("Team statistics are unavailable.")


def _player(data, player_name, team_name=""):
    squads = data.get("boxscore", {}).get("players", [])
    if team_name:
        squads = [_choose(squads, team_name, "team")]
    rows = []
    for squad in squads:
        for group in squad.get("statistics", []):
            for row in group.get("athletes", []):
                rows.append({**row, "team_name": squad["team"]["displayName"],
                             "stat_labels": group.get("labels", []),
                             "stat_keys": group.get("keys", [])})
    return _choose(rows, player_name, "athlete")


def _stats(stats, requested):
    if not stats:
        raise LookupIssue("Statistics are unavailable.")
    if not requested:
        return {s["name"]: s["value"] for s in stats}
    matches = [s for s in stats if _normalize(requested) in
               {_normalize(s.get(k, "")) for k in ("name", "label", "abbreviation")}]
    if len(matches) != 1:
        raise LookupIssue("Statistic is unavailable or ambiguous.",
                          available_stats=[s["name"] for s in stats])
    return {matches[0]["name"]: matches[0]["value"]}


def _available(data, key):
    value = data.get(key)
    if not value:
        raise LookupIssue(f"{key.replace('_', ' ').capitalize()} data is unavailable.")
    return value


@nba_tool
def find_games(date: str = "", team_name: str = ""):
    """Find game IDs, matchups, and start times only; optionally filter by team."""
    day, events = _events(date, team_name)
    return {"date": day, "games": [{"event_id": e["id"], "matchup": e["name"],
                                     "start_time": e.get("date")} for e in events]}


@nba_tool
def game_info(date: str = "", event_id: str = "", team_name: str = ""):
    """Return only the game start time and participating teams, without scores or status."""
    _, c = _game(date, event_id, team_name)
    return {"start_time": c.get("date"),
            "teams": [i["team"]["displayName"] for i in c.get("competitors", [])]}


@nba_tool
def game_status(date: str = "", event_id: str = "", team_name: str = ""):
    """Return only game state, completion, period, and remaining clock."""
    d, c = _game(date, event_id, team_name)
    s = _available(c, "status")
    if "period" not in s or "displayClock" not in s:
        game_day = datetime.fromisoformat(c["date"].replace("Z", "+00:00")).astimezone(
            ZoneInfo("America/New_York")).strftime("%Y%m%d")
        events = _fetch("scoreboard", dates=game_day, limit=100).get("events", [])
        matching = next((e for e in events if str(e.get("id")) ==
                         str(c.get("id", d.get("header", {}).get("id")))), {})
        s = {**matching.get("status", {}), **s}
    return {"status": s.get("type", {}).get("description"),
            "state": s.get("type", {}).get("state"),
            "completed": s.get("type", {}).get("completed"),
            "period": s.get("period"), "clock": s.get("displayClock")}


@nba_tool
def game_score(date: str = "", event_id: str = "", team_name: str = "", quarter: int = 0):
    """Return only team names and scores: full game by default, or one quarter/overtime."""
    _, c = _game(date, event_id, team_name)
    if c.get("status", {}).get("type", {}).get("state") == "pre":
        raise LookupIssue("Game has not started; scores are unavailable.")
    current_period = c.get("status", {}).get("period")
    if quarter and current_period is not None and quarter > current_period:
        raise LookupIssue("The requested period has not started.")
    scores = []
    for competitor in c.get("competitors", []):
        score = competitor.get("score")
        if quarter:
            periods = competitor.get("linescores", [])
            entry = next((v for n, v in enumerate(periods, 1)
                          if v.get("period", n) == quarter), {})
            score = entry.get("displayValue", entry.get("value"))
        if score is None:
            raise LookupIssue("The requested score is unavailable; the period may not have started.")
        scores.append({"team": competitor["team"]["displayName"], "score": score})
    return {"scores": scores}


@nba_tool
def team_game_info(team_name: str, date: str = "", event_id: str = ""):
    """Return only the selected team's home/away designation and final winner flag."""
    d, c = _game(date, event_id, team_name)
    t = _team(d, c, team_name)
    return {"team": t["team"]["displayName"], "home_away": t.get("homeAway"),
            "winner": t.get("winner") if c.get("status", {}).get("type", {}).get("completed") else None}


@nba_tool
def team_game_stats(team_name: str, date: str = "", event_id: str = "", stat: str = ""):
    """Return only the selected team's single-game statistics, optionally one stat."""
    d, c = _game(date, event_id, team_name)
    t = _team(d, c, team_name, "teams")
    stats = [{**s, "value": s.get("displayValue")} for s in t.get("statistics", [])]
    return {"team": t["team"]["displayName"], "stats": _stats(stats, stat)}


@nba_tool
def team_game_leader(team_name: str, category: str, date: str = "", event_id: str = ""):
    """Return only ESPN's leaders for the selected team and one category; not an overall player award."""
    d, c = _game(date, event_id, team_name)
    t = _team(d, c, team_name)["team"]
    categories = next((x.get("leaders", []) for x in d.get("leaders", [])
                       if x.get("team", {}).get("id") == t["id"]), [])
    matches = [x for x in categories if _normalize(category) in
               {_normalize(x.get("name", "")), _normalize(x.get("displayName", ""))}]
    if len(matches) != 1 or not matches[0].get("leaders"):
        raise LookupIssue("Leader category is unavailable.",
                          available_categories=[x.get("name") for x in categories])
    return {"team": t["displayName"], "category": matches[0]["name"],
            "leaders": [{"player": x["athlete"]["displayName"], "value": x.get("displayValue")}
                        for x in matches[0]["leaders"]]}


@nba_tool
def player_info(player_name: str, date: str = "", event_id: str = "", team_name: str = ""):
    """Return only a player's identity, position, jersey number, and headshot from this game's roster."""
    d, _ = _game(date, event_id, team_name)
    p = _player(d, player_name, team_name)["athlete"]
    return {"player": p.get("displayName"), "id": p.get("id"),
            "position": p.get("position", {}).get("displayName"),
            "jersey": p.get("jersey"), "headshot": p.get("headshot", {}).get("href")}


@nba_tool
def player_game_status(player_name: str, date: str = "", event_id: str = "", team_name: str = ""):
    """Return only a player's starter, did-not-play, and ejection flags and applicable absence reason."""
    d, _ = _game(date, event_id, team_name)
    p = _player(d, player_name, team_name)
    return {"player": p["athlete"]["displayName"], "starter": p.get("starter"),
            "did_not_play": p.get("didNotPlay"), "ejected": p.get("ejected"),
            "reason": p.get("reason") if p.get("didNotPlay") else None}


@nba_tool
def player_game_stats(player_name: str, date: str = "", event_id: str = "", team_name: str = "", stat: str = ""):
    """Return only a named player's single-game statistics, optionally one stat."""
    d, _ = _game(date, event_id, team_name)
    p = _player(d, player_name, team_name)
    if p.get("didNotPlay"):
        raise LookupIssue("Player did not play; statistics are unavailable.")
    stats = [{"name": p["stat_keys"][i] if i < len(p["stat_keys"]) else label,
              "label": label, "value": value}
             for i, (label, value) in enumerate(zip(p["stat_labels"], p.get("stats", [])))]
    return {"player": p["athlete"]["displayName"], "stats": _stats(stats, stat)}


def _play(p):
    return {"id": p.get("id"), "period": p.get("period", {}).get("number"),
            "clock": p.get("clock", {}).get("displayValue"), "text": p.get("text"),
            "type": p.get("type", {}).get("text"),
            "home_score": p.get("homeScore"), "away_score": p.get("awayScore")}


def _page(items, offset, limit, key):
    end = offset + limit
    return {key: items[offset:end], "total": len(items),
            "next_offset": end if end < len(items) else None}


@nba_tool
def game_plays(date: str = "", event_id: str = "", team_name: str = "", period: int = 0,
               event_type: str = "", offset: int = 0, limit: int = 50):
    """Return only paginated play-by-play events, optionally filtered by period and event type."""
    d, _ = _game(date, event_id, team_name)
    plays = [p for p in _available(d, "plays")
             if (not period or p.get("period", {}).get("number") == period)
             and (not event_type or event_type.casefold() in p.get("type", {}).get("text", "").casefold())]
    return _page([_play(p) for p in plays], offset, limit, "plays")


@nba_tool
def player_shots(player_name: str, date: str = "", event_id: str = "", team_name: str = "",
                 period: int = 0, offset: int = 0, limit: int = 50):
    """Return only a named player's shot attempts, outcomes, and available coordinates, paginated."""
    d, _ = _game(date, event_id, team_name)
    player = _player(d, player_name, team_name)["athlete"]
    shots = []
    for p in _available(d, "plays"):
        participants = p.get("participants", [])
        # ESPN lists the shooter first; subsequent participants may be an assister or blocker.
        if not p.get("shootingPlay") or not participants:
            continue
        if str(participants[0].get("athlete", {}).get("id")) != str(player["id"]):
            continue
        if period and p.get("period", {}).get("number") != period:
            continue
        shots.append({"period": p.get("period", {}).get("number"),
                      "clock": p.get("clock", {}).get("displayValue"), "text": p.get("text"),
                      "made": p.get("scoringPlay"), "points_attempted": p.get("pointsAttempted"),
                      "coordinate": p.get("coordinate")})
    return {"player": player["displayName"], **_page(shots, offset, limit, "shots")}


@nba_tool
def game_venue(date: str = "", event_id: str = "", team_name: str = ""):
    """Return only the venue name and location."""
    d, _ = _game(date, event_id, team_name)
    v = _available(d.get("gameInfo", {}), "venue")
    return {"venue": v.get("fullName"), "address": v.get("address")}


@nba_tool
def game_officials(date: str = "", event_id: str = "", team_name: str = ""):
    """Return only the game's officials and their roles."""
    d, _ = _game(date, event_id, team_name)
    return {"officials": [{"name": x.get("displayName", x.get("fullName")),
                           "role": x.get("position", {}).get("displayName")}
                          for x in _available(d.get("gameInfo", {}), "officials")]}


@nba_tool
def team_injuries(team_name: str, date: str = "", event_id: str = ""):
    """Return only the selected team's supplied injury reports. These may be current, not historical game-day reports."""
    d, c = _game(date, event_id, team_name)
    t = _team(d, c, team_name)["team"]
    rows = next((x.get("injuries") for x in d.get("injuries", [])
                 if x.get("team", {}).get("id") == t["id"]), None)
    if rows is None:
        raise LookupIssue("Injury reports are unavailable for this team.")
    return {"team": t["displayName"], "injuries": [
        {"player": x.get("athlete", {}).get("displayName"), "status": x.get("status"),
         "reported_at": x.get("date"), "details": x.get("details"),
         "description": x.get("shortComment")} for x in rows],
        "note": "ESPN-supplied reports may be current, not from the game date. An empty list does not confirm no injuries."}


@nba_tool
def game_recap(date: str = "", event_id: str = "", team_name: str = ""):
    """Return only the game recap headline, supplied summary, publication time, and link."""
    d, _ = _game(date, event_id, team_name)
    a = _available(d, "article")
    return {"headline": a.get("headline"), "summary": a.get("description"),
            "published": a.get("published"), "url": a.get("links", {}).get("web", {}).get("href")}


@nba_tool
def game_videos(date: str = "", event_id: str = "", team_name: str = ""):
    """Return only available game video titles, descriptions, and links."""
    d, _ = _game(date, event_id, team_name)
    return {"videos": [{"title": v.get("headline"), "description": v.get("description"),
                        "links": v.get("links", {})} for v in _available(d, "videos")]}


@nba_tool
def team_standings(team_name: str, date: str = "", event_id: str = ""):
    """Return only the selected team's supplied standings; season may differ from the game date. Do not infer rank from list order."""
    d, c = _game(date, event_id, team_name)
    t = _team(d, c, team_name)["team"]
    standings = _available(d, "standings")
    entries = []
    for group in standings.get("groups", []):
        for entry in group.get("standings", {}).get("entries", []):
            identity = entry.get("team", {})
            identity = identity if isinstance(identity, dict) else {}
            if str(entry.get("id", identity.get("id"))) == str(t["id"]):
                entries.append({"group": group.get("header", group.get("name")),
                                "stats": {s["name"]: s.get("displayValue", s.get("value"))
                                          for s in entry.get("stats", [])}})
    if not entries:
        raise LookupIssue("Standings are unavailable for this team.")
    return {"team": t["displayName"], "season_label": standings.get("header"),
            "standings": entries,
            "note": "ESPN-supplied standings may be current, not from the game date. Rank is unavailable unless explicitly supplied."}


def _advanced_state(competition):
    state = competition.get("status", {}).get("type", {})
    if state.get("state") == "pre":
        raise LookupIssue("Game has not started; advanced statistics are unavailable.")
    return not state.get("completed", False)


@nba_tool
def player_advanced_stats(player_name: str, date: str = "", event_id: str = "", team_name: str = "", metric: str = "all"):
    """Calculate a player's single-game eFG%, TS%, Game Score, AST/TO, 3PAr, and FTr. Return formulas, inputs, and availability; TS% is estimated. No award selection."""
    d, c = _game(date, event_id, team_name)
    provisional = _advanced_state(c)
    p = _player(d, player_name, team_name)
    if p.get("didNotPlay"):
        raise LookupIssue("Player did not play; advanced statistics are unavailable.")
    raw = dict(zip(p["stat_labels"], p.get("stats", [])))
    return {"player": p["athlete"]["displayName"], "team": p["team_name"],
            "headshot": p["athlete"].get("headshot", {}).get("href"),
            "metrics": calculate(shooting_inputs(raw), PLAYER_METRICS, metric),
            "provisional": provisional,
            "note": "Calculated locally from ESPN box scores. TS% uses an estimated 0.44 free-throw factor. Game Score summarizes box-score production, not overall impact or an official award."}


def _team_advanced_inputs(d, c, name):
    squad = _team(d, c, name, "teams")
    competitor = _team(d, c, name)
    stats = {s["name"]: s.get("displayValue") for s in squad.get("statistics", [])}
    fields = {"FG": "fieldGoalsMade-fieldGoalsAttempted", "3PT": "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
              "FT": "freeThrowsMade-freeThrowsAttempted", "OREB": "offensiveRebounds", "DREB": "defensiveRebounds",
              "TO": "totalTurnovers" if "totalTurnovers" in stats else "turnovers"}
    raw = {key: stats.get(source) for key, source in fields.items()}
    raw["PTS"] = competitor.get("score")
    return shooting_inputs(raw)


@nba_tool
def team_advanced_stats(team_name: str, date: str = "", event_id: str = "", metric: str = "all"):
    """Calculate one team's single-game shooting rates, turnover/rebound percentages, estimated possessions and offensive/defensive/net ratings. Uses both teams' box scores; estimates may differ from official play-by-play ratings."""
    d, c = _game(date, event_id, team_name)
    provisional = _advanced_state(c)
    team = _team(d, c, team_name)["team"]
    opponents = [x["team"] for x in c.get("competitors", []) if x["team"]["id"] != team["id"]]
    if len(opponents) != 1:
        raise LookupIssue("A unique opponent is required for team advanced statistics.")
    inputs = _team_advanced_inputs(d, c, team["displayName"])
    try:
        opponent = _team_advanced_inputs(d, c, opponents[0]["displayName"])
    except LookupIssue:
        opponent = {}
    inputs.update({"OPP_" + k: v for k, v in opponent.items()})
    return {"team": team["displayName"], "metrics": calculate(inputs, TEAM_METRICS, metric),
            "provisional": provisional,
            "note": "Calculated locally. Possessions use the average of both teams' (FGA + 0.44*FTA - OREB + TO), including overtime. TS%, TOV%, possessions and ratings are estimates, not official NBA possession counts or ratings. Rebound percentage uses box-score rebound totals."}


@nba_tool
def game_players(date: str = "", event_id: str = "", team_name: str = ""):
    """List players in one game's box score: ESPN IDs, names, teams, and did-not-play status. These IDs cannot be used in NBA historical tools; use resolve_player there. Optionally filter by team. No statistics or rankings; use player_advanced_stats separately for each player."""
    data, competition = _game(date, event_id, team_name)
    squads = data.get("boxscore", {}).get("players", [])
    expected = competition.get("competitors", [])
    if team_name:
        expected = [_choose(expected, team_name, "team")]
    expected_ids = {str(c["team"]["id"]) for c in expected}
    players, present, seen = [], set(), set()
    for squad in squads:
        team = squad.get("team", {})
        if str(team.get("id")) not in expected_ids:
            continue
        for group in squad.get("statistics", []):
            for row in group.get("athletes", []):
                athlete = row.get("athlete", {})
                if not athlete.get("displayName"):
                    continue
                present.add(str(team.get("id")))
                identity = (team.get("id"), athlete.get("id", athlete["displayName"]))
                if identity in seen:
                    continue
                seen.add(identity)
                players.append({"player_id": athlete.get("id"), "player_name": athlete["displayName"],
                                "team_name": team.get("displayName"), "did_not_play": row.get("didNotPlay")})
    missing = [c["team"]["displayName"] for c in expected if str(c["team"]["id"]) not in present]
    return {"event_id": str(competition.get("id") or event_id), "players": players,
            "complete": bool(expected_ids) and not missing, "missing_teams": missing,
            "note": "Lists available game box-score entries, not a season roster. Missing teams mean incomplete coverage."}


@nba_tool
def display_panel(panel: dict):
    """Prepare a visual panel for the FINAL answer. For photos/profiles omit metrics unless the user also asks for statistics. When statistics are requested, include only those requested, copied from lookup results; reuse the same player/game profile for follow-ups. This tool does not fetch, calculate, rank or display intermediate candidates."""
    # Accept numeric box-score values copied directly from lookup results while
    # keeping the structured contract (and advanced-stat metadata) intact.
    metrics = panel.get("metrics")
    if isinstance(metrics, dict):
        normalized = {}
        for key, value in metrics.items():
            if isinstance(value, str) and re.fullmatch(r"\d+(?:-\d+|:\d{2}|(?:\.\d+)?%)", value):
                value = {"label": key, "value": None, "display_value": value, "unit": ""}
            elif value is None or type(value) in (str, int, float):
                value = {"label": key, "value": value, "unit": ""}
            normalized[key] = value
        panel = {**panel, "metrics": normalized}
    data = PanelData.model_validate(panel)
    if not data.player.strip() and not data.team.strip():
        raise ValueError("A player or team name is required for a panel.")
    return data.model_dump()


def run_tool(name: str, args: dict) -> str:
    """Dispatch only registered tools and report malformed calls without crashing."""
    if name not in TOOL_MAP:
        return json.dumps(tool_error('unknown_tool', f"Unknown tool '{name}'.",
            'Choose a tool from available_tools and use its parameter schema.', status='revise', available_tools=list(TOOL_MAP)))
    if not isinstance(args, dict):
        return json.dumps(tool_error('invalid_arguments', 'Tool arguments must be an object.',
            'Provide a JSON object whose keys are the tool parameter names.', status='revise'))
    return TOOL_MAP[name](**args)
