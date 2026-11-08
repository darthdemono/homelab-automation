# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The nextcloud plugin against a fake DAV server: save a copy first, undo restores, nothing vanishes."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml

from homelab.cli import main

USER, PASSWORD = "alice", "app-password-value-123"


class Dav(BaseHTTPRequestHandler):
    store: dict = {}
    log: list = []

    def authorised(self):
        token = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if self.headers.get("Authorization") != f"Basic {token}":
            self.send_response(401)
            self.end_headers()
            return False
        return True

    def reply(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.authorised():
            body = self.store.get(self.path)
            self.reply(200, body) if body is not None else self.reply(404)

    def do_PUT(self):  # noqa: N802
        if self.authorised():
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            created = self.path not in self.store
            self.store[self.path] = body
            self.log.append(("PUT", self.path))
            self.reply(201 if created else 204)

    def do_DELETE(self):  # noqa: N802
        if self.authorised():
            self.log.append(("DELETE", self.path))
            self.reply(204) if self.store.pop(self.path, None) is not None else self.reply(404)

    def log_message(self, *args):
        pass


@pytest.fixture
def dav(env, monkeypatch):
    Dav.store, Dav.log = {}, []
    server = HTTPServer(("127.0.0.1", 0), Dav)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tmp, cfg = env[0], env[1]
    (cfg / "plugins.yaml").write_text(
        yaml.dump(
            {
                "plugins": {
                    "demo": {},
                    "nextcloud": {"options": {"url": f"http://127.0.0.1:{server.server_port}", "user": USER}},
                }
            }
        )
    )
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", PASSWORD)
    (tmp / "event.ics").write_text("BEGIN:VCALENDAR\nSUMMARY:Exam\nEND:VCALENDAR\n")
    yield tmp, cfg
    server.shutdown()


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def out(capsys):
    return json.loads(capsys.readouterr().out)


PATH = "/remote.php/dav/calendars/alice/personal/exam.ics"


def put_args(tmp, path=PATH, name="event.ics"):
    return ["plan", "nextcloud.put", "--set", f"path={path}", "--set", f"file={tmp / name}", "--json"]


def test_put_creates_and_undo_saves_a_copy_before_removing(dav, capsys):
    tmp, cfg = dav
    assert run(cfg, *put_args(tmp)) == 0
    data = out(capsys)["data"]
    assert data["plan"]["notes"][0] == "creates it" and not Dav.log
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert Dav.store[PATH].startswith(b"BEGIN:VCALENDAR")
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert PATH not in Dav.store
    removed = list((tmp / "state" / "plugins" / "nextcloud" / "removed").iterdir())
    assert len(removed) == 1 and removed[0].read_bytes().startswith(b"BEGIN:VCALENDAR")


def test_put_over_an_existing_resource_is_restored_by_undo(dav, capsys):
    tmp, cfg = dav
    Dav.store[PATH] = b"OLD CONTENT"
    run(cfg, *put_args(tmp))
    assert out(capsys)["data"]["plan"]["notes"][0] == "replaces an existing resource"
    run(cfg, "apply", "latest", "--yes")
    assert Dav.store[PATH].startswith(b"BEGIN:VCALENDAR")
    assert (tmp / "state" / "plugins" / "nextcloud" / "saved").iterdir()
    run(cfg, "undo", "latest", "--yes")
    assert Dav.store[PATH] == b"OLD CONTENT"


def test_delete_saves_a_copy_and_undo_puts_it_back(dav, capsys):
    tmp, cfg = dav
    Dav.store[PATH] = b"CARD DATA"
    assert run(cfg, "plan", "nextcloud.delete", "--set", f"path={PATH}", "--json") == 0
    capsys.readouterr()
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert PATH not in Dav.store
    saved = list((tmp / "state" / "plugins" / "nextcloud" / "saved").iterdir())
    assert saved[0].read_bytes() == b"CARD DATA"
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert Dav.store[PATH] == b"CARD DATA"


def test_delete_of_a_missing_resource_is_refused_and_nothing_is_called(dav, capsys):
    cfg = dav[1]
    run(cfg, "plan", "nextcloud.delete", "--set", f"path={PATH}", "--json")
    capsys.readouterr()
    assert run(cfg, "apply", "latest", "--yes") == 1
    assert ("DELETE", PATH) not in Dav.log


def test_a_changed_file_is_not_uploaded(dav, capsys):
    tmp, cfg = dav
    run(cfg, *put_args(tmp))
    (tmp / "event.ics").write_text("changed after the plan\n")
    capsys.readouterr()
    assert run(cfg, "apply", "latest", "--yes") == 1
    assert PATH not in Dav.store


def test_the_password_never_appears_in_output_or_the_ledger(dav, capsys):
    tmp, cfg = dav
    run(cfg, *put_args(tmp))
    run(cfg, "apply", "latest", "--yes", "--json")
    shown = capsys.readouterr().out
    assert PASSWORD not in shown
    for path in (tmp / "state").rglob("*.json*"):
        assert PASSWORD not in path.read_text()


def test_a_wrong_password_fails_the_apply_and_changes_nothing(dav, monkeypatch, capsys):
    tmp, cfg = dav
    run(cfg, *put_args(tmp))
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", "wrong-password-0000")
    capsys.readouterr()
    assert run(cfg, "apply", "latest", "--yes") == 1
    assert PATH not in Dav.store


def test_missing_arguments_are_a_note_not_an_action(dav, capsys):
    run(dav[1], "plan", "nextcloud.put", "--json")
    data = out(capsys)["data"]
    assert data["plan"]["actions"] == [] and "set path" in data["plan"]["notes"][0]


def test_the_plugin_passes_conformance(env, capsys):
    run(env[1], "describe", "nextcloud")
    doc = out(capsys)
    assert doc["manifest"]["secrets"] == ["NEXTCLOUD_APP_PASSWORD"] and set(doc["jobs"]) == {"put", "delete"}
