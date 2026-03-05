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
    question="Show me monthly revenue growth",
    sql="""
SELECT
    month,
    monthly_revenue,
    LAG(monthly_revenue, 1) OVER (ORDER BY month) AS prev_month_revenue,
    round(
        (monthly_revenue - LAG(monthly_revenue, 1) OVER (ORDER BY month))
        / LAG(monthly_revenue, 1) OVER (ORDER BY month) * 100,
    2) AS growth_pct
FROM (
    SELECT
        toStartOfMonth(order_date) AS month,
        SUM(total_revenue) AS monthly_revenue
    FROM transformed_marts.daily_sales
    GROUP BY month
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
