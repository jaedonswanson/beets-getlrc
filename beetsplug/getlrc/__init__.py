"""Beets plugin to fetch synced .lrc lyrics from lrclib.net."""

from beets import config
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, decargs
from beets.util import displayable_path, bytestring_path, syspath
import requests
import urllib.parse
import os
import logging
import time
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta


class _C:
    """ANSI color codes."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


class Stats:
    """Thread-safe fetch result counters."""
    def __init__(self):
        self._lock = threading.Lock()
        self.created = 0
        self.plain = 0
        self.skipped = 0
        self.not_found = 0
        self.no_synced = 0
        self.missing_meta = 0
        self.errors = 0
        self.cached = 0

    def add(self, field):
        with self._lock:
            setattr(self, field, getattr(self, field) + 1)

    @property
    def total(self):
        with self._lock:
            return (self.created + self.plain + self.skipped + self.not_found +
                    self.no_synced + self.missing_meta + self.errors + self.cached)

    def print_summary(self, use_color=False):
        c = _C if use_color else type('_NoColor', (), {k: '' for k in dir(_C) if not k.startswith('_')})()
        lines = [
            '',
            f"{c.BOLD}{'─'*50}{c.RESET}",
            f"  {c.GREEN}{'Created (.lrc):':<<20}{c.RESET} {self.created}",
            f"  {c.GREEN}{'Plain lyrics:':<<20}{c.RESET} {self.plain}",
            f"  {'Skipped (exists):':<<20} {self.skipped}",
            f"  {'Cached skip:':<<20} {self.cached}",
            f"  {c.RED}{'Not found (404):':<<20}{c.RESET} {self.not_found}",
            f"  {c.RED}{'No synced lyrics:':<<20}{c.RESET} {self.no_synced}",
            f"  {c.YELLOW}{'Missing metadata:':<<20}{c.RESET} {self.missing_meta}",
            f"  {c.RED}{'Errors:':<<20}{c.RESET} {self.errors}",
            f"{c.BOLD}{'─'*50}{c.RESET}",
            f"  {c.BOLD}{'Total processed:':<<20}{c.RESET} {self.total}",
        ]
        print('\n'.join(lines))


class Progress:
    """Thread-safe terminal progress counter."""
    def __init__(self, total, use_color=False, enabled=True):
        self.total = total
        self.current = 0
        self.enabled = enabled
        self.use_color = use_color
        self._lock = threading.Lock()

    def increment(self):
        if not self.enabled:
            return 0
        with self._lock:
            self.current += 1
            return self.current

    def prefix(self):
        c = _C if self.use_color else type('_NoColor', (), {k: '' for k in dir(_C) if not k.startswith('_')})()
        bar_len = 10
        filled = int((self.current / self.total) * bar_len) if self.total else bar_len
        bar = '█' * filled + '-' * (bar_len - filled)
        return f"{c.BOLD}getlrc: [{self.current:04d}/{self.total:04d}] [{bar}] {c.RESET}"

    def finish(self):
        if self.enabled:
            sys.stdout.write("\n")
            sys.stdout.flush()


class GetLrcPlugin(BeetsPlugin):
    def __init__(self):
        super().__init__()
        self._log = logging.getLogger('beets.getlrc')
        self._use_color = sys.stderr.isatty() and not os.environ.get('NO_COLOR')

        self.config.add({
            'auto': True,
            'overwrite': False,
            'timeout': 30,
            'retries': 3,
            'delay': 0.5,
            'cache_results': True,
            'recheck_days': 30,
            'stats': True,
            'fallback_to_plain': False,
            'workers': 1,
            'progress': True,
            'output_dir': '',
        })

        if self.config['auto']:
            self.register_listener('item_imported', self.item_imported)
            self.register_listener('album_imported', self.album_imported)

    def _safe_name(self, val):
        """Sanitize a string for use in a filesystem path."""
        val = str(val)
        for ch in '/\\:?*"<>|':
            val = val.replace(ch, '-')
        return val

    def _expand_output_dir(self, template, item):
        """Replace simple template tokens in output_dir."""
        replacements = {
            '{albumartist}': self._safe_name(item.albumartist or item.artist or 'Unknown'),
            '{artist}': self._safe_name(item.artist or 'Unknown'),
            '{album}': self._safe_name(item.album or 'Unknown Album'),
            '{title}': self._safe_name(item.title or 'Unknown'),
            '{year}': self._safe_name(item.year or '0000'),
        }
        path = os.path.expanduser(template)
        for key, val in replacements.items():
            path = path.replace(key, val)
        return path

    def _fmt(self, status, item, color=''):
        """Format a log line: Status + Artist - Album - Title."""
        artist = item.albumartist or item.artist or 'Unknown'
        album = item.album or 'Unknown Album'
        title = item.title or 'Unknown'

        if self._use_color and color:
            return (
                f"{color}{status}:{_C.RESET} "
                f"{_C.BLUE}{artist} - {album}{_C.RESET} - {title}"
            )
        return f"{status}: {artist} - {album} - {title}"

    def _print(self, status, item, color='', progress=None):
        """Print directly to stdout with color (bypasses beets logging)."""
        artist = item.albumartist or item.artist or 'Unknown'
        album = item.album or 'Unknown Album'
        title = item.title or 'Unknown'
        prefix = progress.prefix() if progress else ''

        if self._use_color and color:
            print(
                f"{prefix}{color}{status}:{_C.RESET} "
                f"{_C.BLUE}{artist}{_C.RESET} - "
                f"{_C.MAGENTA}{album}{_C.RESET} - "
                f"{_C.CYAN}{title}{_C.RESET}"
            )
        else:
            print(f"{prefix}{status}: {artist} - {album} - {title}")

    def commands(self):
        cmd = Subcommand('getlrc',
                         help='Fetch synced .lrc lyrics from lrclib.net')
        cmd.parser.add_option('-f', '--force', action='store_true',
                              dest='force', help='Overwrite existing .lrc files')
        cmd.parser.add_option('-a', '--album', action='store_true',
                              dest='album', help='Match albums instead of tracks')
        cmd.parser.add_option('-p', '--pretend', action='store_true',
                              dest='pretend', help='Show what would be fetched without writing')
        cmd.parser.add_option('-s', '--stats', action='store_true',
                              dest='stats', help='Print summary stats when done')
        cmd.func = self.command
        return [cmd]

    def _request_with_retry(self, url, timeout, retries):
        if retries < 1:
            retries = 1
        for attempt in range(1, retries + 1):
            try:
                return requests.get(url, timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt == retries:
                    raise
                wait = 2 ** attempt
                self._log.debug(f'Attempt {attempt} failed ({e}), retrying in {wait}s...')
                time.sleep(wait)

    def _should_skip_cached(self, item, force):
        if force or not self.config['cache_results'].get(bool):
            return False
        status = item.get('getlrc_status')
        checked_str = item.get('getlrc_checked')
        if not status or not checked_str:
            return False
        if status in ('created', 'exists'):
            return False
        try:
            checked = datetime.fromisoformat(checked_str)
            recheck = timedelta(days=self.config['recheck_days'].get(int))
            if datetime.now() - checked < recheck:
                self._log.debug(self._fmt(f'Cached skip ({status})', item))
                return True
        except ValueError:
            pass
        return False

    def _update_cache(self, item, status):
        if not self.config['cache_results'].get(bool):
            return
        item['getlrc_status'] = status
        item['getlrc_checked'] = datetime.now().isoformat()
        item.store()

    def _get_lrc_path(self, item):
        """Determine the .lrc file path, respecting output_dir config."""
        lrc_basename = os.path.splitext(os.path.basename(displayable_path(item.path)))[0] + '.lrc'
        
        # Get output_dir template from config (handle None/empty)
        output_template = str(self.config['output_dir']).strip() if self.config['output_dir'] else ''
        if output_template and output_template.lower() == 'none':
            output_template = ''
        
        # Determine the target directory
        if output_template:
            # Use configured output directory template
            dir_path = self._expand_output_dir(output_template, item)
            # Make absolute if relative
            if not os.path.isabs(dir_path):
                dir_path = os.path.abspath(dir_path)
        else:
            # Default: use the same directory as the audio file
            audio_path = displayable_path(item.path)
            if not os.path.isabs(audio_path):
                library_dir = displayable_path(config['directory'].as_filename())
                if not os.path.isabs(library_dir):
                    library_dir = os.path.abspath(os.path.expanduser(library_dir))
                audio_path = os.path.join(library_dir, audio_path)
            dir_path = os.path.dirname(audio_path)
        
        # Final safety check: ensure path is absolute
        if not os.path.isabs(dir_path):
            dir_path = os.path.abspath(dir_path)
        
        # Create directory if needed
        os.makedirs(dir_path, exist_ok=True)
        
        # Build full path and return as bytes
        lrc_path_str = os.path.join(dir_path, lrc_basename)
        return bytestring_path(lrc_path_str)

    def fetch_lrc(self, item, force=False, pretend=False, stats=None, progress=None):
        try:
            lrc_path = self._get_lrc_path(item)
            lrc_path_str = displayable_path(lrc_path)

            if not force and os.path.exists(syspath(lrc_path)):
                self._log.debug(self._fmt('Skip (exists)', item))
                self._update_cache(item, 'exists')
                if stats:
                    stats.add('skipped')
                return False

            if self._should_skip_cached(item, force):
                if stats:
                    stats.add('cached')
                return False

            artist = item.albumartist or item.artist or 'Unknown'
            title = item.title or 'Unknown'
            duration = int(item.length) if item.length else None

            if not item.title or not duration:
                self._log.warning(self._fmt('Skip (missing metadata)', item, _C.YELLOW))
                self._update_cache(item, 'missing')
                if stats:
                    stats.add('missing_meta')
                return False

            params = {
                'artist_name': artist,
                'track_name': title,
                'duration': duration,
            }
            url = 'https://lrclib.net/api/get?' + urllib.parse.urlencode(params)

            if pretend:
                self._print('Would fetch', item, _C.CYAN, progress=progress)
                return True

            timeout = self.config['timeout'].get(int)
            retries = self.config['retries'].get(int)

            try:
                self._log.debug(self._fmt('Querying lrclib', item))
                response = self._request_with_retry(url, timeout, retries)
                response.raise_for_status()
                data = response.json()
            except requests.Timeout:
                self._log.warning(self._fmt('Timeout', item, _C.YELLOW))
                self._update_cache(item, 'timeout')
                if stats:
                    stats.add('errors')
                return False
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    self._print('Not found', item, _C.RED, progress=progress)
                    self._update_cache(item, 'not_found')
                    if stats:
                        stats.add('not_found')
                else:
                    code = e.response.status_code if e.response else '?'
                    self._log.warning(self._fmt(f'HTTP {code}', item, _C.RED))
                    self._update_cache(item, 'error')
                    if stats:
                        stats.add('errors')
                return False
            except requests.RequestException:
                self._log.warning(self._fmt('Network error', item, _C.RED))
                self._update_cache(item, 'error')
                if stats:
                    stats.add('errors')
                return False
            except ValueError:
                self._log.warning(self._fmt('Bad response', item, _C.RED))
                self._update_cache(item, 'error')
                if stats:
                    stats.add('errors')
                return False

            synced = data.get('syncedLyrics')
            plain = data.get('plainLyrics')

            # 1. Synced .lrc file (primary goal)
            if synced and synced not in (None, 'null', 'None'):
                try:
                    with open(syspath(lrc_path), 'w', encoding='utf-8') as f:
                        f.write(synced)
                    self._print('Created', item, _C.GREEN, progress=progress)
                    self._update_cache(item, 'created')
                    if stats:
                        stats.add('created')
                    return True
                except OSError as e:
                    self._log.error(self._fmt('Write failed', item, _C.RED) + f' ({e})')
                    self._update_cache(item, 'error')
                    if stats:
                        stats.add('errors')
                    return False

            # 2. Plain lyrics fallback (store in beets db)
            if self.config['fallback_to_plain'].get(bool) and plain and not item.lyrics:
                item.lyrics = plain
                item.store()
                self._print('Stored plain lyrics', item, _C.GREEN, progress=progress)
                self._update_cache(item, 'plain')
                if stats:
                    stats.add('plain')
                return True

            # 3. Nothing available
            self._print('No synced lyrics', item, _C.RED, progress=progress)
            self._update_cache(item, 'no_synced')
            if stats:
                stats.add('no_synced')
            return False
        finally:
            pass

    def item_imported(self, lib, item):
        self.fetch_lrc(item, force=self.config['overwrite'].get(bool))
        time.sleep(self.config['delay'].get(float))

    def album_imported(self, lib, album):
        for item in album.items():
            self.fetch_lrc(item, force=self.config['overwrite'].get(bool))
            time.sleep(self.config['delay'].get(float))

    def command(self, lib, opts, args):
        force = opts.force or self.config['overwrite'].get(bool)
        pretend = opts.pretend
        show_stats = opts.stats or self.config['stats'].get(bool)
        workers = self.config['workers'].get(int)
        stats = Stats() if show_stats else None

        # Collect all target items first
        items = []
        if opts.album:
            for album in lib.albums(decargs(args)):
                items.extend(album.items())
        else:
            items = list(lib.items(decargs(args)))

        if not items:
            return

        progress = Progress(len(items), self._use_color, self.config['progress'].get(bool)) if show_stats else None

        # Threaded execution
        if workers > 1:
            def run(item):
                try:
                    if progress:
                        progress.increment()
                    self.fetch_lrc(item, force=force, pretend=pretend,
                                 stats=stats, progress=progress)
                    time.sleep(self.config['delay'].get(float))
                except Exception as e:
                    self._log.error(f"Unhandled error for {displayable_path(item.path)}: {e}")

            with ThreadPoolExecutor(max_workers=workers) as executor:
                executor.map(run, items)

        # Sequential execution
        else:
            for item in items:
                if progress:
                    progress.increment()
                self.fetch_lrc(item, force=force, pretend=pretend,
                             stats=stats, progress=progress)
                time.sleep(self.config['delay'].get(float))

        if progress:
            progress.finish()
        if show_stats and stats:
            stats.print_summary(use_color=self._use_color)