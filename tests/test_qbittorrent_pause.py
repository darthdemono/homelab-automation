# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""qbittorrent_pause against a fake qBittorrent: pause what runs, undo resumes exactly those."""

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from homelab.cli import main

PASSWORD = "pw-" + "q" * 12
ROOT = Path(__file__).resolve().parent.parent


class Fake(BaseHTTPRequestHandler):
    states: dict = {}
    calls: list = []
    new_api = True

    def send(self, code=200, body=b""):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        rows = [{"hash": h, "name": f"show {h}", "state": s} for h, s in self.states.items()]
        self.send(200, json.dumps(rows).encode())

    def do_POST(self):  # noqa: N802
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        if self.path.endswith("/auth/login"):
            return self.send(200, b"Ok." if form["password"] == [PASSWORD] else b"Fails.")
        action = self.path.rsplit("/", 1)[1]
        if not self.new_api and action in ("stop", "start"):
            return self.send(404)
        self.calls.append((action, form["hashes"][0].split("|")))
        for h in form["hashes"][0].split("|"):
            self.states[h] = "pausedDL" if action in ("stop", "pause") else "downloading"
        self.send()

    def log_message(self, *args):
        pass


@pytest.fixture
def qb(env, monkeypatch):
    Fake.states = {"a": "downloading", "b": "stalledUP", "c": "pausedDL"}
    Fake.calls, Fake.new_api = [], True
    server = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tmp, cfg = env[0], env[1]
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp / "state"),
                "custom_dir": str(tmp / "custom"),
                "scripts_dir": str(ROOT / "src" / "homelab" / "scripts"),
                "paths": {"lib": str(tmp / "lib")},
            }
        )
    )
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", PASSWORD)
    yield cfg, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def test_pauses_only_running_torrents_and_undo_resumes_exactly_those(qb):
    cfg, url = qb
    assert run(cfg, "plan", "qbittorrent_pause", "--set", f"url={url}") == 0
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert Fake.calls == [("stop", ["a", "b"])]
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert Fake.calls[-1] == ("start", ["a", "b"])
    assert Fake.states["c"] == "pausedDL"  # was paused before, stays paused


def test_an_older_qbittorrent_without_stop_falls_back_to_pause(qb):
    cfg, url = qb
    Fake.new_api = False
    assert run(cfg, "plan", "qbittorrent_pause", "--set", f"url={url}", "--set", "name_contains=show a") == 0
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert Fake.calls == [("pause", ["a"])]
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert Fake.calls[-1] == ("resume", ["a"])


def test_nothing_running_means_no_actions(qb, capsys):
    cfg, url = qb
    Fake.states = {"c": "pausedDL"}
    assert run(cfg, "plan", "qbittorrent_pause", "--set", f"url={url}", "--json") == 0
    data = json.loads(capsys.readouterr().out)["data"]["plan"]
    assert data["actions"] == [] and data["notes"] == ["no running torrent matches"]
