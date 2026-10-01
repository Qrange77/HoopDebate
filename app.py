import json
import uuid
import copy
from datetime import datetime, timezone
from threading import RLock
from pathlib import Path

import litellm
import uvicorn
from fastapi import FastAPI, Cookie, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tools import TOOLS, run_tool

# --- Config ---

SYSTEM_PROMPT = (
    "You are an NBA assistant for English-speaking users. Always respond in English. "
    "Use the relevant NBA tools before reporting game facts; never invent data or event IDs. "
    "Use find_games to identify a game when needed. If multiple games or names match, "
    "ask the user to choose only for a single-game request; do not guess. Reuse the selected event_id across tools. "
    "For comparisons or rankings, compose basic tools: find_games to identify every game in scope, "
    "game_players for each game's candidate players, then player_advanced_stats for every eligible player "
    "using the same requested metric, event_id, full player_name and team_name. "
    "For a whole-day request, examine all returned games instead of asking the user to choose one. "
    "Batch independent tool calls in the same response where possible; continue across rounds until coverage is complete. "
    "Compare and sort the returned metric values yourself and report all ties; no ranking tool is needed. "
    "Skip explicit did-not-play entries and undefined metrics, never treating missing values as zero. "
    "Do not impose an unrequested attempt threshold; if requested, check FGA in metric inputs or player_game_stats. "
    "Ask for a metric when best is undefined. State scope, filters, provisional status and missing coverage. "
    "Only claim an overall maximum after checking all eligible candidates in scope; otherwise label results partial. "
    "Dates are YYYY-MM-DD; omitted dates default to today in America/New_York when no event_id is given. "
    "Team-specific tools require team_name; player-specific tools require player_name. "
    "Keep answers focused on the requested information. Call separate tools for separate categories. "
    "Use game_score with quarter=0 for full-game scores, 1-4 for a quarter, and 5 or higher for overtime. "
    "Use player_game_stats for player statistics and team_game_leader for a single category leader. "
    "Use player_advanced_stats for player efficiency and team_advanced_stats for team advanced metrics. "
    "Report estimate labels, provisional results, and unavailable metrics accurately; never substitute zero for missing values. "
    "Data tools never display panels automatically. For photos/profiles, retrieve data with player_info. "
    "After completing research and comparisons, optionally call display_panel with data copied from prior tool results "
    "only if the user requests a photo/panel or a visual helps present the selected final result. "
    "Do not call display_panel for intermediate candidates or every player inspected. Plain answers need no panel. "
    "Pass only the metrics needed for the final answer, preserving formula, inputs, estimates, availability, "
    "provisional status and caveats. Never invent image URLs or statistics. Then write the final answer. "
    "There is no overall best-player selection tool; do not present a category leader as an official award winner. "
    "For plays and shots, use filters and pagination; follow next_offset if the user requests all events. "
    "Injury reports and standings may be current rather than historical; preserve their dates and season labels. "
    "If data is unavailable or a tool returns an error, explain that without fabricating a result."
)
MAX_TOOL_ROUNDS = 20

# --- The Harness ---


def run_agent(messages: list[dict]) -> tuple[str, list[dict]]:
    """Complete until the model answers without asking for a tool.

    Returns the final text and a record of every tool call made along the way.
    """
    tool_calls = []

    for _ in range(MAX_TOOL_ROUNDS):
        reply = litellm.completion(
            model="vertex_ai/gemini-3.5-flash-lite",
            vertex_location="global",
            messages=messages,
            tools=TOOLS,
        ).choices[0].message

        # Append assistant's reply (text, tool calls, or both) to the context.
        # model_dump() keeps it a plain dict: the raw object carries provider-specific
        # fields that trip Pydantic when LiteLLM re-serializes it next round.
        messages += [reply.model_dump()]

        if not reply.tool_calls:
            return reply.content, tool_calls

        # The harness, not the model, runs each tool and appends the result
        for call in reply.tool_calls:
            args = json.loads(call.function.arguments)
            result = run_tool(call.function.name, args)
            tool_calls += [{"name": call.function.name, "args": args, "result": result}]

            messages += [{"role": "tool", "tool_call_id": call.id, "content": result}]

    # One final model request with tools disabled, after all tool results are appended.
    summary_instruction = {"role": "system", "content": (
        "The tool-call round limit has been reached. Summarize only the collected evidence. "
        "Explicitly state any unfinished coverage; do not claim a definitive winner if candidates remain unchecked. "
        "Do not request further tools."
    )}
    reply = litellm.completion(
        model="vertex_ai/gemini-3.5-flash-lite", vertex_location="global",
        messages=messages + [summary_instruction], tools=TOOLS, tool_choice="none",
    ).choices[0].message
    response = reply.content if not reply.tool_calls else None
    response = response or "I reached the tool-call limit. The collected results may be incomplete; please narrow the scope or continue."
    messages.append({"role": "assistant", "content": response})
    return response, tool_calls


