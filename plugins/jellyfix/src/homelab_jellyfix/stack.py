# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Where the media stack lives: library folders, service URLs, config files, quarantine. One place, set per run.

The maintenance, Sonarr/Radarr, qBittorrent and Jellyfin jobs read these instead of hardcoding paths and ports.
`configure()` is called by the job runner before a script module is imported, from the plugin options.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = "/media"  # the volume that holds the libraries and downloads
LIBRARIES: dict[str, str] = {}  # library name -> folder
QUARANTINE = "/media/quarantine"
PLANS = "plans"  # where a script that keeps its own journal writes it
STATE = "state"
DOCKER_DIR = os.path.expanduser("~/docker")  # holds <app>/config/config.xml for Sonarr, Radarr, Prowlarr
URLS = {
    "sonarr": "http://localhost:8989",
    "radarr": "http://localhost:7878",
    "prowlarr": "http://localhost:9696",
    "jackett": "http://localhost:9117",
    "jellyfin": "http://localhost:8096",
    "qui": "http://localhost:7476",
    "qbittorrent": "http://localhost:8080",
}
QBT_CONTAINER = "qbittorrent"  # the container that qBittorrent runs in; scripts call its API from inside it
QBT_INTERNAL = "http://localhost:8080"  # its address as seen from inside that container (local auth bypass)
DOWNLOADS = "/media/downloads"  # where finished downloads land (host path)
SETTINGS: dict = {}  # the raw settings, for script-specific entries such as `pairs`
LIBRARY_SPECS: list[dict] = []  # [{name, path, kind, anime}] as in the plugin `libraries` option
CONTAINER_PATHS: dict[str, str] = {}  # host folder -> the same folder inside the downloader container
CONFIG_FILES: dict[str, str] = {}  # app -> config file, when it is not <docker_dir>/<app>/config/config.xml


def configure(settings: dict) -> None:
    global ROOT, QUARANTINE, PLANS, STATE, DOCKER_DIR, QBT_CONTAINER, QBT_INTERNAL, DOWNLOADS
    SETTINGS.clear()
    SETTINGS.update(settings)
    ROOT = settings.get("root", ROOT)
    LIBRARIES.clear()
    LIBRARIES.update(settings.get("libraries", {}))
    QUARANTINE = settings.get("quarantine", str(Path(ROOT) / "_quarantine"))
    STATE = settings.get("state", STATE)
    PLANS = settings.get("plans", str(Path(STATE) / "plans"))
    DOCKER_DIR = os.path.expanduser(settings.get("docker_dir", DOCKER_DIR))
    URLS.update(settings.get("urls", {}))
    QBT_CONTAINER = settings.get("qbittorrent_container", QBT_CONTAINER)
    QBT_INTERNAL = settings.get("qbittorrent_internal_url", QBT_INTERNAL)
    DOWNLOADS = settings.get("downloads", str(Path(ROOT) / "downloads"))
    LIBRARY_SPECS[:] = settings.get("library_specs", [])
    CONTAINER_PATHS.clear()
    CONTAINER_PATHS.update(settings.get("container_paths", {}))
    CONFIG_FILES.update(settings.get("config_files", {}))


def lib(name: str) -> str:
    """The folder of a library by name. A library that is not configured gets a path that does not exist, so a
    script skips it instead of failing to load."""
    return LIBRARIES.get(name) or str(Path(ROOT) / "_not_configured" / name)


def libs(*names: str) -> list[str]:
    return [lib(n) for n in names] if names else list(LIBRARIES.values())


def url(app: str) -> str:
    return URLS[app].rstrip("/")


def port(app: str) -> int:
    return int(url(app).rsplit(":", 1)[1].split("/")[0])


def url_for_port(port: int) -> str:
    """The configured URL of whichever service listens on this port (localhost when none is configured)."""
    for value in URLS.values():
        if value.rstrip("/").endswith(f":{port}"):
            return value.rstrip("/")
    return f"http://localhost:{port}"


def container_map() -> dict[str, str]:
    return dict(CONTAINER_PATHS)


def cfg(app: str) -> str:
    return CONFIG_FILES.get(app) or str(Path(DOCKER_DIR) / app / "config" / "config.xml")


def api_key(app: str) -> str:
    """The API key: the environment first (SONARR_API_KEY ...), then the app's own config file."""
    env = os.environ.get(f"{app.upper()}_API_KEY")
    if env:
        return env
    match = re.search(r"<ApiKey>([^<]+)</ApiKey>", Path(cfg(app)).read_text())
    if not match:
        raise SystemExit(f"no API key for {app}: set {app.upper()}_API_KEY or point config_files at its config")
    return match.group(1)


def to_container(path: str) -> str:
    """Translate a host path to the path the downloader container sees (longest prefix first)."""
    for host in sorted(CONTAINER_PATHS, key=len, reverse=True):
        if path.startswith(host):
            return CONTAINER_PATHS[host] + path[len(host) :]
    return path


def to_host(path: str) -> str:
    """Translate a path inside the downloader container back to the host path."""
    for host, container in sorted(CONTAINER_PATHS.items(), key=lambda kv: len(kv[1]), reverse=True):
        if path.startswith(container):
            return host + path[len(container) :]
    return path
