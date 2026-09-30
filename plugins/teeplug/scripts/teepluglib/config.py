"""Subscription CLI settings. Credentials belong to the host CLIs, never Teeplug."""
import json
import os
from pathlib import Path
import shutil

PROVIDERS = ('auto', 'claude', 'codex')
DEFAULTS = {'claude': {'model': 'haiku', 'effort': 'low'},
            'codex': {'model': 'gpt-5.6-luna', 'effort': 'low'}}


class TeeplugError(Exception):
    """An actionable error safe to display without source or credentials."""


def project_root(start=None):
    start = Path(start or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / '.git').exists() or (candidate / '.teeplug.json').is_file():
            return candidate
    return start


def positive(value, name):
    try:
        if isinstance(value, bool):
            raise ValueError
        number = int(value)
        if str(value) != str(number) or number < 1:
            raise ValueError
        return number
    except (ValueError, TypeError, OverflowError):
        raise TeeplugError(f'{name} must be a positive integer') from None


def detect_host(host=None, event=None):
    explicit = host or os.getenv('TEEPLUG_HOST')
    if explicit:
        if explicit not in ('claude', 'codex'):
            raise TeeplugError('TEEPLUG_HOST must be claude or codex')
        return explicit
    event = event or {}
    if str(event.get('model', '')).startswith(('gpt-', 'o3', 'o4')):
        return 'codex'
    if str(event.get('model', '')).startswith('claude') or event.get('tool_name') == 'Read':
        return 'claude'
    if os.getenv('CLAUDECODE'):
        return 'claude'
    if os.getenv('CODEX_THREAD_ID') or event.get('tool_name') in ('exec_command', 'shell_command'):
        return 'codex'
    raise TeeplugError('Host is ambiguous. Pass --host claude or --host codex (or --provider).')


