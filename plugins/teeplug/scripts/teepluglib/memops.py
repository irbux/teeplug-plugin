"""Entry operations on a memory store: every interface routes through these functions."""

import contextlib
import os
from pathlib import Path

from .memguard import (MAX_ENTRIES, MAX_ENTRY_CHARS, MAX_STORE_BYTES, MemoryRefusal,
                       normalize, validate_entry, validate_source)
from .memory import STORE_VERSION, TARGETS, Store, locked, new_id, now, parse


def require_enabled(cfg, action):
    if not cfg.enabled:
        raise MemoryRefusal('memory_disabled',
                            f'Memory is disabled for this project, so {action} is not available. Existing entries are '
                            'preserved; status, list, read, remove, clear and delete still work.',
                            next_action='setup')


def summary(store, view, **extra):
    return {'success': True, 'revision': view['revision'], 'project': store.cfg.project_id,
            'usage': view['usage'], 'store': str(store.dir), **extra}


def check_revision(view, expected):
    if expected and expected != view['revision']:
        raise MemoryRefusal('stale_revision',
                            'The store changed since that revision was read. Nothing was modified; read the affected '
                            'entries again and reconsider the operation before retrying.',
                            revision=view['revision'], expected_revision=expected, next_action='read')


def locate(view, target, entry_id=None, old_text=None):
    """Prefer stable ids; old_text is a convenience that must match exactly one entry."""
    entries = view['entries'][target]
    if entry_id:
        found = [entry for entry in entries if entry['id'] == entry_id]
        if not found:
            raise MemoryRefusal('not_found', f'No {target} entry has id {entry_id}. Nothing was modified.',
                                target=target, next_action='list')
        return found[0]
    if old_text is None:
        raise MemoryRefusal('invalid_request', 'Provide an entry id, or old_text matching exactly one entry.')
    wanted = normalize(old_text)
    if not wanted:
        raise MemoryRefusal('invalid_request', 'old_text must not be empty. Nothing was modified.')
    found = [entry for entry in entries if entry['text'] == wanted]
    if len(found) != 1:
        raise MemoryRefusal('ambiguous_match' if found else 'not_found',
                            f'old_text matched {len(found)} complete {target} entries; exactly one is required. '
                            'Nothing was modified.', target=target, matches=len(found), next_action='list')
    return found[0]


def capacity_error(target, view, entry_chars, projected):
    return MemoryRefusal('memory_full',
                         'This entry would exceed the memory limit. List current entries to review capacity; '
                         'existing entries were preserved.',
                         target=target, usage={**view['usage'][target], 'projected_chars': projected},
                         entry_chars=entry_chars, revision=view['revision'], next_action='list')


def add(cfg, target, text, source=None, expect_revision=None):
    require_enabled(cfg, 'saving')
    store = Store(cfg)
    with locked(store.dir):
        view = store.view()
        check_revision(view, expect_revision)
        value = validate_entry(text, min(cfg.limits[target], MAX_ENTRY_CHARS))
        provenance = validate_source(source)
        entries = view['entries'][target]
        for entry in entries:
            if entry['text'] == value:
                return summary(store, view, status='duplicate', code='duplicate_skipped', target=target,
                               id=entry['id'], message='no duplicate added')
        if len(entries) >= MAX_ENTRIES:
            raise MemoryRefusal('memory_full', f'This store already holds {MAX_ENTRIES} entries; remove one first.',
                                target=target, usage=view['usage'][target], next_action='list')
        projected = view['usage'][target]['chars'] + len(value)
        if projected > cfg.limits[target]:
            raise capacity_error(target, view, len(value), projected)
        stamp = now()
        ids = {entry['id'] for group in view['entries'].values() for entry in group}
        record = {'id': new_id(target, ids), 'text': value, 'created': stamp, 'updated': stamp,
                  'source': provenance, 'chars': len(value), 'status': 'ok', 'withheld_reason': None}
        entries.append(record)
        store.write(target, entries)
        store.identity()
        view['revision'] = store.revision()
        view['usage'][target] = store.usage(entries, target)
        return summary(store, view, status='added', target=target, id=record['id'],
                       entry_chars=len(value), repairs=view['repairs'][target])


