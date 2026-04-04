#!/usr/bin/env python3
"""
Customer churn analysis E2E test.
Runs 6 chat questions then the full DPM → dashboard build flow.
Logs every response so we can audit consistency.

Usage:
    python3 vanna/churn_test.py [--url http://localhost:8084]
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime

BASE_URL = "http://localhost:8084"

QUESTIONS = [
    "How many unique customers ordered in March 2026?",
    "Show me customers who ordered more than once in March 2026",
    "Which city had the biggest drop in customer count compared to February 2026?",
    "What is the average revenue per customer by city for March 2026?",
    "Can we identify churned customers from this data?",
    "Show me the top 10 customers by total revenue in March 2026",
]

DPM_ANSWERS = [
    "We don't know which customers are disengaging until revenue has already dropped — by then it's too late to intervene",
    "Proactively identify high-value customers showing early signs of churn so sales can reach out before they're lost",
    "Sales managers and account managers",
    "Metrics: total revenue per customer, order count per customer, average order value, customer count by type (active vs inactive), customer leaderboard by revenue. Dimensions: by city, by category, by customer",
    "Active means customer placed at least 1 order in the last 30 days. Inactive means no order in the last 30 days. Customer leaderboard means top customers ranked by total revenue descending.",
    "Reach out to top customers who haven't ordered recently, flag cities with declining customer counts, prioritise accounts with dropping order frequency",
]


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def post(url, payload, timeout=120):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return None, f"HTTP {e.code}: {body[:300]}"
    except Exception as exc:
        return None, str(exc)


def divider(title=""):
    w = 60
    if title:
        print(f"\n{'─'*3} {title} {'─'*(w - len(title) - 5)}")
    else:
        print("─" * w)


# ── Chat questions ─────────────────────────────────────────────────────────────

def run_chat(base_url):
    session_id = None
    results = []

    divider("CHAT — 6 questions")

    for i, question in enumerate(QUESTIONS, 1):
        body = {"message": question}
        if session_id:
            body["session_id"] = session_id

        print(f"\nQ{i}: {question}")
        result, err = post(f"{base_url}/chat", body)

        if err:
            print(f"  ERROR: {err}")
            results.append({"q": i, "question": question, "error": err})
            continue

        session_id = result.get("session_id", session_id)
        intent   = result.get("intent", "?")
        sql      = result.get("sql") or ""
        text     = (result.get("text") or "").strip()
        rows     = result.get("row_count")
        chart    = result.get("chart_spec", {}) or {}

        print(f"  intent : {intent}")
        if sql:
            print(f"  sql    : {sql[:120]}{'…' if len(sql)>120 else ''}")
        if rows is not None:
            print(f"  rows   : {rows}")
        if text:
            print(f"  text   : {text[:200]}{'…' if len(text)>200 else ''}")
        if chart.get("type"):
            print(f"  chart  : {chart['type']} — x={chart.get('x_axis')} y={chart.get('y_axis')}")

        results.append({
            "q": i,
            "question": question,
            "intent": intent,
            "sql": sql,
            "row_count": rows,
            "text": text,
            "chart_type": chart.get("type"),
        })

    return session_id, results


# ── Dashboard flow ─────────────────────────────────────────────────────────────

def run_dashboard(base_url, session_id):
    divider("DASHBOARD — DPM + build")

    # Start DPM
    result, err = post(f"{base_url}/dashboard/start", {"session_id": session_id})
    if err or not result or result.get("error"):
        print(f"  FAIL /dashboard/start: {err or result.get('error')}")
        return None

    dpm_session_id = result["dpm_session_id"]
    print(f"\n/dashboard/start → dpm={dpm_session_id[:8]}…  status={result.get('status')}")
    print(f"  DPM: {(result.get('message') or '')[:120]}")

    # Feed DPM answers
    for i, answer in enumerate(DPM_ANSWERS, 1):
        print(f"\nAnswer {i}: {answer[:80]}{'…' if len(answer)>80 else ''}")
        result, err = post(f"{base_url}/dashboard/chat", {
            "dpm_session_id": dpm_session_id,
            "message": answer,
        })
        if err or not result or result.get("error"):
            print(f"  FAIL: {err or result.get('error')}")
            return None

        status = result.get("status")
        msg    = (result.get("message") or "")[:120]
        prd    = result.get("prd")
        print(f"  status: {status}")
        print(f"  DPM  : {msg}")

        if status == "complete":
            if prd:
                print(f"\n  PRD title   : {prd.get('title')}")
                print(f"  PRD metrics : {prd.get('metrics')}")
                print(f"  PRD dims    : {prd.get('dimensions')}")
            break
    else:
        print("  FAIL: DPM never reached complete after all answers")
        return None

    # Build dashboard
    divider("BUILD")
    print("Calling /dashboard/build (may take ~60–120s)…")
    result, err = post(f"{base_url}/dashboard/build",
                       {"dpm_session_id": dpm_session_id}, timeout=180)
    if err:
        print(f"  FAIL: {err}")
        return None

    if result.get("error"):
        print(f"  FAIL: {result['error']}")
        if result.get("yaml_written"):
            print(f"  yaml_written: {result['yaml_written']}")
        return None

    if result.get("needs_new_model"):
        print(f"  needs_new_model: True")
        print(f"  message: {result.get('message')}")
        if result.get("uncovered_metrics"):
            print(f"  uncovered_metrics: {result['uncovered_metrics']}")
        print(f"  suggested_sql: {(result.get('suggested_sql') or '')[:200]}")
        return None

    print(f"  charts_created : {result.get('charts_created')}")
    print(f"  model          : {result.get('db_schema')}.{result.get('model_name')}")
    print(f"  yaml_written   : {result.get('yaml_written')}")
    print(f"  url            : {result.get('url')}")
    if result.get("housekeeper"):
        print(f"  housekeeper    : {result.get('housekeeper')} — {result.get('suggestion')}")
    return result


# ── Main ───────────────────────────────────────────────────────────────────────

def main(base_url):
    print(f"\nChurn analysis test → {base_url}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    session_id, chat_results = run_chat(base_url)

    if not session_id:
        print("\nNo session — cannot proceed to dashboard.")
        sys.exit(1)

    dashboard_result = run_dashboard(base_url, session_id)

    divider("SUMMARY")
    intents = [r.get("intent", "error") for r in chat_results]
    for i, r in enumerate(chat_results, 1):
        status = "✓" if r.get("intent") else "✗"
        print(f"  Q{i} {status} {r.get('intent','ERROR'):12} rows={str(r.get('row_count','-')):>5}  {r['question'][:55]}")

    print(f"\n  Dashboard: {'✓ built' if dashboard_result and not dashboard_result.get('error') else '✗ failed'}")
    print(f"\nFinished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8084")
    args = parser.parse_args()
    main(args.url)
