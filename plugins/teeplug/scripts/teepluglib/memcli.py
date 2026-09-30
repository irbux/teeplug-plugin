"""Deterministic local commands for file memory. No worker CLI, login or model request."""

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from .config import TeeplugError, check_memory, project_root
from .memguard import MAX_PAYLOAD_BYTES, MemoryRefusal
from .memory import DEFAULT_LIMITS, TARGETS, MemoryConfig, Store
from .memops import (add, clear, delete, import_entries, listing, read_entry,
                     remove, replace, status)
from .memsession import session_block

PAYLOAD_FIELDS = {'text', 'old_text', 'source', 'id', 'expect_revision', 'target'}


def settings(args):
    # Workers have no tools and cannot reach this CLI; the guard keeps that true by construction.
    if os.getenv('TEEPLUG_WORKER'):
        raise MemoryRefusal('worker_isolated',
                            'Teeplug workers have no access to project memory. The main agent requests memory '
                            'operations; the worker only answers the payload it was given.')
    root = Path(args.root).expanduser().resolve() if args.root else project_root()
    return MemoryConfig(root, args.config)


def payload(args):
    """Entry payloads arrive as one JSON object; remembered text is never shell-interpolated."""
    if getattr(args, 'payload_file', None):
        path = Path(args.payload_file).expanduser()
        try:
            raw = path.read_bytes()
        except OSError:
            raise MemoryRefusal('invalid_request', f'Cannot read the payload file: {args.payload_file}') from None
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise MemoryRefusal('payload_too_large', f'The payload exceeds {MAX_PAYLOAD_BYTES} bytes; nothing was written.')
        text = raw.decode('utf-8', errors='replace')
    elif sys.stdin.isatty():
        text = ''
    else:
        text = sys.stdin.read(MAX_PAYLOAD_BYTES + 1)
        if len(text.encode('utf-8', errors='replace')) > MAX_PAYLOAD_BYTES:
            raise MemoryRefusal('payload_too_large', f'The payload exceeds {MAX_PAYLOAD_BYTES} bytes; nothing was written.')
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        raise MemoryRefusal('invalid_request',
                            'The payload must be one JSON object with text, old_text, source, id or '
                            'expect_revision. Nothing was written.') from None
    if not isinstance(data, dict) or set(data) - PAYLOAD_FIELDS:
        raise MemoryRefusal('invalid_request', f'The payload supports only {sorted(PAYLOAD_FIELDS)}. Nothing was written.')
    for key in ('source', 'id', 'expect_revision', 'target'):
        flag = getattr(args, key, None)
        if key in data and flag and data[key] != flag:
            raise MemoryRefusal('invalid_request', f'{key} was given twice with different values. Nothing was written.')
        if key in data and not isinstance(data[key], str):
            raise MemoryRefusal('invalid_request', f'{key} must be a JSON string. Nothing was written.')
    return data


def selection(args, data, require_text):
    entry_id = data.get('id') or getattr(args, 'id', None)
    old_text = data.get('old_text')
    if entry_id and old_text:
        raise MemoryRefusal('invalid_request', 'Give either an entry id or old_text, not both. Nothing was modified.')
    if not entry_id and old_text is None:
        raise MemoryRefusal('invalid_request', 'Give an entry id, or old_text matching exactly one entry.')
    if require_text and not isinstance(data.get('text'), str):
        raise MemoryRefusal('invalid_request', 'The payload must include the replacement text as a JSON string.')
    return entry_id, old_text


def target_of(args, data):
    target = data.get('target') or getattr(args, 'target', None)
    if target not in TARGETS:
        raise MemoryRefusal('invalid_request', 'target must be memory or operator.')
    return target