def replace(cfg, target, text, entry_id=None, old_text=None, source=None, expect_revision=None):
    require_enabled(cfg, 'saving')
    store = Store(cfg)
    with locked(store.dir):
        view = store.view()
        check_revision(view, expect_revision)
        value = validate_entry(text, min(cfg.limits[target], MAX_ENTRY_CHARS))
        entries = view['entries'][target]
        found = locate(view, target, entry_id, old_text)
        for entry in entries:
            if entry['text'] == value and entry['id'] != found['id']:
                raise MemoryRefusal('duplicate_conflict',
                                    f'Another {target} entry ({entry["id"]}) already holds that exact text. '
                                    'Nothing was modified.', target=target, id=entry['id'], next_action='list')
        if found['text'] == value:
            return summary(store, view, status='unchanged', target=target, id=found['id'],
                           message='no duplicate added')
        projected = view['usage'][target]['chars'] - found['chars'] + len(value)
        if projected > cfg.limits[target]:
            raise capacity_error(target, view, len(value), projected)
        found.update(text=value, chars=len(value), updated=now(), status='ok', withheld_reason=None,
                     source=validate_source(source) if source else found['source'])
        store.write(target, entries)
        store.identity()
        view['revision'] = store.revision()
        view['usage'][target] = store.usage(entries, target)
        return summary(store, view, status='replaced', target=target, id=found['id'],
                       entry_chars=len(value), repairs=view['repairs'][target])


def remove(cfg, target, entry_id=None, old_text=None, expect_revision=None):
    store = Store(cfg)
    with locked(store.dir):
        view = store.view()
        check_revision(view, expect_revision)
        found = locate(view, target, entry_id, old_text)
        entries = [entry for entry in view['entries'][target] if entry['id'] != found['id']]
        store.write(target, entries)
        view['revision'] = store.revision()
        view['usage'][target] = store.usage(entries, target)
        return summary(store, view, status='removed', target=target, id=found['id'],
                       repairs=view['repairs'][target])


def clear(cfg, target, expect_revision=None):
    store = Store(cfg)
    with locked(store.dir):
        before = store.revision()
        if expect_revision and expect_revision != before:
            raise MemoryRefusal('stale_revision', 'The store changed since that revision was read; nothing was cleared.',
                                revision=before, expected_revision=expect_revision, next_action='status')
        store.write(target, [])
        removed = store.invalidate_snapshots()
        view = store.view()
        return summary(store, view, status='cleared', target=target, snapshots_invalidated=removed,
                       note='Entries already delivered to an open conversation cannot be retracted by a file operation.')


def delete(cfg):
    store = Store(cfg)
    with locked(store.dir):
        removed = store.invalidate_snapshots()
        for path in (*store.paths.values(), store.dir / 'project.json'):
            if not path.is_symlink():
                path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            store.snapshots.rmdir()
    with contextlib.suppress(OSError):
        (store.dir / '.lock').unlink(missing_ok=True)
        store.dir.rmdir()
    return {'success': True, 'status': 'deleted', 'store': str(store.dir), 'snapshots_invalidated': removed,
            'note': 'Memory is now disabled. Re-enabling requires memory setup.'}


def import_entries(cfg, source_dir):
    """Explicit validated migration. The source store is read only and never deleted."""
    require_enabled(cfg, 'migration')
    source = Path(source_dir).expanduser()
    if not source.is_dir():
        raise MemoryRefusal('not_found', f'No memory store directory at {source}.')
    store = Store(cfg)
    if Path(os.path.realpath(source)) == store.dir:
        raise MemoryRefusal('invalid_request', 'The source and destination stores are the same directory.')
    imported, skipped = {t: 0 for t in TARGETS}, {t: 0 for t in TARGETS}
    with locked(store.dir):
        view = store.view()
        ids = {entry['id'] for group in view['entries'].values() for entry in group}
        for target in TARGETS:
            path = source / f'{target.upper()}.md'
            if not path.is_file() or path.is_symlink():
                continue
            if path.stat().st_size > MAX_STORE_BYTES:
                raise MemoryRefusal('store_too_large', f'{path} exceeds the store byte bound; nothing was imported.')
            candidates, _ = parse(target, path.read_text(encoding='utf-8', errors='replace'), set())
            entries = view['entries'][target]
            chars = view['usage'][target]['chars']
            for candidate in candidates:
                known = {entry['text'] for entry in entries}
                if candidate['status'] != 'ok' or candidate['text'] in known:
                    skipped[target] += 1
                    continue
                if chars + candidate['chars'] > cfg.limits[target] or len(entries) >= MAX_ENTRIES:
                    skipped[target] += 1
                    continue
                candidate['id'] = new_id(target, ids)
                ids.add(candidate['id'])
                candidate['updated'] = now()
                entries.append(candidate)
                chars += candidate['chars']
                imported[target] += 1
            if imported[target]:
                store.write(target, entries)
        store.identity()
        view = store.view()
        return summary(store, view, status='imported', imported=imported, skipped=skipped, source=str(source),
                       note='The source store was not modified.')


