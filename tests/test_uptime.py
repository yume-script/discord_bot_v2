"""서비스 응답 확인 (poring_food/uptime.py)"""
from datetime import datetime, timedelta

import pytest

from poring_food import uptime

T0 = datetime(2026, 11, 15, 10, 0)
SVC = [{"name": "YM_PLAYER", "url": "http://ym:8899/api/auth/state", "what": "영상 재생", "env": "PORING_YMPLAYER_URL"},
       {"name": "Plex", "url": "http://plex:32400/identity", "what": "Plex 영상 재생", "env": "PLEX_URL"}]


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(uptime, "UPTIME_PATH", str(tmp_path / "uptime.json"))
    monkeypatch.setattr(uptime, "AUTORESTART", False)   # (자동 복구는 아래 시험에서만 - 진짜 ssh를 부르지 않게)


def run(minute, ym, plex=(True, 0.1, "")):
    res = {"http://ym:8899/api/auth/state": ym, "http://plex:32400/identity": plex}
    return uptime.step(T0 + timedelta(minutes=minute), prober=lambda url: res[url], tlist=SVC)


def test_down_after_three_misses_then_recovers():
    down = (False, 8.0, "8초 안에 응답 없음")
    assert run(0, down) == [] and run(1, down) == []          # 두 번까지는 조용히 (잠깐 끊김)
    msgs = run(2, down)
    assert len(msgs) == 1 and "⛔ **YM_PLAYER** 응답 없음" in msgs[0] and "2분째" in msgs[0] and "영향: 영상 재생" in msgs[0]
    assert run(3, down) == []                                  # 같은 문제는 바로 다시 안 알림
    assert "계속 멈춰 있음" in run(2 + uptime.REPEAT_MIN, down)[0]
    back = run(2 + uptime.REPEAT_MIN + 1, (True, 0.2, ""))
    assert len(back) == 1 and back[0].startswith("✅ **YM_PLAYER** 다시 응답")
    assert run(400, (True, 0.2, "")) == []


def test_blip_is_not_reported():
    assert run(0, (False, 1, "연결 안 됨")) == []
    assert run(1, (True, 0.1, "")) == []                      # 한 번 끊겼다 바로 돌아오면 알리지 않음
    assert uptime._load()["svc"]["YM_PLAYER"]["fails"] == 0


def test_slow_once():
    slow = (True, 7.0, "")
    msgs = [m for i in range(uptime.SLOW_N + 3) for m in run(i, slow)]
    assert len(msgs) == 1 and "🐢 **YM_PLAYER** 응답이 느림" in msgs[0]
    run(20, (True, 0.1, ""))
    assert uptime._load()["svc"]["YM_PLAYER"]["slow_alerted"] == ""


def test_probe_rules():
    assert uptime.probe("u", get=lambda u: 200)[0]
    assert uptime.probe("u", get=lambda u: 302)[0]            # 로그인 화면으로 넘겨도 서버는 살아 있음
    assert uptime.probe("u", get=lambda u: 401)[0]
    ok, _took, err = uptime.probe("u", get=lambda u: 502)
    assert not ok and err == "HTTP 502"

    def boom(u):
        raise ConnectionError("refused")
    ok, _took, err = uptime.probe("u", get=boom)
    assert not ok and "연결 안 됨" in err


def test_targets_and_status(monkeypatch):
    monkeypatch.setenv("PORING_YMPLAYER_URL", "http://192.168.0.31:8899/")
    monkeypatch.setenv("PLEX_URL", "")                        # 비어 있으면 기본 주소
    monkeypatch.delenv("PORING_BOOKOASIS_URL", raising=False)
    monkeypatch.setenv("PORING_ASSISTANT_URL", "http://bad host")   # 이상한 주소는 쓰지 않음
    monkeypatch.setenv("PORING_UPTIME_EXTRA", "IPTV=http://192.168.0.31:9999, 나쁜=javascript:x")
    t = {x["name"]: x["url"] for x in uptime.targets()}
    assert t["YM_PLAYER"] == "http://192.168.0.31:8899/api/auth/state"
    assert t["Plex"] == "http://192.168.0.31:32400/identity"
    assert t["북오아시스"] == "" and t["북오아시스 어시스턴트"] == ""
    assert t["IPTV"] == "http://192.168.0.31:9999" and "나쁜" not in t
    text = uptime.status_text(T0)
    assert "북오아시스: 확인 안 함" in text and "PORING_BOOKOASIS_URL" in text and "아직 확인 전" in text


