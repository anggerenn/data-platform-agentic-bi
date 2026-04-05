from prefect import task
import subprocess
import os

@task(retries=2, retry_delay_seconds=30)
def run_dbt():
    """Run dbt transformations"""
    dbt_dir = os.environ.get(
        "DBT_PROJECT_DIR",
        os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "dbt"))
    )

    for cmd in [["dbt", "run"], ["dbt", "docs", "generate"]]:
        result = subprocess.run(
            cmd + ["--project-dir", dbt_dir, "--profiles-dir", dbt_dir, "--log-path", "/tmp/dbt-logs", "--target-path", "/tmp/dbt-target"],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr)
            raise Exception(f"{cmd[1]} failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
    print("dbt run complete + docs generate complete")
    return True