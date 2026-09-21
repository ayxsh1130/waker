"""Operator console: python -m app.identity.cli bootstrap admin@example.com"""

import argparse
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import Account
from app.db.session import session_scope
from app.identity.service import (
    LOCAL_APPLICATION,
    audit,
    create_account,
    hash_password,
    lock_administration,
    normalize_email,
    revoke_sessions,
)


def main():
    parser = argparse.ArgumentParser(description="Manage Waker accounts from the trusted server console")
    parser.add_argument("command", choices=["bootstrap", "create-user", "reset-password"])
    parser.add_argument("email")
    parser.add_argument("--role", choices=["viewer", "operator", "admin"], default="viewer")
    parser.add_argument("--application", action="append", default=[])
    args = parser.parse_args()
    password = getpass("Password (15–128 characters): ")
    if password != getpass("Repeat password: "):
        parser.error("Passwords do not match")
    try:
        with session_scope() as db:
            if args.command == "reset-password":
                lock_administration(db)
                account = db.scalar(select(Account).where(Account.email == normalize_email(args.email)))
                if not account:
                    raise ValueError("Account not found")
                account.password_hash = hash_password(password)
                revoke_sessions(db, account.id)
                audit(db, "console", "password.reset", account.id)
            else:
                bootstrap = args.command == "bootstrap"
                account = create_account(
                    db,
                    args.email,
                    password,
                    "admin" if bootstrap else args.role,
                    [LOCAL_APPLICATION] if bootstrap else args.application,
                    bootstrap=bootstrap,
                )
        print("Account updated:", account.email, "(" + account.id + ")")
    except ValueError as exc:
        parser.error(str(exc))
    except IntegrityError:
        parser.error("The account already exists or conflicts with an existing record")


if __name__ == "__main__":
    main()
