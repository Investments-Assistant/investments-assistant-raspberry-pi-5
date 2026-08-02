#!/usr/bin/env python3
"""Provision an additional local Investment Assistant user.

Run from the repository root after the database is available:
    python3 scripts/create_user.py

The password is read interactively and only its scrypt hash is stored in
PostgreSQL. This avoids opening public self-registration on the VPN service.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
from pathlib import Path
import re
import sys

# Make direct execution from the repository root work without requiring an
# editable install or a manually configured PYTHONPATH.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402

from src.db.database import async_session, create_all_tables  # noqa: E402
from src.db.models import User  # noqa: E402
from src.web.auth import hash_password  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a local assistant user")
    parser.add_argument("--username", help="Login ID")
    parser.add_argument("--display-name", default="", help="Optional display name")
    return parser.parse_args()


async def _create_user(username: str, display_name: str, password: str) -> int:
    await create_all_tables()
    encoded = hash_password(password)
    async with async_session() as session:
        existing = await session.execute(select(User).where(User.username == username))
        if existing.scalar_one_or_none() is not None:
            print(f"User already exists: {username}", file=sys.stderr)
            return 1
        session.add(
            User(
                username=username,
                password_hash=encoded,
                display_name=display_name.strip()[:128] or username,
            )
        )
        await session.commit()
    print(f"Created local user: {username}")
    return 0


def main() -> int:
    args = _parse_args()
    username = (args.username or input("Login ID: ")).strip()
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
        return asyncio.run(_create_user(username, args.display_name, first))
    except Exception as exc:
        print(f"Could not create user: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
