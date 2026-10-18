from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import Any

from . import auth, receipts
from .config import KauflandError


def _emit(rows: Any, output: str) -> None:
    if output == "json" or not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)


def _add_fetch_args(parser: argparse.ArgumentParser, *, tabular: bool = True) -> None:
    parser.add_argument("-n", "--limit", type=int, default=20, help="number of receipts (default: 20)")
    parser.add_argument("--all", action="store_true", dest="fetch_all", help="fetch the whole history")
    if tabular:
        parser.add_argument("--output", choices=("json", "csv"), default="json", help="output format")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kaufland", description="Fetch Kaufland Card digital receipts.")
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="sign in and store tokens")
    login.add_argument("--email", help="account email (or KAUFLAND_EMAIL)")

    sub.add_parser("status", help="show the saved login")
    sub.add_parser("logout", help="delete the stored tokens")

    _add_fetch_args(sub.add_parser("receipts", help="full receipts as returned by the API"), tabular=False)
    _add_fetch_args(sub.add_parser("items", help="one row per purchased item"))
    _add_fetch_args(sub.add_parser("list", help="one row per receipt"))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "login":
            info = auth.login(args.email)
            print(f"logged in as {info.get('email') or info['sub']}")
            return 0
        if args.command == "status":
            print(json.dumps(auth.status(), indent=2))
            return 0
        if args.command == "logout":
            path = auth.logout()
            print(f"removed {path}" if path else "no saved login")
            return 0

        fetched = receipts.fetch(args.limit, fetch_all=args.fetch_all)
        if args.command == "receipts":
            _emit(fetched, "json")
        elif args.command == "items":
            _emit(receipts.line_items(fetched), args.output)
        else:
            _emit(receipts.summaries(fetched), args.output)
        return 0
    except KauflandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