def status(cfg):
    try:
        store = Store(cfg)
    except MemoryRefusal as refusal:
        # An unresolved or unsafe location must still produce an actionable report.
        return {'success': True, 'enabled': cfg.enabled, 'configured': cfg.configured, 'backend': cfg.backend,
                'location': cfg.location, 'store': None, 'store_exists': False, 'revision': None,
                'project': {'id': cfg.project_id, 'root': str(cfg.root), 'canonical_path': cfg.canonical_path},
                'targets': {}, 'snapshots': 0, 'store_problem': refusal.payload}
    record = store.identity(create=False)
    result = {'success': True, 'enabled': cfg.enabled, 'configured': cfg.configured, 'backend': cfg.backend,
              'location': cfg.location, 'store': str(store.dir), 'store_exists': store.dir.is_dir(),
              'project': {'id': cfg.project_id, 'root': str(cfg.root), 'canonical_path': cfg.canonical_path,
                          'created': record.get('created'), 'previous_paths': record.get('previous_paths', []),
                          'store_version': record.get('store_version', STORE_VERSION)},
              'targets': {}}
    try:
        with locked(store.dir, exclusive=False):
            view = store.view()
        result['revision'] = view['revision']
        for target in TARGETS:
            entries = view['entries'][target]
            result['targets'][target] = {**view['usage'][target], 'state': 'ok', 'file': str(store.paths[target]),
                                         'withheld': sum(entry['status'] != 'ok' for entry in entries),
                                         'repairs': view['repairs'][target]}
    except MemoryRefusal as refusal:
        result['revision'] = None
        result['store_problem'] = refusal.payload
    snapshots = 0
    if store.snapshots.is_dir():
        snapshots = sum(1 for path in store.snapshots.iterdir() if path.name.endswith('.json'))
    result['snapshots'] = snapshots
    return result


def listing(cfg, target=None, limit=50, offset=0):
    store = Store(cfg)
    with locked(store.dir, exclusive=False):
        view = store.view()
    result = {'success': True, 'revision': view['revision'], 'project': cfg.project_id, 'enabled': cfg.enabled,
              'store': str(store.dir), 'usage': view['usage'], 'entries': {}, 'repairs': view['repairs']}
    for name in ([target] if target else list(TARGETS)):
        entries = view['entries'][name]
        page = entries[offset:offset + limit]
        result['entries'][name] = [{'id': entry['id'], 'source': entry['source'], 'created': entry['created'],
                                    'updated': entry['updated'], 'chars': entry['chars'], 'status': entry['status'],
                                    'text': entry['text'] if entry['status'] == 'ok'
                                            else f"<withheld: {entry['withheld_reason']}>"} for entry in page]
        result.setdefault('page', {})[name] = {'offset': offset, 'returned': len(page), 'total': len(entries),
                                               'more': offset + len(page) < len(entries)}
    return result


def read_entry(cfg, target, entry_id):
    store = Store(cfg)
    with locked(store.dir, exclusive=False):
        view = store.view()
    entry = locate(view, target, entry_id)
    payload = dict(entry)
    if entry['status'] != 'ok':
        payload['text'] = f"<withheld: {entry['withheld_reason']}>"
    return {'success': True, 'revision': view['revision'], 'project': cfg.project_id, 'target': target,
            'entry': payload, 'usage': view['usage'][target]}
