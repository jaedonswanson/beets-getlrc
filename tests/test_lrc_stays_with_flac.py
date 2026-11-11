#!/usr/bin/env python3
"""
Test: LRC files staying with FLAC files during beet move operations.

This test simulates the most common user scenario:
1. Import music with auto-fetch enabled
2. LRC files are created alongside FLAC files
3. User runs 'beet move' to organize library
4. LRC files follow the FLAC files to new location
"""

import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from beetsplug.getlrc.plugin import GetLrcPlugin


def test_lrc_stays_with_flac_single_track():
    """Test that .lrc stays with .flac when moving a single track."""
    print("\n" + "="*70)
    print("TEST 1: Single Track Move - LRC Follows FLAC")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Simulate old and new locations
        old_dir = tmpdir / "Music" / "unsorted"
        new_dir = tmpdir / "Music" / "library" / "Artist" / "Album"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        
        # Create FLAC file with LRC sidecar
        old_flac = old_dir / "track01.flac"
        old_lrc = old_dir / "track01.lrc"
        old_flac.write_text("FLAC audio data...")
        old_lrc.write_text("[00:00.00]Verse 1\n[00:10.00]Chorus")
        
        # New locations after move
        new_flac = new_dir / "track01.flac"
        new_lrc = new_dir / "track01.lrc"
        
        # Copy FLAC to new location (beet move does this)
        shutil.copy(str(old_flac), str(new_flac))
        
        print(f"\nSetup:")
        print(f"  Old location: {old_dir.name}/")
        print(f"    - {old_flac.name} ({old_flac.stat().st_size} bytes)")
        print(f"    - {old_lrc.name} ({old_lrc.stat().st_size} bytes)")
        print(f"  New location: {new_dir}")
        print(f"    - FLAC copied (simulating beet move)")
        
        # Simulate item_moved event
        plugin = GetLrcPlugin()
        mock_item = Mock()
        mock_item.title = "Track 01"
        mock_item.artist = "Artist"
        mock_item.album = "Album"
        
        print(f"\nPerforming item_moved event...")
        plugin.item_moved(mock_item, str(old_flac), str(new_flac))
        
        # Verify both files moved
        flac_moved = new_flac.exists()
        lrc_moved = new_lrc.exists()
        old_lrc_gone = not old_lrc.exists()
        
        print(f"\nResults:")
        print(f"  ✓ FLAC file moved" if flac_moved else f"  ✗ FLAC file NOT moved")
        print(f"  ✓ LRC sidecar moved" if lrc_moved else f"  ✗ LRC sidecar NOT moved")
        print(f"  ✓ Old LRC removed" if old_lrc_gone else f"  ✗ Old LRC still exists")
        
        if lrc_moved and old_lrc_gone:
            lrc_content = new_lrc.read_text()
            print(f"  ✓ LRC content preserved ({len(lrc_content)} bytes)")
        
        success = flac_moved and lrc_moved and old_lrc_gone
        print(f"\nStatus: {'✓ PASS' if success else '✗ FAIL'}\n")
        assert success


def test_lrc_stays_with_flac_album_move():
    """Test that .lrc files stay with .flac when moving entire album."""
    print("\n" + "="*70)
    print("TEST 2: Album Move - LRC Files Follow FLAC Files")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Old album directory structure
        old_album = tmpdir / "staging" / "New Album"
        new_album = tmpdir / "library" / "Artist" / "New Album"
        old_album.mkdir(parents=True)
        new_album.mkdir(parents=True)
        
        # Create multiple tracks with LRCs
        tracks = [
            ("01 - Opening.flac", "[00:00]Opening theme\n[01:30]Verse"),
            ("02 - Main.flac", "[00:00]Main track\n[02:00]Chorus"),
            ("03 - Outro.flac", "[00:00]Outro music\n[01:00]Fade"),
        ]
        
        print(f"\nSetup:")
        print(f"  Old album: {old_album}")
        for flac_name, _ in tracks:
            flac_file = old_album / flac_name
            lrc_file = old_album / flac_name.replace(".flac", ".lrc")
            flac_file.write_text("FLAC data")
            lrc_file.write_text(_)
            print(f"    - {flac_name}")
            print(f"    - {flac_name.replace('.flac', '.lrc')}")
        print(f"  New album: {new_album}")
        
        # Copy all FLACs to new location (simulating beet move of audio files)
        for flac_name, _ in tracks:
            old_path = old_album / flac_name
            new_path = new_album / flac_name
            shutil.copy(str(old_path), str(new_path))
        
        print(f"  [All FLACs copied to new location]")
        
        # Simulate album_moved event
        plugin = GetLrcPlugin()
        mock_album = Mock()
        
        print(f"\nPerforming album move...")
        plugin.album_moved(mock_album, str(old_album), str(new_album))
        
        # Check results
        flac_count_old = len(list(old_album.glob("*.flac")))
        flac_count_new = len(list(new_album.glob("*.flac")))
        lrc_count_new = len(list(new_album.glob("*.lrc")))
        expected_count = len(tracks)
        
        print(f"\nResults:")
        print(f"  FLACs in old location: {flac_count_old}/{expected_count} (should remain)")
        print(f"  FLACs in new location: {flac_count_new}/{expected_count} (moved by beet)")
        print(f"  LRCs in new location:  {lrc_count_new}/{expected_count} (moved by plugin)")
        
        for flac_name, _ in tracks:
            lrc_name = flac_name.replace(".flac", ".lrc")
            exists = (new_album / lrc_name).exists()
            status = "✓" if exists else "✗"
            print(f"  {status} {lrc_name}")
        
        success = flac_count_new == expected_count and lrc_count_new == expected_count
        print(f"\nStatus: {'✓ PASS' if success else '✗ FAIL'}\n")
        assert success


