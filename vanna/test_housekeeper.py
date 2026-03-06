"""Smoke test for the housekeeper agent — covers all four verdicts."""
import os, sys
sys.path.insert(0, '/app')

from agents.housekeeper import check, _build_fingerprints, _keywords, _DBT_PATH
from pydantic import BaseModel

class PRD(BaseModel):
    title: str
    objective: str
    audience: str
    metrics: list
    filters: list = []
    action_items: list = []

# ── Debug: show fingerprints ───────────────────────────────────────────────────
fps = _build_fingerprints(_DBT_PATH)
print(f"\n[fingerprints] found {len(fps)} dashboard(s)")
for fp in fps:
    print(f"  {fp['name']}: {sorted(fp['keywords'])}")

# Existing dashboard keywords: {revenue, category, city, order, date}

# ── Scenario 1: FULL (Jaccard = 1.0) ──────────────────────────────────────────
# PRD keywords exactly match existing → {revenue, category, city, order, date}
prd_full = PRD(
    title="City Category Revenue",
    objective="city and category revenue by order date",
    audience="Sales managers",
    metrics=["city revenue", "category revenue", "order date revenue"],
)
kws = _keywords(' '.join(prd_full.metrics) + ' ' + prd_full.objective)
print(f"\n[FULL] PRD keywords: {sorted(kws)}")
r = check(prd_full)
print(f"  verdict : {r.verdict}")
print(f"  matched : {r.matched_dashboard_name}")
print(f"  reason  : {r.reason}")
assert r.verdict == 'full', f"Expected 'full', got '{r.verdict}'"

# ── Scenario 2: PARTIAL_COVERED (PRD ⊆ existing, Jaccard = 0.4) ──────────────
# PRD keywords {revenue, city} ⊆ existing {revenue, category, city, order, date}
# Jaccard = 2/5 = 0.4; no new keywords → covered; score < 0.5 so no LLM call
prd_covered = PRD(
    title="City Revenue",
    objective="show revenue by city",   # 'show'=stopword, 'by'=stopword → {revenue, city}
    audience="Regional heads",
    metrics=["revenue by city"],
)
kws = _keywords(' '.join(prd_covered.metrics) + ' ' + prd_covered.objective)
print(f"\n[PARTIAL_COVERED] PRD keywords: {sorted(kws)}")
assert kws == {'revenue', 'city'}, f"PRD keyword mismatch: {kws}"
r = check(prd_covered)
print(f"  verdict : {r.verdict}")
print(f"  matched : {r.matched_dashboard_name}")
print(f"  reason  : {r.reason}")
assert r.verdict == 'partial_covered', f"Expected 'partial_covered', got '{r.verdict}'"

# ── Scenario 3: PARTIAL_UNCOVERED (PRD ⊄ existing, Jaccard ~0.43) ─────────────
# PRD keywords {revenue, city, category, churn, rate}
# Intersection = 3, Union = 7, Jaccard = 3/7 = 0.43; {churn, rate} are new → uncovered
# Score < 0.5 so no LLM call
prd_uncovered = PRD(
    title="City Revenue and Churn",
    objective="city and category revenue with churn",
    audience="Execs",
    metrics=["city revenue", "category revenue", "churn rate"],
)
kws = _keywords(' '.join(prd_uncovered.metrics) + ' ' + prd_uncovered.objective)
print(f"\n[PARTIAL_UNCOVERED] PRD keywords: {sorted(kws)}")
r = check(prd_uncovered)
print(f"  verdict : {r.verdict}")
print(f"  matched : {r.matched_dashboard_name}")
print(f"  reason  : {r.reason}")
assert r.verdict == 'partial_uncovered', f"Expected 'partial_uncovered', got '{r.verdict}'"

# ── Scenario 4: NONE (Jaccard ~0.0) ───────────────────────────────────────────
# Completely different domain
prd_none = PRD(
    title="Customer Retention",
    objective="understand churn rate and customer lifetime value",
    audience="Product team",
    metrics=["churn rate", "lifetime value", "retention cohort"],
)
kws = _keywords(' '.join(prd_none.metrics) + ' ' + prd_none.objective)
print(f"\n[NONE] PRD keywords: {sorted(kws)}")
r = check(prd_none)
print(f"  verdict : {r.verdict}")
print(f"  reason  : {r.reason}")
assert r.verdict == 'none', f"Expected 'none', got '{r.verdict}'"

print("\nAll 4 housekeeper scenarios passed.")
