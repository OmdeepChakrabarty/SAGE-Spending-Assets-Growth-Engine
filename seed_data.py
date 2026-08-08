"""
seed_data.py
------------
Generates ~200 synthetic personal transactions spread across one full year
(2025) and writes them into transactions.db. Run this once before starting
main.py:

    python seed_data.py

Sign convention used throughout this project: expenses are positive numbers,
income is negative (this matches a simple checking-account ledger where
money going OUT is positive and money coming IN is negative). That means
SUM(amount) over a date range gives net cash flow, and SUM(amount) filtered
to a single category gives total spend in that category.

Kept intentionally procedural (setup -> build rows -> write to db -> report)
rather than wrapped in a class, in the same spirit as the smolagents_examples
scripts - this file is meant to be read top to bottom, not imported.
"""

import random
import sqlite3
from datetime import date, timedelta

DB_PATH = "transactions.db"

# Fixed seed so the dataset is the same every time you re-run this script.
random.seed(42)

# category -> (list of realistic merchants, (low, high) typical amount range)
CATEGORY_MERCHANTS = {
    "groceries": (["Trader Joe's", "Whole Foods", "Safeway", "Local Market"], (15, 120)),
    "dining out": (["Chipotle", "Local Diner", "Pizza Place", "Sushi Bar", "Coffee Shop"], (8, 65)),
    "subscriptions": (["Netflix", "Spotify", "Gym Membership", "Cloud Storage"], (8, 45)),
    "transport": (["Shell Gas", "Uber", "Metro Transit", "Parking Garage"], (5, 60)),
    "entertainment": (["Movie Theater", "Concert Tickets", "Steam Games", "Bowling Alley"], (10, 90)),
    "utilities": (["Electric Co", "Water Utility", "Internet Provider", "Phone Plan"], (30, 150)),
}


def random_date_in_2025():
    """Picks a random day somewhere in the 2025 calendar year."""
    start = date(2025, 1, 1)
    day_offset = random.randint(0, 364)
    return start + timedelta(days=day_offset)


def build_transactions():
    """Builds the full list of (date, merchant, amount, category) rows."""
    rows = []

    # --- Everyday spending across all six spending categories -----------
    # 165 rows spread randomly through the year.
    for _ in range(165):
        category, (merchants, (low, high)) = random.choice(list(CATEGORY_MERCHANTS.items()))
        merchant = random.choice(merchants)
        d = random_date_in_2025()
        amount = round(random.uniform(low, high), 2)

        # Subtle seasonal variation: December dining/entertainment/groceries
        # run noticeably higher (holiday season), so there's a real pattern
        # in the data for the agent to find, not just noise.
        if d.month == 12 and category in ("dining out", "entertainment", "groceries"):
            amount = round(amount * random.uniform(1.3, 1.8), 2)

        rows.append((d.isoformat(), merchant, amount, category))

    # --- Recurring income: paycheck on the 1st and 15th of every month --
    # 24 rows (2 x 12 months). Negative amount = money coming in.
    for month in range(1, 13):
        for day in (1, 15):
            rows.append((date(2025, month, day).isoformat(), "Employer Payroll", -2400.00, "income"))

    # --- Extra December holiday spending on top of the seasonal bump ----
    # 11 one-off purchases concentrated in December, so a "spending by
    # month" chart shows a clear, visible December spike.
    holiday_categories = ["dining out", "entertainment", "groceries"]
    for _ in range(11):
        d = date(2025, 12, random.randint(1, 24))
        category = random.choice(holiday_categories)
        merchant = random.choice(CATEGORY_MERCHANTS[category][0])
        amount = round(random.uniform(60, 250), 2)
        rows.append((d.isoformat(), merchant, amount, category))

    return rows


def seed_database():
    """Creates the transactions table (fresh each run) and inserts the rows."""
    rows = build_transactions()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS transactions")
    cur.execute(
        """
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            merchant TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL
        )
        """
    )
    cur.executemany(
        "INSERT INTO transactions (date, merchant, amount, category) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    print(f"Seeded {len(rows)} transactions into {DB_PATH}")


if __name__ == "__main__":
    seed_database()