# --- Session Store ---

HISTORY_DIR = Path(__file__).parent / "chat_history"
store_lock = RLock()


def browser_owner(response: Response, nba_browser: str | None = Cookie(default=None)):
    try:
        owner = str(uuid.UUID(nba_browser or ""))
    except ValueError:
        owner = str(uuid.uuid4())
        response.set_cookie("nba_browser", owner, httponly=True, samesite="strict", max_age=31536000)
    return owner


def history_path(owner, session_id):
    try:
        session_id = str(uuid.UUID(session_id))
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid session ID")
    return HISTORY_DIR / owner / (session_id + ".json")


def read_history(owner, session_id):
    path = history_path(owner, session_id)
    if not path.exists():
        raise HTTPException(404, "Conversation not found")
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        raise HTTPException(500, "Unable to read saved conversation")


def save_history(owner, record):
    path = history_path(owner, record["session_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    temporary.replace(path)


# --- FastAPI App ---

app = FastAPI()
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"
app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False), name="frontend-assets")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]


@app.get("/")
def index():
    if not (FRONTEND_DIST / "index.html").is_file():
        raise HTTPException(503, "Build the frontend first: cd frontend && npm ci && npm run build")
    return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.svg")
def favicon():
    return FileResponse(Path(__file__).parent / "frontend" / "public" / "favicon.svg")


@app.get("/sessions")
def list_sessions(owner: str = Depends(browser_owner)):
    with store_lock:
        records = []
        for path in (HISTORY_DIR / owner).glob("*.json"):
            try:
                record = json.loads(path.read_text())
                records.append({key: record[key] for key in ("session_id", "title", "updated_at")})
            except (ValueError, OSError, KeyError):
                continue
        return sorted(records, key=lambda item: item["updated_at"], reverse=True)


@app.get("/sessions/{session_id}")
def get_session(session_id: str, owner: str = Depends(browser_owner)):
    with store_lock:
        record = read_history(owner, session_id)
        return {key: record[key] for key in ("session_id", "title", "turns")}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, owner: str = Depends(browser_owner)):
    if not request.message.strip():
        raise HTTPException(400, "Message must not be empty")
    with store_lock:
        if request.session_id:
            record = read_history(owner, request.session_id)
        else:
            record = {"session_id": str(uuid.uuid4()), "title": request.message.strip()[:80],
                      "messages": [{"role": "system", "content": SYSTEM_PROMPT}], "turns": []}
        messages = copy.deepcopy(record["messages"])
        messages[0] = {"role": "system", "content": SYSTEM_PROMPT}
        messages.append({"role": "user", "content": request.message})
        try:
            response, tool_calls = run_agent(messages)
            response = response or "No response was returned. Please try again."
            record["messages"] = messages
        except Exception as e:
            # Keep partially completed tool calls out of future model context.
            response, tool_calls = f"Model call failed: {type(e).__name__}: {str(e)[:300]}", []
        record["turns"].append({"message": request.message, "response": response, "tool_calls": tool_calls})
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        save_history(owner, record)
        return ChatResponse(response=response, session_id=record["session_id"], tool_calls=tool_calls)


@app.post("/clear")
def clear(session_id: str, owner: str = Depends(browser_owner)):
    with store_lock:
        history_path(owner, session_id).unlink(missing_ok=True)
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
