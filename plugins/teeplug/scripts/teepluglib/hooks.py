"""Best-effort read routing; intentionally not a shell security parser."""

import json
import os
from pathlib import Path
import re
import shlex
import sys

from .config import Settings, TeeplugError, project_root, detect_host


def large_file(cwd, value, limit):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    try:
        if not path.is_file():
            return False
        with path.open("rb") as stream:
            for number, _ in enumerate(stream, 1):
                if number > limit:
                    return True
    except (OSError, ValueError):
        pass
    return False


def bounded_head(tokens, limit):
    if not tokens or Path(tokens[0]).name not in ("head", "tail"):
        return False
    if len(tokens) == 1 or (len(tokens) > 1 and not tokens[1].startswith("-")):
        return True  # default 10 lines
    args = tokens[1:]
    try:
        if args[0] in ("-n", "--lines", "-c", "--bytes"):
            amount = args[1]
            ceiling = limit if args[0] in ("-n", "--lines") else limit * 80
        elif re.fullmatch(r"-\d+", args[0]):
            amount, ceiling = args[0][1:], limit
        elif re.fullmatch(r"-[nc]\d+", args[0]):
            amount = args[0][2:]
            ceiling = limit if args[0][1] == "n" else limit * 80
        else:
            return False
        return amount.isdigit() and 0 < int(amount) <= ceiling
    except IndexError:
        return False


def command_paths(tokens):
    # Recognize literal operands only. No shell expansion or command execution.
    paths, skip, positional = [], False, False
    for token in tokens[1:]:
        if skip:
            skip = False
            continue
        if token == "--":
            positional = True
            continue
        if not positional and token in ("-n", "--lines", "-c", "--bytes") and Path(tokens[0]).name in ("head", "tail"):
            skip = True
            continue
        if not positional and token.startswith("-"):
            continue
        if any(c in token for c in ("$", "*", "?", "`")):
            continue
        paths.append(token)
    return paths


def bash_reason(command, cwd, limit):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    # Divide sequences, then pipelines. Track simple cd commands for relative paths.
    groups, current = [], []
    for token in tokens:
        if token in (";", "&&", "||", "&"):
            groups.append(current)
            current = []
        else:
            current.append(token)
    groups.append(current)
    for group in groups:
        if not group:
            continue
        if group[0] == "cd" and len(group) == 2:
            cwd = (cwd / group[1]).resolve()
            continue
        pipeline, segment = [], []
        for token in group:
            if token == "|":
                pipeline.append(segment)
                segment = []
            else:
                segment.append(token)
        pipeline.append(segment)
        # A small final head/tail or wc is a bounded reduction.
        last = pipeline[-1]
        if last and (bounded_head(last, limit) or Path(last[0]).name == "wc"):
            continue
        for segment in pipeline:
            if not segment or Path(segment[0]).name not in ("cat", "head", "tail", "less", "more"):
                continue
            # Only recognize plain stdout redirects; stderr redirects are distinct.
            if any(t in (">", ">>") and (i == 0 or segment[i - 1] != "2") for i, t in enumerate(segment)):
                continue
            if bounded_head(segment, limit):
                continue
            for value in command_paths(segment):
                if large_file(cwd, value, limit):
                    return f"Large read of {value} exceeds {limit} lines"
    return None


def route(event):
    if os.getenv("TEEPLUG_WORKER"):
        return None
    cwd = Path(event.get("cwd") or Path.cwd()).resolve()
    try:
        settings = Settings(project_root(cwd), host=detect_host(event=event))
        settings.executable()
    except (TeeplugError, ValueError, OSError):
        return None  # Never strand an agent when setup is absent or invalid.
    if not settings.enabled:
        return None
    inputs = event.get("tool_input", {})
    if not isinstance(inputs, dict):
        return None
    tool = event.get("tool_name")
    if tool == "Read":
        limit = inputs.get("limit")
        # Allow an explicit bounded range. Offset alone is not a bounded read.
        if isinstance(limit, int) and not isinstance(limit, bool) and 0 < limit <= settings.min_lines:
            return None
        value = inputs.get("file_path")
        if isinstance(value, str) and large_file(cwd, value, settings.min_lines):
            return f"Large read of {value} exceeds {settings.min_lines} lines"
    elif tool in ("Bash", "exec_command", "shell_command"):
        command = inputs.get("command", inputs.get("cmd", ""))
        if isinstance(command, str):
            return bash_reason(command, Path(inputs.get("workdir") or cwd), settings.min_lines)
    return None


def main():
    try:
        data = sys.stdin.read(1_048_577)
        if len(data) > 1_048_576:
            return 0
        event = json.loads(data)
        reason = route(event) if isinstance(event, dict) else None
    except (ValueError, TypeError, OSError):
        return 0
    if reason:
        root = Path(__file__).resolve().parents[2]
        script = shlex.quote(str(root / "scripts" / "bulk-read"))
        try:
            host_arg = " --host " + detect_host(event=event)
        except TeeplugError:
            host_arg = ""
        reason += (f". Use the teeplug bulk-reader skill, or {script}{host_arg} --question 'specific question' --paths FILE. "
                   "For an exact edit, read a bounded section with Read offset/limit or a bounded head/tail command.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                          "permissionDecision": "deny", "permissionDecisionReason": reason}}))
    return 0
