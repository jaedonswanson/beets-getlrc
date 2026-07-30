import tempfile
import shutil
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from beets.util import bytestring_path
from beetsplug.getlrc import GetLrcPlugin


def test_get_lrc_path_uses_audio_dir_for_absolute_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        audio = tmp / "sub" / "track01.flac"
        audio.parent.mkdir(parents=True)
        audio.write_text("audio")

        item = SimpleNamespace()
        item.path = bytestring_path(audio)

        plugin = GetLrcPlugin()
        lrc_path = plugin._get_lrc_path(item, '.lrc')
        lrc_path_str = lrc_path.decode() if isinstance(lrc_path, bytes) else str(lrc_path)

        assert os.path.isabs(lrc_path_str)
        assert lrc_path_str.endswith('.lrc')
        assert os.path.dirname(lrc_path_str) == str(audio.parent)


def test_item_moved_moves_all_sidecars_and_handles_bytes_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        src = tmp / "src"
        dst = tmp / "dst"
        src.mkdir()
        dst.mkdir()

        base = "song"
        flac = src / f"{base}.flac"
        lrc = src / f"{base}.lrc"
        txt = src / f"{base}.txt"

        flac.write_text("FLAC")
        lrc.write_text("[00:00]line")
        txt.write_text("notes")

        # Configure plugin to recognize additional sidecar ext
        plugin = GetLrcPlugin()
        plugin._sidecar_exts = ['.lrc', '.txt']

        # Simulate copy of audio file (beet move copies audio)
        new_flac = dst / flac.name
        shutil.copy(str(flac), str(new_flac))

        # Call with bytes paths to ensure bytes handling branch
        plugin.item_moved(Mock(), bytes(str(flac), 'utf-8'), bytes(str(new_flac), 'utf-8'))

        # Check that both sidecars were moved
        assert (dst / lrc.name).exists()
        assert (dst / txt.name).exists()
        # Original sidecars should no longer exist
        assert not (src / lrc.name).exists()
        assert not (src / txt.name).exists()
