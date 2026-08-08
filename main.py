"""
main.py
-------
SAGE - Spending, Assets & Growth Engine. A single smolagents CodeAgent that
answers natural-language questions about a personal transactions table, and
can also add, correct, or remove transactions on request, by writing and
running SQL - wired into a Gradio chat interface that streams the agent's
steps live as they happen.

Style note: this stays as close as possible to the smolagents_examples
pattern (smol_claude.py, smol_gradio.py) - one model, a couple of plain
@tool functions in tools.py, no manager/managed-agent layers, no extra
classes. The one piece of "framework" code here is the streaming bridge
into Gradio, and even that is a straight, commented adaptation of
smolagents' own stream_to_gradio / GradioUI._stream_response helpers
(see smolagents/gradio_ui.py in the installed package) rather than
anything custom-built.
"""

import os

import gradio as gr
from dotenv import load_dotenv
from smolagents import (
    CodeAgent,
    LiteLLMModel,
    ActionStep,
    PlanningStep,
    FinalAnswerStep,
    ChatMessageStreamDelta,
    agglomerate_stream_deltas,
)
from smolagents.gradio_ui import pull_messages_from_step

import tools  # query_transactions + create_chart

load_dotenv()

# --- Model config -------------------------------------------------------
# Cloudflare Workers AI, called through LiteLLM. Keeping the model id in
# one variable means swapping the small/large model is a one-line change:
#   MODEL_NAME = "cloudflare/@cf/meta/llama-3.1-8b-instruct"      (fast)
#   MODEL_NAME = "cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8" (stronger)
MODEL_NAME = os.getenv("MODEL_NAME", "cloudflare/@cf/meta/llama-3.1-8b-instruct")

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_KEY = os.getenv("CLOUDFLARE_API_KEY")

if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_KEY:
    raise RuntimeError(
        "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_KEY must be set (see .env.example)."
    )

# LiteLLMModel is a thin wrapper around litellm.completion(). Cloudflare's
# Workers AI exposes an OpenAI-compatible endpoint at this URL, built from
# the account id - see:
# https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/
model = LiteLLMModel(
    model_id=MODEL_NAME,
    api_base=f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
    api_key=CLOUDFLARE_API_KEY,
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


# --- Streaming bridge into Gradio ----------------------------------------
def stream_agent_response(message, history):  # noqa: ARG001 - history unused, agent keeps its own memory
    """Runs the agent on `message` and yields a growing list of gr.ChatMessage.

    This is a trimmed-down, commented rewrite of smolagents'
    GradioUI._stream_response (smolagents/gradio_ui.py). agent.run(...,
    stream=True) yields step objects as the agent works - ActionStep /
    PlanningStep / FinalAnswerStep once a step finishes, and
    ChatMessageStreamDelta tokens while the model is still generating.
    pull_messages_from_step() turns a finished step into one or more
    gr.ChatMessage bubbles - this is how the "writing SQL...", "running
    query...", "found N rows..." print() statements inside tools.py
    surface live as their own chat bubbles (they show up as that step's
    execution logs) instead of only appearing after the whole answer is
    done.
    """
    tools.last_chart_path = None  # clear any chart left over from the last turn

    all_messages = []
    accumulated_deltas = []
    streaming_idx = None

    try:
        for event in agent.run(message, stream=True, reset=False):
            if isinstance(event, (ActionStep, PlanningStep, FinalAnswerStep)):
                # A step just finished - drop the in-progress streaming
                # bubble (if any) and replace it with the finished messages.
                if streaming_idx is not None:
                    all_messages.pop(streaming_idx)
                    streaming_idx = None

                for msg in pull_messages_from_step(event, skip_model_outputs=agent.stream_outputs):
                    all_messages.append(gr.ChatMessage(role=msg.role, content=msg.content, metadata=msg.metadata))
                    yield all_messages

                accumulated_deltas = []

            elif isinstance(event, ChatMessageStreamDelta):
                # The model is still generating this step's thought/code -
                # show the tokens live in one growing bubble.
                accumulated_deltas.append(event)
                text = agglomerate_stream_deltas(accumulated_deltas).render_as_markdown()
                msg = gr.ChatMessage(role="assistant", content=text)
                if streaming_idx is None:
                    streaming_idx = len(all_messages)
                    all_messages.append(msg)
                else:
                    all_messages[streaming_idx] = msg
                yield all_messages

    except Exception as e:
        # Error resilience: a malformed/ambiguous prompt, a step-limit hit,
        # or a model/API hiccup should show up as a normal chat message,
        # never crash the whole Gradio app.
        all_messages.append(
            gr.ChatMessage(
                role="assistant",
                content=f"Something went wrong answering that: {e}. Could you try rephrasing your question?",
            )
        )
        yield all_messages
        return

    # If create_chart ran this turn, attach the saved PNG as its own bubble.
    if tools.last_chart_path:
        all_messages.append(
            gr.ChatMessage(role="assistant", content={"path": tools.last_chart_path, "mime_type": "image/png"})
        )
        yield all_messages


# --- Gradio chat interface ------------------------------------------------
# Gradio 5.x needs Chatbot(type="messages") to accept gr.ChatMessage objects;
# Gradio 6 made "messages" the only format and dropped the parameter. This
# one-line check (copied from smolagents' own GradioUI.create_app) keeps the
# app working across both without pinning a single gradio version.
type_messages_kwarg = {"type": "messages"} if gr.__version__.startswith("5") else {}

chatbot = gr.Chatbot(label="SAGE - Spending, Assets & Growth Engine", height=500, **type_messages_kwarg)

demo = gr.ChatInterface(
    fn=stream_agent_response,
    chatbot=chatbot,
    title="SAGE - Spending, Assets & Growth Engine",
    description=(
        "Ask about your own spending and income - e.g. \"How much did I spend on "
        "groceries in June?\" or \"Show me my monthly spending trend for the year.\" "
        "You can also ask SAGE to add, correct, or remove a transaction. "
        "Watch the steps stream in live as it writes and runs SQL."
    ),
    examples=[
        "How much did I spend on dining out in December?",
        "Show me my spending by category as a chart.",
        "Did I spend anything on travel in March 2025?",
        "Add a $45 dining out charge at Local Diner on 2025-07-10.",
    ],
    **type_messages_kwarg,
)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