def test_nested_album_structure():
    """Test LRC movement with nested directories (artist/album structure)."""
    print("\n" + "="*70)
    print("TEST 3: Nested Structure - LRC Follows Complex Directory Layout")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Old structure: staging/artist/album/
        old_root = tmpdir / "staging"
        new_root = tmpdir / "library"
        
        old_album = old_root / "The Beatles" / "Abbey Road"
        new_album = new_root / "The Beatles" / "Abbey Road"
        old_album.mkdir(parents=True)
        new_album.mkdir(parents=True)
        
        # Create nested track (in subdirectory if supported)
        tracks = [
            ("01 - Come Together.flac", "[00:00]Come together\n[01:00]Right now"),
            ("02 - Something.flac", "[00:00]Something\n[02:00]Bridge"),
        ]
        
        print(f"\nSetup:")
        print(f"  Old: {old_album}/")
        
        for flac_name, lrc_content in tracks:
            flac_file = old_album / flac_name
            lrc_file = old_album / flac_name.replace(".flac", ".lrc")
            flac_file.write_text("FLAC")
            lrc_file.write_text(lrc_content)
            print(f"    {flac_name} + .lrc")
        
        print(f"  New: {new_album}/")
        
        # Move album
        plugin = GetLrcPlugin()
        mock_album = Mock()
        
        print(f"\nPerforming nested album move...")
        plugin.album_moved(mock_album, str(old_album), str(new_album))
        
        # Verify
        lrc_files = list(new_album.glob("*.lrc"))
        print(f"\nResults:")
        print(f"  LRC files in new location: {len(lrc_files)}/{len(tracks)}")
        
        for lrc_file in lrc_files:
            content = lrc_file.read_text()
            lines = content.count('\n')
            print(f"  ✓ {lrc_file.name} ({lines} lines)")
        
        success = len(lrc_files) == len(tracks)
        print(f"\nStatus: {'✓ PASS' if success else '✗ FAIL'}\n")
        assert success


def test_lrc_with_unicode_filenames():
    """Test LRC movement with unicode/special characters in filenames."""
    print("\n" + "="*70)
    print("TEST 4: Unicode Filenames - LRC Follows Non-ASCII Characters")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        old_dir = tmpdir / "old"
        new_dir = tmpdir / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        
        # Unicode filename
        track_name = "アニメ-オープニング (Opening Theme).flac"
        old_flac = old_dir / track_name
        old_lrc = old_dir / track_name.replace(".flac", ".lrc")
        new_flac = new_dir / track_name
        new_lrc = new_dir / track_name.replace(".flac", ".lrc")
        
        old_flac.write_text("FLAC")
        old_lrc.write_text("[00:00]テーマ曲\n[00:30]サビ")
        
        print(f"\nSetup:")
        print(f"  Filename with unicode: {track_name}")
        
        # Move with unicode
        plugin = GetLrcPlugin()
        mock_item = Mock()
        mock_item.title = track_name.split("-")[0]
        mock_item.artist = "Anime"
        mock_item.album = "OST"
        
        print(f"Moving file with unicode characters...")
        plugin.item_moved(mock_item, str(old_flac), str(new_flac))
        
        # Verify
        success = new_lrc.exists() and not old_lrc.exists()
        print(f"\nResults:")
        print(f"  {'✓ PASS' if success else '✗ FAIL'} - Unicode filename handled correctly")

        if new_lrc.exists():
            print(f"  ✓ LRC exists in new location")
            content = new_lrc.read_text()
            print(f"  ✓ Content preserved ({len(content)} bytes)")

        print()
        assert success


def main():
    print("\n" + "╔" + "="*68 + "╗")
    print("║" + "LRC STAYS WITH FLAC: COMPREHENSIVE MOVEMENT TESTS".center(68) + "║")
    print("╚" + "="*68 + "╝")
    
    tests = [
        ("Single Track Move", test_lrc_stays_with_flac_single_track),
        ("Album Move", test_lrc_stays_with_flac_album_move),
        ("Nested Structure", test_nested_album_structure),
        ("Unicode Filenames", test_lrc_with_unicode_filenames),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            test_func()
            results.append((name, True))
        except Exception as e:
            print(f"✗ {name} crashed: {e}\n")
            results.append((name, False))
    
    # Summary
    print("="*70)
    print("SUMMARY: LRC SIDECAR MOVEMENT")
    print("="*70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {name:30s}: {status}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ LRC FILES CORRECTLY STAY WITH FLAC FILES DURING MOVES")
    else:
        print(f"\n✗ {total - passed} tests failed")
    
    print("="*70 + "\n")


if __name__ == '__main__':
    main()
