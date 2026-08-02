#!/usr/bin/env python3
"""Generate the password material used by the Pi browser login.

Usage:
    python3 scripts/create_auth_hash.py

Copy the three printed values into ``.env``.  The password itself is never
written to disk by this script.
"""

from __future__ import annotations

import getpass
from pathlib import Path
import re
import secrets
import sys

# Make direct execution from the repository root work without requiring an
# editable install or a manually configured PYTHONPATH.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.web.auth import hash_password  # noqa: E402


def main() -> int:
    username = input("Login ID [admin]: ").strip() or "admin"
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", username):
        print(
            "Login ID may contain only letters, numbers, dot, underscore, @, and hyphen.",
            file=sys.stderr,
        )
        return 1
    first = getpass.getpass("Password (12+ characters): ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    try:
        password_hash = hash_password(first)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("\nAdd these lines to .env:\n")
    print(f"AUTH_USERNAME={username}")
    print(f"AUTH_PASSWORD_HASH={password_hash}")
    print(f"AUTH_SESSION_SECRET={secrets.token_urlsafe(48)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
