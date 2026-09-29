"""Create an admin user, or promote an existing user to admin.

Usage:
    python -m app.scripts.create_admin --email admin@example.com --name "Admin"

The password is read from the ADMIN_PASSWORD environment variable if set, otherwise it
is prompted for interactively (never passed on the command line, where it would end up
in shell history and process listings).
"""

import argparse
import getpass
import os
import sys

from pydantic import ValidationError

from app.core.security import hash_password
from app.db.database import SessionLocal
from app.models.user import User, UserRole
from app.schemas.auth import SignupRequest
from app.services.auth_service import get_user_by_email


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="Administrator")
    args = parser.parse_args()

    with SessionLocal() as db:
        existing = get_user_by_email(db, args.email.strip().lower())
        if existing is not None:
            existing.role = UserRole.ADMIN
            db.commit()
            print(f"Promoted existing user {existing.email} to admin.")
            return 0

        password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
        try:
            data = SignupRequest(name=args.name, email=args.email, password=password)
        except ValidationError as exc:
            print(f"Invalid admin details:\n{exc}", file=sys.stderr)
            return 1

        db.add(User(name=data.name, email=data.email, password_hash=hash_password(data.password), role=UserRole.ADMIN))
        db.commit()
        print(f"Created admin user {data.email}.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
