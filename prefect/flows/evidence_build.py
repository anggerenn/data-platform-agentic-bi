from prefect import task
import subprocess
import os

@task(retries=1, retry_delay_seconds=10)
def build_evidence():
    """Build Evidence static site"""
    evidence_dir = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "evidence")
    )

    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=evidence_dir,
        capture_output=True,
        text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise Exception(f"Evidence build failed: {result.stderr}")
    print("Evidence build complete")
    return True