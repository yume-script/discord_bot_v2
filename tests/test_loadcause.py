"""부하의 원인 가리기: 프로세스·컨테이너 이름으로 웹툰 내려받기 / 영상 입고 / 재생 / 보안 점검을 나누고, 원인에 맞춰 이야기·사람·조치가 달라진다."""
from datetime import datetime

from poring_food import loadcause, loadshed


def test_parse_ps_and_classify():
    ps = """ 182.0 /opt/bookoasis_assistant/venv/bin/python /opt/bookoasis_assistant/app.py
  35.0 /usr/lib/plexmediaserver/Plex Media Server
   3.0 sshd: root
 bad line
"""
    rows = loadcause.parse_ps(ps)
    assert rows[0] == (182.0, "/opt/bookoasis_assistant/venv/bin/python /opt/bookoasis_assistant/app.py") and len(rows) == 3
    got = loadcause.classify(rows, None, playing=0)
    assert got["cause"] == "webtoon" and got["label"] == "웹툰·원고 내려받기" and "182%" in got["top"]
    # Plex 변환이 크면 재생, 라이브러리 스캔·rclone이 크면 입고
    assert loadcause.classify([(240.0, "/usr/lib/plexmediaserver/Plex Transcoder -i x"), (20.0, "rclone move a b")], None, 2)["cause"] == "playback"
    got = loadcause.classify([(130.0, "/usr/lib/plexmediaserver/Plex Media Scanner --scan"), (90.0, "rclone move a b"), (30.0, "Plex Transcoder")], None, 0)
    assert got["cause"] == "download" and got["shares"]["download"] == 220.0
    assert loadcause.classify([(95.0, "/usr/bin/clamscan -r /mnt")], None, 0)["cause"] == "scan"
    # 프로세스를 못 봤으면 컨테이너 통계로
    assert loadcause.classify(None, {"flaskfarm": 150.0, "bookoasis": 20.0}, 0)["cause"] == "download"
    # 뚜렷한 원인이 없는데 보는 사람이 있으면 재생, 아무도 없으면 알 수 없음
    assert loadcause.classify([(12.0, "python something")], None, 3) == {"cause": "playback", "label": "영상 재생(시청)", "top": "재생 3건", "shares": {}}
    assert loadcause.classify([(12.0, "python something")], None, 0)["cause"] == "other"
    # 둘이 비슷하게 크면(뚜렷하지 않으면) 재생 중일 땐 재생으로 본다
    assert loadcause.classify([(100.0, "bookoasis_assistant"), (95.0, "rclone copy")], None, 1, struggling=True)["cause"] == "playback"


def test_story_and_people():
    up = {"host": "plex01", "do": "stage", "name": "서버 경보", "from": 0, "to": 2, "why": "5분 평균 76%", "cause": "webtoon", "cause_top": "bookoasis_assistant 182%"}
    text, title, mark = loadshed._story(up)
    assert "웹툰" in text and "bookoasis_assistant 182%" in text and "원고 운반 봇을 먼저 늦추고" in text
    assert "웹툰·원고 내려받기" in title and "웹툰·원고 내려받기" in mark
    text2, title2, _ = loadshed._story(dict(up, cause="playback", cause_top="재생 4건"))
    assert "시청이 몰린 것 (재생 4건)" in text2 and "재생은 건드리지 않는다" in text2
    text3, title3, _ = loadshed._story(dict(up, cause="other", cause_top=""))
    assert "짐 나르는 일과 정비부터" in text3 and "알 수 없음" not in title3            # 모르면 예전 글 그대로
    down, dtitle, _ = loadshed._story(dict(up, **{"from": 2, "to": 1}))
    assert "웹툰" not in down and "웹툰" not in dtitle                                   # 내릴 때는 원인을 붙이지 않는다
    from poring_food import stewards
    people = loadshed.key_people(["서버 경보"], cause="playback")
    assert stewards.steward("plex") in people and loadshed.ACTOR in people
    assert stewards.steward("assistant") in loadshed.key_people(["서버 경보"], cause="webtoon")
    assert loadshed.key_people(["서버 경보"], cause="") == loadshed.key_people(["서버 경보"])


