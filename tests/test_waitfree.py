"""기다무 충전 시간대: 시간대별 기록 -> 한가한 시간대 추천, 원격 미리 보기/적용의 안전장치."""
from datetime import datetime, timedelta

from poring_food import loadshed, waitfree

T0 = datetime(2026, 10, 28, 12, 0)


def _hourly(days=5, busy=(), watch=()):
    data = {}
    for d in range(days):
        for h in range(24):
            now = T0.replace(hour=h) - timedelta(days=d)
            for _ in range(3):
                loadshed.record_hour(data, now, 85.0 if h in busy else 30.0, 1 if h in watch else 0)
    return data


def test_record_hour_keeps_14_days():
    data = {}
    loadshed.record_hour(data, T0 - timedelta(days=20), 50, 0)
    loadshed.record_hour(data, T0, 40, None)
    loadshed.record_hour(data, T0, 60, 2)
    assert list(data["hourly"]) == ["2026-10-28T12"]
    assert data["hourly"]["2026-10-28T12"] == [100.0, 2, 1, 1]


def test_best_window_avoids_busy_and_watching():
    data = _hourly(busy=range(1, 8), watch=range(18, 24))    # 새벽 FF rclone 새로 고침 + 저녁 시청
    s, e, why = waitfree.best_window(10, waitfree.profile(data))
    hours = {(s + i) % 24 for i in range(10)}
    assert not hours & set(range(1, 8)) and not hours & set(range(18, 24)), (s, e)
    assert (s, e) == (8, 18) and "점수" in why


def test_best_window_default_without_history():
    assert waitfree.best_window(10, waitfree.profile(_hourly(days=2)))[:2] == waitfree.DEFAULT
    assert waitfree.best_window(10, {})[:2] == waitfree.DEFAULT


def test_window_ok():
    assert waitfree._window_ok(1, 11) is None and waitfree._window_ok(22, 6) is None
    assert waitfree._window_ok(1, 3) and waitfree._window_ok(0, 20) and waitfree._window_ok(1, 30)


def test_clean_drops_titles():
    out = "■ 1일 기다무 3개\n\n  예:\n  비밀 작품: 충전 10-28 13:00 → 10-29 02:00\n  다른 작품: 충전 x → y\n\n미리 보기입니다."
    got = waitfree._clean(out)
    assert "작품" not in got.replace("기다무", "") and "미리 보기입니다." in got and "■" in got


def test_guard_and_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(waitfree, "STATE_PATH", str(tmp_path / "waitfree.json"))
    monkeypatch.setattr(loadshed, "_load", lambda: {"hosts": {"plex01": {"stage": 1}}})
    monkeypatch.setattr(loadshed, "_viewing", lambda: (0, 0))
    monkeypatch.setattr(loadshed, "DRY_RUN", False)
    calls = []
    monkeypatch.setattr(waitfree, "_remote", lambda script, timeout: (calls.append(script), (0, "적용했습니다. 백업: x"))[1])
    assert "시간대가 이상해요" in waitfree.apply(1, 2, now=T0) and not calls
    got = waitfree.apply(8, 18, now=T0)
    assert "적용했습니다" in got and len(calls) == 1
    s = calls[0]
    assert s.index("systemctl stop") < s.index("--spread 8 18 --apply") < s.index("systemctl start")
    assert "시간이 안 지났" in waitfree.apply(8, 18, now=T0 + timedelta(hours=5)) and len(calls) == 1
    # 재생 중 / 높은 단계면 안 함
    monkeypatch.setattr(waitfree, "STATE_PATH", str(tmp_path / "w2.json"))
    monkeypatch.setattr(loadshed, "_viewing", lambda: (2, 0))
    assert "재생 중" in waitfree.apply(8, 18, now=T0)
    monkeypatch.setattr(loadshed, "_viewing", lambda: (0, 0))
    monkeypatch.setattr(loadshed, "_load", lambda: {"hosts": {"plex01": {"stage": 3}}})
    assert "3단계" in waitfree.apply(8, 18, now=T0) and len(calls) == 1


