"""scripts/prune_env.py: 코드에서 안 쓰는 .env 변수 찾기·지우기"""
import importlib.util
import os

SPEC = importlib.util.spec_from_file_location("prune_env", os.path.join(os.path.dirname(__file__), "..", "scripts", "prune_env.py"))
pe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pe)


def _repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text(
        'import os\nA = os.getenv("USED_ONE", "")\nB = os.environ.get(f"{k.upper()}_BOT_TOKEN")\n'
        'C = os.getenv(f"DYN_PART_{x}")\n', encoding="utf-8")
    (tmp_path / "mcp.yaml").write_text("env:\n  KEY: ${FROM_YAML}\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text('os.getenv("ONLY_IN_TEST")\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("OLD_THING을 쓰세요\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(
        "# 봇\nUSED_ONE=1\nUSED_ONE=2\n\n"
        "# 예전 기능 - 이제 안 씀\nOLD_THING=secret-value\n\n"
        "MAIN_BOT_TOKEN=t\nDYN_PART_ETC=x\nFROM_YAML=y\nONLY_IN_TEST=z\nOPENAI_API_KEY=k\n"
        "# KEEP_ME 설명\nKEEP_ME=1\n", encoding="utf-8")
    return env


def test_plan_and_apply(tmp_path, capsys):
    env = _repo(tmp_path)
    unused, dup, drop = pe.plan(str(env), str(tmp_path), {"KEEP_ME"})
    assert unused == ["OLD_THING", "ONLY_IN_TEST"] and dup == ["USED_ONE"]
    assert pe.main(["--env", str(env), "--root", str(tmp_path), "--keep", "KEEP_ME"]) == 0
    out = capsys.readouterr().out
    assert "OLD_THING" in out and "secret-value" not in out and "보여 주기만" in out
    assert "OLD_THING" in env.read_text(encoding="utf-8")                                    # 보여 주기만 - 안 바뀜
    assert pe.main(["--env", str(env), "--root", str(tmp_path), "--keep", "KEEP_ME", "--apply"]) == 0
    text = env.read_text(encoding="utf-8")
    assert "OLD_THING" not in text and "예전 기능" not in text and "ONLY_IN_TEST" not in text   # 그 변수의 설명 주석도
    assert "USED_ONE=1" not in text and "USED_ONE=2" in text and "# 봇" in text and "KEEP_ME=1" in text
    for k in ("MAIN_BOT_TOKEN", "DYN_PART_ETC", "FROM_YAML", "OPENAI_API_KEY"):
        assert k in text, k
    assert [p for p in os.listdir(tmp_path) if p.startswith(".env.bak.")]                    # 백업
    assert pe.main(["--env", str(env), "--root", str(tmp_path), "--keep", "KEEP_ME"]) == 0
    assert "지울 것이 없습니다" in capsys.readouterr().out


def test_real_env_example_is_all_used():
    """저장소의 env.example에 적힌 변수는 모두 코드에서 쓴다 (안 쓰는 걸 넣어 두지 않게)"""
    root = os.path.join(os.path.dirname(__file__), "..")
    unused, dup, _drop = pe.plan(os.path.join(root, "env.example"), root, set())
    assert unused == [] and dup == [], (unused, dup)
