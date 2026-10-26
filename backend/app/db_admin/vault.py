"""
Saved connection profiles and the "use as the app database" override file.

Passwords are encrypted with a Fernet key kept next to the files (data/db_admin/.key, owner-only where the OS allows).
That protects the files from being read by copying them elsewhere; it does NOT protect against someone who can read
the whole folder. If the `cryptography` package is missing, nothing with a password is written.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

from app.core.exceptions import DocIntError

BACKEND_ROOT = Path(__file__).resolve().parents[2]
OVERRIDE_NAME = "db_override.json"


def admin_dir(configured: str | None = None, create: bool = True) -> Path:
    base = configured
    if base is None:
        try:
            from app.core.config import settings
            base = getattr(settings, "DB_ADMIN_DIR", "") or ""
        except Exception:
            base = ""
    d = Path(base) if base else BACKEND_ROOT / "data" / "db_admin"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic_write(path: Path, data: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _fernet(create: bool):
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    key_file = admin_dir(create=create) / ".key"
    if not key_file.exists():
        if not create:
            return None
        _atomic_write(key_file, Fernet.generate_key().decode())
    return Fernet(key_file.read_text(encoding="utf-8").strip().encode())


def encrypt(plain: str) -> str:
    f = _fernet(create=True)
    if f is None:
        raise DocIntError("Passwords cannot be stored on this server (the 'cryptography' package is missing).", "DB_ADMIN_NO_CRYPTO")
    return f.encrypt(plain.encode()).decode()


def decrypt(token: str) -> str | None:
    f = _fernet(create=False)
    if f is None:
        return None
    try:
        return f.decrypt(token.encode()).decode()
    except Exception:
        return None


# ── profiles ────────────────────────────────────────────────────────────────

def _profiles_path(create: bool = False) -> Path:
    return admin_dir(create=create) / "profiles.json"


def _load_profiles() -> list[dict[str, Any]]:
    p = _profiles_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def list_profiles() -> list[dict[str, Any]]:
    out = []
    for r in _load_profiles():
        out.append({"id": r["id"], "name": r["name"], "params": r.get("params", {}), "has_password": bool(r.get("password_enc"))})
    return out


def save_profile(name: str, params: dict[str, Any], password: str | None, profile_id: str | None = None) -> dict[str, Any]:
    name = (name or "").strip()
    if not name or len(name) > 60 or not re.match(r"^[\w .\-()@]+$", name):
        raise DocIntError("Give the connection a short name (letters, digits, space, . - _ ( ) @).", "DB_CONNECT_FAILED")
    rows = _load_profiles()
    existing = next((r for r in rows if r["id"] == profile_id), None) if profile_id else next((r for r in rows if r["name"] == name), None)
    rec = existing or {"id": secrets.token_hex(6)}
    rec["name"] = name
    rec["params"] = {k: v for k, v in params.items() if k != "password"}
    if password:
        rec["password_enc"] = encrypt(password)
    elif not existing:
        rec["password_enc"] = ""
    if not existing:
        rows.append(rec)
    _atomic_write(_profiles_path(create=True), json.dumps(rows, indent=1))
    return {"id": rec["id"], "name": rec["name"]}


def delete_profile(profile_id: str) -> bool:
    rows = _load_profiles()
    keep = [r for r in rows if r["id"] != profile_id]
    if len(keep) == len(rows):
        return False
    _atomic_write(_profiles_path(), json.dumps(keep, indent=1))
    return True


def profile_params(profile_id: str) -> dict[str, Any]:
    r = next((x for x in _load_profiles() if x["id"] == profile_id), None)
    if not r:
        raise DocIntError("That saved connection no longer exists.", "DB_PROFILE_NOT_FOUND")
    params = dict(r.get("params", {}))
    if r.get("password_enc"):
        pw = decrypt(r["password_enc"])
        if pw is None:
            raise DocIntError("The saved password cannot be decrypted (the key file changed). Save the connection again.", "DB_PROFILE_NOT_FOUND")
        params["password"] = pw
    return params


# ── app database override ───────────────────────────────────────────────────

def override_path(create: bool = False) -> Path:
    return admin_dir(create=create) / OVERRIDE_NAME


def write_override(url_with_password: str, label: str) -> None:
    _atomic_write(override_path(create=True), json.dumps({"url_enc": encrypt(url_with_password), "label": label}))


def clear_override() -> bool:
    p = override_path()
    if p.exists():
        p.unlink()
        return True
    return False


def override_info() -> dict[str, Any] | None:
    p = override_path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {"label": d.get("label", "")}
    except Exception:
        return None


def read_override_url() -> str | None:
    p = override_path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return decrypt(d.get("url_enc", ""))
    except Exception:
        return None


def apply_override(settings_obj) -> bool:
    """Called once when settings are created (API and every worker): the override replaces DATABASE_URL."""
    try:
        url = read_override_url()
    except Exception:
        return False
    if not url:
        return False
    settings_obj.DATABASE_URL = url
    return True
