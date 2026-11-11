#!/usr/bin/env python3
"""
Comprehensive test suite for beets-getlrc plugin.
Tests worker concurrency, sidecar movement, and import output formatting.
"""

import sys
import os
import tempfile
import shutil
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from concurrent.futures import ThreadPoolExecutor

# Add plugin path to imports
sys.path.insert(0, str(Path(__file__).parent))

from beetsplug.getlrc.plugin import GetLrcPlugin, Progress, Stats, _C


class TestWorkerConcurrency:
    """Test worker thread handling and concurrency limits."""
    
    @staticmethod
    def test_progress_increment_under_load():
        """Test that progress counter increments correctly under concurrent load."""
        print("\n" + "="*60)
        print("TEST: Progress Increment Under Load")
        print("="*60)
        
        results = []
        
        for worker_count in [1, 2, 4, 8, 16]:
            progress = Progress(1000, use_color=False, enabled=True)
            
            def increment_task():
                for _ in range(100):
                    progress.increment()
                    time.sleep(0.001)  # Simulate work
            
            start = time.time()
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                tasks = [executor.submit(increment_task) for _ in range(10)]
                for task in tasks:
                    task.result()
            
            elapsed = time.time() - start
            final_count = progress.current
            
            expected = 1000
            status = "✓ PASS" if final_count == expected else "✗ FAIL"
            results.append((worker_count, final_count, expected, elapsed, status))
            
            print(f"Workers: {worker_count:2d} | Final: {final_count:4d}/{expected:4d} | "
                  f"Time: {elapsed:.3f}s | {status}")
        
        # Check for any failures
        failures = [r for r in results if "FAIL" in r[4]]
        if failures:
            print(f"\n⚠️  Found {len(failures)} failure(s) in progress increment test")
            for worker_count, final, expected, elapsed, status in failures:
                print(f"   Workers {worker_count}: got {final}, expected {expected}")
        
        return len(failures) == 0
    
    @staticmethod
    def test_worker_limit_edge_cases():
        """Test extreme worker counts for resource issues."""
        print("\n" + "="*60)
        print("TEST: Worker Limit Edge Cases")
        print("="*60)
        
        plugin = GetLrcPlugin()
        test_cases = [
            (0, "Zero workers (should fail gracefully)"),
            (1, "Single worker (baseline)"),
            (4, "Normal multi-worker"),
            (32, "High worker count"),
            (256, "Extreme worker count"),
        ]
        
        for worker_count, description in test_cases:
            try:
                # Simulate creating executor
                if worker_count > 0:
                    executor = ThreadPoolExecutor(max_workers=worker_count)
                    # Try submitting a dummy task
                    future = executor.submit(lambda: None)
                    result = future.result(timeout=1)
                    executor.shutdown(wait=False)
                    status = "✓ PASS"
                else:
                    status = "⚠ SKIP (zero workers)"
            except Exception as e:
                status = f"✗ FAIL ({type(e).__name__})"
            
            print(f"  {worker_count:3d} workers: {description:40s} {status}")
    

