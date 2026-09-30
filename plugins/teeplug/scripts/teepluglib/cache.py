"""Bounded project cache maintenance. Only hashed summary entries are touched."""
from pathlib import Path
import re
import time
from .config import TeeplugError
from .files import within_root


def entries(root):
    directory = Path(root) / '.teeplug' / 'cache'
    if directory.is_symlink() or directory.parent.is_symlink():
        raise TeeplugError('Cache directory must not be a symlink')
    directory = within_root(root, directory)
    result = []
    if directory.is_dir():
        for path in directory.iterdir():
            if re.fullmatch(r'[0-9a-f]{64}\.json', path.name) and not path.is_symlink() and path.is_file():
                try:
                    stat = path.stat()
                    result.append((stat.st_mtime, stat.st_size, path))
                except FileNotFoundError:
                    pass
    return directory, sorted(result)


def cache_status(root, ttl):
    directory, items = entries(root)
    return {'path': str(directory), 'entries': len(items), 'bytes': sum(x[1] for x in items),
            'expired': sum(time.time() - x[0] > ttl for x in items), 'ttl_seconds': ttl}


def prune_cache(root, ttl, maximum, clear=False):
    _, items = entries(root)
    size = sum(x[1] for x in items)
    deleted = 0
    now = time.time()
    for modified, length, path in items:
        if clear or now - modified > ttl or size > maximum:
            path.unlink(missing_ok=True)
            size -= length
            deleted += 1
    return {'removed': deleted, **cache_status(root, ttl)}
