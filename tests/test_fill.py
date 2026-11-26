"""채우기 운영 루프 (poring_food/fill.py) - 재생 중엔 양보, 아무도 안 보면 채우기"""
from datetime import datetime, timedelta

import pytest

from poring_food import fill

T0 = datetime(2026, 11, 25, 3, 0)


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(fill, "STATE_PATH", str(tmp_path / "fill.json"))
    monkeypatch.setattr(fill, "ENABLED", True)


class Boa:
    def __init__(self, priority="low", wtm_running=False, backlog=False, backlog_at=None, wtm=None):
        self.st = {"encode_priority": priority}
        self.wtm = dict(wtm or {})
        self.wtm_running = wtm_running
        self.backlog, self.backlog_at = backlog, backlog_at
        self.calls = []

    def __call__(self, name, args=None):
        self.calls.append((name, args))
        if name == "tgup_settings":
            return dict(self.st)
        if name == "tgup_set_settings":
            self.st.update(args["settings"])
            return {"changed": {}}
        if name == "wtm_status":
            return {"job": {"running": self.wtm_running, "waitfree_backlog": self.backlog,
                            "waitfree_due_at": self.backlog_at or T0.timestamp()}, "title_job": {"running": False}}
        if name == "wtm_run":
            return "시작"
        if name == "wtm_settings":
            return {"fields": [{"key": k, "value": v} for k, v in self.wtm.items()]}
        if name == "wtm_set_settings":
            self.wtm.update(args["values"])
            return {"changed": {}}
        raise AssertionError(name)


class Rclone:
    def __init__(self, rate="5M:off"):
        self.rate = rate
        self.set = []

    def __call__(self, rate=None):
        if rate:
            self.set.append(rate)
            self.rate = rate
        return self.rate


def run(minute, playing, stage=0, boa=None, rclone=None, slowed=False):
    return fill.step(T0 + timedelta(minutes=minute), {"playing": playing, "stage": stage, "rclone_slowed": slowed},
                     boa=boa, rclone=rclone)


def test_fill_after_idle_then_yield_on_playback():
    boa, rc = Boa("low"), Rclone("5M:off")
    assert run(0, 1, boa=boa, rclone=rc) == []                   # 재생 중: 이미 낮게 -> 할 일 없음
    assert run(5, 0, boa=boa, rclone=rc) == []                   # 재생 끝난 지 10분 안 됨 -> 그대로
    msgs = run(15, 0, boa=boa, rclone=rc)                        # 채우기
    assert any("채우기" in m for m in msgs)
    assert boa.st["encode_priority"] == "high" and rc.set == ["off"]
    assert ("wtm_run", {"job": "run_full_cycle_now"}) in boa.calls
    assert run(20, 0, boa=boa, rclone=rc) == []                  # 이미 맞춰 둠 - 다시 안 씀, 웹툰 실행도 간격 안이라 안 함
    assert sum(1 for c in boa.calls if c[0] == "wtm_run") == 1
    msgs = run(25, 2, boa=boa, rclone=rc)                        # 재생 시작 -> 바로 양보
    assert any("양보" in m for m in msgs) and boa.st["encode_priority"] == "low"
    assert any("낮게" in m for m in msgs)


def test_hold_when_busy_or_unknown_and_respects_loadshed():
    boa, rc = Boa("low"), Rclone("5M:off")
    assert run(30, 0, stage=3, boa=boa, rclone=rc) == [] and boa.st["encode_priority"] == "low"   # 경보 높음 -> 그대로
    assert fill.step(T0, {"playing": None, "stage": None, "rclone_slowed": False}, boa=boa, rclone=rc) == []   # 모름 -> 그대로
    run(60, 0, stage=1, boa=boa, rclone=rc, slowed=True)        # 서버 조절이 rclone을 늦추는 중이면 안 건드림
    assert rc.set == [] and boa.st["encode_priority"] == "high"


def test_wtm_busy_not_rerun_and_gap(monkeypatch):
    boa = Boa("high", wtm_running=True)
    run(0, 0, boa=boa, rclone=Rclone("off"))
    assert not any(c[0] == "wtm_run" for c in boa.calls)        # 이미 돌고 있음
    boa.wtm_running = False
    run(5, 0, boa=boa, rclone=Rclone("off"))
    run(5 + fill.RUN_GAP_MIN - 1, 0, boa=boa, rclone=Rclone("off"))
    assert sum(1 for c in boa.calls if c[0] == "wtm_run") == 1
    run(5 + fill.RUN_GAP_MIN, 0, boa=boa, rclone=Rclone("off"))
    assert sum(1 for c in boa.calls if c[0] == "wtm_run") == 2
    monkeypatch.setattr(fill, "WTM_RUN", False)
    run(5 + 3 * fill.RUN_GAP_MIN, 0, boa=boa, rclone=Rclone("off"))
    assert sum(1 for c in boa.calls if c[0] == "wtm_run") == 2


