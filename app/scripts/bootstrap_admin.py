from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.db.session import SessionFactory


async def promote(email: str, create_if_missing: bool, display_name: str | None) -> None:
    async with SessionFactory.begin() as session:
        result = await session.execute(
            text(
                """
                UPDATE app.users
                SET role = 'admin'
                WHERE lower(email) = lower(:email)
                  AND status = 'active'
                RETURNING id, email
                """
            ),
            {"email": email},
        )
        row = result.mappings().one_or_none()
        if row is None and create_if_missing:
            result = await session.execute(
                text(
                    """
                    INSERT INTO app.users (role, status, display_name, email)
                    VALUES ('admin', 'active', :display_name, :email)
                    RETURNING id, email
                    """
                ),
                {"display_name": display_name, "email": email},
            )
            row = result.mappings().one()
        if row is None:
            raise SystemExit("Active user with this email was not found")
        print(f"Administrator enabled for {row['email']} ({row['id']})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote an existing user to admin")
    parser.add_argument("--email", required=True)
    parser.add_argument("--create-if-missing", action="store_true")
    parser.add_argument("--display-name")
    args = parser.parse_args()
    asyncio.run(promote(args.email, args.create_if_missing, args.display_name))


if __name__ == "__main__":
    main()
