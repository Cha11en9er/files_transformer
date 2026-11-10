"""Create the first admin. Prints the password once to stdout.

    python -m app.bootstrap_admin
    python -m app.bootstrap_admin --login chief
"""

from __future__ import annotations

import argparse
import secrets
import sys

from app.services import auth_store
from app.services.auth_store import AuthError


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap an admin account")
    parser.add_argument("--login", default="admin")
    args = parser.parse_args()
    password = secrets.token_urlsafe(9)
    if len(password) < 5:
        password = password + "a1"
    try:
        auth_store.create_user(login=args.login, password=password, role="admin")
    except AuthError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc
    print(password)


if __name__ == "__main__":
    main()
