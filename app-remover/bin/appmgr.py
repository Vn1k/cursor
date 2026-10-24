#!/usr/bin/env python3
"""App remover engine for the Noctalia plugin vn1k/app-remover.

Every subcommand prints exactly one JSON object on stdout and nothing else;
failures are {"ok": false, "error": ...} with exit 1. The panel only renders
what comes back. The work lives in remover/, one module per concern.

  list [--all]                         apps with a launcher, or every user-installed package
  preview KEY...                       what removing KEYs takes with it, no password asked
  remove KEY... [--state F] [--delete-data] [--reopen PANEL]
                                       uninstall, progress to F; run it detached
  trash PATH...                        move leftover folders to the Trash

A KEY is what `list` calls an item's key: rpm:<name> or flatpak:<id>@<installation>.
"""
import argparse
import json
import sys
from pathlib import Path

from remover import inventory, jobs, leftovers
from remover.util import Fail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="appmgr")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--all", action="store_true", help="every user-installed package, not just apps")
    p = sub.add_parser("preview")
    p.add_argument("keys", nargs="+")
    p = sub.add_parser("remove")
    p.add_argument("keys", nargs="+")
    p.add_argument("--state", type=Path)
    p.add_argument("--delete-data", action="store_true")
    p.add_argument("--reopen", metavar="PANEL", help="noctalia panel to open once the password is in")
    p = sub.add_parser("trash")
    p.add_argument("paths", nargs="+")

    try:
        args = parser.parse_args(argv)
        if args.cmd == "list":
            result = {"ok": True, **inventory.load("packages" if args.all else "apps")}
        elif args.cmd == "preview":
            result = jobs.preview(args.keys)
        elif args.cmd == "remove":
            result = jobs.remove(args.keys, args.state, args.delete_data, args.reopen)
        else:
            result = leftovers.trash(args.paths)
    except SystemExit as exc:  # argparse: usage errors still answer in JSON
        result = {"ok": False, "error": f"bad arguments (exit {exc.code})"}
    except Fail as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - stdout must always be one JSON object
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
