from prefect import task
import subprocess
import os

@task(retries=2, retry_delay_seconds=30)
def run_dbt():
    """Run dbt transformations"""
    dbt_dir = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "dbt")
    )

    result = subprocess.run(
        ["dbt", "run", "--project-dir", dbt_dir, "--profiles-dir", dbt_dir],
        capture_output=True,
        text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise Exception(f"dbt run failed: {result.stderr}")
    print("dbt run complete")
    return True