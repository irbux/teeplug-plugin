"""Entry counting, escaping and best-effort content filtering for file memory.

Pattern scanning recognizes known suspicious shapes only. It is not a guarantee that
every prompt injection, exfiltration attempt or credential format is detected, and a
direct external edit never passes through this module's write path, which is why the
same checks also run on load. Remembered text stays reference data, never instructions.
"""

import re
import unicodedata

from .config import TeeplugError

MAX_ENTRY_CHARS = 1000
MAX_ENTRY_LINES = 12
MAX_ENTRIES = 200
MAX_SOURCE_CHARS = 120
MAX_PAYLOAD_BYTES = 65536
MAX_STORE_BYTES = 262144
MAX_RENDER_CHARS = 6000
MAX_LIMIT_CHARS = 20000

SOURCE_RE = re.compile(r'[A-Za-z0-9_.:/#@+-]{1,%d}' % MAX_SOURCE_CHARS)
TIMESTAMP_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')

# Known credential shapes. Matches are reported by name; the text is never echoed.
SECRET_PATTERNS = (
    ('aws_access_key', re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b')),
    ('github_token', re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}')),
    ('slack_token', re.compile(r'\bxox[abposr]-[A-Za-z0-9-]{12,}')),
    ('anthropic_key', re.compile(r'\bsk-ant-[A-Za-z0-9_-]{20,}')),
    ('openai_key', re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}')),
    ('google_api_key', re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b')),
    ('private_key_block', re.compile(r'-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----')),
    ('json_web_token', re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}')),
)
# A credential assignment needs a mixed-class value, so prose such as
# "API key rotation happens monthly" or "staging uses SSH port 2222" stays allowed.
ASSIGNMENT_RE = re.compile(
    r'(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|'
    r'client[_-]?secret|private[_-]?key)\b\s*[:=]\s*["\']?([A-Za-z0-9_+/=-]{12,})')
PLACEHOLDER_RE = re.compile(r'(?i)\A(?:x{3,}|redacted|changeme|placeholder|example|'
                            r'your[_-]?\w+|dummy\w*|rotated\w*|managed\w*)\Z')

THREAT_PATTERNS = (
    ('instruction_override', re.compile(
        r'(?i)\b(?:ignore|disregard|forget)\b[^\n]{0,30}\b(?:previous|prior|earlier|above|all)\b'
        r'[^\n]{0,20}\b(?:instruction|prompt|rule)s?\b')),
    ('policy_override', re.compile(
        r'(?i)\b(?:override|replace|bypass|ignore)\b[^\n]{0,30}\b(?:system prompt|developer message|'
        r'safety|guardrail|permission check|sandbox|managed policy)')),
    ('auto_approval', re.compile(
        r'(?i)\b(?:always|automatically)\b[^\n]{0,20}\b(?:approve|allow|auto-?approve|grant)\b'
        r'[^\n]{0,30}\b(?:command|tool|request|permission|edit)s?\b')),
    ('disable_controls', re.compile(
        r'(?i)\bdisable\b[^\n]{0,30}\b(?:sandbox|safety|guardrail|permission check|approval policy|validation)')),
    ('pipe_to_shell', re.compile(r'(?i)\b(?:curl|wget)\b[^\n]{0,160}\|\s*(?:sudo\s+)?(?:ba|z|d|k)?sh\b')),
    ('credential_exfiltration', re.compile(
        r'(?i)\b(?:send|post|upload|exfiltrate|email|leak|forward)\b[^\n]{0,60}'
        r'(?:\b(?:credential|secret|api key|token|password|ssh key)s?\b|\.env\b|\bid_rsa\b)')),
    ('credential_read', re.compile(
        r'(?i)\b(?:cat|copy|read|dump)\b[^\n]{0,40}(?:~/\.(?:aws|ssh)\b|\bid_rsa\b|\.env\b)'
        r'[^\n]{0,60}\b(?:curl|https?://|upload|post|send)')),
    ('reverse_shell', re.compile(r'(?i)(?:\bnc\b[^\n]{0,20}\s-e\s|/dev/tcp/|\bbash\s+-i\b[^\n]{0,20}>&)')),
    ('backdoor_key', re.compile(r'(?i)\b(?:append|add|write|echo|install)\b[^\n]{0,60}\bauthorized_keys\b')),
    ('setuid_backdoor', re.compile(r'(?i)\bchmod\b\s+(?:u?\+s\b|[24][0-7]{3}\b)')),
)


class MemoryRefusal(TeeplugError):
    """A bounded structured refusal. Payloads never contain the rejected text."""

    def __init__(self, code, error, **extra):
        super().__init__(error)
        self.payload = {'success': False, 'code': code, 'error': error, **extra}


def normalize(text):
    """The documented counting rule: NFC, LF endings, per-line right-trim, outer strip."""
    if not isinstance(text, str):
        raise MemoryRefusal('invalid_entry', 'Entry text must be a JSON string.')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = unicodedata.normalize('NFC', text)
    return '\n'.join(line.rstrip(' \t') for line in text.split('\n')).strip()


def count(text):
    """Unicode scalar values of the normalized entry text; metadata is counted apart."""
    return len(normalize(text))


def control_problem(text):
    for char in text:
        code = ord(char)
        if char in '\n\t':
            continue
        if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            return 'prohibited control character'
        if char in '​‎‏⁠﻿':
            return 'zero-width or directional formatting character'
        if '‪' <= char <= '‮' or '⁦' <= char <= '⁩':
            return 'bidirectional control character'
        if 0xE0000 <= code <= 0xE007F:
            return 'Unicode tag character'
    return None


def scan(text):
    """Return (code, detail) for known suspicious content, or None. Never echoes text."""
    problem = control_problem(text)
    if problem:
        return 'forbidden_character', f'Entry contains a {problem}.'
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return 'secret_detected', f'Entry matches a known credential format ({name}).'
    for match in ASSIGNMENT_RE.finditer(text):
        value = match.group(1)
        mixed = any(c.isdigit() for c in value) and any(c.isalpha() for c in value)
        if mixed and not PLACEHOLDER_RE.match(value):
            return 'secret_detected', 'Entry looks like a credential assignment; store the policy, not the value.'
    for name, pattern in THREAT_PATTERNS:
        if pattern.search(text):
            return 'suspicious_pattern', f'Entry matches a prohibited instruction pattern ({name}).'
    return None


def validate_entry(text, limit=MAX_ENTRY_CHARS):
    """Normalize and check one entry. Raises MemoryRefusal without echoing the payload."""
    value = normalize(text)
    ceiling = min(limit, MAX_ENTRY_CHARS)
    if not value:
        raise MemoryRefusal('invalid_entry', 'Entry text must not be empty.')
    if '\n\n' in value:
        raise MemoryRefusal('invalid_entry', 'Entry text must not contain blank lines.')
    if value.count('\n') + 1 > MAX_ENTRY_LINES:
        raise MemoryRefusal('invalid_entry', f'Entry must not exceed {MAX_ENTRY_LINES} lines.')
    if len(value) > ceiling:
        raise MemoryRefusal('entry_too_long', f'Entry is {len(value)} characters; the maximum is {ceiling}.',
                            entry_chars=len(value), max_entry_chars=ceiling)
    found = scan(value)
    if found:
        raise MemoryRefusal(found[0], found[1] + ' Nothing was written and the text was not stored or echoed.')
    return value


def validate_source(source):
    source = (source or 'unspecified').strip()
    if not SOURCE_RE.fullmatch(source):
        raise MemoryRefusal('invalid_source',
                            'Provenance must be one token of up to 120 characters from [A-Za-z0-9_.:/#@+-], '
                            'for example operator-statement, operator-correction or verified:path/to/file.py:20-40.')
    return source


def encode_line(line):
    """Escape one stored line so a block stays unambiguous and hand-editable."""
    line = line.replace('\\', '\\\\').replace('#=>', '\\#=>')
    return '\\' + line if line.startswith('<!--') else line


def decode_line(line):
    out, index = [], 0
    while index < len(line):
        char = line[index]
        if char != '\\':
            out.append(char)
            index += 1
            continue
        following = line[index + 1:index + 2]
        if following not in ('\\', '#', '<'):
            raise MemoryRefusal('malformed_store',
                                'A memory entry uses an unknown backslash escape; repair the file by hand '
                                'or clear the affected store. Nothing was overwritten.')
        out.append(following)
        index += 2
    return ''.join(out)