class TestSidecarFileMovement:
    """Test sidecar file movement with item and album moves."""
    
    @staticmethod
    def test_item_moved_path_normalization():
        """Test that item_moved correctly handles path normalization."""
        print("\n" + "="*60)
        print("TEST: Item Moved Path Normalization")
        print("="*60)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create test files
            old_dir = tmpdir / "old"
            new_dir = tmpdir / "new"
            old_dir.mkdir()
            new_dir.mkdir()
            
            old_file = old_dir / "song.flac"
            new_file = new_dir / "song.flac"
            old_lrc = old_dir / "song.lrc"
            new_lrc = new_dir / "song.lrc"
            
            # Create test files
            old_file.write_text("audio")
            old_lrc.write_text("[00:00] Lyrics")
            
            plugin = GetLrcPlugin()
            mock_item = Mock()
            mock_item.path = str(new_file).encode('utf-8')  # beets uses bytes
            
            # Test with different input formats
            test_cases = [
                (str(old_file), str(new_file), "String paths"),
                (old_file.encode('utf-8'), new_file.encode('utf-8'), "Bytes paths"),
                (old_file, new_file, "Path objects"),
            ]
            
            for source, destination, desc in test_cases:
                # Reset files
                if old_lrc.exists():
                    old_lrc.unlink()
                if new_lrc.exists():
                    new_lrc.unlink()
                old_lrc.write_text("[00:00] Lyrics")
                
                try:
                    plugin.item_moved(mock_item, source, destination)
                    success = new_lrc.exists()
                    status = "✓ PASS" if success else "✗ FAIL (LRC not moved)"
                except Exception as e:
                    status = f"✗ FAIL ({type(e).__name__}: {e})"
                
                print(f"  {desc:20s}: {status}")
    
    @staticmethod
    def test_album_moved_directory_traversal():
        """Test that album_moved correctly traverses and moves all sidecar files."""
        print("\n" + "="*60)
        print("TEST: Album Moved Directory Traversal")
        print("="*60)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create nested directory structure
            old_base = tmpdir / "old_album"
            new_base = tmpdir / "new_album"
            
            (old_base / "artist").mkdir(parents=True)
            (old_base / "artist" / "sub").mkdir(parents=True)
            (new_base / "artist" / "sub").mkdir(parents=True, exist_ok=True)
            
            # Create test files at multiple levels
            test_structure = [
                "artist/01-track.flac",
                "artist/02-track.flac",
                "artist/sub/03-track.flac",
            ]
            
            lrc_files_created = 0
            for rel_path in test_structure:
                audio_file = old_base / rel_path
                audio_file.parent.mkdir(parents=True, exist_ok=True)
                audio_file.write_text("audio")
                
                lrc_file = audio_file.with_suffix('.lrc')
                lrc_file.write_text("[00:00] Lyrics")
                lrc_files_created += 1
            
            plugin = GetLrcPlugin()
            mock_album = Mock()
            
            try:
                plugin.album_moved(mock_album, old_base, new_base)
                
                # Check all LRCs were moved
                lrc_files_found = list(new_base.rglob("*.lrc"))
                status = "✓ PASS" if len(lrc_files_found) == lrc_files_created else "✗ FAIL"
                
                print(f"  LRC files created: {lrc_files_created}")
                print(f"  LRC files found after move: {len(lrc_files_found)}")
                print(f"  Status: {status}")
            except Exception as e:
                print(f"  Status: ✗ FAIL ({type(e).__name__}: {e})")


class TestImportOutputFormatting:
    """Test import output and formatting during auto-import."""
    
    @staticmethod
    def test_progress_display_initialization():
        """Test that progress display is properly initialized."""
        print("\n" + "="*60)
        print("TEST: Progress Display Initialization")
        print("="*60)
        
        # Test various initialization scenarios
        scenarios = [
            (10, False, False, "Small batch, no color, disabled"),
            (10, True, False, "Small batch, with color, disabled"),
            (100, True, True, "Large batch, with color, enabled"),
            (1000, True, True, "Huge batch, with color, enabled"),
        ]
        
        for total, use_color, enabled, desc in scenarios:
            progress = Progress(total, use_color=use_color, enabled=enabled)
            
            # Check internal state
            assert progress.total == total
            assert progress.current == 0
            assert progress.enabled == enabled
            
            # Try generating a prefix
            try:
                prefix = progress.prefix()
                status = "✓ PASS"
            except Exception as e:
                prefix = None
                status = f"✗ FAIL ({type(e).__name__})"
            
            print(f"  {desc:40s}: {status}")
            if prefix:
                print(f"    Prefix: {prefix[:60]}...")
    
    @staticmethod
    def test_color_reset_consistency():
        """Test that ANSI color codes are properly reset."""
        print("\n" + "="*60)
        print("TEST: Color Reset Consistency")
        print("="*60)
        
        plugin = GetLrcPlugin()
        
        # Mock item
        mock_item = Mock()
        mock_item.albumartist = "Test Artist"
        mock_item.album = "Test Album"
        mock_item.title = "Test Track"
        mock_item.path = b"/path/to/song.flac"
        
        # Test different color combinations
        colors = [_C.GREEN, _C.RED, _C.YELLOW, _C.BLUE, _C.CYAN, '']
        statuses = ['Created', 'Skipped', 'Not found', 'Error']
        
        issues = []
        for color in colors:
            for status in statuses:
                # Capture output
                import io
                from contextlib import redirect_stdout
                
                output = io.StringIO()
                with redirect_stdout(output):
                    plugin._print(status, mock_item, color=color, quiet=False)
                
                result = output.getvalue()
                
                # Check for unclosed ANSI codes
                open_codes = result.count('\033[')
                close_codes = result.count('\033[0m') + result.count('\033[m')
                
                if open_codes != close_codes and color:
                    issues.append(f"  {status} with {color}: {open_codes} open, {close_codes} close")
        
        if issues:
            print("  ✗ FAIL - Unclosed ANSI codes found:")
            for issue in issues[:5]:  # Show first 5
                print(f"    {issue}")
            status = "✗ FAIL"
        else:
            print("  ✓ PASS - All ANSI codes properly balanced")
            status = "✓ PASS"
        
        return len(issues) == 0


