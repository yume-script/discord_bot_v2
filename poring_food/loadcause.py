"""
부하의 원인 가리기 - 서버가 왜 바쁜지에 따라 이야기·반응하는 사람·조치가 달라진다.

원인 (CAUSES):
  webtoon   웹툰·원고 내려받기 (bookoasis_assistant의 웹툰 다운로더·드라이브 감시)
  download  Plex 영상 내려받기·입고 (flaskfarm plex_mate/GDS, rclone 올리기·옮기기, 내려받기 프로그램, Plex 라이브러리 스캔)
  playback  영상 재생 (Plex 변환, YM_PLAYER/SHYNI 중계 - 보는 사람이 많다)
  scan      보안 점검 (ClamAV 바이러스 검사)
  other     그 밖 / 알 수 없음

가리는 법: 서버에서 CPU를 많이 쓰는 프로세스(`ps`)와 컨테이너(`docker stats`)의 이름을 낱말로 묶어 종류별로 더한다.
가장 큰 종류가 MIN_CPU(한 코어의 40%) 이상이고 둘째보다 뚜렷이 크면 그것이 원인. 재생 중인데 뚜렷한 원인이 없으면 재생.
낱말은 .env로 바꾼다 (PORING_LOADCAUSE_WEBTOON 등, 쉼표로). 순수 함수라 테스트하기 쉽다.
"""
from __future__ import annotations

import os

MIN_CPU = float(os.getenv("PORING_LOADCAUSE_MIN_CPU", "40"))   # 이만큼(코어 하나의 %)은 써야 원인으로 본다
LEAD = 1.25                                                     # 둘째보다 이 배수는 커야 뚜렷한 원인


def _words(env: str, default: str) -> tuple[str, ...]:
    return tuple(w.strip().lower() for w in os.getenv(env, default).split(",") if w.strip())


KEYWORDS = {
    "webtoon": _words("PORING_LOADCAUSE_WEBTOON", "bookoasis_assistant,gdrive_watch,webtoon,wtm,gdw"),
    "playback": _words("PORING_LOADCAUSE_PLAYBACK", "plex transcoder,transcoder,ym_player,ym-player,ymplayer,shyni,ffmpeg,tautulli"),
    "download": _words("PORING_LOADCAUSE_DOWNLOAD",
                       "flaskfarm,plex_mate,rclone,qbittorrent,transmission,aria2,sabnzbd,nzbget,radarr,sonarr,jdownloader,"
                       "plex media scanner,media scanner,gds"),
    "scan": _words("PORING_LOADCAUSE_SCAN", "clamscan,clamd,clamav,freshclam"),
}
ORDER = ("webtoon", "playback", "download", "scan")   # 낱말이 겹치면 앞의 것 (plex transcoder는 재생, plex media scanner는 입고)

# 이야기(출판사 세계)와 조치 안내. steward = stewards.SYSTEMS의 키 (그 원인을 맡은 사람들이 바로 반응한다)
CAUSES = {
    "webtoon": {"label": "웹툰·원고 내려받기", "mark": "📚",
                "story": "원인은 편집국 원고(웹툰) 내려받기가 한꺼번에 몰린 것",
                "act": "원고 운반 봇을 먼저 늦추고, 급하지 않은 회차는 한가한 시간으로 미룬다. 시청은 그대로 지킨다.",
                "stewards": ("assistant", "bookoasis")},
    "download": {"label": "Plex 영상 내려받기·입고", "mark": "📥",
                 "story": "원인은 영상팀 새 영상 입고(내려받기·옮기기·서가 정리)가 몰린 것",
                 "act": "올리는 속도를 낮추고 입고를 차례로 나눈다. 재생은 그대로 지킨다.",
                 "stewards": ("ff", "upload")},
    "playback": {"label": "영상 재생(시청)", "mark": "🎬",
                 "story": "원인은 시청이 몰린 것",
                 "act": "재생은 건드리지 않는다. 대신 내려받기·정비 같은 다른 일을 뒤로 미룬다.",
                 "stewards": ("plex", "ym_player", "tautulli")},
    "scan": {"label": "보안 점검(바이러스 검사)", "mark": "🛡️",
             "story": "원인은 보안 점검(바이러스 검사)",
             "act": "검사 속도를 낮추거나 다음 차례로 미룬다.",
             "stewards": ("clamav",)},
    "other": {"label": "알 수 없음", "mark": "❔", "story": "", "act": "", "stewards": ()},
}


def parse_ps(out: str) -> list[tuple[float, str]]:
    """`ps -eo pcpu=,args= --sort=-pcpu` 출력 → [(CPU %, 명령줄)]"""
    rows = []
    for line in (out or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        try:
            rows.append((float(parts[0]), parts[1][:200]))
        except ValueError:
            continue
    return rows


def match(text: str) -> tuple[str, str] | None:
    """(종류, 걸린 낱말) 또는 None"""
    low = text.lower()
    for key in ORDER:
        for w in KEYWORDS[key]:
            if w in low:
                return key, w
    return None


def kind_of(text: str) -> str | None:
    hit = match(text)
    return hit[0] if hit else None


def classify(procs: list[tuple[float, str]] | None, containers: dict[str, float] | None,
             playing: int | None = None, struggling: bool = False) -> dict:
    """원인 하나: {"cause", "label", "top": "이름 123%" 또는 "", "shares": {종류: CPU 합}}.
    프로세스 목록이 있으면 그것으로(컨테이너 안 프로세스도 호스트에서 보인다), 없으면 컨테이너 통계로."""
    shares: dict[str, float] = {}
    best: dict[str, tuple[float, str]] = {}
    rows = [(c, t) for c, t in (procs or [])] or [(c, n) for n, c in (containers or {}).items()]
    for cpu, text in rows:
        hit = match(text)
        if not hit or cpu <= 0:
            continue
        key = hit[0]
        shares[key] = shares.get(key, 0.0) + cpu
        if cpu > best.get(key, (0.0, ""))[0]:
            best[key] = (cpu, hit[1])   # 가장 많이 쓰는 것의 이름 (걸린 낱말)
    ranked = sorted(shares.items(), key=lambda kv: kv[1], reverse=True)
    cause = "other"
    if ranked and ranked[0][1] >= MIN_CPU and (len(ranked) == 1 or ranked[0][1] >= ranked[1][1] * LEAD):
        cause = ranked[0][0]
    elif playing and (struggling or shares.get("playback", 0) >= MIN_CPU / 2 or not ranked or ranked[0][1] < MIN_CPU):
        cause = "playback"   # 보는 사람이 있는데 뚜렷한 다른 원인이 없으면 재생
    elif ranked and ranked[0][1] >= MIN_CPU:
        cause = ranked[0][0]
    top = ""
    if cause in best:
        top = f"{best[cause][1]} {shares[cause]:.0f}%"
    elif cause == "playback" and playing:
        top = f"재생 {playing}건"
    return {"cause": cause, "label": CAUSES[cause]["label"], "top": top, "shares": {k: round(v, 1) for k, v in shares.items()}}


def story(cause: str, top: str = "") -> str:
    """경보 글에 붙일 한 문장 (원인 + 그래서 할 일). 모르면 빈 글"""
    c = CAUSES.get(cause) or CAUSES["other"]
    if not c["story"]:
        return ""
    return f" {c['story']}{f' ({top})' if top else ''}. {c['act']}"


def stewards_of(cause: str) -> tuple[str, ...]:
    return (CAUSES.get(cause) or CAUSES["other"])["stewards"]
