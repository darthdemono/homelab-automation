# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Extract the completed Oyasumi Punpun RAR into the Manga library once."""

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.request

from homelab_jellyfix import stack

CFG = stack.SETTINGS.get(
    "import_archive", {}
)  # hash, dest, work_dir, komga_url, komga_library, jellyfin_library, komga_env
HASH = CFG.get("hash", "")
DEST = "<root>/Books/Manga/Oyasumi Punpun (2007)"
KOMGA_LIBRARY = CFG.get("komga_library", "")
PLANS = stack.PLANS


def qbt(path):
    return subprocess.check_output(
        ["docker", "exec", stack.QBT_CONTAINER, "curl", "-fsS", "-m", "30", f"{stack.QBT_INTERNAL}/api/v2/{path}"],
        text=True,
    )


def komga_scan():
    values = dict(
        line.rstrip("\n").split("=", 1)
        for line in open(CFG["komga_env"])
        if "=" in line and not line.lstrip().startswith("#")
    )
    password = values["KOMGA_PASSWORD"].encode()
    user = values["KOMGA_USER"].encode()
    import base64

    request = urllib.request.Request(
        f"{CFG['komga_url']}/api/v1/libraries/{KOMGA_LIBRARY}/scan",
        method="POST",
        headers={"Authorization": "Basic " + base64.b64encode(user + b":" + password).decode()},
    )
    urllib.request.urlopen(request, timeout=30).read()


def jellyfin_scan():
    db = CFG["jellyfin_db"]
    token = sqlite3.connect(db).execute("select AccessToken from ApiKeys limit 1").fetchone()[0]
    request = urllib.request.Request(
        f"{stack.url('jellyfin')}/Library/Refresh?libraryId={CFG['jellyfin_library']}",
        method="POST",
        headers={"X-Emby-Token": token},
    )
    urllib.request.urlopen(request, timeout=30).read()


def main():
    torrent = json.loads(qbt(f"torrents/info?hashes={HASH}"))
    if len(torrent) != 1:
        raise SystemExit("the archive torrent was not found: set import_archive.hash")
    torrent = torrent[0]
    if torrent["progress"] < 1:
        raise SystemExit(75)
    source = stack.ROOT + torrent["content_path"].removeprefix("/media")
    if not os.path.isfile(source):
        raise SystemExit(f"completed archive missing: {source}")
    if os.path.isdir(DEST) and any(name.endswith(".cbz") for name in os.listdir(DEST)):
        print("already imported")
        komga_scan()
        jellyfin_scan()
        return

    with tempfile.TemporaryDirectory(prefix="punpun-", dir="<root>/Books/Manga") as work:
        # This 2014 release uses legacy RAR 1.5 compression, which 7-Zip 26
        # cannot decode; the installed unrar handles it.
        subprocess.run(["unrar", "x", "-o+", "-idq", source, work + os.sep], check=True)
        chapters = []
        for root, _dirs, files in os.walk(work):
            images = [f for f in files if os.path.splitext(f)[1].lower() in {".jpg", ".jpeg", ".png", ".webp"}]
            if images:
                chapters.append((root, images))
        if not chapters:
            raise SystemExit("archive contained no chapter image folders")
        os.makedirs(DEST, exist_ok=True)
        created = []
        for root, images in chapters:
            name = os.path.basename(root).strip() or "Chapter"
            archive = os.path.join(DEST, name + ".cbz")
            if not os.path.exists(archive):
                shutil.make_archive(archive[:-4], "zip", root, ".")
                os.replace(archive[:-4] + ".zip", archive)
                created.append(archive)
        if not any(name.endswith(".cbz") for name in os.listdir(DEST)):
            raise SystemExit("no CBZ chapters were created")
    os.makedirs(PLANS, exist_ok=True)
    journal = os.path.join(PLANS, time.strftime("%Y%m%d-%H%M%S") + "-punpun-import.json")
    json.dump(
        {"source": source, "destination": DEST, "transform": "extract-rar-to-cbz", "files": created},
        open(journal, "w"),
        indent=2,
    )
    komga_scan()
    jellyfin_scan()
    print(f"imported Punpun into {DEST}; journal: {journal}")


if __name__ == "__main__":
    main()