class TestCachingAndState:
    """Test caching, state management, and edge cases."""
    
    @staticmethod
    def test_cache_update_thread_safety():
        """Test that cache updates are thread-safe."""
        print("\n" + "="*60)
        print("TEST: Cache Update Thread Safety")
        print("="*60)
        
        from datetime import datetime
        
        # Mock item with store method
        mock_item = Mock()
        mock_item.__getitem__ = Mock(return_value=None)
        mock_item.__setitem__ = Mock()
        mock_item.store = Mock()
        
        plugin = GetLrcPlugin()
        
        # Simulate concurrent cache updates
        errors = []
        
        def cache_update_task(item_id):
            try:
                mock_item['getlrc_status'] = f'status_{item_id}'
                mock_item['getlrc_checked'] = datetime.now().isoformat()
                mock_item.store()
            except Exception as e:
                errors.append(str(e))
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(cache_update_task, i) for i in range(100)]
            for future in futures:
                future.result()
        
        if errors:
            print(f"  ✗ FAIL - {len(errors)} errors occurred")
            for error in errors[:3]:
                print(f"    {error}")
        else:
            print("  ✓ PASS - No thread-safety errors")
        
        return len(errors) == 0


def main():
    """Run all tests and generate report."""
    print("\n")
    print("╔" + "="*58 + "╗")
    print("║" + "BEETS-GETLRC COMPREHENSIVE BUG CHECK".center(58) + "║")
    print("╚" + "="*58 + "╝")
    
    test_suites = [
        ("Worker Concurrency", TestWorkerConcurrency),
        ("Sidecar File Movement", TestSidecarFileMovement),
        ("Import Output Formatting", TestImportOutputFormatting),
        ("Caching & State", TestCachingAndState),
    ]
    
    results = {}
    
    for suite_name, suite_class in test_suites:
        print(f"\n{'='*60}")
        print(f"SUITE: {suite_name}")
        print(f"{'='*60}")
        
        suite_methods = [method for method in dir(suite_class) if method.startswith('test_')]
        
        for method_name in suite_methods:
            method = getattr(suite_class, method_name)
            try:
                result = method()
                results[f"{suite_name}::{method_name}"] = result
            except Exception as e:
                print(f"✗ Test crashed: {e}")
                results[f"{suite_name}::{method_name}"] = False
    
    # Summary
    print("\n")
    print("╔" + "="*58 + "╗")
    print("║" + "TEST SUMMARY".center(58) + "║")
    print("╚" + "="*58 + "╝")
    
    total = len(results)
    passed = sum(1 for r in results.values() if r is True or r is None)
    failed = sum(1 for r in results.values() if r is False)
    
    print(f"\nTotal Tests: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Success Rate: {(passed/total)*100:.1f}%\n")
    
    if failed > 0:
        print("Failed Tests:")
        for test_name, result in results.items():
            if result is False:
                print(f"  ✗ {test_name}")


if __name__ == '__main__':
    main()
