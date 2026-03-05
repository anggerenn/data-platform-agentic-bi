#!/usr/bin/env python3
"""
Vanna smoke test — run after any deployment to verify all intents work.

Usage:
    python vanna/smoke_test.py [--url http://localhost:8084]
"""
import argparse
import json
import sys
import urllib.request
import urllib.error

TESTS = [
    # (label, question, expected_intent, assertions)
    # --- Explore ---
    {
        "label": "explore: total sales by category",
        "question": "Show me total sales by category",
        "intent": "explore",
        "checks": ["sql_present", "rows_gt_0"],
    },
    {
        "label": "explore: monthly revenue trend",
        "question": "What is the monthly revenue trend?",
        "intent": "explore",
        "checks": ["sql_present", "rows_gt_0"],
    },
    {
        "label": "explore: top 3 cities by sales",
        "question": "Which 3 cities have the highest total sales?",
        "intent": "explore",
        "checks": ["sql_present", "rows_gt_0"],
    },
    # --- Semantic ---
    {
        "label": "semantic: what is daily_sales",
        "question": "What does the daily_sales table contain?",
        "intent": "semantic",
        "checks": ["text_present"],
    },
    {
        "label": "semantic: what is units_sold",
        "question": "What does the units_sold column represent?",
        "intent": "semantic",
        "checks": ["text_present"],
    },
    # --- Clarify ---
    {
        "label": "clarify: what can you help with",
        "question": "What can you help me with?",
        "intent": "clarify",
        "checks": ["text_present"],
    },
    {
        "label": "clarify: out of scope question",
        "question": "What is the weather today?",
        "intent": "clarify",
        "checks": ["text_present"],
    },
]


def post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def run(base_url):
    chat_url = f"{base_url}/chat"
    session_id = None
    passed = 0
    failed = 0

    print(f"\nVanna smoke test  →  {base_url}\n{'─'*60}")

    for t in TESTS:
        label    = t["label"]
        question = t["question"]
        expected = t["intent"]
        checks   = t["checks"]

        try:
            body = {"message": question}
            if session_id:
                body["session_id"] = session_id

            result = post(chat_url, body)
            session_id = result.get("session_id", session_id)

            errors = []

            # Intent check
            actual_intent = result.get("intent")
            if actual_intent != expected:
                errors.append(f"intent={actual_intent!r}, want {expected!r}")

            # Per-check assertions
            for check in checks:
                if check == "sql_present" and not result.get("sql"):
                    errors.append("no SQL returned")
                elif check == "rows_gt_0" and not (result.get("row_count") or 0) > 0:
                    errors.append(f"row_count={result.get('row_count')}")
                elif check == "text_present" and not (result.get("text") or "").strip():
                    errors.append("empty text")

            if errors:
                print(f"  FAIL  {label}")
                for e in errors:
                    print(f"        - {e}")
                failed += 1
            else:
                snippet = (result.get("text") or "")[:80].replace("\n", " ")
                print(f"  PASS  {label}")
                print(f"        {snippet}…" if len(result.get("text", "")) > 80 else f"        {snippet}")
                passed += 1

        except Exception as exc:
            print(f"  ERROR {label}: {exc}")
            failed += 1

    print(f"\n{'─'*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(TESTS)} tests")
    return failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8084")
    args = parser.parse_args()

    ok = run(args.url)
    sys.exit(0 if ok else 1)
