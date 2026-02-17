from prefect import task
import subprocess
import os

@task(retries=1, retry_delay_seconds=10)
def refresh_evidence_sources():
    """Refresh Evidence data sources only"""
    # Use mounted path in container, fallback to relative for local dev
    evidence_dir = os.environ.get(
        "EVIDENCE_DIR",
        os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "evidence")
        )
    )

    result = subprocess.run(
        ["npm", "run", "sources"],
        cwd=evidence_dir,
        capture_output=True,
        text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise Exception(f"Evidence sources refresh failed: {result.stderr}")
    print("Evidence sources refreshed")
    return True