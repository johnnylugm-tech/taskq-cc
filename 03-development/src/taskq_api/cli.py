"""[FR-03] ``python -m taskq_api`` CLI — ``key create`` subcommand.

The CLI entry point is intentionally tiny: it parses ``argv`` via
argparse, dispatches to the ``key create`` handler, and prints the
plaintext exactly once to stdout. The plaintext is never logged,
echoed to stderr, or persisted to any sink — only the SHA-256 digest
lands in ``api_keys.key_hash`` (AC-3.2 + AC-3.4).

Citations: SPEC.md §3 FR-03 "明文只在建立當下印出一次"; SAD.md §2.2 CLI.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Optional, Sequence

from taskq_api.repository import key_repo


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskq_api", description="taskq-api CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    key_cmd = sub.add_parser("key", help="Manage API keys")
    key_sub = key_cmd.add_subparsers(dest="key_command", required=True)
    create = key_sub.add_parser("create", help="Mint a new API key")
    create.add_argument(
        "--scope",
        required=True,
        choices=("read", "write", "admin"),
        help="Scope granted to the key.",
    )
    return parser


def _handle_key_create(args: argparse.Namespace) -> int:
    """Mint a key and print the plaintext to stdout exactly once (AC-3.4).

    The plaintext is the only piece of the new key the caller ever sees
    in human-readable form; only the SHA-256 digest lands in
    ``api_keys.key_hash``.
    """
    key_id, plaintext, _key_hash = key_repo.create(scope=args.scope)
    sys.stdout.write(f"id: {key_id}\nkey: {plaintext}\n")
    sys.stdout.flush()
    return 0


# Dispatch table — kept tiny on purpose. Adding a new subcommand means
# one parser entry + one handler; ``main`` itself does not change.
_HANDLERS: dict[tuple[str, str], Callable[[argparse.Namespace], int]] = {
    ("key", "create"): _handle_key_create,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse ``argv`` and dispatch to a subcommand handler.

    Returns the process exit code (0 on success, non-zero on error).
    ``argv`` defaults to ``sys.argv[1:]`` so ``python -m taskq_api``
    works as expected.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS.get((args.command, args.key_command))
    if handler is None:
        parser.error(f"unhandled command: {args.command!r}")  # pragma: no cover
        return 2  # pragma: no cover
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())