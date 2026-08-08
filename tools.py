"""
tools.py
--------
Small, single-purpose tools for the SAGE (Spending, Assets & Growth Engine)
agent, decorated with @tool exactly like the tool functions in
smolagents_examples/smoltools/jinaai.py - a plain function, a docstring
with an Args/Returns section (smolagents turns that docstring into the
tool description the LLM sees), and nothing else.

  1. query_transactions - runs a read-only SQL SELECT against transactions.db
     and returns the results as plain text (or a clear "no results" message).
  2. modify_transactions - runs a write SQL statement (INSERT/UPDATE/DELETE)
     against transactions.db, so the agent can correct, add, or remove
     transactions on request - not just read them.
  3. create_chart - turns labels/values into a saved Matplotlib PNG, for
     questions that are naturally visual.

No classes, no tool registry - main.py just imports these functions.
"""

import sqlite3

import matplotlib
matplotlib.use("Agg")  # headless backend - there's no display on a server
import matplotlib.pyplot as plt
import pandas as pd
from smolagents import tool

DB_PATH = "transactions.db"
CHART_PATH = "latest_chart.png"

# main.py reads this after each agent turn to know whether a chart was
# produced, so it can attach the image to the chat. A plain module-level
# variable is the simplest thing that works here - no callback registry
# needed for a single-user app.
last_chart_path = None


@tool
def query_transactions(sql_query: str) -> str:
    """Runs a read-only SQL SELECT query against the personal transactions database and returns the results.

    The `transactions` table has these columns:
      - date (TEXT, format YYYY-MM-DD)
      - merchant (TEXT)
      - amount (REAL) - positive = expense, negative = income
      - category (TEXT) - one of: groceries, dining out, subscriptions,
        transport, entertainment, utilities, income

    Args:
        sql_query: A single SQL SELECT statement to run against the transactions table.

    Returns:
        str: The query results as a plain-text table, or a clear message
        saying no matching transactions were found if the query returns
        zero rows. Never invents data - trust this return value exactly.
    """
    # This agent only answers questions, it never edits data - so refuse
    # anything that isn't a SELECT before it ever touches the database.
    cleaned = sql_query.strip().rstrip(";")
    if not cleaned.lower().startswith("select"):
        return "Only SELECT queries are allowed. Please rewrite this as a SELECT statement."

    print(f"Running SQL: {cleaned}")

    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(cleaned, conn)
        conn.close()
    except Exception as e:
        # A malformed query shouldn't crash the app - hand the LLM a clear
        # error message so it can write a corrected query on the next step.
        return f"That query failed to run: {e}. Check the column names and try again."

    if df.empty:
        print("Query returned 0 rows.")
        return "No matching transactions were found for that query."

    print(f"Query returned {len(df)} row(s).")
    return df.to_string(index=False)


@tool
def modify_transactions(sql_statement: str) -> str:
    """Runs a write SQL statement (INSERT, UPDATE, or DELETE) against the transactions database.

    Use this when the user asks to add a transaction, correct a mistaken
    entry (wrong amount, category, merchant, or date), or remove a
    transaction - anything that changes stored data rather than just
    reading it. `query_transactions` stays read-only; this tool is the
    only one allowed to change rows, and it should be used deliberately.

    The `transactions` table has these columns:
      - id (INTEGER, primary key, autoincrement)
      - date (TEXT, format YYYY-MM-DD)
      - merchant (TEXT)
      - amount (REAL) - positive = expense, negative = income
      - category (TEXT) - one of: groceries, dining out, subscriptions,
        transport, entertainment, utilities, income

    Guidance:
      - Prefer a targeted UPDATE/DELETE with a WHERE clause on `id` (look
        the row up with `query_transactions` first if you don't already
        know its id) over broad, unfiltered statements.
      - For INSERT, provide all five columns except `id` (it's assigned
        automatically).
      - Confirm what changed in your final answer - state the row(s)
        affected, not just that the statement ran.

    Args:
        sql_statement: A single SQL INSERT, UPDATE, or DELETE statement to run against the transactions table.

    Returns:
        str: A confirmation message including how many rows were affected,
        or a clear error message if the statement failed or was rejected.
    """
    cleaned = sql_statement.strip().rstrip(";")
    first_word = cleaned.split(None, 1)[0].lower() if cleaned else ""

    if first_word not in ("insert", "update", "delete"):
        return (
            "Only INSERT, UPDATE, or DELETE statements are allowed here. "
            "Use query_transactions for SELECT statements."
        )

    # Blunt but effective guardrail: refuse UPDATE/DELETE with no WHERE
    # clause, since that would touch every row in the table at once.
    if first_word in ("update", "delete") and " where " not in f" {cleaned.lower()} ":
        return (
            "Refused: this statement has no WHERE clause and would affect every row. "
            "Add a WHERE clause (e.g. on id) that targets only the intended transaction(s)."
        )

    print(f"Modifying data: {cleaned}")

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(cleaned)
        affected = cur.rowcount
        conn.commit()
        conn.close()
    except Exception as e:
        return f"That statement failed to run: {e}. Check the column names and try again."

    print(f"{affected} row(s) affected.")
    return f"Done - {affected} row(s) affected."


@tool
def create_chart(chart_type: str, labels: list, values: list, title: str) -> str:
    """Creates and saves a Matplotlib chart (bar, line, or pie) from labels and numeric values.

    Only call this for questions that are naturally visual - spending trends
    over time, or a breakdown across categories. Skip it for a simple
    single-number lookup; just answer that in a sentence instead.

    Args:
        chart_type: One of "bar", "line", or "pie".
        labels: The x-axis labels (or pie slice labels), e.g. month names or category names.
        values: The numeric values matching each label, e.g. total spend per month.
        title: A short, human-readable chart title.

    Returns:
        str: The file path where the chart image was saved.
    """
    global last_chart_path
    print(f"Generating {chart_type} chart: {title}")

    plt.figure(figsize=(7, 4))
    if chart_type == "pie":
        plt.pie(values, labels=labels, autopct="%1.0f%%")
    elif chart_type == "line":
        plt.plot(labels, values, marker="o")
        plt.xticks(rotation=45, ha="right")
        plt.ylabel("Amount ($)")
    else:  # default to a bar chart
        plt.bar(labels, values)
        plt.xticks(rotation=45, ha="right")
        plt.ylabel("Amount ($)")

    plt.title(title)
    plt.tight_layout()
    plt.savefig(CHART_PATH)
    plt.close()

    last_chart_path = CHART_PATH
    print(f"Chart saved to {CHART_PATH}")
    return CHART_PATH
