"""
One-time training script. Run once after the stack is up to seed the BM25 store.
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
    order_date     DATE              NOT NULL,
    category       VARCHAR,
    city           VARCHAR,
    order_count    BIGINT            NOT NULL,
    customer_count BIGINT            NOT NULL,
    units_sold     BIGINT,
    revenue        DOUBLE PRECISION,
    total_revenue  DOUBLE PRECISION
);
""")

vn.train(ddl="""
CREATE TABLE transformed_staging.stg_orders (
    order_id    BIGINT,
    customer_id BIGINT,
    order_date  DATE              NOT NULL,
    category    VARCHAR,
    city        VARCHAR,
    amount      DOUBLE PRECISION,
    quantity    BIGINT,
    line_total  DOUBLE PRECISION
);
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
Data availability:
- The dataset currently covers 2026-03-04 to 2026-04-03 (approximately 1 month).
- There is NO data before March 2026 — do not query February 2026 or earlier.
- Month-over-month comparisons are not possible with this dataset.
- For current month analysis use: order_date >= '2026-03-01' AND order_date < '2026-04-01'
""")

vn.train(documentation="""
PostgreSQL date rules:
- Use DATE_TRUNC('month', col) to get the first day of the month
- Use EXTRACT(day FROM col) for day-of-month, EXTRACT(year FROM col) for year
- Use CURRENT_DATE for today's date
- Use INTERVAL '1 month' (with quotes) for date arithmetic
- Example current month start: DATE_TRUNC('month', CURRENT_DATE)
- Example previous month start: DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
""")

vn.train(documentation="""
PostgreSQL window function rules:
- LAG() and LEAD() are fully supported — use them directly
- Example: LAG(col, 1) OVER (ORDER BY x) AS prev_value
- Window functions cannot be mixed with GROUP BY in the same SELECT — use a subquery
- Correct pattern:
    SELECT ..., LAG(col, 1) OVER (ORDER BY x) AS prev
    FROM ( SELECT x, SUM(...) AS col FROM ... GROUP BY x ) sub
- Safe division: use NULLIF(denominator, 0) to avoid division by zero
""")

vn.train(documentation="""
Columns in transformed_marts.daily_sales:
- order_date: the day sales occurred — stored as DATE type
  Examples: DATE_TRUNC('month', order_date), EXTRACT(year FROM order_date), order_date >= '2024-01-01'
- category: product category (e.g. Electronics, Clothing, Food, Books)
- city: city where orders were placed (e.g. New York, Los Angeles, Chicago, Houston)
- order_count: number of distinct orders that day (per date/category/city row)
- customer_count: number of distinct customers that day (per date/category/city row).
  WARNING: SUM(customer_count) double-counts customers who appear on multiple days or categories.
  For true unique customer counts always use COUNT(DISTINCT customer_id) from transformed_staging.stg_orders.
- units_sold: total units sold that day
- revenue: sum of unit prices (excludes quantity multiplier)
- total_revenue: sum of (amount * quantity) — the correct revenue metric
""")

