# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Every environment variable the engine itself reads or sets. `.env.example` is generated from this
table plus the secrets that bundled plugins and scripts declare, and a test fails if code uses a
HOMELAB_ variable that is not listed here."""

# name -> (group, description)
ENGINE_ENV = {
    "HOMELAB_CONFIG": ("engine", "folder with homelab.yaml, plugins.yaml and jobs.yaml (default: ./config)"),
    "HOMELAB_SECRETS_LOADED": ("internal", "set by the engine when it re-executes under unv; never set it yourself"),
}

# Variables UnENVerse reads (`unv describe` lists them). Only needed when unv has to authenticate
# on its own, for example in a scheduled run; see docs/UNV.md.
UNV_ENV = {
    "UNV_SERVER_URL": "remote unv-server, for example http://localhost:8743",
    "UNV_TOKEN": "API token instead of a password (needs UNV_SERVER_URL); best for scheduled runs",
    "UNV_USER": "sign in as this sub-user instead of the vault owner (needs UNV_SERVER_URL)",
    "UNV_TOTP": "second-factor code for a UNV_USER login that has TOTP on",
    "UNV_PASSWORD": "vault password; avoid, prefer --password-file or `unv login`",
    "UNV_FINGERPRINT": "pin the server's TLS certificate to this SHA-256",
    "UNV_CA_CERT": "trust this CA certificate (PEM), and only this one, for UNV_SERVER_URL",
}
