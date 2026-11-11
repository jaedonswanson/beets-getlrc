import os
import datetime as _dt
from datetime import datetime, timedelta
from types import SimpleNamespace
from pathlib import Path

import pytest

import requests

from beets.util import bytestring_path
from beetsplug.getlrc.plugin import GetLrcPlugin, Progress, Stats


class Item:
    def __init__(self, path, title='Title', artist='Artist', album='Album', length=120, albumartist=None):
        self.path = bytestring_path(path)
        self.title = title
        self.artist = artist
        self.album = album
        self.length = length
        self.albumartist = albumartist
        self._data = {}
        self._store_called = False
        self.lyrics = None

    # mapping-style access
    def get(self, key, default=None):
        return self._data.get(key, default)

    def __setitem__(self, key, value):
        self._data[key] = value

    def __getitem__(self, key):
        return self._data[key]

    def store(self):
        self._store_called = True


class MockResponse:
    def __init__(self, status_code=200, json_data=None, raise_exc=None):
        self.status_code = status_code
        self._json = json_data or {}
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise:
            raise self._raise

    def json(self):
        return self._json


def test_cached_skip_respects_recheck(tmp_path):
    plugin = GetLrcPlugin()
    item = Item(tmp_path / 'track.flac')
    # set recent checked status that should cause cached skip
    item['getlrc_status'] = 'not_found'
    item['getlrc_checked'] = datetime.now().isoformat()

    stats = Stats()
    result = plugin.fetch_lrc(item, force=False, pretend=False, stats=stats, progress=None, progress_count=None)

    assert result is False
    assert stats.cached == 1


def test_recheck_window_allows_retry_and_stores_plain(tmp_path):
    plugin = GetLrcPlugin()
    plugin.config['search_fallback'] = False
    item = Item(tmp_path / 'track2.flac')
    # set old checked time beyond recheck window
    old = datetime.now() - timedelta(days=plugin.config['recheck_days'].get(int) + 1)
    item['getlrc_status'] = 'not_found'
    item['getlrc_checked'] = old.isoformat()

    # Mock network to return plain lyrics only
    def fake_request(url, timeout, retries):
        return MockResponse(status_code=200, json_data={'plainLyrics': 'plain text', 'syncedLyrics': None})

    plugin._request_with_retry = fake_request
    # Ensure explicit config to avoid cross-test state
    plugin.config['fallback_to_plain'] = True
    plugin.config['fallback_to_plain_lrc'] = False

    stats = Stats()
    result = plugin.fetch_lrc(item, force=False, pretend=False, stats=stats, progress=None, progress_count=None)

    assert result is True
    assert item.lyrics == 'plain text'
    assert item._store_called is True
    assert stats.plain == 1


def test_fallback_to_plain_lrc_writes_file(tmp_path):
    plugin = GetLrcPlugin()
    plugin.config['search_fallback'] = False
    item = Item(tmp_path / 'track3.flac')

    def fake_request(url, timeout, retries):
        return MockResponse(status_code=200, json_data={'plainLyrics': 'plain file content', 'syncedLyrics': None})

    plugin._request_with_retry = fake_request
    # Ensure explicit config
    plugin.config['fallback_to_plain_lrc'] = True
    plugin.config['fallback_to_plain'] = False

    stats = Stats()
    result = plugin.fetch_lrc(item, force=False, pretend=False, stats=stats, progress=None, progress_count=None)

    # lrc file should be created next to audio
    lrc_path = plugin._get_lrc_path(item, '.txt')
    p = Path(lrc_path.decode() if isinstance(lrc_path, bytes) else str(lrc_path))
    assert p.exists()
    assert p.read_text() == 'plain file content'
    assert stats.plain == 1