vn.train(documentation="""
Columns in transformed_staging.stg_orders:
- order_id: unique identifier for each order line
- customer_id: identifier for the customer
- order_date: date the order was placed — stored as DATE type
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
    question="Show me daily revenue by city for march 2026",
    sql="""
SELECT
    order_date,
    city,
    SUM(total_revenue) AS daily_revenue
FROM transformed_marts.daily_sales
WHERE order_date >= '2026-03-01' AND order_date < '2026-04-01'
GROUP BY order_date, city
ORDER BY order_date, city
""")

vn.train(
    question="Show me monthly revenue by city",
    sql="""
SELECT
    DATE_TRUNC('month', order_date) AS month_start,
    city,
    SUM(total_revenue) AS monthly_revenue
FROM transformed_marts.daily_sales
GROUP BY DATE_TRUNC('month', order_date), city
ORDER BY month_start, monthly_revenue DESC
""")

vn.train(
    question="MTD revenue comparison: compare each month filtered to the same day-of-month as the latest date in the data",
    sql="""
SELECT
    DATE_TRUNC('month', order_date) AS month_start,
    SUM(total_revenue) AS mtd_revenue
FROM transformed_marts.daily_sales
WHERE EXTRACT(day FROM order_date) <= EXTRACT(day FROM MAX(order_date) OVER ())
GROUP BY DATE_TRUNC('month', order_date)
ORDER BY month_start
""")

vn.train(
    question="Compare February and March revenue up to the same day (e.g. if March data goes to the 5th, filter February to day <= 5)",
    sql="""
SELECT
    DATE_TRUNC('month', order_date) AS month_start,
    SUM(total_revenue) AS mtd_revenue
FROM transformed_marts.daily_sales
WHERE EXTRACT(day FROM order_date) <= (
    SELECT EXTRACT(day FROM MAX(order_date))
    FROM transformed_marts.daily_sales
    WHERE DATE_TRUNC('month', order_date) = DATE_TRUNC('month', CURRENT_DATE)
)
GROUP BY DATE_TRUNC('month', order_date)
ORDER BY month_start
""")

vn.train(
    question="Which category had the most orders last month?",
    sql="""
SELECT
    category,
    SUM(order_count) AS total_orders
FROM transformed_marts.daily_sales
WHERE order_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
  AND order_date <  DATE_TRUNC('month', CURRENT_DATE)
GROUP BY category
ORDER BY total_orders DESC
""")

vn.train(
    question="How many unique customers do we have per city?",
    sql="""
SELECT
    city,
    COUNT(DISTINCT customer_id) AS total_customers
FROM transformed_staging.stg_orders
GROUP BY city
ORDER BY total_customers DESC
""")

vn.train(
    question="Show me total unique customers in march 2026",
    sql="""
SELECT
    COUNT(DISTINCT customer_id) AS total_customers
FROM transformed_staging.stg_orders
WHERE order_date >= '2026-03-01' AND order_date < '2026-04-01'
""")

vn.train(
    question="How many total units were sold in march 2026?",
    sql="""
SELECT
    SUM(units_sold) AS total_units_sold
FROM transformed_marts.daily_sales
WHERE order_date >= '2026-03-01' AND order_date < '2026-04-01'
""")

vn.train(
    question="Show me units sold by category",
    sql="""
SELECT
    category,
    SUM(units_sold) AS total_units_sold
FROM transformed_marts.daily_sales
GROUP BY category
ORDER BY total_units_sold DESC
""")

vn.train(
    question="What is the total order count last month?",
    sql="""
SELECT
    SUM(order_count) AS total_orders
FROM transformed_marts.daily_sales
WHERE order_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
  AND order_date <  DATE_TRUNC('month', CURRENT_DATE)
""")

vn.train(
    question="What is the average order value by category?",
    sql="""
SELECT
    category,
    SUM(total_revenue) / NULLIF(SUM(order_count), 0) AS avg_order_value
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
    ROUND(
        ((revenue - prev_revenue) / NULLIF(prev_revenue, 0) * 100)::NUMERIC, 2
    ) AS growth_pct
FROM (
    SELECT
        DATE_TRUNC('month', order_date) AS month_start,
        SUM(total_revenue) AS revenue,
        LAG(SUM(total_revenue), 1) OVER (
            ORDER BY DATE_TRUNC('month', order_date)
        ) AS prev_revenue
    FROM transformed_marts.daily_sales
    GROUP BY DATE_TRUNC('month', order_date)
) sub
WHERE month_start = DATE_TRUNC('month', CURRENT_DATE)
""")

vn.train(
    question="Show me monthly revenue growth",
    sql="""
SELECT
    month,
    monthly_revenue,
    prev_month_revenue,
    ROUND(
        ((monthly_revenue - prev_month_revenue)
        / NULLIF(prev_month_revenue, 0) * 100)::NUMERIC, 2
    ) AS growth_pct
FROM (
    SELECT
        month,
        monthly_revenue,
        LAG(monthly_revenue, 1) OVER (ORDER BY month) AS prev_month_revenue
    FROM (
        SELECT
            DATE_TRUNC('month', order_date) AS month,
            SUM(total_revenue) AS monthly_revenue
        FROM transformed_marts.daily_sales
        GROUP BY DATE_TRUNC('month', order_date)
    ) monthly
) sub
ORDER BY month
""")

vn.train(
    question="What is the revenue breakdown by month and category?",
    sql="""
SELECT
    DATE_TRUNC('month', order_date) AS month,
    category,
    SUM(total_revenue) AS monthly_revenue
FROM transformed_marts.daily_sales
GROUP BY DATE_TRUNC('month', order_date), category
ORDER BY month, monthly_revenue DESC
""")

vn.train(
    question="What is the revenue contribution of each city as a percentage of total?",
    sql="""
SELECT
    city,
    SUM(total_revenue) AS city_revenue,
    ROUND((SUM(total_revenue) / SUM(SUM(total_revenue)) OVER () * 100)::NUMERIC, 2) AS pct_of_total
FROM transformed_marts.daily_sales
GROUP BY city
ORDER BY city_revenue DESC
""")

vn.train(
    question="What is the average revenue per customer by city?",
    sql="""
SELECT
    city,
    SUM(line_total) / NULLIF(COUNT(DISTINCT customer_id), 0) AS revenue_per_customer
FROM transformed_staging.stg_orders
GROUP BY city
ORDER BY revenue_per_customer DESC
""")

vn.train(
    question="Show me revenue per customer by category",
    sql="""
SELECT
    category,
    SUM(line_total) / NULLIF(COUNT(DISTINCT customer_id), 0) AS revenue_per_customer
FROM transformed_staging.stg_orders
GROUP BY category
ORDER BY revenue_per_customer DESC
""")

vn.train(
    question="What is the total revenue per customer overall?",
    sql="""
SELECT
    SUM(line_total) / NULLIF(COUNT(DISTINCT customer_id), 0) AS revenue_per_customer
FROM transformed_staging.stg_orders
""")

vn.train(
    question="Show me each customer's total revenue",
    sql="""
SELECT
    customer_id,
    city,
    category,
    SUM(line_total) AS total_revenue,
    COUNT(DISTINCT order_id) AS order_count
FROM transformed_staging.stg_orders
GROUP BY customer_id, city, category
ORDER BY total_revenue DESC
""")

vn.train(
    question="Show me top customer by revenue per city in march 2026 with their contribution to city total",
    sql="""
SELECT
    city,
    customer_id,
    customer_revenue,
    ROUND((customer_revenue / city_total * 100)::NUMERIC, 2) AS pct_of_city
FROM (
    SELECT
        city,
        customer_id,
        SUM(line_total) AS customer_revenue,
        SUM(SUM(line_total)) OVER (PARTITION BY city) AS city_total,
        ROW_NUMBER() OVER (PARTITION BY city ORDER BY SUM(line_total) DESC) AS rn
    FROM transformed_staging.stg_orders
    WHERE order_date >= '2026-03-01' AND order_date < '2026-04-01'
    GROUP BY city, customer_id
) ranked
WHERE rn = 1
ORDER BY customer_revenue DESC
""")

print("Training complete.")
