#!/usr/bin/env python3
import shutil
from pathlib import Path


def move_lrc_sidecar(source, destination):
    from pathlib import Path
    import shutil

    old = Path(source).with_suffix('.lrc')
    new = Path(destination).with_suffix('.lrc')
    if old.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(new))
        return True
    return False


def album_move_sidecars(src_dir, dst_dir):
    from pathlib import Path
    import shutil

    src = Path(src_dir)
    dst = Path(dst_dir)
    moved = []
    if not src.exists():
        return moved
    for lrc in src.rglob('*.lrc'):
        rel = lrc.relative_to(src)
        target = dst.joinpath(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(lrc), str(target))
        moved.append(str(target))
    return moved


def main():
    base = Path('tests/tmp_album_move')
    if base.exists():
        shutil.rmtree(base)
    src = base / 'old'
    dst = base / 'new'
    src.mkdir(parents=True)
    dst.mkdir(parents=True)

    # create files
    (src / 'song1.mp3').write_text('audio')
    (src / 'song1.lrc').write_text('lyrics1')

    # nested
    sub = src / 'Disc 1'
    sub.mkdir()
    (sub / 'track2.flac').write_text('audio')
    (sub / 'track2.lrc').write_text('lyrics2')

    # test item move
    item_src = str(src / 'song1.mp3')
    item_dst = str(dst / 'song1.mp3')
    ok = move_lrc_sidecar(item_src, item_dst)
    print('item move ok:', ok, 'exists at', (dst / 'song1.lrc').exists())

    # recreate src lrcs for album test
    (src / 'song1.lrc').write_text('lyrics1')
    (sub / 'track2.lrc').write_text('lyrics2')

    moved = album_move_sidecars(str(src), str(dst))
    print('album moved files:', moved)
    print('dst song1.lrc exists:', (dst / 'song1.lrc').exists())
    print('dst nested exists:', (dst / 'Disc 1' / 'track2.lrc').exists())

    shutil.rmtree(base)


if __name__ == '__main__':
    main()
