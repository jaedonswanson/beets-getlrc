#!/usr/bin/env python3
"""
Simplified bug check for beets-getlrc plugin.
"""

import sys
import tempfile
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from beetsplug.getlrc.plugin import GetLrcPlugin, Progress, Stats


def test_progress_increment():
    """Test progress counter under concurrent load."""
    print("\n" + "="*60)
    print("TEST 1: Progress Increment Under Concurrent Load")
    print("="*60)
    
    for worker_count in [1, 2, 4, 8]:
        progress = Progress(100, use_color=False, enabled=True)
        
        def work():
            for _ in range(10):
                progress.increment()
        
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            list(executor.map(lambda _: work(), range(10)))
        
        expected = 100
        actual = progress.current
        status = "✓ PASS" if actual == expected else f"✗ FAIL ({actual}/{expected})"
        print(f"  Workers {worker_count}: {status}")


def test_sidecar_movement():
    """Test sidecar file movement logic."""
    print("\n" + "="*60)
    print("TEST 2: Sidecar File Movement with item_moved()")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        old_dir = tmpdir / "old"
        new_dir = tmpdir / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        
        # Create test files
        old_file = old_dir / "song.flac"
        new_file = new_dir / "song.flac"
        old_lrc = old_dir / "song.lrc"
        new_lrc = new_dir / "song.lrc"
        
        old_file.write_text("audio")
        old_lrc.write_text("[00:00] Lyrics")
        
        plugin = GetLrcPlugin()
        mock_item = Mock()
        mock_item.path = str(new_file).encode('utf-8')
        
        try:
            # Test 1: String paths
            plugin.item_moved(mock_item, str(old_file), str(new_file))
            status1 = "✓ PASS" if new_lrc.exists() else "✗ FAIL (LRC not moved)"
            print(f"  String paths: {status1}")
        except Exception as e:
            print(f"  String paths: ✗ FAIL ({e})")
        
        # Clean up for second test
        if new_lrc.exists():
            new_lrc.unlink()
        old_lrc.write_text("[00:00] Lyrics")
        
        try:
            # Test 2: Bytes paths (how beets actually calls it)
            plugin.item_moved(mock_item, str(old_file).encode('utf-8'), str(new_file).encode('utf-8'))
            status2 = "✓ PASS" if new_lrc.exists() else "✗ FAIL (LRC not moved)"
            print(f"  Bytes paths: {status2}")
        except Exception as e:
            print(f"  Bytes paths: ✗ FAIL ({e})")


def test_album_movement():
    """Test album directory sidecar movement."""
    print("\n" + "="*60)
    print("TEST 3: Album Moved Directory Traversal")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        old_base = tmpdir / "old_album"
        new_base = tmpdir / "new_album"
        
        # Create nested structure
        (old_base / "artist" / "sub").mkdir(parents=True)
        (new_base / "artist" / "sub").mkdir(parents=True)
        
        # Create audio and LRC files at multiple levels
        files_to_create = [
            "artist/01.flac",
            "artist/02.flac",
            "artist/sub/03.flac",
        ]
        
        for rel_path in files_to_create:
            audio_file = old_base / rel_path
            audio_file.parent.mkdir(parents=True, exist_ok=True)
            audio_file.write_text("audio")
            (audio_file.with_suffix('.lrc')).write_text("[00:00] Lyrics")
        
        plugin = GetLrcPlugin()
        mock_album = Mock()
        
        try:
            plugin.album_moved(mock_album, old_base, new_base)
            
            lrc_files = list(new_base.rglob("*.lrc"))
            expected = len(files_to_create)
            status = "✓ PASS" if len(lrc_files) == expected else f"✗ FAIL ({len(lrc_files)}/{expected})"
            print(f"  LRC files moved: {status}")
        except Exception as e:
            print(f"  Album moved: ✗ FAIL ({e})")


def test_progress_formatting():
    """Test progress bar formatting with colors."""
    print("\n" + "="*60)
    print("TEST 4: Progress Bar Formatting")
    print("="*60)
    
    test_cases = [
        (100, True, True, "With color, enabled"),
        (100, False, True, "No color, enabled"),
        (1, True, True, "Single item"),
        (1000, True, True, "Large batch"),
    ]
    
    for total, use_color, enabled, desc in test_cases:
        try:
            progress = Progress(total, use_color=use_color, enabled=enabled)
            prefix = progress.prefix()
            
            # Check for reasonable format
            has_bracket = '[' in prefix and ']' in prefix
            has_percent = '%' in prefix
            
            status = "✓ PASS" if (has_bracket or not enabled) else "✗ FAIL"
            print(f"  {desc:25s}: {status}")
        except Exception as e:
            print(f"  {desc:25s}: ✗ FAIL ({type(e).__name__})")


def test_stats_thread_safety():
    """Test Stats counter thread safety."""
    print("\n" + "="*60)
    print("TEST 5: Stats Counter Thread Safety")
    print("="*60)
    
    stats = Stats()
    
    def increment_all():
        for field in ['created', 'plain', 'skipped', 'not_found', 'no_synced', 'errors']:
            for _ in range(10):
                stats.add(field)
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: increment_all(), range(4)))
    
    total = stats.total
    expected = 6 * 10 * 4  # 6 fields, 10 increments each, 4 threads
    status = "✓ PASS" if total == expected else f"✗ FAIL ({total}/{expected})"
    print(f"  Total increments: {status}")


def test_import_queue_handling():
    """Test import queue behavior."""
    print("\n" + "="*60)
    print("TEST 6: Import Queue Handling")
    print("="*60)
    
    plugin = GetLrcPlugin()
    mock_items = [Mock(title=f"Track {i}") for i in range(5)]
    
    try:
        # Simulate item_imported calls
        for item in mock_items:
            plugin.item_imported(None, item)
        
        # Check queue
        queued_count = len(plugin._import_queue)
        expected = len(mock_items)
        status = "✓ PASS" if queued_count == expected else f"✗ FAIL ({queued_count}/{expected})"
        print(f"  Items queued: {status}")
    except Exception as e:
        print(f"  Import queue: ✗ FAIL ({e})")


def main():
    print("\n" + "╔" + "="*58 + "╗")
    print("║" + "BEETS-GETLRC BUG CHECK REPORT".center(58) + "║")
    print("╚" + "="*58 + "╝")
    
    tests = [
        test_progress_increment,
        test_sidecar_movement,
        test_album_movement,
        test_progress_formatting,
        test_stats_thread_safety,
        test_import_queue_handling,
    ]
    
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"\n✗ Test {test.__name__} crashed: {e}")
    
    print("\n" + "="*60)
    print("Bug Check Complete - See details above")
    print("="*60 + "\n")


if __name__ == '__main__':
    main()