def test_status_text():
    run(15, 0, boa=Boa("low"), rclone=Rclone("off"))
    t = fill.status_text(T0)
    assert "채우기" in t and "웹툰 전체 실행" in t


def test_observe_reads_loadshed(monkeypatch):
    from poring_food import loadshed
    now = T0
    monkeypatch.setattr(loadshed, "_load", lambda: {"hosts": {"plex01": {"at": (now - timedelta(minutes=1)).isoformat(),
                                                                          "playing": 0, "stage": 1, "slow": {"rclone:upload": {}}}}})
    assert fill.observe(now) == {"playing": 0, "stage": 1, "rclone_slowed": True}
    monkeypatch.setattr(loadshed, "_load", lambda: {"hosts": {"plex01": {"at": (now - timedelta(minutes=30)).isoformat(),
                                                                          "playing": 0, "stage": 0}}})
    assert fill.observe(now)["playing"] is None                 # 오래된 기록 -> 모름


def test_no_full_run_while_waitfree_backlog():
    boa = Boa("high", backlog=True, backlog_at=(T0 + timedelta(minutes=10)).timestamp())
    run(15, 0, boa=boa, rclone=Rclone("off"))
    assert not any(c[0] == "wtm_run" for c in boa.calls)        # 기다무가 밀린 동안은 전체 실행 안 함
    old = Boa("high", backlog=True, backlog_at=(T0 - timedelta(hours=3)).timestamp())
    run(15, 0, boa=old, rclone=Rclone("off"))
    assert any(c[0] == "wtm_run" for c in old.calls)            # 오래된 기록(1시간 넘음)은 믿지 않음


ADMIN = {"FAST_MODE": True, "PARALLEL_TITLES": 6, "MAX_CONCURRENT_DOWNLOADS": 10, "DELAY_SECONDS": 1, "ENABLE_SCHEDULER": True}


def test_throttle_slows_not_stops_then_restores():
    boa = Boa("low", wtm=ADMIN)
    msgs = run(0, 2, stage=4, boa=boa, rclone=Rclone("off"))
    assert any("속도만 낮춤" in m for m in msgs)
    assert boa.wtm == {**ADMIN, "FAST_MODE": False, "PARALLEL_TITLES": 1, "MAX_CONCURRENT_DOWNLOADS": 4, "DELAY_SECONDS": 2.0}
    assert boa.wtm["ENABLE_SCHEDULER"] is True                       # 끄지 않음
    assert "낮춰 둠" in fill.status_text(T0)
    run(5, 2, stage=4, boa=boa, rclone=Rclone("off"))
    assert sum(1 for c in boa.calls if c[0] == "wtm_set_settings") == 1   # 같은 단계면 다시 안 씀
    run(10, 1, stage=2, boa=boa, rclone=Rclone("off"))              # 단계가 내려가면 관리자 값 기준으로 조금 풀기
    assert (boa.wtm["PARALLEL_TITLES"], boa.wtm["MAX_CONCURRENT_DOWNLOADS"], boa.wtm["DELAY_SECONDS"]) == (3, 8, 1)
    run(15, 0, stage=1, boa=boa, rclone=Rclone("off"))
    assert boa.wtm["PARALLEL_TITLES"] == 3                           # 한가해진 지 15분 안 됨
    run(31, 0, stage=0, boa=boa, rclone=Rclone("off"))
    assert boa.wtm == ADMIN                                          # 되돌림
    assert "speed" not in fill._load()


def test_throttle_keeps_admin_manual_change():
    boa = Boa("low", wtm=ADMIN)
    run(0, 1, stage=3, boa=boa, rclone=Rclone("off"))
    assert boa.wtm["PARALLEL_TITLES"] == 2
    boa.wtm["MAX_CONCURRENT_DOWNLOADS"] = 3                          # 관리자가 그 사이 손으로 더 낮춤
    run(5, 0, stage=0, boa=boa, rclone=Rclone("off"))
    run(25, 0, stage=0, boa=boa, rclone=Rclone("off"))
    assert boa.wtm["PARALLEL_TITLES"] == 6 and boa.wtm["FAST_MODE"] is True
    assert boa.wtm["MAX_CONCURRENT_DOWNLOADS"] == 3                  # 관리자가 바꾼 칸은 그대로


def test_throttle_needs_known_stage():
    boa = Boa("low", wtm=ADMIN)
    fill.step(T0, {"playing": None, "stage": None, "rclone_slowed": False}, boa=boa, rclone=Rclone("off"))
    assert boa.wtm == ADMIN
