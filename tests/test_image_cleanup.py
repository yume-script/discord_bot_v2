"""포링푸드 그림 작업 파일 정리 (보내다 봇이 꺼져 남은 것)"""
import os
import time

import pytest

pytest.importorskip("discord")


def test_sweep_images_old_only(tmp_path, monkeypatch):
    from poring_food import config
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    from cogs import poring_food
    d = tmp_path / "images"
    d.mkdir()
    old, new = d / "draw_1.png", d / "draw_2.png"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(old, (past, past))
    assert poring_food.sweep_images() == 1
    assert sorted(os.listdir(d)) == ["draw_2.png"]
