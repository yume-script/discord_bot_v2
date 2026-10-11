"""scripts/sync_env.py - 기존 값 유지, 새 변수만 기본값으로, .env에만 있던 변수는 보존."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import sync_env  # noqa: E402


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_keeps_values_adds_new_and_preserves_extra(tmp_path):
    example = tmp_path / "env.example"
    env = tmp_path / ".env"
    _write(example, "# 디스코드\nDISCORD_BOT_TOKEN=\n# 새 기능\nNEW_FLAG=1\nPORING_AGENT_MAX_TURNS=6   # 대화 길이\n")
    _write(env, "DISCORD_BOT_TOKEN=secret-token\nPORING_AGENT_MAX_TURNS=10   # 옛 주석\nMY_OWN=keep me\n")
    assert sync_env.main(["--env", str(env), "--example", str(example), "--apply"]) == 0
    out = env.read_text(encoding="utf-8")
    assert "DISCORD_BOT_TOKEN=secret-token" in out
    assert "PORING_AGENT_MAX_TURNS=10\n" in out          # 기존 값 유지, 같은 줄 주석은 떼고
    assert "# 대화 길이\nPORING_AGENT_MAX_TURNS" in out    # env.example 설명은 윗줄로
    assert "NEW_FLAG=1" in out and "# 새 기능" in out      # 새 변수는 기본값 + 설명
    assert "MY_OWN=keep me" in out                         # .env에만 있던 변수 보존
    assert any(p.name.startswith(".env.bak.") for p in tmp_path.iterdir())


def test_quoted_values_and_hash_in_value():
    assert sync_env.parse_value('"a # b"  # 주석') == '"a # b"'
    assert sync_env.parse_value("http://x/#frag") == "http://x/#frag"   # 공백 없는 #은 값
    assert sync_env.parse_value("   # 빈 값 뒤 주석") == ""


def test_check_and_dry_run_do_not_write(tmp_path):
    example = tmp_path / "env.example"
    env = tmp_path / ".env"
    _write(example, "A=1\nB=2\n")
    _write(env, "A=9\n")
    assert sync_env.main(["--env", str(env), "--example", str(example), "--check"]) == 1
    assert sync_env.main(["--env", str(env), "--example", str(example)]) == 0
    assert env.read_text(encoding="utf-8") == "A=9\n"
    sync_env.main(["--env", str(env), "--example", str(example), "--apply"])
    assert sync_env.main(["--env", str(env), "--example", str(example), "--check"]) == 0


def test_repo_env_example_has_no_duplicates_or_inline_comments():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    keys = []
    with open(os.path.join(root, "env.example"), encoding="utf-8") as f:
        for line in f:
            m = sync_env._KEY_LINE.match(line.rstrip("\n"))
            if m and not line.lstrip().startswith("#"):
                keys.append(m.group(1))
                assert " #" not in m.group(2), f"{m.group(1)}: 값 뒤 같은 줄 주석은 윗줄로"
    assert len(keys) == len(set(keys))
