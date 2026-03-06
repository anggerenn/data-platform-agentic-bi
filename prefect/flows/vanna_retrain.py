import os
import subprocess
import requests
from prefect import task


@task(name="validate-schema")
def validate_schema():
    """Validate dbt schema.yml conventions before retraining Vanna."""
    dbt_dir = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "dbt")
    )
    result = subprocess.run(
        ["python", os.path.join(dbt_dir, "validate_schema.py")],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        raise Exception(f"Schema validation failed:\n{result.stdout}")


@task(name="retrain-vanna-schema")
def retrain_vanna_schema():
    """Trigger vanna to re-parse dbt schema and update ChromaDB training pairs."""
    url = os.environ.get('VANNA_INTERNAL_URL', 'http://vanna:8084')
    try:
        r = requests.post(f"{url}/retrain/schema", timeout=120)
        result = r.json()
        if result.get('status') == 'ok':
            print(
                f"Schema retrain complete — "
                f"Q&A: {result.get('qa_added', 0)} added, {result.get('qa_skipped', 0)} skipped | "
                f"Docs: {result.get('docs_added', 0)} added, {result.get('docs_skipped', 0)} skipped"
            )
        else:
            print(f"Schema retrain warning: {result}")
    except Exception as e:
        print(f"Schema retrain failed (non-blocking): {e}")
