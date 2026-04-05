"""
Lightdash sync flow — pulls all dashboards + charts from Lightdash and
commits any changes to git.

Runs on a schedule (every 15 minutes). Covers dashboards created in the
Lightdash UI that never go through the agent pipeline.

Flow:
  1. Spin up lightdash-deploy container via Docker SDK
     → lightdash download -p /dbt/lightdash
  2. Git diff dbt/lightdash/
  3. If changes: git add + commit
"""
import os
import subprocess

import docker
from prefect import flow, task


_NETWORK_DEFAULT = os.environ.get('DOCKER_NETWORK', 'data-platform_data-network')
_REPO_PATH = '/repo'


def _detect_network(client):
    """Return the Docker network this container is connected to."""
    try:
        hostname = os.environ.get('HOSTNAME', '')
        self_container = client.containers.get(hostname)
        networks = list(self_container.attrs.get('NetworkSettings', {}).get('Networks', {}).keys())
        if networks:
            return networks[0]
    except Exception:
        pass
    return _NETWORK_DEFAULT


def _find_lightdash_deploy_image(client):
    """Find the lightdash-deploy image via env var, container name, or image tag."""
    override = os.environ.get('LIGHTDASH_DEPLOY_IMAGE')
    if override:
        return override
    # Most reliable on Coolify: read the image reference from the container config
    for container in client.containers.list(all=True):
        if 'lightdash-deploy' in container.name:
            image_ref = container.attrs.get('Config', {}).get('Image', '')
            if image_ref:
                return image_ref
    # Fallback: search image tags
    for img in client.images.list():
        for tag in img.tags:
            if 'lightdash-deploy' in tag:
                return tag
    return 'data-platform-lightdash-deploy'  # last-resort default


@task(name="lightdash-download")
def download_lightdash_content():
    """Run `lightdash download` in the deploy container, writing to dbt/lightdash/."""
    lightdash_url = os.environ.get('LIGHTDASH_INTERNAL_URL', 'http://lightdash:8080')
    api_key       = os.environ.get('LIGHTDASH_API_KEY', '')

    client = docker.from_env()
    image = _find_lightdash_deploy_image(client)
    network = _detect_network(client)
    print(f"Using image: {image}  network: {network}")

    # Find the host path for ./dbt by inspecting this container's mounts
    hostname = os.environ.get('HOSTNAME', '')
    host_dbt_path = None
    try:
        self_container = client.containers.get(hostname)
        for mount in self_container.attrs.get('Mounts', []):
            if mount.get('Destination') == '/opt/prefect/dbt':
                host_dbt_path = mount['Source']
                break
    except Exception:
        pass

    if not host_dbt_path:
        raise RuntimeError("Could not determine host dbt path from container mounts")

    print(f"Downloading from {lightdash_url} → {host_dbt_path}/lightdash/")

    logs = client.containers.run(
        image=image,
        command=(
            f'sh -c "'
            f'lightdash login {lightdash_url} --token {api_key} && '
            f'lightdash download -p /dbt/lightdash'
            f'"'
        ),
        volumes={host_dbt_path: {'bind': '/dbt', 'mode': 'rw'}},
        network=network,
        remove=True,
        detach=False,
    )
    print(logs.decode() if isinstance(logs, bytes) else logs)


@task(name="git-commit-sync")
def commit_if_changed():
    """Git add + commit any new or modified files under dbt/lightdash/."""
    status = subprocess.run(
        ['git', '-C', _REPO_PATH, 'status', '--porcelain', 'dbt/lightdash/'],
        capture_output=True, text=True,
    )
    if not status.stdout.strip():
        print("No changes — nothing to commit.")
        return

    print("Changes detected:\n" + status.stdout)

    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    subprocess.run(
        ['git', '-C', _REPO_PATH, 'add', 'dbt/lightdash/'],
        check=True,
    )
    subprocess.run(
        ['git', '-C', _REPO_PATH, 'commit', '-m', f'sync: lightdash download {timestamp}'],
        check=True,
    )
    print(f"Committed at {timestamp}")


@flow(name="lightdash_sync")
def lightdash_sync():
    download_lightdash_content()
    commit_if_changed()


if __name__ == '__main__':
    lightdash_sync()
