#!/usr/bin/env python3
"""
Advanced verification tests for beets-getlrc bug fixes.
Tests the specific improvements made:
- Worker count validation and bounds
- Import progress display
- Path normalization improvements
- Error tracking in stats
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from beetsplug.getlrc.plugin import GetLrcPlugin, Progress, Stats


def test_worker_validation():
    """Test the new _validate_and_constrain_workers method."""
    print("\n" + "="*60)
    print("TEST: Worker Count Validation & Bounds")
    print("="*60)
    
    plugin = GetLrcPlugin()
    
    test_cases = [
        (0, 1, "Zero becomes minimum (1)"),
        (1, 1, "One stays one"),
        (4, 4, "Four stays four"),
        (32, 32, "32 stays 32 (under limit)"),
        (64, 64, "64 is max allowed"),
        (65, 64, "65 clamped to 64"),
        (128, 64, "128 clamped to 64"),
        (1000, 64, "1000 clamped to 64"),
    ]
    
    all_pass = True
    for input_workers, expected, description in test_cases:
        result = plugin._validate_and_constrain_workers(input_workers)
        status = "✓ PASS" if result == expected else f"✗ FAIL (got {result})"
        if result != expected:
            all_pass = False
        print(f"  {description:35s}: {status}")
    
    return all_pass


def test_import_task_progress():
    """Test that item_imported/album_imported trigger fetches."""
    print("\n" + "="*60)
    print("TEST: Import Event Fetching")
    print("="*60)
    
    plugin = GetLrcPlugin()
    plugin.config.set({'auto': True, 'quiet_import': True})
    
    mock_item = Mock()
    mock_item.title = "Track 1"
    mock_item.path = "/music/track1.flac"
    mock_item.album = "Test Album"
    mock_item.artist = "Test Artist"
    mock_item.length = 180
    mock_item.lyrics = None
    mock_item.get = Mock(return_value=None)
    mock_item.__setitem__ = Mock()
    mock_item.store = Mock()

    with patch.object(plugin, 'fetch_lrc') as mock_fetch:
        mock_fetch.return_value = None
        plugin.item_imported(None, mock_item)

        album = Mock()
        album.items = Mock(return_value=[mock_item, mock_item])
        plugin.album_imported(None, album)

        call_count = mock_fetch.call_count
        status1 = "✓ PASS" if call_count == 3 else f"✗ FAIL (called {call_count}x, expected 3x)"
        print(f"  item_imported + album_imported triggered fetches: {status1}")
        return call_count == 3


def test_import_task_quiet_mode():
    """Test that import-time fetch is quiet when configured."""
    print("\n" + "="*60)
    print("TEST: Import Quiet Mode")
    print("="*60)

    plugin = GetLrcPlugin()
    plugin.config.set({'auto': True, 'quiet_import': True})

    mock_item = Mock()
    mock_item.title = "Track 2"
    mock_item.path = "/music/track2.flac"
    mock_item.album = "Test Album"
    mock_item.artist = "Test Artist"
    mock_item.length = 180
    mock_item.lyrics = None
    mock_item.get = Mock(return_value=None)
    mock_item.__setitem__ = Mock()
    mock_item.store = Mock()

    with patch.object(plugin, 'fetch_lrc') as mock_fetch:
        mock_fetch.return_value = None
        plugin.item_imported(None, mock_item)

        call_count = mock_fetch.call_count
        quiet_args = all(call.kwargs.get('quiet') is True for call in mock_fetch.call_args_list)
        status = "✓ PASS" if call_count == 1 and quiet_args else f"✗ FAIL (calls={call_count}, quiet={quiet_args})"
        print(f"  item_imported called fetch quietly: {status}")
        return call_count == 1 and quiet_args


def test_path_normalization_safety():
    """Test improved None checks in item_moved."""
    print("\n" + "="*60)
    print("TEST: Path Normalization Safety Checks")
    print("="*60)
    
    plugin = GetLrcPlugin()
    mock_item = Mock()
    
    # Test cases: (source, destination, expected_early_return, description)
    test_cases = [
        (None, "/path/to/new", True, "None source"),
        ("/path/to/old", None, True, "None destination"),
        (None, None, True, "Both None"),
    ]
    
    all_pass = True
    for source, dest, should_return_early, description in test_cases:
        # Mock the logger to catch errors
        with patch.object(plugin, '_log') as mock_log:
            try:
                plugin.item_moved(mock_item, source, dest)
                
                # Check if error was logged (early return)
                error_logged = mock_log.error.called
                status = "✓ PASS" if error_logged == should_return_early else "✗ FAIL"
                if error_logged != should_return_early:
                    all_pass = False
                print(f"  {description:25s}: {status}")
            except Exception as e:
                print(f"  {description:25s}: ✗ FAIL ({type(e).__name__})")
                all_pass = False
    
    return all_pass


def test_stats_error_tracking():
    """Test that errors are properly counted in stats."""
    print("\n" + "="*60)
    print("TEST: Error Tracking in Stats")
    print("="*60)
    
    stats = Stats()
    
    # Simulate various outcomes
    outcomes = [
        ('created', 5),
        ('not_found', 3),
        ('errors', 2),
        ('skipped', 4),
    ]
    
    for field, count in outcomes:
        for _ in range(count):
            stats.add(field)
    
    total = stats.total
    error_count = stats.errors
    expected_total = sum(count for _, count in outcomes)
    
    status1 = "✓ PASS" if total == expected_total else f"✗ FAIL (got {total}, expected {expected_total})"
    status2 = "✓ PASS" if error_count == 2 else f"✗ FAIL (got {error_count}, expected 2)"
    
    print(f"  Total stats count: {status1}")
    print(f"  Error count: {status2}")
    
    return total == expected_total and error_count == 2


def test_info_level_logging():
    """Test that sidecar moves are logged at INFO level (not debug)."""
    print("\n" + "="*60)
    print("TEST: Sidecar Move Logging Level")
    print("="*60)
    
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        old_dir = tmpdir / "old"
        new_dir = tmpdir / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        
        old_file = old_dir / "song.flac"
        new_file = new_dir / "song.flac"
        old_lrc = old_dir / "song.lrc"
        
        old_file.write_text("audio")
        old_lrc.write_text("[00:00] Lyrics")
        
        plugin = GetLrcPlugin()
        mock_item = Mock()
        mock_item.title = "Test"
        mock_item.artist = "Artist"
        mock_item.album = "Album"
        
        with patch.object(plugin, '_log') as mock_log:
            plugin.item_moved(mock_item, str(old_file), str(new_file))
            
            # Check that info() was called (not debug())
            info_called = mock_log.info.called
            debug_called = mock_log.debug.called
            
            status = "✓ PASS" if info_called and not debug_called else "✗ FAIL"
            print(f"  Logged at INFO level: {status}")
            
            return info_called and not debug_called


def main():
    print("\n" + "╔" + "="*58 + "╗")
    print("║" + "BEETS-GETLRC: BUG FIX VERIFICATION TESTS".center(58) + "║")
    print("╚" + "="*58 + "╝")
    
    tests = [
        ("Worker Validation", test_worker_validation),
        ("Import Progress Display", test_import_task_progress),
        ("Import Quiet Mode", test_import_task_quiet_mode),
        ("Path Normalization Safety", test_path_normalization_safety),
        ("Stats Error Tracking", test_stats_error_tracking),
        ("Info Level Logging", test_info_level_logging),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ {name} crashed: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "="*60)
    print("VERIFICATION SUMMARY")
    print("="*60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {name:35s}: {status}")
    
    print(f"\n  Total: {passed}/{total} tests passed")
    print(f"  Success Rate: {(passed/total)*100:.1f}%")
    print("\n" + "="*60 + "\n")


if __name__ == '__main__':
    main()
