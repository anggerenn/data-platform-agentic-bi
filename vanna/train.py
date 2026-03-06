"""
One-time training script. Run once after the stack is up to seed ChromaDB.
Re-run whenever dbt models or schema changes.

Usage:
  python train.py
"""
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

from vn import get_vanna

vn = get_vanna()

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
vn.train(ddl="""
CREATE TABLE transformed_marts.daily_sales (
    order_date     Date              COMMENT 'Date of the order',
    category       Nullable(String)  COMMENT 'Product category',
    city           Nullable(String)  COMMENT 'City of the order',
    order_count    UInt64            COMMENT 'Number of distinct orders',
    customer_count UInt64            COMMENT 'Number of distinct customers',
    units_sold     Nullable(Int64)   COMMENT 'Total units sold',
    revenue        Nullable(Float64) COMMENT 'Sum of order amounts (unit price)',
    total_revenue  Nullable(Float64) COMMENT 'Sum of amount * quantity — use this for revenue analysis'
) ENGINE = MergeTree() ORDER BY (order_date, category, city);
""")

vn.train(ddl="""
CREATE TABLE transformed_staging.stg_orders (
    order_id    Nullable(Int64)   COMMENT 'Unique order identifier',
    customer_id Nullable(Int64)   COMMENT 'Unique customer identifier',
    order_date  Date              COMMENT 'Date the order was placed',
    category    Nullable(String)  COMMENT 'Product category',
    city        Nullable(String)  COMMENT 'City where the order was placed',
    amount      Nullable(Float64) COMMENT 'Order amount (unit price)',
    quantity    Nullable(Int64)   COMMENT 'Units ordered',
    line_total  Nullable(Float64) COMMENT 'Total line value (amount * quantity)'
) ENGINE = MergeTree() ORDER BY (order_date, order_id);
""")

# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------
vn.train(documentation="""
The primary table for business questions is transformed_marts.daily_sales.
It is pre-aggregated by order_date, category, and city.
Use total_revenue (not revenue) when analysing revenue — it accounts for quantity.
For order-level detail use transformed_staging.stg_orders.
""")

vn.train(documentation="""
ClickHouse date rules — CRITICAL:
- NEVER pass a string literal directly to a date function like toStartOfMonth('2024-03-01') — this will fail.
- Always wrap string date literals with toDate(): toStartOfMonth(toDate('2024-03-01'))
- For current month use: toStartOfMonth(today())
- For previous month use: toStartOfMonth(today() - INTERVAL 1 MONTH)
- For a specific named month (e.g. March 2026) use: toStartOfMonth(toDate('2026-03-01'))
""")

vn.train(documentation="""
ClickHouse window function rules — CRITICAL, always follow these:
- NEVER use LAG() or LEAD() — they do not exist in ClickHouse.
- Always use lagInFrame() and leadInFrame() instead.
- lagInFrame / leadInFrame REQUIRE a frame spec: ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
- Window functions cannot be mixed with GROUP BY in the same SELECT — always use a subquery.
- Correct pattern:
    SELECT ..., lagInFrame(col, 1) OVER (ORDER BY x ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev
    FROM ( SELECT x, SUM(...) AS col FROM ... GROUP BY x ) sub
- Safe division: use nullIf(denominator, 0) to avoid division by zero.
""")

vn.train(documentation="""
Columns in transformed_marts.daily_sales:
- order_date: the day sales occurred — stored as Date type. Use directly in date functions.
  Examples: toStartOfMonth(order_date), toYear(order_date), order_date >= '2024-01-01'
- category: product category (e.g. Electronics, Clothing, Food)
- city: city where orders were placed
- order_count: number of distinct orders that day
- customer_count: number of distinct customers that day
- units_sold: total units sold that day
- revenue: sum of unit prices (excludes quantity multiplier)
- total_revenue: sum of (amount * quantity) — the correct revenue metric
""")

vn.train(documentation="""
Columns in transformed_staging.stg_orders:
- order_id: unique identifier for each order line
- customer_id: identifier for the customer
- order_date: date the order was placed — stored as Date type
- category: product category
- city: city of the order
- amount: unit price of the item
- quantity: number of units ordered
- line_total: amount * quantity for this line
""")

# ---------------------------------------------------------------------------
# Example question → SQL pairs
# ---------------------------------------------------------------------------
vn.train(
    question="What is the total revenue by category?",
    sql="""
SELECT
    category,
    SUM(total_revenue) AS total_revenue
FROM transformed_marts.daily_sales
GROUP BY category
ORDER BY total_revenue DESC
""")