def test_notify_channel_and_kakao(monkeypatch):
    from poring_food import health
    posted, sent = [], []
    monkeypatch.setattr(health, "ENABLED", True)
    uptime.notify(["⛔ **YM_PLAYER** 응답 없음"], T0, post=posted.append, send=sent.append)
    assert "**YM_PLAYER**" in posted[0] and sent and "**" not in sent[0]
    monkeypatch.setattr(health, "ENABLED", False)
    sent.clear()
    uptime.notify(["x"], T0, post=posted.append, send=sent.append)
    assert sent == []                                         # 카톡 알림을 끄면 채널에만


def test_auto_restart_with_gap_and_daily_cap(monkeypatch):
    monkeypatch.setattr(uptime, "AUTORESTART", True)
    monkeypatch.delenv("PORING_UPTIME_RESTART", raising=False)
    ran = []

    def restarter(cmd):
        ran.append(cmd)
        return True, ""

    def go(minute, ym):
        res = {"http://ym:8899/api/auth/state": ym, "http://plex:32400/identity": (True, 0.1, "")}
        return uptime.step(T0 + timedelta(minutes=minute), prober=lambda url: res[url], tlist=SVC, restarter=restarter)
    down = (False, 8.0, "8초 안에 응답 없음")
    assert go(0, down) == [] and go(1, down) == [] and ran == []
    msgs = go(2, down)                                           # 3번 연속 -> 알리고 바로 다시 시작
    assert ran == ["systemctl restart ym_player"] and any("🔧 **YM_PLAYER** 응답이 없어 다시 시작" in m for m in msgs)
    assert go(3, down) == [] and len(ran) == 1                   # 간격(15분) 안에는 다시 안 함
    go(2 + uptime.RESTART_GAP_MIN, down)
    go(2 + 2 * uptime.RESTART_GAP_MIN, down)
    assert len(ran) == uptime.RESTART_DAILY_MAX
    capped = go(2 + 3 * uptime.RESTART_GAP_MIN, down)            # 하루 한도 -> 관리자에게 한 번만
    assert len(ran) == uptime.RESTART_DAILY_MAX and any("자동 복구 한도" in m for m in capped)
    assert not any("한도" in m for m in go(2 + 4 * uptime.RESTART_GAP_MIN, down))
    back = go(200, (True, 0.1, ""))
    assert back and back[0].startswith("✅ **YM_PLAYER** 다시 응답")


def test_restart_commands_and_failure(monkeypatch):
    monkeypatch.setattr(uptime, "AUTORESTART", True)
    monkeypatch.setenv("PORING_UPTIME_RESTART", "Plex=;YM_PLAYER=systemctl restart ymp2")
    cmds = uptime.restart_cmds()
    assert "Plex" not in cmds and cmds["YM_PLAYER"] == "systemctl restart ymp2" and cmds["북오아시스"] == "docker restart bookoasis"
    down = (False, 8.0, "연결 안 됨")
    res = {"http://ym:8899/api/auth/state": down, "http://plex:32400/identity": down}
    ran = []
    msgs = []
    for m in range(3):
        msgs += uptime.step(T0 + timedelta(minutes=m), prober=lambda url: res[url], tlist=SVC,
                            restarter=lambda cmd: (ran.append(cmd), (False, "Unit not found"))[1])
    assert ran == ["systemctl restart ymp2"]                     # Plex는 비워서 자동 복구 안 함
    assert any("⚠️ **YM_PLAYER** 다시 시작하지 못했어요" in m and "Unit not found" in m for m in msgs)
