"""
main.py
-------
SAGE - Spending, Assets & Growth Engine. A single smolagents CodeAgent that
answers natural-language questions about a personal transactions table, and
can also add, correct, or remove transactions on request, by writing and
running SQL.

The UI is a small FastAPI app serving a hand-written HTML/CSS/JS frontend
(in static/) - no Gradio. The agent's steps stream to the browser over
Server-Sent Events (SSE), and a second endpoint lets the page show the raw
`transactions` table live, so you can actually see the database, not just
take the agent's word for it.

Style note: the agent definition itself stays exactly as small and explicit
as the smolagents_examples pattern (smol_claude.py) - one model, a few
plain @tool functions in tools.py, no manager/managed-agent layers. The
FastAPI layer below is the minimum needed to replace what Gradio was doing:
serve a page, stream steps, and answer a couple of small JSON endpoints.
"""

import json
import os
import sqlite3

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from smolagents import (
    ActionStep,
    ChatMessageStreamDelta,
    CodeAgent,
    FinalAnswerStep,
    OpenAIServerModel,
    PlanningStep,
    agglomerate_stream_deltas,
)

import tools  # query_transactions + modify_transactions + create_chart

load_dotenv()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# --- Model config -------------------------------------------------------
# OpenRouter, called through smolagents' OpenAIServerModel - OpenRouter
# exposes a plain OpenAI-compatible endpoint, so this talks to it directly
# with the `openai` client under the hood, no LiteLLM in the middle.
#   MODEL_NAME = "openrouter/free"                       (routes to whichever
#                                                           free model is
#                                                           available - the
#                                                           default here)
#   MODEL_NAME = "meta-llama/llama-3.1-8b-instruct:free"  (a specific free
#                                                           model instead of
#                                                           the router)
MODEL_NAME = os.getenv("MODEL_NAME", "openrouter/free")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY must be set (see .env.example).")

