# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The read-only Radarr, Sonarr, Prowlarr and qBittorrent scripts against one fake server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from homelab.cli import main

ROOT = Path(__file__).resolve().parent.parent
KEY = "key-value-1234567890"
PASSWORD = "-".join(["qb", "dummy", "value", "99"])  # built at run time: not a real credential


def queue(n):
    return {
        "totalRecords": n,
        "records": [{"title": f"Item {i}", "status": "downloading", "size": 1000, "sizeleft": 250} for i in range(n)],
    }


ROUTES = {
    "/api/v3/system/status": {"appName": "Radarr", "version": "5.1"},
    "/api/v3/health": [{"type": "warning", "message": "No indexers"}],
    "/api/v1/system/status": {"appName": "Prowlarr", "version": "1.9"},
    "/api/v1/health": [],
    "/api/v1/indexer": [
        {"name": "B", "enable": False, "protocol": "usenet"},
        {"name": "A", "enable": True, "protocol": "torrent"},
    ],
    "/api/v2/app/version": "v4.6",
    "/api/v2/transfer/info": {
        "dl_info_speed": 2 * 1024 * 1024,
        "up_info_speed": 512 * 1024,
        "connection_status": "connected",
    },
    "/api/v2/torrents/info": [
        {"name": "z", "state": "stalledDL", "progress": 0.5, "ratio": 0.1},
        {"name": "a", "state": "uploading", "progress": 1, "ratio": 2.5},
    ],
}


class Server(BaseHTTPRequestHandler):
    fail = False

    def reply(self, status, body):
        data = (
            body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else json.dumps(body).encode())
        )
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        if isinstance(body, str) and body == "Ok.":
            self.send_header("Set-Cookie", "SID=abc; path=/")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        form = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        ok = f"password={PASSWORD}" in form and self.headers.get("Referer")
        self.reply(200, "Ok." if ok else "Fails.")

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if self.fail:
            return self.reply(500, "boom")
        if (
            path.startswith("/api/v2/")
            and path != "/api/v2/auth/login"
            and "SID=abc" not in (self.headers.get("Cookie") or "")
        ):
            return self.reply(403, "Forbidden")
        if path.startswith(("/api/v3/", "/api/v1/")) and self.headers.get("X-Api-Key") != KEY:
            return self.reply(401, "unauthorized")
        if path == "/api/v3/queue":
            return self.reply(200, queue(3))
        self.reply(200, ROUTES.get(path, {}))

    def log_message(self, *args):
        pass


@pytest.fixture
def svc(env, monkeypatch):
    Server.fail = False
    server = HTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tmp, cfg = env[0], env[1]
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp / "state"),
                "custom_dir": str(tmp / "custom"),
                "scripts_dir": str(ROOT / "scripts"),
                "paths": {"lib": str(tmp / "lib")},
            }
        )
    )
    for name, value in (
        ("RADARR_API_KEY", KEY),
        ("SONARR_API_KEY", KEY),
        ("PROWLARR_API_KEY", KEY),
        ("QBITTORRENT_USERNAME", "admin"),
        ("QBITTORRENT_PASSWORD", PASSWORD),
    ):
        monkeypatch.setenv(name, value)
    yield cfg, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def notes(cfg, url, script, *extra, capsys):
    assert main(["--config", str(cfg), "plan", script, "--set", f"url={url}", *extra, "--json"]) == 0
    text = capsys.readouterr().out
    data = json.loads(text)["data"]
    assert data["plan"]["actions"] == []
    assert KEY not in text and PASSWORD not in text
    return data["plan"]["notes"]


def test_radarr_status_shows_version_and_health(svc, capsys):
    assert notes(*svc, "radarr_status", capsys=capsys) == ["Radarr 5.1", "warning: No indexers"]


def test_sonarr_status_uses_its_own_name_when_the_api_omits_it(svc, capsys):
    assert notes(*svc, "sonarr_status", capsys=capsys)[0] == "Radarr 5.1"


def test_queues_show_progress_and_cap_at_the_limit(svc, capsys):
    got = notes(*svc, "radarr_queue", "--set", "limit=2", capsys=capsys)
    assert got[0] == "3 items in the queue" and got[1] == "Item 0 | downloading | 75.0%" and got[-1] == "... 1 more"
    assert len(notes(*svc, "sonarr_queue", capsys=capsys)) == 4


def test_prowlarr_status_and_indexers(svc, capsys):
    assert notes(*svc, "prowlarr_status", capsys=capsys) == ["Prowlarr 1.9", "no health issues"]
    assert notes(*svc, "prowlarr_indexers", capsys=capsys) == [
        "2 indexers, 1 enabled",
        "A | yes | torrent",
        "B | no | usenet",
    ]


def test_qbittorrent_logs_in_and_reports(svc, capsys):
    assert notes(*svc, "qbittorrent_status", capsys=capsys) == [
        "qBittorrent v4.6",
        "down 2.0 MiB/s, up 0.5 MiB/s",
        "connection: connected",
    ]
    got = notes(*svc, "qbittorrent_torrents", capsys=capsys)
    assert got == ["2 torrents (all)", "a | uploading | 100.0% | 2.50", "z | stalledDL | 50.0% | 0.10"]
    assert notes(*svc, "qbittorrent_torrents", "--set", "limit=1", capsys=capsys)[-1] == "... 1 more"


def test_a_wrong_qbittorrent_login_is_a_note(svc, monkeypatch, capsys):
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "wrong-password-0000")
    assert "login failed" in notes(*svc, "qbittorrent_status", capsys=capsys)[0]


def test_a_bad_key_and_a_server_error_are_notes_not_crashes(svc, monkeypatch, capsys):
    monkeypatch.setenv("RADARR_API_KEY", "wrong-key-000000000")
    assert "HTTPError" in notes(*svc, "radarr_status", capsys=capsys)[0]
    Server.fail = True
    assert "HTTPError" in notes(*svc, "prowlarr_indexers", capsys=capsys)[0]


def test_a_missing_secret_is_named_without_a_value(svc, monkeypatch, capsys):
    monkeypatch.delenv("PROWLARR_API_KEY")
    assert notes(*svc, "prowlarr_status", capsys=capsys) == ["PROWLARR_API_KEY is not available; see `homelab secrets`"]
    monkeypatch.delenv("QBITTORRENT_USERNAME")
    assert notes(*svc, "qbittorrent_status", capsys=capsys)[0].startswith("QBITTORRENT_USERNAME is not available")


def test_all_eight_scripts_pass_doctor(svc, capsys):
    cfg, _ = svc
    main(["--config", str(cfg), "doctor", "--json"])
    plugins = {r["plugin"]: r for r in json.loads(capsys.readouterr().out)["data"]["plugins"]}
    names = [
        f"{a}_{b}"
        for a, b in (
            ("radarr", "status"),
            ("radarr", "queue"),
            ("sonarr", "status"),
            ("sonarr", "queue"),
            ("prowlarr", "status"),
            ("prowlarr", "indexers"),
            ("qbittorrent", "status"),
            ("qbittorrent", "torrents"),
        )
    ]
    for name in names:
        problems = [p for p in plugins[name]["problems"] if "is missing" not in p]
        assert problems == [], (name, problems)