def test_tools_policy():
    from core.tool_policy import is_server_affecting
    from poring_food import tools
    names = {t.name for t in tools.PORING_FOOD_TOOLS}
    assert {"get_download_plan", "preview_waitfree_window", "set_waitfree_window"} <= names
    assert is_server_affecting("set_waitfree_window")
    assert not is_server_affecting("get_download_plan") and not is_server_affecting("preview_waitfree_window")


def _pilot_env(monkeypatch, tmp_path, stage=1):
    monkeypatch.setattr(waitfree, "STATE_PATH", str(tmp_path / "waitfree.json"))
    data = _hourly(busy=range(1, 8), watch=range(18, 24)) | {"hosts": {"plex01": {"stage": stage}}}
    monkeypatch.setattr(loadshed, "_load", lambda: data)
    monkeypatch.setattr(loadshed, "_viewing", lambda: (0, 0))
    monkeypatch.setattr(loadshed, "DRY_RUN", False)
    monkeypatch.setattr(waitfree, "_decider", lambda: "짐꾼담당")
    monkeypatch.setattr("poring_food.memory.remember", lambda *a, **k: None)
    calls = []

    def remote(script, timeout):
        calls.append(script)
        return 0, ("적용했습니다. 백업: x" if "--apply" in script else "■ 분포\n  예:\n  제목: 충전 a → b\n\n미리 보기입니다.")
    monkeypatch.setattr(waitfree, "_remote", remote)
    return calls


def test_autopilot_applies_llm_decision_once_a_day(monkeypatch, tmp_path):
    calls = _pilot_env(monkeypatch, tmp_path)
    posts, seen = [], []

    def decide():
        return {"apply": True, "start": 8, "end": 18, "reason": "저녁 재생과 새벽 부하를 피함"}
    d = waitfree.autopilot(now=T0, decide=decide, post=posts.append, playing=0)
    assert d["apply"] and any("--spread 8 18 --apply" in c for c in calls)
    assert "08:00~17:59" in posts[0] and "적용함" in posts[0] and "제목" not in posts[0]
    assert waitfree._state()["applied"][-1]["by"] == "자율(짐꾼담당)"
    # 같은 날 다시 불러도 안 살펴봄 / 다음 날 추천과 같으면 LLM 안 부름
    assert waitfree.autopilot(now=T0 + timedelta(hours=3), decide=lambda: seen.append(1), playing=0) is None
    assert waitfree.autopilot(now=T0 + timedelta(days=1), decide=lambda: seen.append(1), playing=0) is None and not seen


def test_autopilot_keep_and_safety(monkeypatch, tmp_path):
    calls = _pilot_env(monkeypatch, tmp_path)
    posts = []
    d = waitfree.autopilot(now=T0, decide=lambda: {"apply": False, "reason": "충분함"}, post=posts.append, playing=0)
    assert not d["apply"] and not any("--apply" in c for c in calls) and "그대로" in posts[0]
    # 재생 중이면 LLM도 안 부르고 30분 뒤 다시
    monkeypatch.setattr(waitfree, "STATE_PATH", str(tmp_path / "b.json"))
    asked = []
    assert waitfree.autopilot(now=T0, decide=lambda: asked.append(1), playing=2) is None and not asked
    assert waitfree.autopilot(now=T0 + timedelta(minutes=10), decide=lambda: asked.append(1), playing=0) is None and not asked
    # 기록이 모자라면 아무것도 안 함
    monkeypatch.setattr(waitfree, "STATE_PATH", str(tmp_path / "c.json"))
    monkeypatch.setattr(loadshed, "_load", lambda: _hourly(days=1))
    assert waitfree.autopilot(now=T0, decide=lambda: asked.append(1), playing=0) is None and not asked
