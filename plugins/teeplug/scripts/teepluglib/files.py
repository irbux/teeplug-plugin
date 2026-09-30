"""Bounded local source reads, content hashes, and staged file writes."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from .config import TeeplugError


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def within_root(root, value):
    root = Path(root).resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.is_relative_to(root) or ".git" in path.relative_to(root).parts:
        raise TeeplugError(f"Path is outside the project or inside .git: {value}")
    return path


def read_sources(root, values, maximum, numbered=True):
    documents, seen, used = [], set(), 0
    for value in values:
        path = within_root(root, value)
        if path in seen:
            continue
        seen.add(path)
        try:
            if not path.is_file() or path.stat().st_size > maximum - used:
                raise TeeplugError(f"Not a regular file or input budget exceeded: {value}")
            with path.open("rb") as source:
                raw = source.read(maximum - used + 1)
        except OSError:
            raise TeeplugError(f"Cannot read source file: {value}") from None
        used += len(raw)
        if used > maximum:
            raise TeeplugError("Input budget exceeded; send fewer or smaller files")
        try:
            text = raw.decode("utf-8")
            if "\x00" in text:
                raise ValueError
        except ValueError:
            raise TeeplugError(f"Source must be UTF-8 text, not a binary file: {value}") from None
        lines = text.splitlines()
        content = "\n".join(f"{i}: {line}" for i, line in enumerate(lines, 1)) if numbered else text
        documents.append({"path": str(path.relative_to(root)), "sha256": digest(raw), "lines": len(lines), "content": content})
    return documents, used


def metadata(documents):
    return [{k: v for k, v in doc.items() if k != "content"} for doc in documents]


def snapshot(path):
    if path.is_symlink():
        raise TeeplugError("Refusing a symlink target")
    if not path.exists():
        return None
    if not path.is_file():
        raise TeeplugError("Target is not a regular file")
    return digest(path.read_bytes())


def clean_code(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        match = re.fullmatch(r"```[^\n`]*\n(.*)\n```", stripped, re.DOTALL)
        if not match:
            raise TeeplugError("Malformed fenced generation; no output written")
        text = match.group(1)
    if not text.strip():
        raise TeeplugError("Empty generated file; no output written")
    return text.rstrip("\n") + "\n"


def write_code(root, target, text, expected, overwrite=False):
    original = Path(target).expanduser()
    if original.is_symlink():
        raise TeeplugError("Refusing a symlink target")
    path = within_root(root, target)
    if snapshot(path) != expected:
        raise TeeplugError("Target changed during generation; no output written")
    if expected is not None and not overwrite:
        raise TeeplugError("Target exists; use --overwrite for an intentional replacement")
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if expected is not None else 0o644
    fd, temporary = tempfile.mkstemp(prefix=".teeplug-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        if snapshot(path) != expected:
            raise TeeplugError("Target changed during generation; no output written")
        if expected is None:
            # Atomic create without clobbering a file created by another process.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    except FileExistsError:
        raise TeeplugError("Target appeared during generation; no output written") from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    raw = text.encode()
    return {"status": "written", "path": str(path.relative_to(root)), "bytes": len(raw),
            "lines": len(text.splitlines()), "sha256": digest(raw), "review_required": True}


def cache_path(root, identity):
    key = digest(json.dumps(identity, sort_keys=True).encode())
    directory = Path(root) / ".teeplug" / "cache"
    if directory.is_symlink() or directory.parent.is_symlink():
        raise TeeplugError("Cache directory must not be a symlink")
    directory = within_root(root, directory)
    return directory / f"{key}.json"


def cache_read(path, ttl=604800):
    try:
        if path.is_symlink() or time.time() - path.stat().st_mtime > ttl or path.stat().st_size > 100000:
            return None
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("text"), str) and isinstance(data.get("usage"), dict):
            return data
    except (OSError, ValueError):
        pass
    return None


def cache_write(path, answer):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(answer, stream)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
