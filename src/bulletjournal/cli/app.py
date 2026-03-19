from __future__ import annotations

import argparse
import json
from pathlib import Path

from bulletjournal.cli.dev import dev_server
from bulletjournal.cli.doctor import doctor
from bulletjournal.cli.export_project import export_project
from bulletjournal.cli.import_project import import_project
from bulletjournal.cli.init_project import init_project
from bulletjournal.cli.mark_environment_changed import mark_environment_changed
from bulletjournal.cli.rebuild_state import rebuild_state
from bulletjournal.cli.start import start_server
from bulletjournal.cli.validate_templates import validate_templates
from bulletjournal.storage import is_project_root, require_project_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='bulletjournal', description='BulletJournal notebook orchestration platform')
    subparsers = parser.add_subparsers(dest='command')

    init_parser = subparsers.add_parser('init', help='Initialize a new BulletJournal project')
    init_parser.add_argument('path')
    init_parser.add_argument('--project-id', required=True)
    init_parser.add_argument('--title', default=None)

    start_parser = subparsers.add_parser('start', help='Start the BulletJournal server')
    start_parser.add_argument('path')
    start_parser.add_argument('--open', action='store_true')
    start_parser.add_argument('--base-path', default='')

    dev_parser = subparsers.add_parser('dev', help='Start BulletJournal in development mode')
    dev_parser.add_argument('path')
    dev_parser.add_argument('--open', action='store_true')
    dev_parser.add_argument('--base-path', default='')

    doctor_parser = subparsers.add_parser('doctor', help='Check environment and project health')
    doctor_parser.add_argument('path')

    validate_parser = subparsers.add_parser('validate-templates', help='Validate notebook templates')
    validate_parser.add_argument('path', nargs='?', default=None)

    rebuild_parser = subparsers.add_parser('rebuild-state', help='Reparse notebooks and rebuild derived state')
    rebuild_parser.add_argument('path')

    mark_env_parser = subparsers.add_parser('mark-environment-changed', help='Mark notebook outputs stale after an environment change')
    mark_env_parser.add_argument('path')
    mark_env_parser.add_argument('--reason', required=True)

    export_parser = subparsers.add_parser('export', help='Export a BulletJournal project as a zip archive')
    export_parser.add_argument('path')
    export_parser.add_argument('archive')
    export_parser.add_argument('--without-artifacts', action='store_true')

    import_parser = subparsers.add_parser('import', help='Import a BulletJournal project from a zip archive')
    import_parser.add_argument('archive')
    import_parser.add_argument('path')
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
        root = init_project(args.path, title=args.title, project_id=args.project_id)
        print(root)
        return
    if args.command == 'start':
        require_project_root(Path(args.path).resolve())
        start_server(args.path, open_browser=args.open, base_path=args.base_path)
        return
    if args.command == 'dev':
        require_project_root(Path(args.path).resolve())
        dev_server(args.path, open_browser=args.open, base_path=args.base_path)
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
    if args.command == 'mark-environment-changed':
        print(json.dumps(mark_environment_changed(args.path, reason=args.reason), indent=2, sort_keys=True))
        return
    if args.command == 'export':
        print(json.dumps(export_project(args.path, args.archive, include_artifacts=not args.without_artifacts), indent=2, sort_keys=True))
        return
    if args.command == 'import':
        print(json.dumps(import_project(args.archive, args.path), indent=2, sort_keys=True))
        return
    parser.error(f'Unknown command {args.command!r}')