def save_config(cfg, section, config_path=None):
    """Merge one section into .teeplug.json, preserving every unrelated setting."""
    path = Path(config_path).expanduser() if config_path else cfg.root / '.teeplug.json'
    data = dict(cfg.data)
    merged = {**data.get('memory', {}), **section}
    check_memory(merged)
    data['memory'] = merged
    mode = stat.S_IMODE(path.stat().st_mode) if path.is_file() else 0o644
    handle, temporary = tempfile.mkstemp(prefix='.teeplug-config-', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path, merged


def run_setup(args):
    cfg = settings(args)
    section = {'enabled': True, 'backend': 'file'}
    previous_store, previous_entries = None, 0
    if args.location or args.data_dir:
        try:
            store = Store(cfg)
            previous_store = store.dir
            previous_entries = sum(len(group) for group in store.view()['entries'].values())
        except (TeeplugError, OSError):
            previous_store, previous_entries = None, 0
    if args.location:
        section['location'] = args.location
    if args.data_dir:
        section['data_dir'] = str(Path(args.data_dir).expanduser().resolve())
    limits = dict(cfg.data.get('memory', {}).get('limits', {}))
    if args.memory_chars:
        limits['memory_chars'] = args.memory_chars
    if args.operator_chars:
        limits['operator_chars'] = args.operator_chars
    if limits:
        section['limits'] = limits
    candidate = MemoryConfig(cfg.root, args.config, data={**cfg.data, 'memory': {**cfg.data.get('memory', {}), **section}})
    if section.get('location') == 'plugin-data' or (not section.get('location') and cfg.location == 'plugin-data'):
        if not section.get('data_dir') and not cfg.data_dir:
            from .memory import plugin_data_dir
            section['data_dir'] = str(plugin_data_dir().resolve())
            candidate = MemoryConfig(cfg.root, args.config,
                                     data={**cfg.data, 'memory': {**cfg.data.get('memory', {}), **section}})
    destination = candidate.store_dir()
    if previous_store and previous_entries and previous_store != destination and not (args.migrate or args.keep):
        raise MemoryRefusal('location_change_requires_migration',
                            f'{previous_entries} entries already exist at {previous_store}. Re-run with --migrate to '
                            'copy them into the new location, or --keep to switch without copying. Nothing was changed.',
                            store=str(previous_store), next_action='status')
    path, merged = save_config(cfg, section, args.config)
    result = {'success': True, 'status': 'configured', 'config': str(path), 'memory': merged,
              'store': str(destination), 'project': candidate.project_id,
              'limits': candidate.limits,
              'note': 'Memory is project-scoped. OPERATOR.md holds preferences for this project only and does not '
                      'propagate to other projects. Saving is agent judgment; Python enforces structure and limits.'}
    updated = MemoryConfig(cfg.root, args.config)
    Store(updated).identity()
    if previous_store and previous_entries and previous_store != destination and args.migrate:
        result['migration'] = import_entries(updated, previous_store)
    emit(result)


def run_disable(args):
    cfg = settings(args)
    path, merged = save_config(cfg, {'enabled': False}, args.config)
    removed = 0
    try:
        removed = Store(cfg).invalidate_snapshots()
    except (TeeplugError, OSError):
        removed = 0
    emit({'success': True, 'status': 'disabled', 'config': str(path), 'memory': merged,
          'snapshots_invalidated': removed,
          'note': 'Automatic saving and session loading stop. Saved entries are preserved; status, list, read, '
                  'remove, clear and delete remain available. Re-enabling requires memory setup.'})


def run_status(args):
    emit(status(settings(args)))


def run_list(args):
    emit(listing(settings(args), args.target, args.limit, args.offset))


def run_read(args):
    emit(read_entry(settings(args), args.target, args.id))


def run_add(args):
    data = payload(args)
    if not isinstance(data.get('text'), str):
        raise MemoryRefusal('invalid_request', 'The payload must include the entry text as a JSON string.')
    emit(add(settings(args), target_of(args, data), data['text'],
             data.get('source') or args.source, data.get('expect_revision') or args.expect_revision))


def run_replace(args):
    data = payload(args)
    entry_id, old_text = selection(args, data, require_text=True)
    emit(replace(settings(args), target_of(args, data), data['text'], entry_id, old_text,
                 data.get('source') or args.source, data.get('expect_revision') or args.expect_revision))


def run_remove(args):
    data = payload(args)
    entry_id, old_text = selection(args, data, require_text=False)
    emit(remove(settings(args), target_of(args, data), entry_id, old_text,
                data.get('expect_revision') or args.expect_revision))


def run_clear(args):
    if not args.confirm:
        raise MemoryRefusal('confirmation_required',
                            'Clearing a store is an explicit management action; pass --confirm. Nothing was changed.')
    emit(clear(settings(args), args.target, args.expect_revision))


def run_delete(args):
    if not args.confirm:
        raise MemoryRefusal('confirmation_required',
                            'Deleting memory is an explicit management action; pass --confirm. Nothing was changed.')
    cfg = settings(args)
    result = delete(cfg)
    if cfg.configured:
        path, merged = save_config(cfg, {'enabled': False}, args.config)
        result.update(config=str(path), memory=merged)
    emit(result)


def run_migrate(args):
    emit(import_entries(settings(args), args.source))


def run_context(args):
    cfg = settings(args)
    if not cfg.enabled:
        raise MemoryRefusal('memory_disabled', 'Memory is disabled for this project; no session context is loaded.')
    block = session_block(cfg, args.session_id, args.reason)
    emit({'success': True, 'project': cfg.project_id, 'reason': args.reason,
          'block': block, 'chars': len(block or '')})


def emit(value):
    print(json.dumps(value, ensure_ascii=False))


def add_parser(sub):
    memory = sub.add_parser('memory', help='Project-scoped file memory; local only, no model calls')
    actions = memory.add_subparsers(dest='action', required=True)
    base = argparse.ArgumentParser(add_help=False)
    base.add_argument('--root', help='Project boundary used for memory identity')
    base.add_argument('--config', help='Explicit JSON configuration path')

    setup = actions.add_parser('setup', parents=[base], help='Configure or re-enable memory')
    setup.add_argument('--location', choices=('plugin-data', 'project'))
    setup.add_argument('--data-dir', help='Explicit persistent data directory for plugin-data storage')
    setup.add_argument('--memory-chars', type=int, help=f'MEMORY limit (default {DEFAULT_LIMITS["memory"]})')
    setup.add_argument('--operator-chars', type=int, help=f'OPERATOR limit (default {DEFAULT_LIMITS["operator"]})')
    setup.add_argument('--migrate', action='store_true', help='Copy existing entries into a new location')
    setup.add_argument('--keep', action='store_true', help='Switch location and leave existing entries in place')
    setup.set_defaults(run=run_setup)

    actions.add_parser('disable', parents=[base], help='Stop automatic saving and loading').set_defaults(run=run_disable)
    actions.add_parser('status', parents=[base], help='Enablement, location, revision and capacity').set_defaults(run=run_status)

    listed = actions.add_parser('list', parents=[base], help='Bounded current entries with ids and provenance')
    listed.add_argument('--target', choices=TARGETS)
    listed.add_argument('--limit', type=int, default=50)
    listed.add_argument('--offset', type=int, default=0)
    listed.set_defaults(run=run_list)

    reader = actions.add_parser('read', parents=[base], help='Read one current entry by id')
    reader.add_argument('--target', choices=TARGETS, required=True)
    reader.add_argument('--id', required=True)
    reader.set_defaults(run=run_read)

    for name, runner, need_target in (('add', run_add, True), ('replace', run_replace, True), ('remove', run_remove, True)):
        parser = actions.add_parser(name, parents=[base], help=f'{name.capitalize()} an entry; payload is JSON on stdin')
        parser.add_argument('--target', choices=TARGETS, required=need_target)
        parser.add_argument('--id')
        parser.add_argument('--source', help='Compact provenance token, for example operator-statement')
        parser.add_argument('--expect-revision')
        parser.add_argument('--payload-file', help='Read the JSON payload from a file instead of stdin')
        parser.set_defaults(run=runner)

    cleared = actions.add_parser('clear', parents=[base], help='Clear one store and invalidate snapshots')
    cleared.add_argument('--target', choices=TARGETS, required=True)
    cleared.add_argument('--expect-revision')
    cleared.add_argument('--confirm', action='store_true')
    cleared.set_defaults(run=run_clear)

    deleted = actions.add_parser('delete', parents=[base], help='Disable memory and delete both stores')
    deleted.add_argument('--confirm', action='store_true')
    deleted.set_defaults(run=run_delete)

    migrate = actions.add_parser('migrate', parents=[base], help='Import entries from another store directory')
    migrate.add_argument('--from', dest='source', required=True)
    migrate.set_defaults(run=run_migrate)

    context = actions.add_parser('context', parents=[base], help='Render the session block that hooks provide')
    context.add_argument('--session-id', default='manual')
    context.add_argument('--reason', default='startup', choices=('startup', 'resume', 'clear', 'compact', 'fork'))
    context.set_defaults(run=run_context)