model = OpenAIServerModel(
    model_id=MODEL_NAME,
    api_base="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# --- Agent scope & guardrails --------------------------------------------
# `instructions` is appended after smolagents' built-in CodeAgent system
# prompt (see code_agent.yaml inside the smolagents package) rather than
# replacing it - the agent keeps its normal Thought/Code/Observation loop
# and just gets extra domain rules layered on top.
AGENT_INSTRUCTIONS = """
You are SAGE (Spending, Assets & Growth Engine), a personal finance assistant.
You answer questions ONLY about the user's own transaction history, using the
`query_transactions` tool to run read-only SQL against the `transactions`
table, the `modify_transactions` tool to add, correct, or remove
transactions, and the `create_chart` tool when a chart would genuinely help.

Rules:
- Every factual answer about spending, income, or transactions MUST come
  from a `query_transactions` call. Never guess or invent a number.
- If `query_transactions` reports no matching rows, say so plainly in
  natural language (for example: "No spending found in travel for March
  2025."). Do not make up a figure instead.
- Only use `modify_transactions` when the user explicitly asks to add,
  correct, update, or delete a transaction. Never modify data in response
  to a question that's only asking to look something up. When you do
  modify data, target it precisely (a specific `id`, or a narrow WHERE
  clause) and state clearly in your final answer what changed.
- Only call `create_chart` for questions that are naturally visual - a
  trend over time or a breakdown across categories. For a single-number
  lookup, just answer in a sentence and skip the chart.
- If the question is NOT about the user's personal transactions (general
  knowledge, weather, market or stock predictions, or anything unrelated
  to this data), do not call any tool at all. Immediately call:
  final_answer("I can only answer questions about your personal transaction data.")
""".strip()

# Tools list is the plain functions from tools.py - CodeAgent adds
# `final_answer` automatically. `stream_outputs=True` also streams the
# model's own thought/code tokens live, not just the finished steps.
agent = CodeAgent(
    tools=[tools.query_transactions, tools.modify_transactions, tools.create_chart],
    model=model,
    instructions=AGENT_INSTRUCTIONS,
    max_steps=6,
    stream_outputs=True,
)


# --- Step events -----------------------------------------------------------
def step_to_events(step) -> list[dict]:
    """Turns one finished agent step into plain-dict events for the SSE stream.

    This is a small, hand-written equivalent of smolagents' own
    pull_messages_from_step (smolagents/gradio_ui.py) - written from scratch
    instead of imported, so this project has no Gradio dependency at all,
    not even for this one helper. Each dict has a "type" the frontend
    switches on (see static/app.js) and a "content" string to display.
    """
    events = []
    if isinstance(step, ActionStep):
        if step.code_action:
            events.append({"type": "code", "content": step.code_action})
        if step.observations:
            # This is where the "Running SQL: ...", "Query returned N
            # row(s).", "Modifying data: ...", "Generating bar chart: ..."
            # print() statements from tools.py show up - CodeAgent captures
            # a tool's stdout as this step's observations.
            events.append({"type": "log", "content": step.observations.strip()})
        if step.error:
            events.append({"type": "error", "content": str(step.error)})
    elif isinstance(step, PlanningStep):
        events.append({"type": "plan", "content": step.plan})
    elif isinstance(step, FinalAnswerStep):
        events.append({"type": "final", "content": str(step.output)})
    return events


def sse(event: dict) -> str:
    """Formats one dict as a Server-Sent Events data line."""
    return f"data: {json.dumps(event)}\n\n"


# --- FastAPI app -------------------------------------------------------------
app = FastAPI(title="SAGE - Spending, Assets & Growth Engine")


@app.get("/api/chat")
def chat(message: str):
    """Streams one agent turn to the browser as Server-Sent Events.

    A plain (synchronous) generator is fine here - Starlette runs it in a
    worker thread automatically when handed to StreamingResponse, so the
    blocking agent.run() call doesn't block the server's event loop. This
    is a straight rewrite of the same idea used for the Gradio version:
    consume agent.run(message, stream=True) directly and turn each event
    into something the UI can render as it happens, rather than waiting
    for the whole answer.
    """

    def event_stream():
        tools.last_chart_path = None  # clear any chart left over from the last turn
        accumulated_deltas = []

        try:
            for event in agent.run(message, stream=True, reset=False):
                if isinstance(event, ChatMessageStreamDelta):
                    # The model is still generating this step's thought/code -
                    # stream the growing text so the UI shows it live.
                    accumulated_deltas.append(event)
                    text = agglomerate_stream_deltas(accumulated_deltas).render_as_markdown()
                    yield sse({"type": "delta", "content": text})
                elif isinstance(event, (ActionStep, PlanningStep, FinalAnswerStep)):
                    accumulated_deltas = []
                    for e in step_to_events(event):
                        yield sse(e)
        except Exception as e:
            # Error resilience: a malformed/ambiguous prompt, a step-limit
            # hit, or a model/API hiccup should show up as a normal chat
            # message, never crash the server or hang the page.
            yield sse(
                {
                    "type": "error",
                    "content": f"Something went wrong answering that: {e}. Could you try rephrasing your question?",
                }
            )
            yield sse({"type": "done"})
            return

        # If create_chart ran this turn, tell the page to fetch the image.
        if tools.last_chart_path:
            yield sse({"type": "chart", "content": "/api/chart"})

        yield sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/transactions")
def get_transactions():
    """Returns every row in the transactions table as JSON.

    This is what powers the "Database" panel in the UI - a direct read of
    transactions.db, independent of anything the agent has said, so you can
    always see the real state of the data.
    """
    conn = sqlite3.connect(tools.DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, date, merchant, amount, category FROM transactions ORDER BY date DESC, id DESC")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


@app.get("/api/chart")
def get_chart():
    """Serves the most recently generated chart PNG."""
    return FileResponse(tools.CHART_PATH, media_type="image/png")


# Serves static/index.html at "/" and the rest of static/ (style.css,
# app.js) alongside it. This is mounted last, after the /api/* routes
# above, because Starlette matches routes in registration order - a Mount
# on "/" would otherwise swallow every request, API routes included.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
