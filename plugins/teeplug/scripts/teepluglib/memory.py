"""Project-scoped file memory: MEMORY.md and OPERATOR.md with locked, atomic writes.

The Markdown files are authoritative and carry their own per-entry metadata, so a
failed write cannot leave text and metadata disagreeing. project.json holds identity
only and is recreated when missing. The revision is derived from the stored bytes,
so an external edit invalidates it exactly like a write from another session.
"""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from .config import load, positive
from .files import digest
from .memguard import (MAX_ENTRIES, MAX_STORE_BYTES, MemoryRefusal, TIMESTAMP_RE,
                       decode_line, encode_line, normalize, scan)

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX hosts use the fallback lock
    fcntl = None

STORE_VERSION = 1
TARGETS = ('memory', 'operator')
DEFAULT_LIMITS = {'memory': 12000, 'operator': 6000}
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
LOCK_SECONDS = 5.0
SNAPSHOT_KEEP = 20
SNAPSHOT_MAX_AGE = 30 * 86400

HEADER_RE = re.compile(r'<!-- teeplug-memory v(\d+) target=(memory|operator) -->')
ENTRY_RE = re.compile(r'- ([mo]-[0-9a-f]{6}) #=> (.*)')
TRAILER_RE = re.compile(r'<!-- teeplug created=(\S+) updated=(\S+) source=(\S*) -->')
INTRO = ('Remembered reference notes for this project, managed by the Teeplug file-memory tool.\n'
         'Each entry starts at a bullet with a stable id and the `#=>` delimiter, may continue\n'
         'on indented lines, and ends with a metadata comment. Hand edits are validated on load;\n'
         'see the plugin docs/memory.md for the entry format and escaping rules.')


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def project_identity(root):
    canonical = os.path.realpath(root)
    slug = re.sub(r'[^a-z0-9]+', '-', Path(canonical).name.lower()).strip('-')[:32] or 'project'
    return f'{slug}-{hashlib.sha256(canonical.encode()).hexdigest()[:12]}', canonical


class MemoryConfig:
    """Local configuration only. Memory never resolves a provider, host CLI or login."""

    def __init__(self, root=None, config_path=None, data=None):
        from .config import project_root
        self.root = Path(root).resolve() if root else project_root()
        self.data = load(self.root, config_path) if data is None else data
        section = self.data.get('memory', {})
        self.configured = bool(section)
        self.enabled = os.getenv('TEEPLUG_MEMORY_ENABLED', '1' if section.get('enabled', False) else '0') != '0'
        self.backend = section.get('backend', 'file')
        limits = section.get('limits', {})
        self.limits = {'memory': positive(limits.get('memory_chars', DEFAULT_LIMITS['memory']), 'memory.limits.memory_chars'),
                       'operator': positive(limits.get('operator_chars', DEFAULT_LIMITS['operator']),
                                            'memory.limits.operator_chars')}
        self.project_id, self.canonical_path = project_identity(self.root)

    def store_dir(self):
        """Memory always lives in the project: <root>/.teeplug/memories, never inside the plugin."""
        resolved = Path(os.path.realpath(self.root / '.teeplug' / 'memories'))
        if resolved == PLUGIN_ROOT or PLUGIN_ROOT in resolved.parents:
            raise MemoryRefusal('unsafe_path', 'Refusing to store memory inside the installed plugin directory.')
        if not resolved.is_relative_to(Path(os.path.realpath(self.root))):
            raise MemoryRefusal('unsafe_path', 'Project memory must stay inside the project root.')
        return resolved


