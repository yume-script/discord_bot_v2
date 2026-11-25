""".env를 기본(.env)과 포링 이야기(poring.env)로 나누기 (scripts/split_env.py) + 나눈 뒤 sync_env"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import split_env  # noqa: E402
import sync_env  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "env.example")


def _keys(text):
    return {m.group(1): sync_env.parse_value(m.group(2)) for m in (sync_env._KEY_LINE.match(ln) for ln in text.split("\n")
                                                                    if not ln.lstrip().startswith("#")) if m}


def test_classify_by_code_usage():
    used = split_env.usage(ROOT)
    assert split_env.is_story("PORING_VILLAINS", used)            # 빌런 - 이야기만
    assert split_env.is_story("PORING_ARCADE", used)
    assert not split_env.is_story("PORING_LLM_MODEL", used)       # 운영자·다운로드 에이전트도 쓰는 LLM
    assert not split_env.is_story("PORING_UPTIME_AUTORESTART", used)
    assert not split_env.is_story("PORING_FILL", used) and not split_env.is_story("PORING_LOADSHED_PLEX_SSH", used)
    assert not split_env.is_story("PORING_FOOD_ENABLED", used) and not split_env.is_story("SERVER_OPS_ENABLED", used)
    assert not split_env.is_story("DISCORD_TOKEN", used)
    assert split_env.is_story("PORING_MY_OWN_THING", {}) and not split_env.is_story("MY_OWN_THING", {})   # 코드에 없는 것은 이름으로


def test_split_keeps_values_and_is_idempotent(tmp_path, monkeypatch):
    env, por = tmp_path / ".env", tmp_path / "poring.env"
    env.write_text("DISCORD_TOKEN=secret-token\nPORING_LLM_MODEL=gemini-3.7-flash\nPORING_VILLAINS=1\n"
                   "PORING_ARCADE=0\nPORING_UPTIME_AUTORESTART=1\nPORING_CUSTOM_X=7\nCUSTOM_Y=8\n", encoding="utf-8")
    assert split_env.main(["--env", str(env), "--poring", str(por), "--example", EXAMPLE, "--apply", "--quiet"]) == 0
    e, p = _keys(env.read_text(encoding="utf-8")), _keys(por.read_text(encoding="utf-8"))
    assert e["DISCORD_TOKEN"] == "secret-token" and e["PORING_LLM_MODEL"] == "gemini-3.7-flash" and e["PORING_UPTIME_AUTORESTART"] == "1"
    assert p["PORING_VILLAINS"] == "1" and p["PORING_ARCADE"] == "0" and p["PORING_CUSTOM_X"] == "7" and e["CUSTOM_Y"] == "8"
    assert not set(e) & set(p)                                     # 한 변수는 한 파일에만
    assert "PORING_VILLAINS" not in e and "DISCORD_TOKEN" not in p
    assert list(tmp_path.glob(".env.bak.*"))                      # 원래 .env 백업
    assert oct(os.stat(por).st_mode)[-3:] == "600"                 # 새 poring.env는 600
    before = (env.read_text(encoding="utf-8"), por.read_text(encoding="utf-8"))
    split_env.main(["--env", str(env), "--poring", str(por), "--example", EXAMPLE, "--apply", "--quiet"])
    assert (env.read_text(encoding="utf-8"), por.read_text(encoding="utf-8")) == before   # 다시 돌려도 그대로


def test_sync_after_split_puts_new_story_vars_in_poring_env(tmp_path, monkeypatch):
    env, por = tmp_path / ".env", tmp_path / "poring.env"
    env.write_text("DISCORD_TOKEN=t\n", encoding="utf-8")
    por.write_text("PORING_VILLAINS=1\n", encoding="utf-8")
    p = split_env.plan(EXAMPLE, str(env), str(por))
    e, s = _keys(p["env"]), _keys(p["poring"])
    assert "PORING_ARCADE" in s and "PORING_ARCADE" not in e       # 새 이야기 변수 -> poring.env (기본값)
    assert "SERVER_OPS_ENABLED" in e and s["PORING_VILLAINS"] == "1"
    # 나눈 뒤 sync_env.py는 split_env로 넘긴다 (poring.env가 있으면)
    monkeypatch.setattr(sync_env, "BASE_DIR", str(tmp_path))
    called = []
    monkeypatch.setattr(split_env, "main", lambda argv=None: called.append(argv) or 0)
    assert sync_env.main(["--apply"]) == 0 and called == [["--apply"]]
