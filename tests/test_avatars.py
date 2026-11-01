"""인물 초상화 아바타: 만들기(가짜 생성기) -> 저장 -> 공개 주소로 바꿔 끼우기."""
import asyncio
import io

import pytest

from poring_food import avatars

PIL = pytest.importorskip("PIL")


def _png(w=640, h=896):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 60)).save(buf, "PNG")
    return buf.getvalue()


def test_make_and_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(avatars, "DIR", str(tmp_path))
    monkeypatch.setattr(avatars, "INDEX", str(tmp_path / "avatars.json"))
    monkeypatch.setattr(avatars, "BASE_URL", "https://poring.example")
    prompts = []

    async def gen(prompt):
        prompts.append(prompt)
        return _png()
    agent = {"name": "페른", "profile": "10대 후반 마법사", "speech": "무표정"}
    fname = asyncio.run(avatars.make(agent, gen=gen, llm=lambda s, u: {"desc": "teen girl mage, purple hair"}))
    assert fname == f"{avatars.key('페른')}.png" and "purple hair" in prompts[0] and "realistic" in prompts[0]
    from PIL import Image
    assert Image.open(tmp_path / fname).size == (avatars.SIZE, avatars.SIZE)        # 정사각형으로 잘라 줄임
    got = avatars.apply([{"name": "페른", "avatar_url": "old"}, {"name": "슈타르크", "avatar_url": "old2"},
                         {"name": "애순이", "account": "aesun", "avatar_url": ""}])
    assert got[0]["avatar_url"].startswith(f"https://poring.example/avatars/{fname}?v=")
    assert got[1]["avatar_url"] == "old2" and got[2]["avatar_url"] == ""             # 없는 사람/봇 계정은 그대로
    assert [a["name"] for a in avatars.missing(got)] == ["슈타르크"]
    assert avatars.path_for(fname) and avatars.path_for("../etc/passwd") is None and avatars.path_for("x.png") is None


def test_off_without_base_url(monkeypatch):
    monkeypatch.setattr(avatars, "BASE_URL", "")
    a = [{"name": "페른", "avatar_url": "old"}]
    assert avatars.apply(a)[0]["avatar_url"] == "old" and asyncio.run(avatars.make_next(a)) is None


def test_failed_generation_keeps_old(monkeypatch, tmp_path):
    monkeypatch.setattr(avatars, "DIR", str(tmp_path))
    monkeypatch.setattr(avatars, "INDEX", str(tmp_path / "avatars.json"))

    async def bad(prompt):
        raise RuntimeError("horde busy")
    assert asyncio.run(avatars.make({"name": "페른"}, gen=bad, llm=lambda s, u: {})) is None
    assert avatars._index() == {}