vn.train(
    question="Show me the top 5 cities by revenue",
    sql="""
SELECT
    city,
    SUM(total_revenue) AS total_revenue
FROM transformed_marts.daily_sales
GROUP BY city
ORDER BY total_revenue DESC
LIMIT 5
""")

vn.train(
    question="What is the daily revenue trend?",
    sql="""
SELECT
    order_date,
    SUM(total_revenue) AS daily_revenue
FROM transformed_marts.daily_sales
GROUP BY order_date
ORDER BY order_date
""")

vn.train(
    question="Show me monthly revenue by city",
    sql="""
SELECT
    toStartOfMonth(order_date) AS month_start,
    city,
    SUM(total_revenue) AS monthly_revenue
FROM transformed_marts.daily_sales
GROUP BY toStartOfMonth(order_date), city
ORDER BY month_start, monthly_revenue DESC
""")

vn.train(
    question="MTD revenue comparison: compare each month filtered to the same day-of-month as the latest date in the data",
    sql="""
SELECT
    toStartOfMonth(order_date) AS month_start,
    SUM(total_revenue) AS mtd_revenue
FROM transformed_marts.daily_sales
WHERE toDayOfMonth(order_date) <= toDayOfMonth(max(order_date) OVER ())
GROUP BY toStartOfMonth(order_date)
ORDER BY month_start
""")

vn.train(
    question="Compare February and March revenue up to the same day (e.g. if March data goes to the 5th, filter February to day <= 5)",
    sql="""
SELECT
    toStartOfMonth(order_date) AS month_start,
    SUM(total_revenue) AS mtd_revenue
FROM transformed_marts.daily_sales
WHERE toDayOfMonth(order_date) <= (
    SELECT toDayOfMonth(max(order_date))
    FROM transformed_marts.daily_sales
    WHERE toStartOfMonth(order_date) = toStartOfMonth(today())
)
GROUP BY toStartOfMonth(order_date)
ORDER BY month_start
""")

vn.train(
    question="Which category had the most orders last month?",
    sql="""
SELECT
    category,
    SUM(order_count) AS total_orders
FROM transformed_marts.daily_sales
WHERE order_date >= toStartOfMonth(today()) - INTERVAL 1 MONTH
  AND order_date <  toStartOfMonth(today())
GROUP BY category
ORDER BY total_orders DESC
""")

vn.train(
    question="How many unique customers do we have per city?",
    sql="""
SELECT
    city,
    SUM(customer_count) AS total_customers
FROM transformed_marts.daily_sales
GROUP BY city
ORDER BY total_customers DESC
""")

vn.train(
    question="What is the average order value by category?",
    sql="""
SELECT
    category,
    SUM(total_revenue) / SUM(order_count) AS avg_order_value
FROM transformed_marts.daily_sales
GROUP BY category
ORDER BY avg_order_value DESC
""")

vn.train(
    question="Show me total revenue this month and month-over-month growth as a scorecard",
    sql="""
SELECT
    month_start,
    revenue,
    prev_revenue,
    round(
        (revenue - prev_revenue) / nullIf(prev_revenue, 0) * 100,
    2) AS growth_pct
FROM (
    SELECT
        toStartOfMonth(order_date) AS month_start,
        SUM(total_revenue) AS revenue,
        lagInFrame(SUM(total_revenue), 1) OVER (
            ORDER BY toStartOfMonth(order_date)
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS prev_revenue
    FROM transformed_marts.daily_sales
    GROUP BY toStartOfMonth(order_date)
) sub
WHERE month_start = toStartOfMonth(today())
""")

vn.train(
    question="Show me monthly revenue growth",
    sql="""
SELECT
    month,
    monthly_revenue,
    prev_month_revenue,
    round(
        (monthly_revenue - prev_month_revenue)
        / nullIf(prev_month_revenue, 0) * 100,
    2) AS growth_pct
FROM (
    SELECT
        month,
        monthly_revenue,
        lagInFrame(monthly_revenue, 1) OVER (
            ORDER BY month
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS prev_month_revenue
    FROM (
        SELECT
            toStartOfMonth(order_date) AS month,
            SUM(total_revenue) AS monthly_revenue
        FROM transformed_marts.daily_sales
        GROUP BY toStartOfMonth(order_date)
    ) monthly
) sub
ORDER BY month
""")

vn.train(
    question="What is the revenue breakdown by month and category?",
    sql="""
SELECT
    toStartOfMonth(order_date) AS month,
    category,
    SUM(total_revenue) AS monthly_revenue
FROM transformed_marts.daily_sales
GROUP BY month, category
ORDER BY month, monthly_revenue DESC
""")

print("Training complete.")
