from __future__ import annotations

import argparse
import json
from pathlib import Path

from bulletjournal.cli.dev import dev_server
from bulletjournal.cli.doctor import doctor
from bulletjournal.cli.init_project import init_project
from bulletjournal.cli.rebuild_state import rebuild_state
from bulletjournal.cli.start import start_server
from bulletjournal.cli.validate_templates import validate_templates
from bulletjournal.storage import is_project_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='bulletjournal', description='BulletJournal notebook orchestration platform')
    subparsers = parser.add_subparsers(dest='command')

    init_parser = subparsers.add_parser('init', help='Initialize a new BulletJournal project')
    init_parser.add_argument('path')
    init_parser.add_argument('--title', default=None)

    start_parser = subparsers.add_parser('start', help='Start the BulletJournal server')
    start_parser.add_argument('path', nargs='?', default='.')
    start_parser.add_argument('--open', action='store_true')

    dev_parser = subparsers.add_parser('dev', help='Start BulletJournal in development mode')
    dev_parser.add_argument('path', nargs='?', default='.')
    dev_parser.add_argument('--open', action='store_true')

    doctor_parser = subparsers.add_parser('doctor', help='Check environment and project health')
    doctor_parser.add_argument('path', nargs='?', default='.')

    validate_parser = subparsers.add_parser('validate-templates', help='Validate notebook templates')
    validate_parser.add_argument('path', nargs='?', default=None)

    rebuild_parser = subparsers.add_parser('rebuild-state', help='Reparse notebooks and rebuild derived state')
    rebuild_parser.add_argument('path', nargs='?', default='.')
    return parser


def app() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        current = Path('.').resolve()
        if not is_project_root(current):
            parser.error('No command provided and current directory is not an BulletJournal project root.')
        start_server(str(current), open_browser=False)
        return
    if args.command == 'init':
        root = init_project(args.path, title=args.title)
        print(root)
        return
    if args.command == 'start':
        start_server(args.path, open_browser=args.open)
        return
    if args.command == 'dev':
        dev_server(args.path, open_browser=args.open)
        return
    if args.command == 'doctor':
        print(json.dumps(doctor(args.path), indent=2, sort_keys=True))
        return
    if args.command == 'validate-templates':
        print(json.dumps(validate_templates(args.path), indent=2, sort_keys=True))
        return
    if args.command == 'rebuild-state':
        print(json.dumps(rebuild_state(args.path), indent=2, sort_keys=True))
        return
    parser.error(f'Unknown command {args.command!r}')