def test_http_404_updates_cache_and_stats(tmp_path):
    plugin = GetLrcPlugin()
    item = Item(tmp_path / 'track4.flac')

    # Build response with status_code and have raise_for_status raise HTTPError
    def fake_request(url, timeout, retries):
        resp = MockResponse(status_code=404, json_data={})
        def raise_exc():
            raise requests.HTTPError(response=resp)
        resp.raise_for_status = raise_exc
        return resp

    plugin._request_with_retry = fake_request

    stats = Stats()
    result = plugin.fetch_lrc(item, force=False, pretend=False, stats=stats, progress=None, progress_count=None)

    assert result is False
    # cache should be updated to not_found (item mapping)
    # _update_cache prefers mapping set; item supports mapping
    assert item.get('getlrc_status') == 'not_found' or getattr(item, 'getlrc_status', None) == 'not_found'
    assert stats.not_found == 1

def test_log_prints_immediately_regardless_of_count_order(capsys):
    """Regression: per-track output must not stall on skipped/out-of-order counts.

    Items consume a sequence count on increment whether or not they ever
    print (existing .lrc skips, cache hits). The old ordered buffer waited
    forever on those silent counts, so nothing after the first skip ever
    printed. Immediate printing must not depend on arrival order.
    """
    progress = Progress(100, use_color=False, enabled=True)

    # counts 1-2 consumed silently by skipped items (no log call)
    progress.increment()
    progress.increment()

    # out-of-order completions
    progress.log("Task 5", 5)
    progress.log("Task 3", 3)

    out = capsys.readouterr().out
    assert "Task 5" in out
    assert "Task 3" in out

# ---------------------------------------------------------------------------
# _should_skip_cached: terminal vs negative statuses, recheck window


TERMINAL_STATUSES = ['got', 'found', 'exists']
NEGATIVE_STATUSES = ['no_synced', 'not_found', 'missing', 'timeout', 'plain', 'error']


def _make_cached_item(path, status, age_days):
    item = Item(path)
    item['getlrc_status'] = status
    item['getlrc_checked'] = (
        _dt.datetime.now() - _dt.timedelta(days=age_days)
    ).isoformat()
    return item


@pytest.mark.parametrize('status', TERMINAL_STATUSES)
def test_terminal_statuses_are_never_cached_skipped(tmp_path, status):
    """got/found/exists always re-run; file-existence check owns those."""
    plugin = GetLrcPlugin()
    item = _make_cached_item(tmp_path / 'a.flac', status, age_days=0)
    assert plugin._should_skip_cached(item, force=False) is False


@pytest.mark.parametrize('status', NEGATIVE_STATUSES)
def test_negative_statuses_skip_when_fresh(tmp_path, status):
    plugin = GetLrcPlugin()
    item = _make_cached_item(tmp_path / 'b.flac', status, age_days=5)
    assert plugin._should_skip_cached(item, force=False) is True


@pytest.mark.parametrize('status', NEGATIVE_STATUSES)
def test_negative_statuses_recheck_when_stale(tmp_path, status):
    recheck_days = GetLrcPlugin().config['recheck_days'].get(int)
    plugin = GetLrcPlugin()
    item = _make_cached_item(tmp_path / 'c.flac', status,
                             age_days=recheck_days + 1)
    assert plugin._should_skip_cached(item, force=False) is False


def test_force_overrides_cached_skip(tmp_path):
    plugin = GetLrcPlugin()
    item = _make_cached_item(tmp_path / 'd.flac', 'no_synced', age_days=0)
    assert plugin._should_skip_cached(item, force=True) is False


def test_item_without_cache_state_is_not_skipped(tmp_path):
    plugin = GetLrcPlugin()
    assert plugin._should_skip_cached(Item(tmp_path / 'e.flac'), force=False) is False


def test_malformed_timestamp_is_not_skipped(tmp_path):
    plugin = GetLrcPlugin()
    item = Item(tmp_path / 'f.flac')
    item['getlrc_status'] = 'no_synced'
    item['getlrc_checked'] = 'not-a-date'
    assert plugin._should_skip_cached(item, force=False) is False
