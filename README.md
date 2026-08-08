# SAGE — Spending, Assets & Growth Engine
<img width="1920" height="1060" alt="image" src="https://github.com/user-attachments/assets/be8a87e3-e536-4bac-a3f9-52b17a6ca644" />


A single [smolagents](https://github.com/huggingface/smolagents) `CodeAgent` that acts as a conversational
interface over a local SQLite ledger: it answers plain-English questions about your finances, generates
charts on demand, and — unlike a typical read-only reporting bot — can also add, correct, or delete
transactions when you ask it to, all by writing and executing its own SQL. It runs on
[OpenRouter](https://openrouter.ai) and ships with a hand-written HTML/CSS/JS chat interface — no Gradio —
that streams the agent's reasoning and tool calls live, step by step, next to a panel that shows the actual
`transactions` table so you can always see the real data, not just take the agent's word for it.

## Why this exists

Answering "how much did I spend on dining out last month?" usually means opening a spreadsheet, filtering
rows, and adding up a column by hand. Fixing a mistyped entry means the same tedious hunt-and-edit. SAGE
collapses both workflows into one conversation: you describe what you want — a lookup, a trend, a
correction — and the agent decides which tool it needs, writes the SQL, executes it, and reports back in
plain language, with a chart attached if the question is naturally visual. One small agent, one database,
no dashboard or admin panel to maintain — except the live table view built into the page itself.

The project follows the same small, explicit, single-purpose style as
[samwit/smolagents_examples](https://github.com/samwit/smolagents_examples) — one model, a small set of
plain `@tool` functions, no manager/agent hierarchies, no ORM layer between the agent and the database, and
no frontend framework either — just a page, a stylesheet, and one script file.

## How SAGE thinks

SAGE is a single `CodeAgent` with three tools and one guiding split: **read vs. write**.

| Tool                  | Purpose                                              | Access        |
|------------------------|-------------------------------------------------------|---------------|
| `query_transactions`   | Runs a read-only `SELECT` and returns the result set  | Read-only     |
| `modify_transactions`  | Runs `INSERT` / `UPDATE` / `DELETE` against a row      | Read-write    |
| `create_chart`         | Renders a bar, line, or pie chart from labels/values   | Local file I/O|

The system instructions layered on top of smolagents' own `CodeAgent` prompt enforce a simple contract:

- Every number in an answer must trace back to a `query_transactions` call — the model is not allowed to
  guess, extrapolate, or "recall" a figure from earlier in the conversation.
- `modify_transactions` only fires on an explicit request to add, fix, or remove a transaction, and is
  expected to target a specific row (typically by `id`) rather than run broad, unfiltered statements.
  As a structural guardrail, `modify_transactions` itself rejects any `UPDATE`/`DELETE` that has no `WHERE`
  clause before it ever reaches the database.
- `create_chart` is reserved for genuinely visual questions — trends over time, breakdowns across
  categories — not single-number lookups.
- Anything outside the scope of the user's own transaction data (weather, general knowledge, market
  predictions) short-circuits to a fixed refusal via `final_answer`, without ever touching a tool.

This keeps the agent's behavior legible: reasoning steps, generated SQL, and tool output are all visible
in the transcript, so a "why did it say that?" question is always answerable by scrolling up — and the
database panel means you never have to *just* trust the transcript either.

## The interface

Two panels, side by side:

- **Chat** (left) — ask a question or give an instruction. Each step of the agent's work streams in as its
  own bubble as it happens: the code/SQL it wrote, the execution log (where "Running SQL: ...", "Query
  returned N row(s)", "Modifying data: ..." come from), and finally its answer — with a chart image attached
  if `create_chart` ran.
- **Database** (right) — a live, read-only view of the actual `transactions` table, fetched straight from
  `transactions.db`. It refreshes automatically after every chat turn (so an add/correct/delete you just
  asked for shows up immediately) and has a manual **Refresh** button too.

There's no Gradio, no React, no build step — `static/index.html`, `static/style.css`, and `static/app.js`
are plain files served as-is by the FastAPI backend, using nothing but `fetch` and `EventSource`.

## Project layout

```
main.py            FastAPI app: CodeAgent definition, SSE chat endpoint, transactions/chart JSON endpoints
tools.py            query_transactions, modify_transactions, and create_chart - the agent's tools
seed_data.py        generates ~200 synthetic transactions into transactions.db
static/
  index.html         page shell - chat panel + database panel
  style.css           styling, no framework
  app.js               SSE streaming + live table refresh, no framework
requirements.txt
zerops.yaml          Zerops deployment config
.env.example         environment variable template
```

## Local setup

**1. Clone the repo and install dependencies**

```bash
git clone <your-repo-url>
cd <your-repo-name>
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**2. Configure environment variables**

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `OPENROUTER_API_KEY` — create one at [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys)
  (OpenRouter keys start with `sk-or-`)
- `MODEL_NAME` — defaults to `openrouter/free`, OpenRouter's router that automatically picks an available
  free model for each request. Because SAGE now has write access to the database, pinning a specific,
  stronger model (e.g. `meta-llama/llama-3.1-8b-instruct:free`, or a paid model if you have credits) is
  worth considering over the router — it's more consistent about scoping `WHERE` clauses correctly, where
  the router's random model choice can vary run to run. Browse options at
  [openrouter.ai/models](https://openrouter.ai/models).

**3. Seed the database**

```bash
python3 seed_data.py
```

This creates `transactions.db` with ~200 synthetic transactions across 2025, including a visible December
spending spike so there's a real seasonal pattern to ask about.

**4. Run the app**

```bash
python3 main.py
```

Open `http://localhost:7860` and start asking questions, e.g.:

- "How much did I spend on groceries in June?"
- "Show me my spending by category as a chart."
- "Did I spend anything on travel in March 2025?"
- "Add a $45 dining out charge at Local Diner on 2025-07-10."
- "I mis-entered the electric bill from March — it should be $92.50, not $29.50."
- "Delete the duplicate Spotify charge on 2025-05-01."
- "What's the weather like today?" (out of scope — the agent will say so, not guess)

Watch the **Database** panel on the right after an add/correct/delete — it refreshes on its own once the
agent finishes, so you can confirm the row actually changed.

## Read/write data model

SAGE operates on a single `transactions` table:

| Column     | Type    | Notes                                                                 |
|------------|---------|------------------------------------------------------------------------|
| `id`       | INTEGER | Primary key, autoincrement                                            |
| `date`     | TEXT    | `YYYY-MM-DD`                                                          |
| `merchant` | TEXT    | Free text, e.g. `"Trader Joe's"`                                      |
| `amount`   | REAL    | Sign convention below                                                 |
| `category` | TEXT    | `groceries`, `dining out`, `subscriptions`, `transport`, `entertainment`, `utilities`, or `income` |

**Sign convention:** expenses are positive amounts, income is negative — a simple ledger convention where
money going *out* is positive. That means `SUM(amount)` over any filtered date range gives net cash flow
for that range, and `SUM(amount)` filtered to a category gives total spend in that category.

**Write path guardrails**, enforced in `tools.py` before any statement reaches SQLite:

1. Only `INSERT`, `UPDATE`, or `DELETE` statements are accepted by `modify_transactions` — anything else is
   rejected with a message redirecting the agent to `query_transactions`.
2. Any `UPDATE`/`DELETE` without a `WHERE` clause is refused outright, so a single ambiguous instruction
   can't silently rewrite or wipe the entire table.
3. Every successful write reports the number of rows affected, which the agent is instructed to surface
   back to you in plain language — so "fix the March electric bill" ends with a confirmation of exactly
   which row changed and to what value, not just a silent "done." The Database panel refreshing right after
   gives you a second, independent confirmation.

None of this is a substitute for real database access controls in a multi-user or production setting —
see [Security notes](#security-notes) below.

## How the live step-streaming works

`agent.run(message, stream=True)` doesn't just return a final answer — it's a generator that yields events
as the agent works: model output tokens while it's still "thinking," then a finished `ActionStep` once a
step completes (the code it ran, the tool it called, and that tool's output), and finally a
`FinalAnswerStep`.

`main.py`'s `/api/chat` endpoint consumes that generator directly inside a plain Python generator function,
turns each event into a small JSON dict (`step_to_events()` — a hand-written equivalent of smolagents' own
`pull_messages_from_step`, written from scratch so this project has no Gradio dependency at all), and yields
it as a [Server-Sent Event](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events). The
browser opens that endpoint with a plain `EventSource` (`static/app.js`) and appends a new chat bubble for
each event as it arrives — this is what you're seeing as "writing SQL...", "running query...", "found N
rows..." messages appear one after another instead of the whole answer popping in at once. Concretely:

- The SQL the agent writes shows up as its own bubble as soon as that step finishes — for both
  `query_transactions` and `modify_transactions` calls, so writes are just as visible as reads.
- The `print()` statements inside the tool functions (in `tools.py`) get captured as that step's execution
  logs and streamed as a separate bubble — that's where "Running SQL: ...", "Query returned N row(s)",
  "Modifying data: ...", and "Generating bar chart: ..." come from.
- If a chart was generated, the page fetches `/api/chart` and attaches it as an image bubble once the run
  finishes.
- A final `"done"` event — sent whether the turn succeeded or hit an error — re-enables the input box and
  triggers a `/api/transactions` refresh, so the Database panel is never left stale or the input stuck
  disabled.

## Security notes

SAGE's write path — and its interface — are intentionally minimal, and intentionally *not* hardened for
anything beyond local, single-user use:

- SQL is generated by the model and executed with no parameterization or ORM layer between the agent and
  SQLite — the only checks are the statement-type allowlist and the mandatory `WHERE` clause described
  above. Treat this as a personal tool, not something to expose to untrusted input.
- There is no authentication on the app, the chat endpoint, or the `/api/transactions` view — anyone who
  can reach the URL can read and (through chat) write your transaction data. Fine for `localhost` or a
  private deployment; don't expose this publicly without adding auth in front of it.
- There is no per-user data isolation and no audit log beyond the chat transcript in the browser and
  whatever the terminal/container logs capture.
- `transactions.db` is regenerated from `seed_data.py` on every container start on Zerops (see below),
  which means writes made through the chat interface in a deployed environment do **not** persist across
  restarts unless you remove `seed_data.py` from `initCommands`. This is by design for a demo environment,
  but is worth knowing before you rely on SAGE to hold real edits over time.

## Pushing this to GitHub

**1. Create a new empty repository on GitHub** (no README/license/gitignore — you already have those).

**2. Initialize git locally and push:**

```bash
cd <your-project-folder>
git init
git add .
git commit -m "SAGE: Spending, Assets & Growth Engine"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo-name>.git
git push -u origin main
```

`.env` is already in `.gitignore`, so your OpenRouter credentials won't be committed. `transactions.db` is
also ignored — each environment (local or Zerops) generates its own copy from `seed_data.py`.

## Deploying to Zerops

**1. Create a project and Python service**

In the [Zerops dashboard](https://app.zerops.io), create a new project, then add a service using **Python**
as the technology (or import it via `zerops.yaml`, already set up for Python 3.11).

**2. Set your environment variables as secrets**

In the service detail, go to **Environment variables** and add secret variables for:
- `OPENROUTER_API_KEY`
- `MODEL_NAME` (optional — `zerops.yaml`/`.env.example` default of `openrouter/free` is used otherwise)

Don't put real values in `zerops.yaml` or a committed `.env` — use Zerops' secret variables instead.

**3. Connect your GitHub repository**

In **Pipelines & CI/CD settings**, connect the GitHub repo you pushed above. Zerops will read
`zerops.yaml` from the repo root and handle the rest:

- installs Python 3.11 and your `requirements.txt` dependencies
- deploys `static/` alongside the Python files, so the frontend ships with the app
- runs `seed_data.py` before each container start (via `run.initCommands`), so `transactions.db` is always
  present (see the persistence caveat in [Security notes](#security-notes))
- starts the app with `python3 main.py` (a Uvicorn server), listening on port `7860`

**4. Trigger the first deploy**

Push a commit (or use `zcli service deploy` from the repo root) to trigger the pipeline. Once it finishes,
Zerops will show a public subdomain URL for the service — open it to reach the chat interface.

## Roadmap ideas

Not implemented, but natural next steps if SAGE's scope grows:

- Swap the container-local SQLite file for a persistent volume or managed Postgres instance, so writes
  survive restarts in the deployed environment.
- Add a lightweight audit table that logs every `modify_transactions` call (who/what/when/before/after)
  independent of the chat transcript.
- Add basic auth in front of the FastAPI app before deploying anywhere beyond `localhost` or a trusted
  private network.
- Extend the category set and add budget-threshold tools ("warn me if dining out exceeds $X this month").

## A note on the data

`seed_data.py` generates realistic-looking but entirely synthetic transactions — no real financial data is
involved. The sign convention: expenses are positive amounts, income is negative (a simple ledger
convention), so `SUM(amount)` over any filtered range gives net cash flow for that range.
