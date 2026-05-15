"""Beets plugin to fetch synced .lrc lyrics from lrclib.net."""

from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, decargs
from beets.util import displayable_path, bytestring_path, syspath
import requests
import urllib.parse
import os
import logging
import time
import sys
from datetime import datetime, timedelta


class _C:
    """ANSI color codes."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


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
        })

        if self.config['auto']:
            self.register_listener('item_imported', self.item_imported)
            self.register_listener('album_imported', self.album_imported)

    def _fmt(self, status, item, color=''):
        """Format a log line: Status + Artist - Album - Song."""
        artist = item.artist or item.albumartist or 'Unknown'
        album = item.album or 'Unknown Album'
        title = item.title or 'Unknown'

        if self._use_color and color:
            return (
                f"{color}{status}:{_C.RESET} "
                f"{_C.BLUE}{artist} - {album}{_C.RESET} - {title}"
            )
        return f"{status}: {artist} - {album} - {title}"

    def commands(self):
        cmd = Subcommand('getlrc',
                         help='Fetch synced .lrc lyrics from lrclib.net')
        cmd.parser.add_option('-f', '--force', action='store_true',
                              dest='force', help='Overwrite existing .lrc files')
        cmd.parser.add_option('-a', '--album', action='store_true',
                              dest='album', help='Match albums instead of tracks')
        cmd.parser.add_option('-p', '--pretend', action='store_true',
                              dest='pretend', help='Show what would be fetched without writing')
        cmd.func = self.command
        return [cmd]

    def _request_with_retry(self, url, timeout, retries):
        for attempt in range(1, retries + 1):
            try:
                return requests.get(url, timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt == retries:
                    raise
                wait = 2 ** attempt
                self._log.debug(f'Attempt {attempt} failed ({e}), retrying in {wait}s...')
                time.sleep(wait)
        return None

        def _should_skip_cached(self, item, force):
        if force or not self.config['cache_results']:
            return False
        status = item.get('getlrc_status')
        checked_str = item.get('getlrc_checked')
        if not status or not checked_str:
            return False
        
        # Only skip negative results. Positive results (created/exists) 
        # are handled by the filesystem .lrc check, so deleting a file 
        # allows immediate re-fetch without waiting for cache expiry.
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
        if not self.config['cache_results']:
            return
        item['getlrc_status'] = status
        item['getlrc_checked'] = datetime.now().isoformat()
        item.store()

    def fetch_lrc(self, item, force=False, pretend=False):
        base = os.path.splitext(item.path)[0]
        lrc_path = bytestring_path(base + b'.lrc')
        if not force and os.path.exists(syspath(lrc_path)):
            self._log.debug(self._fmt('Skip (exists)', item))
            self._update_cache(item, 'exists')
            return False

        if self._should_skip_cached(item, force):
            return False

        artist = item.artist or item.albumartist or 'Unknown'
        title = item.title or 'Unknown'
        duration = int(item.length) if item.length else None

        if not item.artist or not item.title or not duration:
            self._log.warning(self._fmt('Skip (missing metadata)', item, _C.YELLOW))
            self._update_cache(item, 'missing')
            return False

        params = {
            'artist_name': artist,
            'track_name': title,
            'duration': duration,
        }
        url = 'https://lrclib.net/api/get?' + urllib.parse.urlencode(params)

        if pretend:
            self._log.info(self._fmt('Would fetch', item, _C.CYAN))
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
            return False
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                self._log.info(self._fmt('Not found', item, _C.RED))
                self._update_cache(item, 'not_found')
            else:
                status = e.response.status_code if e.response else '?'
                self._log.warning(self._fmt(f'HTTP {status}', item, _C.RED))
                self._update_cache(item, 'error')
            return False
        except requests.RequestException:
            self._log.warning(self._fmt('Network error', item, _C.RED))
            self._update_cache(item, 'error')
            return False
        except ValueError:
            self._log.warning(self._fmt('Bad response', item, _C.RED))
            self._update_cache(item, 'error')
            return False

        synced = data.get('syncedLyrics')
        if not synced or synced in (None, 'null', 'None'):
            self._log.info(self._fmt('No synced lyrics', item, _C.RED))
            self._update_cache(item, 'no_synced')
            return False

        try:
            with open(syspath(lrc_path), 'w', encoding='utf-8') as f:
                f.write(synced)
            self._log.info(self._fmt('Created', item, _C.GREEN))
            self._update_cache(item, 'created')
            return True
        except OSError as e:
            self._log.error(self._fmt('Write failed', item, _C.RED) + f' ({e})')
            self._update_cache(item, 'error')
            return False

    def item_imported(self, lib, item):
        self.fetch_lrc(item, force=self.config['overwrite'])
        time.sleep(self.config['delay'].get(float))

    def album_imported(self, lib, album):
        for item in album.items():
            self.fetch_lrc(item, force=self.config['overwrite'])
            time.sleep(self.config['delay'].get(float))

    def command(self, lib, opts, args):
        force = opts.force or self.config['overwrite']
        pretend = opts.pretend

        if opts.album:
            for album in lib.albums(decargs(args)):
                for item in album.items():
                    self.fetch_lrc(item, force=force, pretend=pretend)
                    time.sleep(self.config['delay'].get(float))
        else:
            for item in lib.items(decargs(args)):
                self.fetch_lrc(item, force=force, pretend=pretend)
                time.sleep(self.config['delay'].get(float))