"""LLM 운영자 (poring_food/ops_agent.py) · 하루 운영 요약 (poring_food/ops_digest.py)"""
from datetime import datetime, timedelta

import pytest

from poring_food import ops_agent, ops_digest

T0 = datetime(2026, 11, 25, 9, 0)


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_agent, "STATE_PATH", str(tmp_path / "ops_agent.json"))
    monkeypatch.setattr(ops_agent, "ENABLED", True)
    monkeypatch.setattr(ops_digest, "STATE_PATH", str(tmp_path / "ops_digest.json"))
    monkeypatch.setattr(ops_digest, "ENABLED", True)


def logs(cmd):
    if "journalctl" in cmd:
        return "Traceback ...\nOSError: [Errno 28] No space left on device\nMP_TOKEN=abcd1234"
    return "/dev/sda1  100G  100G  0  100% /"


def test_report_dedup_and_diagnose():
    assert ops_agent.report("restart_cap", "YM_PLAYER", "3번 다시 시작해도 응답 없음", T0)
    assert not ops_agent.report("restart_cap", "YM_PLAYER", "또", T0 + timedelta(minutes=5))      # 쌓여 있거나 6시간 안
    assert not ops_agent.report("nope", "YM_PLAYER", "x", T0)
    seen = []

    def ask(facts):
        seen.append(facts)
        return {"cause": "디스크가 꽉 참 (100%)", "confidence": "높음", "steps": ["du -sh /mnt/* 로 큰 폴더 찾기", "systemctl restart ym_player"],
                "urgent": True}
    msgs = ops_agent.step(T0, ask=ask, run=logs)
    assert len(msgs) == 1 and "🩺 **YM_PLAYER** 자동 복구 한도" in msgs[0] and "급함" in msgs[0] and "1. du -sh" in msgs[0]
    assert "No space left" in seen[0] and "100%" in seen[0] and "abcd1234" not in seen[0]   # 비밀 같은 값은 가림
    assert ops_agent.step(T0, ask=ask, run=logs) == []                    # 다 처리함
    assert not ops_agent.report("restart_cap", "YM_PLAYER", "x", T0 + timedelta(hours=1))
    assert ops_agent.report("restart_cap", "YM_PLAYER", "x", T0 + timedelta(hours=7))


def test_llm_failure_and_daily_cap(monkeypatch):
    monkeypatch.setattr(ops_agent, "DAILY_MAX", 1)
    ops_agent.report("restart_failed", "Plex", "Unit not found", T0)
    ops_agent.report("restart_failed", "북오아시스", "docker 오류", T0)
    calls = []
    msgs = ops_agent.step(T0, ask=lambda f: calls.append(1), run=logs)   # LLM이 답을 못 줌 + 하루 1번
    assert len(calls) == 1 and len(msgs) == 2
    assert all("진단하지 못했어요" in m for m in msgs) and "한도" in msgs[1]


def test_digest_builds_and_sends_once(monkeypatch):
    from poring_food import fill, gdw_queue, updater, uptime
    monkeypatch.setattr(uptime, "_load", lambda: {"svc": {"Plex": {"fails": 0, "restart_day": "2026-11-25", "restarts": 1,
                                                                    "down_day": "2026-11-25", "down_min": 4}}})
    monkeypatch.setattr(uptime, "targets", lambda: [{"name": "Plex", "url": "http://p", "what": "", "env": ""}])
    monkeypatch.setattr(fill, "status_text", lambda now=None: "⚙️ **채우기 운영** - 채우기 (아무도 안 봄)\n• 이 모드 시작: 03:10")
    monkeypatch.setattr(gdw_queue, "fetch", lambda: {})
    monkeypatch.setattr(gdw_queue, "digest", lambda st: {"pending": 3, "failed": 1, "alive": True})
    monkeypatch.setattr(updater, "_state", lambda: {"ym_player": {"history": [{"at": "2026-11-25T05:00", "ok": True, "to": "abc1234"}]},
                                                    "bookoasis": {"pending": {"update": False}}})
    ts = (T0 - timedelta(hours=2)).timestamp()

    def boa(name, args=None):
        if name == "wtm_history":
            return [{"type": "download", "ts": ts}, {"type": "download", "ts": ts, "platform": "kakao"},
                    {"type": "download", "ts": ts - 3 * 86400}, {"type": "download_fail", "ts": ts}]
        if name == "tgup_status":
            return {"queue_total": 5, "retry": [{}], "waiting_subs": [], "recent": [{"result": "올림", "ago_min": 30},
                                                                                     {"result": "실패", "ago_min": 60}]}
        raise AssertionError(name)
    text = ops_digest.build(T0, boa=boa)
    assert "Plex ✅ (자동 복구 1번, 멈춤 약 4분)" in text
    assert "2화 받음 (네이버 1 · 카카오 1) · 실패 1" in text
    assert "진행 목록 5 · 다시 시도 1" in text and "올림 1/실패 1" in text
    assert "대기 3 · 실패 1" in text and "업데이트 YM_PLAYER abc1234" in text and "관리자 확인 기다림 북오아시스" in text
    monkeypatch.setattr(ops_digest, "build", lambda now=None: "요약")
    posts = []
    assert not ops_digest.run(T0.replace(hour=8), post=posts.append)         # 정한 시각이 아님
    assert ops_digest.run(T0, post=posts.append) and posts == ["요약"]
    assert not ops_digest.run(T0.replace(minute=30), post=posts.append)      # 하루 한 번
