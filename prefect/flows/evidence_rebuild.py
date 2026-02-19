import subprocess
from prefect import task

@task(retries=1, retry_delay_seconds=10)
def rebuild_evidence():
    # Get the evidence container ID dynamically by label
    find_container = subprocess.run(
        "docker ps --filter 'name=evidence' --format '{{.Names}}' | head -1",
        shell=True,
        capture_output=True,
        text=True
    )
    
    container_name = find_container.stdout.strip()
    
    if not container_name:
        raise Exception("Evidence container not found — is it running?")
    
    result = subprocess.run(
        f"docker exec {container_name} /bin/sh -c 'npm run sources && npm run build'",
        shell=True,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise Exception(
            f"Evidence build failed (container: {container_name}):\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )
    
    return f"Evidence rebuilt successfully in {container_name}"