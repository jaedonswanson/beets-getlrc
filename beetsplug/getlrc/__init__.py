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
    """ANSI color codes (colorblind-friendly palette)."""
    GREEN = '\033[92m'   # Green success color
    RED = '\033[95m'     # Magenta-like action/error color
    YELLOW = '\033[93m'  # Warning color
    BLUE = '\033[94m'    # Informational color
    CYAN = '\033[96m'    # Highlight color
    MAGENTA = '\033[95m' # Error/notice color
    GREY = '\033[90m'    # Dim background / unfilled bar
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
        self._next_print = 1
        self._pending = {}
        self._start_time = time.monotonic()

    def increment(self):
        if not self.enabled:
            return 0
        with self._lock:
            self.current += 1
            return self.current

    def _format_elapsed(self, seconds):
        minutes, seconds = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def prefix(self, current=None):
        c = _C if self.use_color else type('_NoColor', (), {k: '' for k in dir(_C) if not k.startswith('_')})()
        with self._lock:
            current = self.current if current is None else current
            total = self.total
        elapsed = time.monotonic() - self._start_time
        bar_len = 10
        filled = int((current / total) * bar_len) if total else bar_len
        percent = int((current / total) * 100) if total else 100
        filled_part = f"{c.GREEN}{'█' * filled}{c.RESET}"
        empty_part = f"{c.GREY}{'-' * (bar_len - filled)}{c.RESET}"
        bar = filled_part + empty_part
        return f"{c.BOLD}[{current:04d}/{total:04d}] [{bar}] {percent:3d}% {self._format_elapsed(elapsed)}{c.RESET} "

    def log(self, message, count=None):
        if not self.enabled or count is None:
            print(message, flush=True)
            return

        with self._lock:
            if count == self._next_print:
                print(message, flush=True)
                self._next_print += 1
                while self._next_print in self._pending:
                    print(self._pending.pop(self._next_print), flush=True)
                    self._next_print += 1
            else:
                self._pending[count] = message

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
            'delay': 0.2,
            'cache_results': True,
            'recheck_days': 30,
            'stats': True,
            'fallback_to_plain': False,
            'fallback_to_plain_lrc': False,
            'workers': 4,
            'progress': True,
            'output_dir': '',
            'sidecar_extensions': ['.lrc'],
        })

        # Populate sidecar extensions from config, ensuring all start with '.'
        exts = self.config['sidecar_extensions'].get(list) or ['.lrc']
        self._sidecar_exts = [e if e.startswith('.') else f'.{e}' for e in exts]

        # Queue to defer lyrics fetching until after import prompts
        self._import_queue = []
        self._import_queue_lock = threading.Lock()

        if self.config['auto']:
            self.register_listener('item_imported', self.item_imported)
            self.register_listener('album_imported', self.album_imported)
            self.register_listener('import_task_done', self.import_task_done)
            # Move .lrc sidecar files when items are moved by beets
            self.register_listener('item_moved', self.item_moved)
            # Move .lrc sidecar files when albums (directories) are moved
            self.register_listener('album_moved', self.album_moved)

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

    def _print(self, status, item, color='', progress=None, progress_count=None, quiet=False):
        """Print directly to stdout with color (bypasses beets logging)."""
        if quiet:
            return

        artist = item.albumartist or item.artist or 'Unknown'
        album = item.album or 'Unknown Album'
        title = item.title or 'Unknown'
        prefix = progress.prefix(progress_count) if progress else ''
        message = (
            f"{prefix}{color}{status}:{_C.RESET} "
            f"{_C.BLUE}{artist}{_C.RESET} - "
            f"{_C.MAGENTA}{album}{_C.RESET} - "
            f"{_C.CYAN}{title}{_C.RESET}"
        ) if self._use_color and color else f"{prefix}{status}: {artist} - {album} - {title}"

        if progress:
            progress.log(message, progress_count)
        else:
            print(message, flush=True)

    def commands(self):
        cmd = Subcommand('getlrc',
                         help='Fetch synced .lrc lyrics from lrclib.net')
        cmd.parser.add_option('-f', '--force', action='store_true',
                              dest='force', help='Overwrite existing .lrc files')
        cmd.parser.add_option('-a', '--album', action='store_true',
                              dest='album', help='Match albums instead of tracks')
        cmd.parser.add_option('-p', '--pretend', action='store_true',
                              dest='pretend', help='Show what would be fetched without writing')
        cmd.parser.add_option('-q', '--quiet', action='store_true',
                              dest='quiet', help='Suppress per-track output and progress display')
        cmd.parser.add_option('-w', '--workers', type='int',
                              dest='workers', help='Number of concurrent fetch workers')
        cmd.parser.add_option('--delay', type='float',
                              dest='delay', help='Delay in seconds between lookups')
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
            except (requests.Timeout, requests.ConnectionError, requests.RequestException) as e:
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

    def fetch_lrc(self, item, force=False, pretend=False, stats=None, progress=None, progress_count=None, quiet=False):
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
                self._print('Would fetch', item, _C.CYAN, progress=progress, progress_count=progress_count)
                return True

            timeout = self.config['timeout'].get(int)
            retries = self.config['retries'].get(int)

            try:
                self._log.debug(self._fmt('Querying lrclib', item))
                response = self._request_with_retry(url, timeout, retries)
                response.raise_for_status()
                data = response.json()
            except requests.Timeout:
                self._print('Timeout', item, _C.YELLOW, progress=progress, progress_count=progress_count)
                self._update_cache(item, 'timeout')
                if stats:
                    stats.add('errors')
                return False
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    self._print('Not found', item, _C.RED, progress=progress, progress_count=progress_count)
                    self._update_cache(item, 'not_found')
                    if stats:
                        stats.add('not_found')
                else:
                    code = e.response.status_code if e.response else '?'
                    self._print(f'HTTP {code}', item, _C.RED, progress=progress, progress_count=progress_count)
                    self._update_cache(item, 'error')
                    if stats:
                        stats.add('errors')
                return False
            except requests.ConnectionError:
                self._print('Connection error', item, _C.RED, progress=progress, progress_count=progress_count)
                self._update_cache(item, 'error')
                if stats:
                    stats.add('errors')
                return False
            except requests.RequestException as e:
                self._print(f'Request error: {e}', item, _C.RED, progress=progress, progress_count=progress_count)
                self._update_cache(item, 'error')
                if stats:
                    stats.add('errors')
                return False
            except ValueError:
                self._print('Bad response', item, _C.RED, progress=progress, progress_count=progress_count)
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
                    self._print('Created', item, _C.GREEN, progress=progress, progress_count=progress_count)
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

            # 2. Plain lyrics fallback (write as .lrc file if configured)
            if self.config['fallback_to_plain_lrc'].get(bool) and plain and plain not in (None, 'null', 'None'):
                try:
                    with open(syspath(lrc_path), 'w', encoding='utf-8') as f:
                        f.write(plain)
                    self._print('Created (plain lyrics)', item, _C.GREEN, progress=progress, progress_count=progress_count)
                    self._update_cache(item, 'created')
                    if stats:
                        stats.add('created')
                    return True
                except OSError as e:
                    self._log.error(self._fmt('Write failed (plain)', item, _C.RED) + f' ({e})')
                    self._update_cache(item, 'error')
                    if stats:
                        stats.add('errors')
                    return False

            # 3. Plain lyrics fallback (store in beets db only if not writing as file)
            if self.config['fallback_to_plain'].get(bool) and plain and not item.lyrics:
                item.lyrics = plain
                item.store()
                self._print('Stored plain lyrics', item, _C.GREEN, progress=progress, progress_count=progress_count)
                self._update_cache(item, 'plain')
                if stats:
                    stats.add('plain')
                return True

            # 4. Nothing available
            self._print('No synced lyrics', item, _C.RED, progress=progress, progress_count=progress_count)
            self._update_cache(item, 'no_synced')
            if stats:
                stats.add('no_synced')
            return False
        finally:
            pass

    def item_imported(self, lib, item):
        """Queue item for deferred LRC fetch (after import prompts complete)."""
        with self._import_queue_lock:
            self._import_queue.append(item)

    def album_imported(self, lib, album):
        """Queue album items for deferred LRC fetch (after import prompts complete)."""
        with self._import_queue_lock:
            for item in album.items():
                self._import_queue.append(item)

    def import_task_done(self, lib, task, **kwargs):
        """Process queued items after an import task completes (all prompts done)."""
        # Collect items queued during this task
        with self._import_queue_lock:
            items_to_process = list(self._import_queue)
            self._import_queue.clear()
        
        if not items_to_process:
            return
        
        force = self.config['overwrite'].get(bool)
        delay = self.config['delay'].get(float)
        workers = self.config['workers'].get(int)
        
        # Process queued items with same threading/sequencing logic
        if workers > 1:
            def run(item):
                try:
                    self.fetch_lrc(item, force=force, quiet=False)
                    time.sleep(delay)
                except Exception as e:
                    self._log.error(f"Error fetching lyrics for {displayable_path(item.path)}: {e}")

            with ThreadPoolExecutor(max_workers=workers) as executor:
                try:
                    list(executor.map(run, items_to_process, timeout=None))
                except Exception as e:
                    self._log.error(f"Critical error in import lyrics worker: {e}")
        else:
            for item in items_to_process:
                try:
                    self.fetch_lrc(item, force=force, quiet=False)
                    time.sleep(delay)
                except Exception as e:
                    self._log.error(f"Error fetching lyrics for {displayable_path(item.path)}: {e}")

    def import_defer_start(self, task, **kwargs):
        """Process queued items after import task's choice prompt is done."""
        # The import task will fire this event when it needs user input.
        # We process all queued items in the next idle moment after prompts.
        # However, since this can be called multiple times per import task,
        # we only process if the queue is substantial and likely done growing.
        pass  # Actual processing happens in import_task_done

    def item_moved(self, *args, **kwargs):
        """Move sidecar files when an item file is moved.

        Accepts either (lib, item, source, destination) or (item, source, destination)
        or keyword form from beets: item=..., source=..., destination=... .
        """
        from pathlib import Path
        import shutil

        # Normalize parameters
        if 'item' in kwargs and 'source' in kwargs and 'destination' in kwargs:
            item = kwargs.get('item')
            source = kwargs.get('source')
            destination = kwargs.get('destination')
        else:
            if len(args) == 4:
                _, item, source, destination = args
            elif len(args) == 3:
                item, source, destination = args
            else:
                self._log.error('item_moved: unexpected args')
                return

        try:
            # Normalize to str paths (handle bytes from beets internals)
            import os as _os
            source_path = _os.fspath(source)
            destination_path = _os.fspath(destination)
            if isinstance(source_path, (bytes, bytearray)):
                source_path = source_path.decode('utf-8', 'surrogateescape')
            if isinstance(destination_path, (bytes, bytearray)):
                destination_path = destination_path.decode('utf-8', 'surrogateescape')

            exts = getattr(self, '_sidecar_exts', None)
            if exts is None:
                exts = ['.lrc']

            for ext in exts:
                old = Path(source_path).with_suffix(ext)
                new = Path(destination_path).with_suffix(ext)
                if old.exists():
                    new.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old), str(new))
                    self._log.debug(self._fmt(f'Moved sidecar {ext}', item, _C.GREEN))
        except Exception as e:
            self._log.error(self._fmt(f'Failed moving sidecar: {e}', item, _C.RED))

    def album_moved(self, *args, **kwargs):
        """Move sidecar files under `source` to `destination` when an album dir moves.

        Accepts either (lib, album, source, destination) or (album, source, destination)
        or keyword form: album=..., source=..., destination=... .
        """
        from pathlib import Path
        import shutil

        # Normalize parameters
        if 'album' in kwargs and 'source' in kwargs and 'destination' in kwargs:
            album = kwargs.get('album')
            source = kwargs.get('source')
            destination = kwargs.get('destination')
        else:
            if len(args) == 4:
                _, album, source, destination = args
            elif len(args) == 3:
                album, source, destination = args
            else:
                self._log.error('album_moved: unexpected args')
                return

        try:
            # Normalize to str paths (handle bytes from beets internals)
            import os as _os
            src_path = _os.fspath(source)
            dst_path = _os.fspath(destination)
            if isinstance(src_path, (bytes, bytearray)):
                src_path = src_path.decode('utf-8', 'surrogateescape')
            if isinstance(dst_path, (bytes, bytearray)):
                dst_path = dst_path.decode('utf-8', 'surrogateescape')

            src_dir = Path(src_path)
            dst_dir = Path(dst_path)

            if not src_dir.exists():
                return

            exts = getattr(self, '_sidecar_exts', None)
            if exts is None:
                exts = ['.lrc']

            for ext in exts:
                for p in src_dir.rglob(f'*{ext}'):
                    try:
                        rel = p.relative_to(src_dir)
                    except Exception:
                        rel = p.name
                    target = dst_dir.joinpath(rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(target))
                    self._log.debug(f'Moved album sidecar {ext}: {p} -> {target}')
        except Exception as e:
            # Try get a representative item for logging
            rep = None
            try:
                rep = album.items()[0]
            except Exception:
                rep = None
            self._log.error(self._fmt(f'Failed moving album sidecars: {e}', rep, _C.RED))

    def command(self, lib, opts, args):
        force = opts.force or self.config['overwrite'].get(bool)
        pretend = opts.pretend
        quiet = opts.quiet
        show_stats = opts.stats or self.config['stats'].get(bool)
        workers = opts.workers if getattr(opts, 'workers', None) is not None else self.config['workers'].get(int)
        delay = opts.delay if getattr(opts, 'delay', None) is not None else self.config['delay'].get(float)
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

        progress_enabled = self.config['progress'].get(bool) and not quiet
        progress = Progress(len(items), self._use_color, progress_enabled) if self.config['progress'].get(bool) else None

        # Threaded execution
        if workers > 1:
            def run(item):
                try:
                    count = progress.increment() if progress else None
                    self.fetch_lrc(item, force=force, pretend=pretend,
                                 stats=stats, progress=progress,
                                 progress_count=count, quiet=quiet)
                    time.sleep(delay)
                except Exception as e:
                    # Ensure progress counter is incremented even on error
                    if progress:
                        progress.increment()
                    self._log.error(f"Error processing {displayable_path(item.path)}: {e}")

            with ThreadPoolExecutor(max_workers=workers) as executor:
                # Use list() to consume all results and let exceptions propagate if critical
                try:
                    list(executor.map(run, items, timeout=None))
                except Exception as e:
                    self._log.error(f"Critical error in worker thread: {e}")

        # Sequential execution
        else:
            for item in items:
                try:
                    count = progress.increment() if progress else None
                    self.fetch_lrc(item, force=force, pretend=pretend,
                                 stats=stats, progress=progress,
                                 progress_count=count, quiet=quiet)
                    time.sleep(delay)
                except Exception as e:
                    self._log.error(f"Error processing {displayable_path(item.path)}: {e}")
                    if progress:
                        progress.increment()

        if progress:
            progress.finish()
        if show_stats and stats:
            stats.print_summary(use_color=self._use_color)