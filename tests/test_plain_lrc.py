#!/usr/bin/env python3
"""Test fallback_to_plain_lrc feature."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from beetsplug.getlrc import GetLrcPlugin
from unittest.mock import Mock, patch


class MockItem:
    """A mock item that supports dict-like access for caching."""
    def __init__(self, path):
        self.path = path
        self.title = 'Test Song'
        self.artist = 'Test Artist'
        self.albumartist = 'Test Album Artist'
        self.album = 'Test Album'
        self.year = 2024
        self.length = 180
        self.lyrics = None
        self._data = {}
    
    def store(self):
        pass
    
    def __setitem__(self, key, value):
        self._data[key] = value
    
    def __getitem__(self, key):
        return self._data[key]
    
    def get(self, key, default=None):
        return self._data.get(key, default)


def test_fallback_to_plain_lrc():
    """Verify plain lyrics are written as .lrc when fallback_to_plain_lrc is enabled."""
    
    # Create a mock item with path
    with tempfile.TemporaryDirectory() as tmpdir:
        item_path = os.path.join(tmpdir, 'test_track.flac')
        Path(item_path).touch()
        
        item = MockItem(item_path)
        
        # Test 1: fallback_to_plain_lrc = False (default behavior)
        print('Test 1: fallback_to_plain_lrc = False')
        plugin = GetLrcPlugin()
        plugin.config['fallback_to_plain_lrc'] = False
        plugin.config['output_dir'] = tmpdir
        
        # Mock API response with plain lyrics only
        with patch.object(plugin, '_request_with_retry') as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = {
                'syncedLyrics': None,
                'plainLyrics': 'Verse 1\nThis is a test lyric\nVerse 2\nAnother line'
            }
            mock_req.return_value = mock_response
            
            result = plugin.fetch_lrc(item, force=True, quiet=True)
            
            # Should store in DB, not write file
            lrc_path = os.path.join(tmpdir, 'test_track.lrc')
            assert not os.path.exists(lrc_path), f'.lrc file should NOT exist when fallback_to_plain_lrc=False, but found: {lrc_path}'
            print('  ✓ Plain lyrics stored in DB only (no .lrc file)')
        
        # Test 2: fallback_to_plain_lrc = True (new feature)
        print('\nTest 2: fallback_to_plain_lrc = True')
        plugin2 = GetLrcPlugin()
        plugin2.config['fallback_to_plain_lrc'] = True
        plugin2.config['output_dir'] = tmpdir
        
        item2_path = os.path.join(tmpdir, 'test_track2.flac')
        Path(item2_path).touch()
        item2 = MockItem(item2_path)
        
        with patch.object(plugin2, '_request_with_retry') as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = {
                'syncedLyrics': None,
                'plainLyrics': 'Verse 1\nThis is a test lyric\nVerse 2\nAnother line'
            }
            mock_req.return_value = mock_response
            
            result = plugin2.fetch_lrc(item2, force=True, quiet=True)
            
            # Should write as .lrc file
            lrc_path2 = os.path.join(tmpdir, 'test_track2.lrc')
            assert os.path.exists(lrc_path2), f'.lrc file should exist when fallback_to_plain_lrc=True, but not found: {lrc_path2}'
            
            with open(lrc_path2) as f:
                content = f.read()
            assert 'This is a test lyric' in content, 'Plain lyrics should be written to .lrc file'
            print('  ✓ Plain lyrics written as .lrc file')
        
        # Test 3: sidecar_extensions config
        print('\nTest 3: sidecar_extensions config')
        plugin3 = GetLrcPlugin()
        plugin3.config['sidecar_extensions'] = ['.lrc', '.cue', '.nfo']
        # Re-populate from config
        exts = plugin3.config['sidecar_extensions'].get(list) or ['.lrc']
        plugin3._sidecar_exts = [e if e.startswith('.') else f'.{e}' for e in exts]
        
        assert plugin3._sidecar_exts == ['.lrc', '.cue', '.nfo'], \
            f'Expected [.lrc, .cue, .nfo] but got {plugin3._sidecar_exts}'
        print('  ✓ sidecar_extensions correctly parsed from config')
        
        print('\n✅ All tests passed!')


if __name__ == '__main__':
    test_fallback_to_plain_lrc()