@contextlib.contextmanager
def locked(directory, exclusive=True, timeout=None):
    """Bound the whole read-validate-modify-write operation, including clear and delete."""
    timeout = float(os.getenv('TEEPLUG_MEMORY_LOCK_SECONDS', timeout if timeout is not None else LOCK_SECONDS))
    if not exclusive and not directory.is_dir():
        yield  # A pure read never creates a store; atomic renames keep any concurrent write consistent.
        return
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / '.lock'
    if path.is_symlink():
        raise MemoryRefusal('unsafe_path', 'The memory lock file must not be a symlink.')
    deadline = time.monotonic() + timeout
    busy = MemoryRefusal('locked', 'Another session is using this memory store. Nothing was changed; retry shortly.',
                         next_action='status')
    if fcntl is None:  # pragma: no cover - exercised only on non-POSIX hosts
        while True:
            try:
                handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError:
                stale = time.time() - path.stat().st_mtime > 30 if path.exists() else False
                if stale:
                    path.unlink(missing_ok=True)
                    continue
                if time.monotonic() > deadline:
                    raise busy from None
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(handle)
            path.unlink(missing_ok=True)
        return
    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        flag = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
        while True:
            try:
                fcntl.flock(handle, flag)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise busy from None
                time.sleep(0.05)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def serialize(target, entries):
    title = 'MEMORY' if target == 'memory' else 'USER'
    lines = [f'<!-- teeplug-memory v{STORE_VERSION} target={target} -->', f'# Teeplug {title}', '', INTRO, '']
    for entry in entries:
        text = entry['text'].split('\n')
        lines.append(f"- {entry['id']} #=> {encode_line(text[0])}")
        lines.extend(f'  {encode_line(line)}' for line in text[1:])
        lines.append(f"  <!-- teeplug created={entry['created']} updated={entry['updated']} source={entry['source']} -->")
        lines.append('')
    return '\n'.join(lines).rstrip('\n') + '\n'


def parse(target, text, ids):
    """Strict entry parsing with reported repairs. Nothing is discarded or rewritten here."""
    lines = text.split('\n')
    header = HEADER_RE.fullmatch(lines[0].strip()) if lines else None
    if not header or header.group(2) != target:
        raise MemoryRefusal('malformed_store',
                            f'{target.upper()}.md is not a valid Teeplug memory file (missing or wrong header). '
                            'Repair it by hand or use the clear action; nothing was overwritten.',
                            target=target)
    if int(header.group(1)) > STORE_VERSION:
        raise MemoryRefusal('unsupported_store_version',
                            f'{target.upper()}.md was written by a newer Teeplug memory version; upgrade the plugin.',
                            target=target)
    entries, repairs, current, seen = [], [], None, False

    def close():
        if current is None:
            return
        entry = dict(current)
        entry['text'] = normalize('\n'.join(entry.pop('lines')))
        if not entry['text']:
            repairs.append(f"dropped empty block at line {entry['line']}")
            return
        if entry['id'] in ids:
            repairs.append(f"reassigned duplicate id {entry['id']}")
            entry['id'] = new_id(target, ids)
        ids.add(entry['id'])
        for field in ('created', 'updated'):
            if not TIMESTAMP_RE.fullmatch(entry[field] or ''):
                repairs.append(f"repaired {field} metadata on {entry['id']}")
                entry[field] = now()
        if not entry['source']:
            repairs.append(f"repaired provenance on {entry['id']}")
            entry['source'] = 'manual-edit'
        found = scan(entry['text'])
        entry['status'] = 'withheld' if found else 'ok'
        entry['withheld_reason'] = found[1] if found else None
        entry['chars'] = len(entry['text'])
        entry.pop('line')
        entries.append(entry)

    for number, raw in enumerate(lines[1:], 2):
        match = ENTRY_RE.fullmatch(raw)
        if current is not None and raw.startswith('  '):
            content = raw[2:]
            trailer = TRAILER_RE.fullmatch(content.strip())
            if trailer and not content.startswith(' '):
                current.update(created=trailer.group(1), updated=trailer.group(2), source=trailer.group(3))
                close()
                current = None
            else:
                current['lines'].append(decode_line(content))
            continue
        if current is not None:
            repairs.append(f"repaired missing metadata comment near line {current['line']}")
            close()
            current = None
        if match:
            seen = True
            current = {'id': match.group(1), 'lines': [decode_line(match.group(2))], 'line': number,
                       'created': '', 'updated': '', 'source': ''}
        elif raw.strip() and (seen or number > 20):
            raise MemoryRefusal('malformed_store',
                                f'{target.upper()}.md has unexpected content at line {number}. Repair it by hand '
                                'or use the clear action; nothing was overwritten.', target=target, line=number)
    close()
    if len(entries) > MAX_ENTRIES:
        raise MemoryRefusal('malformed_store', f'{target.upper()}.md holds more than {MAX_ENTRIES} entries.', target=target)
    return entries, repairs


def new_id(target, existing):
    prefix = 'm' if target == 'memory' else 'o'
    while True:
        candidate = f'{prefix}-{os.urandom(3).hex()}'
        if candidate not in existing:
            return candidate