def load(root, config_path=None):
    path = Path(config_path).expanduser() if config_path else root / '.teeplug.json'
    if not path.exists() and not config_path:
        return {}
    try:
        with path.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise TeeplugError('Configuration exceeds 64 KiB')
        data = json.loads(raw)
    except (OSError, ValueError):
        raise TeeplugError(f'Cannot read valid JSON configuration: {path}') from None
    allowed = {'provider', 'providers', 'timeout_seconds', 'max_input_bytes', 'min_lines',
               'cache', 'cache_ttl_seconds', 'cache_max_bytes', 'enabled', 'reader', 'writer',
               'memory'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise TeeplugError('Unknown configuration field; see docs/configuration.md. API configuration is unsupported.')
    if data.get('provider', 'auto') not in PROVIDERS:
        raise TeeplugError('provider must be auto, claude or codex')
    for mode in ('reader', 'writer'):
        part = data.get(mode, {})
        if not isinstance(part, dict) or set(part) - {'max_output_tokens'}:
            raise TeeplugError(f'{mode} supports max_output_tokens; configure models under providers')
    providers = data.get('providers', {})
    if not isinstance(providers, dict) or set(providers) - set(DEFAULTS):
        raise TeeplugError('providers supports claude and codex')
    for part in providers.values():
        if not isinstance(part, dict) or set(part) - {'model', 'effort', 'command', 'reader', 'writer'}:
            raise TeeplugError('Provider supports model, effort, command, reader and writer')
        for mode in ('reader', 'writer'):
            override = part.get(mode, {})
            if not isinstance(override, dict) or set(override) - {'model', 'effort'}:
                raise TeeplugError('Provider workflow overrides support model and effort')
    for key in ('cache', 'enabled'):
        if key in data and not isinstance(data[key], bool):
            raise TeeplugError(f'{key} must be a JSON boolean')
    check_memory(data.get('memory', {}))
    return data


def check_memory(section):
    """Validate the memory section locally: no worker CLI, login check or model request."""
    if not isinstance(section, dict) or set(section) - {'enabled', 'backend', 'limits'}:
        raise TeeplugError('memory supports enabled, backend and limits')
    if 'enabled' in section and not isinstance(section['enabled'], bool):
        raise TeeplugError('memory.enabled must be a JSON boolean')
    if section.get('backend', 'file') != 'file':
        raise TeeplugError('memory.backend supports file only; app-based memory is not implemented')
    limits = section.get('limits', {})
    if not isinstance(limits, dict) or set(limits) - {'memory_chars', 'operator_chars'}:
        raise TeeplugError('memory.limits supports memory_chars and operator_chars')
    for key, value in limits.items():
        if positive(value, f'memory.limits.{key}') > 20000:
            raise TeeplugError(f'memory.limits.{key} must not exceed 20000 characters')
    return section


def find_cli(provider, command=None):
    command = command or os.getenv(f'TEEPLUG_{provider.upper()}_BIN') or provider
    if not isinstance(command, str) or not command.strip():
        raise TeeplugError('CLI command must be one executable path, without arguments')
    found = shutil.which(os.path.expanduser(command))
    if not found and command == provider:
        candidates = ([Path.home() / '.local/bin/claude'] if provider == 'claude' else
                      [Path('/Applications/ChatGPT.app/Contents/Resources/codex'),
                       Path('/Applications/Codex.app/Contents/Resources/codex')])
        found = next((str(p) for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
    if not found:
        raise TeeplugError(f'{provider} CLI not found. Install it or set TEEPLUG_{provider.upper()}_BIN.')
    return str(Path(found).absolute())


class Settings:
    def __init__(self, root, mode='reader', config_path=None, provider=None, model=None,
                 max_output_tokens=None, host=None, effort=None):
        self.root, self.mode = Path(root).resolve(), mode
        data = load(self.root, config_path)
        selected = provider or os.getenv('TEEPLUG_PROVIDER') or data.get('provider', 'auto')
        if selected not in PROVIDERS:
            raise TeeplugError('provider must be auto, claude or codex')
        self.provider = detect_host(host) if selected == 'auto' else selected
        part = data.get('providers', {}).get(self.provider, {})
        workflow = part.get(mode, {})
        def resolve(key):
            return (os.getenv(f'TEEPLUG_{mode.upper()}_{key.upper()}') or os.getenv(f'TEEPLUG_{key.upper()}')
                    or workflow.get(key) or part.get(key) or DEFAULTS[self.provider][key])
        self.model = model or resolve('model')
        if not isinstance(self.model, str) or not self.model.strip() or self.model.startswith('-'):
            raise TeeplugError('Worker model must be a nonempty model name')
        self.effort = effort or resolve('effort')
        if self.effort not in ('low', 'medium', 'high', 'xhigh', 'max'):
            raise TeeplugError('effort must be low, medium, high, xhigh or max')
        self.command = part.get('command')
        self.timeout = positive(os.getenv('TEEPLUG_TIMEOUT_SECONDS', data.get('timeout_seconds', 180)), 'timeout_seconds')
        self.max_input_bytes = positive(data.get('max_input_bytes', 512000), 'max_input_bytes')
        budget = max_output_tokens if max_output_tokens is not None else os.getenv(
            f'TEEPLUG_{mode.upper()}_MAX_OUTPUT_TOKENS', data.get(mode, {}).get('max_output_tokens', 4000 if mode == 'reader' else 32000))
        self.max_output_tokens = positive(budget, 'max_output_tokens')
        self.min_lines = positive(os.getenv('TEEPLUG_MIN_LINES', data.get('min_lines', 350)), 'min_lines')
        self.cache = data.get('cache', True)
        self.cache_ttl = positive(data.get('cache_ttl_seconds', 604800), 'cache_ttl_seconds')
        self.cache_max_bytes = positive(data.get('cache_max_bytes', 33554432), 'cache_max_bytes')
        self.enabled = os.getenv('TEEPLUG_ENABLED', '1' if data.get('enabled', True) else '0') != '0'

    def executable(self):
        return find_cli(self.provider, self.command)