def test_plan_slows_the_cause_one_stage_early(monkeypatch):
    now = datetime(2026, 11, 12, 21, 0)
    host = "plex01"
    monkeypatch.setitem(loadshed.HOSTS[host], "units", ["bookoasis_assistant.service"])
    monkeypatch.setitem(loadshed.HOSTS[host], "rclone", "http://127.0.0.1:5276")
    early = loadshed.SLOW_STAGE - 1
    # 원인을 모르면 예전 그대로: 3단계에서는 아무것도 늦추지 않는다 (버벅임이 있어도)
    assert loadshed.plan(host, early, {}, {}, now, playing=True, struggling=True) == []
    # 웹툰이 원인이고 재생 중이면 버벅임을 기다리지 않고 한 단계 일찍 짐꾼만 늦춘다
    acts = loadshed.plan(host, early, {}, {}, now, playing=True, struggling=False, cause="webtoon")
    assert [(a["do"], a["name"], a["weight"]) for a in acts] == [("throttle", "bookoasis_assistant.service", max(loadshed.UNIT_WEIGHT.values()))]
    assert "웹툰" in acts[0]["why"]
    # 영상 입고가 원인이면 업로드만
    acts = loadshed.plan(host, early, {}, {}, now, playing=True, struggling=False, cause="download")
    assert [(a["do"], a["name"]) for a in acts] == [("throttle", "upload")]
    # 아무도 안 보면 원인이 무엇이든 늦추지 않는다 (자료 등록 우선) / 재생이 원인이면 재생 쪽은 건드리지 않는다
    assert loadshed.plan(host, early, {}, {}, now, playing=False, struggling=False, cause="webtoon") == []
    assert loadshed.plan(host, early, {}, {}, now, playing=True, struggling=False, cause="playback") == []
    # 늦춘 뒤 원인이 사라지면(다른 원인) 되돌린다 - 깜빡이지 않게 같은 기준으로
    st = {"throttled": {"unit:bookoasis_assistant.service": {"kind": "unit", "name": "bookoasis_assistant.service", "weight": 30, "orig": "100", "at": now.isoformat()}}}
    assert loadshed.plan(host, early, st, {}, now, playing=True, struggling=False, cause="webtoon") == []
    back = loadshed.plan(host, early, st, {}, now, playing=True, struggling=False, cause="playback")
    assert [(a["do"], a["name"]) for a in back] == [("unthrottle", "bookoasis_assistant.service")]
    monkeypatch.setattr(loadshed, "CAUSE_ACT", False)
    assert loadshed.plan(host, early, {}, {}, now, playing=True, struggling=False, cause="webtoon") == []


TOP = """cores 8
guest vm:110:win10 320.5
guest ct:105:plex01 150.0
guest ct:102:etc 20.0
guest host:-:zeeps 60.0
proc vm:110:win10 kvm 320.5
proc ct:105:plex01 Plex_Transcoder 140.0
proc host:-:zeeps clamscan 55.0
proc ct:102:etc python3 20.0
"""


def test_host_top_is_the_basis():
    t = loadcause.parse_host_top(TOP)
    assert t["cores"] == 8 and t["guests"][0] == ("win10", "vm", 320.5) and (55.0, "zeeps clamscan") in t["procs"]
    assert loadcause.parse_host_top("village-ctl: 허용되지 않은 명령: top") is None
    assert loadcause.guests_line(t) == "win10 40% · plex01 19% · zeeps 8%"
    # 들여다보는 서버(plex01)는 그 안의 ps(명령줄)로, 나머지 게스트·호스트는 호스트가 본 이름으로
    inside = {"plex01": [(140.0, "/usr/lib/plexmediaserver/Plex Transcoder -i x")], "etc": None}
    rows = loadcause.host_procs(t, inside)
    assert (140.0, "/usr/lib/plexmediaserver/Plex Transcoder -i x") in rows and (140.0, "plex01 Plex_Transcoder") not in rows
    assert (55.0, "zeeps clamscan") in rows and (20.0, "etc python3") in rows       # etc는 ps를 못 받았으니 호스트가 본 것
    assert loadcause.host_procs(None, inside) is None


def test_step_uses_host_cause_for_every_server(tmp_path, monkeypatch):
    from datetime import datetime

    from poring_food import loadshed
    monkeypatch.setattr(loadshed, "LOADSHED_PATH", str(tmp_path / "loadshed.json"))
    top = loadcause.parse_host_top("cores 4\nguest host:-:zeeps 300.0\nproc host:-:zeeps clamscan 300.0\nguest ct:102:etc 5.0\n")
    readings = {"etc": {"probe": {"index": 40.0, "cpu": 40.0, "busy": 40.0, "mem": 30.0, "psi": {}}, "running": {},
                        "procs": [(5.0, "python3 app.py")]},
                "plex01": {"probe": {"index": 40.0, "cpu": 40.0, "busy": 40.0, "mem": 30.0, "psi": {}}, "running": {},
                           "procs": [(3.0, "sshd")]},
                "_host_top": top, "_playing": 0, "_struggle": False}
    loadshed.step(datetime(2026, 11, 12, 20, 0), measurer=lambda: readings, throttler=lambda h, a: {}, docker=lambda *a: True,
                  stage_writer=lambda n: [])
    data = loadshed._load()
    assert data["host"]["cause"]["cause"] == "scan" and data["host"]["cause"]["guests"] == "zeeps 75%"
    assert data["hosts"]["etc"]["cause"]["cause"] == "scan" and data["hosts"]["plex01"]["cause"]["cause"] == "scan"
