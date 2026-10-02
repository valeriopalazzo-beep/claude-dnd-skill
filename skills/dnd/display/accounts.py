"""
accounts.py — per-character PIN login for the LAN display.

In LAN mode (--lan) every browser that is not on the DM's own machine must log
in before it sees anything: a player picks their character and types its PIN,
and the server then binds that browser to that character for every action,
roll and sheet read. The DM can also set a PIN of their own to run the main
display from another device (a TV, a tablet).

Storage — PINs are never stored in clear, only as salted PBKDF2 hashes:
    <campaign>/accounts.json   {"characters": {"Mira": {salt, hash, iter, changed}},
                                "logout_before": <epoch>}
    <runtime>/dm_account.json  {salt, hash, iter, changed}

Character PINs live with the campaign (a character only exists inside one), so
they survive plugin updates and never leak into another campaign. The files
are deliberately separate from the character sheets: the sheets are read into
the DM's context during play, and a PIN must never end up there.

Changing a PIN (or `logout-all`) stamps a time; the server rejects any session
created before it, so a reset also kicks out whoever was logged in.

CLI (run by the DM / Claude):
    python accounts.py set-pin     --campaign <c> --character <Name> --pin 123456
    python accounts.py remove      --campaign <c> --character <Name>
    python accounts.py list        --campaign <c>
    python accounts.py logout-all  --campaign <c>
    python accounts.py set-dm-pin  --pin 123456
    python accounts.py remove-dm-pin
--pin can be omitted: the PIN is then read from stdin (one line).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt  # noqa: E402

sys.path.insert(0, os.path.join(_HERE, os.pardir, "scripts"))
from paths import find_campaign  # noqa: E402

PIN_RE = re.compile(r"[0-9]{4,8}")   # ASCII digits only (\d also matches e.g. "１")
_ITER = 200_000
ACCOUNTS_NAME = "accounts.json"
DM_FILE = rt("dm_account.json")


# ─── Hashing ──────────────────────────────────────────────────────────────────

def valid_pin(pin: str) -> bool:
    """A PIN is 4 to 8 digits."""
    return bool(PIN_RE.fullmatch(pin or ""))


def hash_pin(pin: str) -> dict:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _ITER)
    return {"salt": salt.hex(), "hash": digest.hex(), "iter": _ITER, "changed": time.time()}


def verify_pin(pin: str, rec: Optional[dict]) -> bool:
    if not rec or not valid_pin(pin):
        return False
    try:
        salt = bytes.fromhex(rec["salt"])
        expected = bytes.fromhex(rec["hash"])
        iters = int(rec.get("iter", _ITER))
    except (KeyError, ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iters)
    return hmac.compare_digest(digest, expected)


# ─── Files ────────────────────────────────────────────────────────────────────

def _safe_campaign(campaign: str) -> str:
    # Same allowlist the display uses for CAMP_FILE values.
    return re.sub(r"[^A-Za-z0-9_-]", "", campaign or "")[:50]


def accounts_path(campaign: str) -> Optional[Path]:
    camp = _safe_campaign(campaign)
    if not camp:
        return None
    return find_campaign(camp) / ACCOUNTS_NAME


def _read_json(path: Optional[Path | str]) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path | str, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load(campaign: str) -> dict:
    data = _read_json(accounts_path(campaign))
    if not isinstance(data.get("characters"), dict):
        data["characters"] = {}
    return data


def find_character(data: dict, name: str) -> Optional[str]:
    """Canonical stored name matching `name` case-insensitively, or None."""
    key = (name or "").strip().lower()
    for stored in data.get("characters", {}):
        if stored.lower() == key:
            return stored
    return None


def set_pin(campaign: str, character: str, pin: str) -> None:
    if not valid_pin(pin):
        raise ValueError("the PIN must be 4 to 8 digits")
    character = character.strip()
    if not character:
        raise ValueError("character name is empty")
    path = accounts_path(campaign)
    if path is None:
        raise ValueError("invalid campaign name")
    data = load(campaign)
    stored = find_character(data, character)
    if stored and stored != character:
        data["characters"].pop(stored)
    data["characters"][character] = hash_pin(pin)
    _write_json(path, data)


def remove(campaign: str, character: str) -> bool:
    path = accounts_path(campaign)
    data = load(campaign)
    stored = find_character(data, character)
    if path is None or stored is None:
        return False
    data["characters"].pop(stored)
    _write_json(path, data)
    return True


def logout_all(campaign: str) -> None:
    path = accounts_path(campaign)
    if path is None:
        raise ValueError("invalid campaign name")
    data = load(campaign)
    data["logout_before"] = time.time()
    _write_json(path, data)


def dm_record() -> Optional[dict]:
    rec = _read_json(DM_FILE)
    return rec or None


def set_dm_pin(pin: str) -> None:
    if not valid_pin(pin):
        raise ValueError("the PIN must be 4 to 8 digits")
    _write_json(DM_FILE, hash_pin(pin))


def remove_dm_pin() -> bool:
    try:
        os.remove(DM_FILE)
        return True
    except FileNotFoundError:
        return False


def web_accounts_path() -> Path:
    """PINs chosen by players who created their character on the web (/crea)."""
    from paths import characters_dir
    return characters_dir() / "web-accounts.json"


def import_web_pin(campaign: str, character: str) -> bool:
    """Copy the PIN a player chose at /crea into the campaign's accounts.

    Returns False when that character has no web PIN. The record keeps its
    hash; its "changed" time is reset so older sessions for the name end.
    """
    path = accounts_path(campaign)
    if path is None:
        raise ValueError("invalid campaign name")
    web = _read_json(web_accounts_path())
    stored = find_character(web, character) if isinstance(web.get("characters"), dict) else None
    if stored is None:
        return False
    rec = dict(web["characters"][stored])
    rec["changed"] = time.time()
    data = load(campaign)
    old = find_character(data, stored)
    if old:
        data["characters"].pop(old)
    data["characters"][stored] = rec
    _write_json(path, data)
    return True


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _read_pin(args) -> str:
    if args.pin is not None:
        return args.pin.strip()
    return sys.stdin.readline().strip()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Manage display login PINs.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("set-pin", "remove", "import-web-pin"):
        s = sub.add_parser(name)
        s.add_argument("--campaign", required=True)
        s.add_argument("--character", required=True)
        if name == "set-pin":
            s.add_argument("--pin")
    for name in ("list", "logout-all"):
        s = sub.add_parser(name)
        s.add_argument("--campaign", required=True)
    s = sub.add_parser("set-dm-pin")
    s.add_argument("--pin")
    sub.add_parser("remove-dm-pin")
    args = p.parse_args(argv)

    try:
        if args.cmd == "set-pin":
            set_pin(args.campaign, args.character, _read_pin(args))
            print(f"PIN set for {args.character.strip()} (campaign {args.campaign}). "
                  "Any open session for this character was logged out.")
        elif args.cmd == "import-web-pin":
            if import_web_pin(args.campaign, args.character):
                print(f"{args.character.strip()} can log in with the PIN chosen on the web (campaign {args.campaign}).")
            else:
                print(f"No web PIN for {args.character}: set one with set-pin.")
                return 1
        elif args.cmd == "remove":
            if remove(args.campaign, args.character):
                print(f"Removed {args.character}: that character can no longer log in.")
            else:
                print(f"No PIN found for {args.character}.")
                return 1
        elif args.cmd == "list":
            data = load(args.campaign)
            names = sorted(data["characters"])
            print("Characters with a PIN: " + (", ".join(names) if names else "(none)"))
            print("DM PIN: " + ("set" if dm_record() else "not set (DM logs in only from this PC)"))
        elif args.cmd == "logout-all":
            logout_all(args.campaign)
            print("Every player session for this campaign was logged out.")
        elif args.cmd == "set-dm-pin":
            set_dm_pin(_read_pin(args))
            print("DM PIN set.")
        elif args.cmd == "remove-dm-pin":
            print("DM PIN removed." if remove_dm_pin() else "No DM PIN was set.")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
