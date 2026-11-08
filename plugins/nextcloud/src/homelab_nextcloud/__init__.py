# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Nextcloud DAV writes that save a copy first and can be undone."""

from __future__ import annotations

import base64
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from homelab_plugin_api import Action, Job, Manifest, Permissions, Plan, Plugin
from pydantic import BaseModel, ConfigDict

ENV_NAME = "NEXTCLOUD_APP_PASSWORD"
TYPES = {".ics": "text/calendar; charset=utf-8", ".vcf": "text/vcard; charset=utf-8"}


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    user: str
    path: str = ""
    file: str = ""
    content_type: str = ""


def request(options: Options, password: str, method: str, path: str, body: bytes | None = None, ctype: str = ""):
    """One DAV request to the configured server. The host never comes from a plan, only from options."""
    base = options.url.rstrip("/")
    if not path.startswith("/"):
        raise ValueError(f"path must start with / : {path!r}")
    req = urllib.request.Request(base + urllib.parse.quote(path, safe="/%~"), data=body, method=method)  # noqa: S310
    token = base64.b64encode(f"{options.user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def current(options: Options, password: str, path: str) -> bytes | None:
    status, body = request(options, password, "GET", path)
    if status == 200:
        return body
    if status == 404:
        return None
    raise RuntimeError(f"could not read {path}: HTTP {status}")


def keep(ctx, folder: str, path: str, body: bytes) -> str:
    where = ctx.path("state") / folder
    where.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y%m%dT%H%M%S')}-{hashlib.sha256(path.encode()).hexdigest()[:8]}-{Path(path).name}"
    (where / name).write_bytes(body)
    return str(where / name)


def ctype_for(options: Options) -> str:
    return options.content_type or TYPES.get(
        Path(options.file or options.path).suffix.lower(), "application/octet-stream"
    )


def plan_put(ctx, options: Options) -> Plan:
    source = Path(options.file).expanduser()
    if not options.path or not source.is_file():
        return Plan(notes=["set path and an existing file (--set path=... --set file=...)"])
    body = source.read_bytes()
    password = ctx.secret(ENV_NAME)
    state = "unknown (no password available at plan time)"
    if password:
        try:
            state = (
                "replaces an existing resource"
                if current(options, password, options.path) is not None
                else "creates it"
            )
        except (RuntimeError, OSError) as err:
            state = f"could not check the server: {err}"
    action = Action(
        kind="plugin",
        describe=f"PUT {options.path} from {source.name} ({len(body)} bytes)",
        args={
            "op": "put",
            "path": options.path,
            "file": str(source),
            "sha256": hashlib.sha256(body).hexdigest(),
            "ctype": ctype_for(options),
        },
    )
    return Plan(actions=[action], notes=[state, "a copy of what is there is saved first; undo restores it"])


def plan_delete(ctx, options: Options) -> Plan:
    if not options.path:
        return Plan(notes=["set path (--set path=...)"])
    action = Action(
        kind="plugin",
        describe=f"DELETE {options.path} after saving a copy",
        args={"op": "delete", "path": options.path},
    )
    return Plan(actions=[action], notes=["the resource is saved to state/plugins/nextcloud first; undo puts it back"])


def apply_action(ctx, options: Options, action: Action) -> dict:
    password = ctx.secret(ENV_NAME)
    if not password:
        raise RuntimeError(f"{ENV_NAME} is not available; see `homelab secrets`")
    a = action.args
    previous = current(options, password, a["path"])
    saved = keep(ctx, "saved", a["path"], previous) if previous is not None else None
    if a["op"] == "put":
        body = Path(a["file"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != a["sha256"]:
            raise RuntimeError(f"{a['file']} changed since the plan, nothing was written")
        status, _ = request(options, password, "PUT", a["path"], body, a["ctype"])
    else:
        if previous is None:
            raise RuntimeError(f"{a['path']} does not exist, nothing to delete")
        status, _ = request(options, password, "DELETE", a["path"])
    if status not in (200, 201, 204):
        raise RuntimeError(f"{a['op'].upper()} {a['path']} failed: HTTP {status}")
    return {"path": a["path"], "saved": saved, "ctype": a.get("ctype", "")}


def undo_action(ctx, options: Options, data: dict) -> None:
    password = ctx.secret(ENV_NAME)
    if not password:
        raise RuntimeError(f"{ENV_NAME} is not available; see `homelab secrets`")
    if data["saved"]:
        body = Path(data["saved"]).read_bytes()
        status, _ = request(
            options, password, "PUT", data["path"], body, data["ctype"] or TYPES.get(Path(data["path"]).suffix, "")
        )
    else:
        # it was created by this run: keep a copy before removing it, because contacts have no trash
        now = current(options, password, data["path"])
        if now is None:
            return
        keep(ctx, "removed", data["path"], now)
        status, _ = request(options, password, "DELETE", data["path"])
    if status not in (200, 201, 204):
        raise RuntimeError(f"undo of {data['path']} failed: HTTP {status}")


PLUGIN = Plugin(
    manifest=Manifest(
        name="nextcloud",
        version="0.1.0",
        category="files",
        summary="Nextcloud DAV writes (calendar, contacts, files) that save a copy first and can be undone",
        permissions=Permissions(reads=["{state}"], writes=["{state}"], network=["the host in options.url"]),
        secrets=[ENV_NAME],
    ),
    config_model=Options,
    jobs={
        "put": Job("Upload a local file to a server path, replacing what is there", plan_put),
        "delete": Job("Remove a resource after saving a copy", plan_delete),
    },
    example={"url": "https://cloud.example.com", "user": "alice"},
    apply_action=apply_action,
    undo_action=undo_action,
)