class Store:
    """All interfaces share this implementation; nothing else writes the memory files."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.dir = cfg.store_dir()
        self.paths = {target: self.dir / f'{target.upper()}.md' for target in TARGETS}
        self.snapshots = self.dir / 'snapshots'

    # -- raw access -------------------------------------------------------
    def raw(self, target):
        path = self.paths[target]
        if path.is_symlink():
            raise MemoryRefusal('unsafe_path', f'{target.upper()}.md is a symlink; refusing to read or write it.')
        if not path.is_file():
            return b''
        if path.stat().st_size > MAX_STORE_BYTES:
            raise MemoryRefusal('store_too_large',
                                f'{target.upper()}.md exceeds {MAX_STORE_BYTES} bytes; repair or clear it by hand.',
                                target=target)
        return path.read_bytes()

    def revision(self):
        return digest(b'teeplug-memory-v1\x00' + self.raw('memory') + b'\x00' + self.raw('operator'))[:16]

    def decode(self, target, ids):
        raw = self.raw(target)
        if not raw:
            return [], []
        try:
            text = raw.decode('utf-8')
        except ValueError:
            raise MemoryRefusal('malformed_store', f'{target.upper()}.md is not valid UTF-8 text.',
                                target=target) from None
        return parse(target, text, ids)

    def view(self):
        """A consistent view of both stores plus the revision they were read at."""
        ids, entries, repairs = set(), {}, {}
        for target in TARGETS:
            entries[target], repairs[target] = self.decode(target, ids)
        return {'revision': self.revision(), 'entries': entries, 'repairs': repairs,
                'usage': {t: self.usage(entries[t], t) for t in TARGETS}}

    def usage(self, entries, target):
        limit = self.cfg.limits[target]
        chars = sum(entry['chars'] for entry in entries)
        return {'chars': chars, 'limit': limit, 'entries': len(entries),
                'percent': round(chars * 100 / limit) if limit else 0,
                'near_capacity': chars * 100 >= limit * 80, 'over_limit': chars > limit}

    def write(self, target, entries):
        path = self.paths[target]
        if path.is_symlink():
            raise MemoryRefusal('unsafe_path', f'{target.upper()}.md is a symlink; refusing to write it.')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = serialize(target, entries).encode('utf-8')
        if len(raw) > MAX_STORE_BYTES:
            raise MemoryRefusal('store_too_large', 'The store would exceed its byte bound; nothing was written.', target=target)
        handle, temporary = tempfile.mkstemp(prefix='.teeplug-memory-', dir=path.parent)
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    # -- identity ---------------------------------------------------------
    def identity(self, create=True):
        path = self.dir / 'project.json'
        record, changed = None, False
        if path.is_file() and not path.is_symlink():
            try:
                loaded = json.loads(path.read_text())
                record = loaded if isinstance(loaded, dict) else None
            except (OSError, ValueError):
                record = None
        if record is None:
            record = {'store_version': STORE_VERSION, 'project_id': self.cfg.project_id,
                      'canonical_path': self.cfg.canonical_path, 'created': now()}
            changed = create
        if record.get('canonical_path') != self.cfg.canonical_path:
            record['previous_paths'] = sorted({*record.get('previous_paths', []), record.get('canonical_path', '')} - {''})
            record['canonical_path'] = self.cfg.canonical_path
            changed = create
        if changed:
            with contextlib.suppress(OSError):
                self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
                os.chmod(path, 0o600)
        return record

    # -- snapshots --------------------------------------------------------
    def invalidate_snapshots(self):
        removed = 0
        if self.snapshots.is_dir() and not self.snapshots.is_symlink():
            for path in self.snapshots.iterdir():
                if path.name.endswith('.json') and not path.is_symlink():
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed

    def prune_snapshots(self):
        if not self.snapshots.is_dir() or self.snapshots.is_symlink():
            return
        found = []
        for path in self.snapshots.iterdir():
            if path.name.endswith('.json') and not path.is_symlink() and path.is_file():
                with contextlib.suppress(OSError):
                    found.append((path.stat().st_mtime, path))
        found.sort(reverse=True)
        for index, (modified, path) in enumerate(found):
            if index >= SNAPSHOT_KEEP or time.time() - modified > SNAPSHOT_MAX_AGE:
                path.unlink(missing_ok=True)
