"""Beets plugin to fetch synced .lrc lyrics from lrclib.net."""

from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, decargs
from beets.util import displayable_path, bytestring_path, syspath
import requests
import urllib.parse
import os
import logging
import time


class GetLrcPlugin(BeetsPlugin):
    def __init__(self):
        super().__init__()
        self._log = logging.getLogger('beets.getlrc')

        self.config.add({
            'auto': True,
            'overwrite': False,
            'timeout': 30,
            'retries': 3,
            'delay': 0.5,
        })

        if self.config['auto']:
            self.register_listener('item_imported', self.item_imported)
            self.register_listener('album_imported', self.album_imported)

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
        """GET with exponential backoff on timeout/connection errors."""
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

    def fetch_lrc(self, item, force=False, pretend=False):
        audio_path_str = displayable_path(item.path)
        base = os.path.splitext(item.path)[0]
        lrc_path = bytestring_path(base + b'.lrc')
        lrc_path_str = displayable_path(lrc_path)

        if not force and os.path.exists(syspath(lrc_path)):
            self._log.debug(f'Skipping {audio_path_str}: .lrc already exists')
            return False

        artist = item.artist or item.albumartist
        title = item.title
        duration = int(item.length) if item.length else None

        if not artist or not title or not duration:
            self._log.warning(f'Missing metadata for {audio_path_str}')
            return False

        params = {
            'artist_name': artist,
            'track_name': title,
            'duration': duration,
        }
        url = 'https://lrclib.net/api/get?' + urllib.parse.urlencode(params)

        if pretend:
            self._log.info(f'Would fetch: {artist} - {title} ({duration}s)')
            return True

        timeout = self.config['timeout'].get(int)
        retries = self.config['retries'].get(int)

        try:
            self._log.debug(f'Querying lrclib: {artist} - {title}')
            response = self._request_with_retry(url, timeout, retries)
            response.raise_for_status()
            data = response.json()
        except requests.Timeout:
            self._log.warning(f'Timeout after {retries} attempts: {audio_path_str}')
            return False
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                self._log.info(f'Not found on lrclib: {artist} - {title}')
            else:
                self._log.warning(f'Request failed for {audio_path_str}: {e}')
            return False
        except requests.RequestException as e:
            self._log.warning(f'Request failed for {audio_path_str}: {e}')
            return False
        except ValueError as e:
            self._log.warning(f'Invalid JSON for {audio_path_str}: {e}')
            return False

        synced = data.get('syncedLyrics')
        if not synced or synced in (None, 'null', 'None'):
            self._log.info(f'No synced lyrics found: {artist} - {title}')
            return False

        try:
            with open(syspath(lrc_path), 'w', encoding='utf-8') as f:
                f.write(synced)
            self._log.info(f'Created {lrc_path_str}')
            return True
        except OSError as e:
            self._log.error(f'Failed to write {lrc_path_str}: {e}')
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
                    self.fetch_lrc(item, force=force,