"""
포링푸드 연재 드라마 - 인물들이 실제로 대화하고, 그 대화가 쌓여서 줄거리가 된다.

[구조]
1) 장면(run_scene): SCENE_HOURS(기본 업무 시간 2시간 간격)마다 2~3명이 대사를 주고받는 짧은
   대본을 만들어 디스코드/카톡에 올린다. 출연진은 진행 중인 줄거리(아크)의 등장인물에서 고르고,
   가끔은 같은 장소에 있던 사람끼리의 우연한 만남(줄거리 없는 일상)도 섞는다.
2) 기억 3층:
   - 대화 원문: dialogues.jsonl (모든 장면)
   - 관계 기억: relationships.json의 각 쌍에 최근 사건(memories) 누적 + 친밀도
   - 줄거리: story_arcs.json의 진행 중인 사건 2~4개(등장인물/전제/전개/다음 떡밥/상태)와
     "지난 이야기"(오래된 전개를 압축한 요약)
3) 작가 회의(maybe_run_writers_room): 하루 한 번(WRITERS_ROOM_HOUR) 그날 장면과 바깥 세상
   변화(signals.py)를 읽고 줄거리를 진행/종결/새로 띄운다. 줄거리가 하나도 없으면 바로 연다.
4) 매시 일지(main.py)에도 story_block_for()로 진행 중인 줄거리를 넣어 같은 세계가 이어지게 한다.

모든 LLM 출력은 JSON으로 받고, 대사 화자가 출연진이 아니거나 형식이 깨지면 그 장면은 버린다
(엉뚱한 사람이 말하는 장면이 방송되는 것보다 한 번 쉬는 게 낫다).
"""
from __future__ import annotations

import json
import os
import random
import uuid
from datetime import datetime, timedelta

import requests

from .clock import now_kst
from . import characters, signals
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import API_URL, LITELLM_MASTER_KEY, LLM_MODEL, STATE_DIR

STORY_ENABLED = os.getenv("PORING_STORY_ENABLED", "1") not in ("0", "false", "False", "")
SCENE_HOURS = {int(h) for h in os.getenv("PORING_SCENE_HOURS", "9,11,13,15,17,20").split(",") if h.strip()}
WRITERS_ROOM_HOUR = int(os.getenv("PORING_WRITERS_ROOM_HOUR", "23"))
ARC_EPISODE_PROBABILITY = float(os.getenv("PORING_ARC_EPISODE_PROBABILITY", "0.8"))  # 나머지는 우연한 일상 장면

DIALOGUES_PATH = os.path.join(STATE_DIR, "dialogues.jsonl")
ARCS_PATH = os.path.join(STATE_DIR, "story_arcs.json")

MAX_ACTIVE_ARCS = 4
MAX_BEATS_PER_ARC = 15
MAX_PAIR_MEMORIES = 10
MAX_ARCHIVED_ARCS = 30
MAX_DIGESTS = 14
SCENE_TIMEOUT_SEC = 45
WRITERS_TIMEOUT_SEC = 90
_AMBIGUOUS_LOCATIONS = {"집"}


# ===================================================================== 공용
def _llm_json(system: str, user: str, *, temperature: float, timeout: int) -> dict | None:
    if not (API_URL and LITELLM_MASTER_KEY):
        return None
    try:
        res = requests.post(
            API_URL,
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}", "Content-Type": "application/json"},
            timeout=timeout,
        )
        if res.status_code != 200:
            print(f"[경고] 스토리 LLM 응답 오류: HTTP {res.status_code}")
            return None
        data = json.loads(res.json()["choices"][0]["message"]["content"])
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 스토리 LLM 호출 실패: {e}")
        return None


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_arcs() -> dict:
    data = _load_json(ARCS_PATH, {})
    data.setdefault("arcs", [])
    data.setdefault("archived", [])
    data.setdefault("previously", "")
    data.setdefault("digests", [])
    return data


def _active_arcs(data: dict) -> list[dict]:
    return [a for a in data["arcs"] if a.get("status") != "resolved"]


def _recent_scenes(limit: int = 6, since: datetime | None = None, name: str | None = None) -> list[dict]:
    if not os.path.exists(DIALOGUES_PATH):
        return []
    scenes = []
    with open(DIALOGUES_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                s = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since and s.get("ts", "") < since.isoformat():
                continue
            if name and name not in s.get("participants", []):
                continue
            scenes.append(s)
    return scenes[-limit:]


# ===================================================================== 인물 표기
def _display_keys(roster: list[dict]) -> dict[str, dict]:
    """LLM에게 보여줄 인물 키. 이름이 겹치면(예: 회사가 다른 동명이인) "이름(회사)"로 구분한다."""
    counts: dict[str, int] = {}
    for c in roster:
        counts[c["name"]] = counts.get(c["name"], 0) + 1
    keys = {}
    for c in roster:
        key = c["name"] if counts[c["name"]] == 1 else f"{c['name']}({c['company'].split(' ')[0]})"
        keys[key] = c
    return keys


def _cast_line(key: str, c: dict) -> str:
    return (f"- {key}: {c['company']} {c['dept']} {c['rank']} / 겉모습: {c.get('outer_persona', '')} "
            f"/ 속마음: {c.get('inner_truth', '')}")


# ===================================================================== 장면
def _pick_scene(roster: list[dict], states: dict, rnd: random.Random) -> tuple[list[dict], dict | None]:
    """이번 장면의 출연진(roster 항목 2~3명)과 줄거리(없으면 None)를 고른다."""
    keys = _display_keys(roster)
    awake = {cid for cid, s in states.items() if s.get("state") != "자는 중"}
    data = load_arcs()
    arcs = _active_arcs(data)

    if arcs and rnd.random() < ARC_EPISODE_PROBABILITY:
        # 가장 오래 진전이 없던 줄거리부터 (같은 줄거리만 계속 나오지 않게)
        arcs.sort(key=lambda a: a.get("last_scene_at", ""))
        for arc in arcs[:2]:
            cast = [keys[k] for k in arc.get("cast", []) if k in keys and keys[k]["id"] in awake]
            if len(cast) >= 2:
                size = 3 if len(cast) >= 3 and rnd.random() < 0.3 else 2
                return rnd.sample(cast, size), arc

    pair = characters.find_colocated_pair({cid: s for cid, s in states.items() if cid in awake}, rnd)
    if not pair:
        return [], None
    by_id = characters.roster_by_id(roster)
    return [by_id[pair[0]], by_id[pair[1]]], None


def _pair_memories(cast: list[dict]) -> list[str]:
    rels = characters.load_relationships()
    lines = []
    for i, a in enumerate(cast):
        for b in cast[i + 1:]:
            rel = rels.get(characters._rel_key(a["id"], b["id"]), {})
            mems = rel.get("memories") or ([{"event": rel["summary"]}] if rel.get("summary") else [])
            recent = " / ".join(m["event"] for m in mems[-4:]) or "특별한 인연 없음"
            lines.append(f"- {a['name']} ↔ {b['name']} (친밀도 {rel.get('affinity', 0)}): {recent}")
    return lines


def _update_relationships(cast: list[dict], changes: list, summary: str, now: datetime) -> None:
    rels = characters.load_relationships()
    by_name = {c["name"]: c for c in cast}
    touched = set()
    for ch in changes if isinstance(changes, list) else []:
        a, b = by_name.get(str(ch.get("a", "")).split("(")[0]), by_name.get(str(ch.get("b", "")).split("(")[0])
        if not a or not b or a is b:
            continue
        key = characters._rel_key(a["id"], b["id"])
        rel = rels.get(key, {"affinity": 0, "summary": "", "count": 0})
        try:
            delta = max(-10, min(10, int(ch.get("delta", 0))))
        except (TypeError, ValueError):
            delta = 0
        memory = str(ch.get("memory") or summary)[:200]
        rel["affinity"] = max(-100, min(100, rel.get("affinity", 0) + delta))
        rel["summary"] = memory
        rel["count"] = rel.get("count", 0) + 1
        rel["last_interaction"] = now.isoformat()
        rel.setdefault("memories", []).append({"date": now.strftime("%Y-%m-%d"), "event": memory})
        rel["memories"] = rel["memories"][-MAX_PAIR_MEMORIES:]
        rels[key] = rel
        touched.add(key)
    # LLM이 관계 변화를 안 줬어도 같이 등장한 사이에는 이번 장면을 기억으로 남긴다
    for i, a in enumerate(cast):
        for b in cast[i + 1:]:
            key = characters._rel_key(a["id"], b["id"])
            if key in touched:
                continue
            rel = rels.get(key, {"affinity": 0, "summary": "", "count": 0})
            rel.setdefault("memories", []).append({"date": now.strftime("%Y-%m-%d"), "event": summary[:200]})
            rel["memories"] = rel["memories"][-MAX_PAIR_MEMORIES:]
            rel["count"] = rel.get("count", 0) + 1
            rel["last_interaction"] = now.isoformat()
            rels[key] = rel
    characters.save_relationships(rels)


def _format_scene(scene: dict, arc_title: str | None, kakao: bool) -> str:
    names = " × ".join(scene["participants"])
    header = f"🎬 [{scene['location']} · {scene['hour']:02d}:00] {names}"
    out = [header if kakao else f"🎬 **[{scene['location']} · {scene['hour']:02d}:00] {names}**"]
    if arc_title:
        out.append(f"📖 {arc_title}" if kakao else f"-# 📖 {arc_title}")
    for ln in scene["lines"]:
        inner = ln.get("inner")
        if kakao:
            out.append(f"{ln['speaker']}: \"{ln['line']}\"" + (f" (속마음: {inner})" if inner else ""))
        else:
            out.append(f"**{ln['speaker']}**: \"{ln['line']}\"" + (f" *(속마음: {inner})*" if inner else ""))
    if scene.get("narration"):
        out.append(f"— {scene['narration']}")
    return "\n".join(out)


def run_scene(roster: list[dict], states: dict, rnd: random.Random) -> dict | None:
    """
    장면 하나를 만들고 기억/줄거리/히스토리에 반영한다. 방송용 텍스트를 돌려준다(전송은 호출부).
    반환: {"discord": str, "kakao": str, "participants": [이름...], "summary": str} 또는 None
    """
    now = now_kst()
    cast, arc = _pick_scene(roster, states, rnd)
    if len(cast) < 2:
        print("[스토리] 이번 장면에 등장할 인물을 못 골라서 건너뜁니다.")
        return None

    keys = {c["id"]: k for k, c in _display_keys(roster).items()}
    cast_keys = [keys[c["id"]] for c in cast]
    data = load_arcs()
    whereabouts = [
        f"- {keys[c['id']]}: 지금 {states.get(c['id'], {}).get('location', '어딘가')}에서 "
        f"{states.get(c['id'], {}).get('activity', '')}"
        for c in cast
    ]
    recent = [f"- {s.get('ts', '')[5:16].replace('T', ' ')} {s.get('summary', '')}" for s in _recent_scenes(5)]
    arc_block = (
        f"[이번 장면이 이어갈 줄거리: {arc['title']}]\n전제: {arc.get('premise', '')}\n"
        f"지금까지: {arc.get('summary_so_far', '')}\n"
        f"최근 전개: {' / '.join(b['text'] for b in arc.get('beats', [])[-4:]) or '아직 없음'}\n"
        f"다음 떡밥(이번 장면에서 조금 진전시켜라): {arc.get('next_hook', '')}\n"
        if arc else
        "[이번 장면은 줄거리와 상관없는 우연한 마주침/일상 대화다. 진행 중인 줄거리를 아는 척 슬쩍 언급해도 좋다]\n"
    )

    system = (
        "너는 가상의 회사 세계(포링푸드와 라이벌 회사들)를 연재 드라마처럼 써 내려가는 작가다. "
        "인물마다 겉모습과 속마음이 다르고, 그 괴리가 재미의 핵심이다. 대사는 각자 말투가 확실히 달라야 한다."
    )
    user = (
        "등장인물 (이 사람들만 말할 수 있다):\n" + "\n".join(_cast_line(k, c) for k, c in zip(cast_keys, cast)) + "\n\n"
        "지금 위치/하는 일:\n" + "\n".join(whereabouts) + "\n\n"
        "이 사람들 사이의 지난 일:\n" + ("\n".join(_pair_memories(cast)) or "- 없음") + "\n\n"
        f"[지난 이야기 요약]\n{data.get('previously') or '아직 없음'}\n\n"
        f"{arc_block}\n"
        "최근 다른 장면들:\n" + ("\n".join(recent) or "- 없음") + "\n\n"
        f"{signals.format_block(signals.collect())}\n\n"
        "작성 규칙:\n"
        f"- {now.hour}시에 있을 법한 장소에서 대사 4~8줄짜리 짧은 장면을 써라. 지금 위치가 같으면 그곳, "
        "다르면 둘이 마주칠 만한 곳(사내 휴게실, 엘리베이터, 회사 앞 편의점 등)으로 정해라.\n"
        "- 바깥 세상 변화 중 어울리는 게 있으면 인물들이 그걸 화제로 삼거나 그 영향을 받게 해라(전부 쓸 필요는 없다).\n"
        "- 지난 일을 기억하고 있는 티를 내라. 같은 대화를 반복하지 말고 관계가 조금씩 변하게 해라.\n"
        "- 'inner'(속마음)는 꼭 필요한 대사 1~2개에만 짧게.\n"
        "- 실존 인물/정치/혐오 소재는 쓰지 마라.\n"
        "반드시 JSON으로만 응답:\n"
        '{"location": "장소", "lines": [{"speaker": "등장인물 이름 그대로", "line": "대사", "inner": "속마음(선택)"}], '
        '"narration": "장면을 마무리하는 한 문장", "summary": "누가 무엇을 했는지 한 줄 요약", '
        '"arc_beat": "이 장면으로 줄거리가 어떻게 진전됐는지 한 줄(줄거리 장면이 아니면 빈 문자열)", '
        '"relationship": [{"a": "이름", "b": "이름", "delta": -10~10 정수, "memory": "둘 사이에 남은 기억 한 줄"}]}'
    )
    out = _llm_json(system, user, temperature=0.9, timeout=SCENE_TIMEOUT_SEC)
    if not out:
        return None

    allowed = {k: k for k in cast_keys} | {c["name"]: k for k, c in zip(cast_keys, cast)}
    lines = []
    for ln in out.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        speaker = allowed.get(str(ln.get("speaker", "")).strip())
        text = str(ln.get("line", "")).strip()
        if not speaker or not text:
            print(f"[경고] 출연진이 아닌 화자/빈 대사가 섞여서 장면을 버립니다: {ln.get('speaker')!r}")
            return None
        inner = str(ln.get("inner") or "").strip()
        lines.append({"speaker": speaker, "line": text[:300], **({"inner": inner[:120]} if inner else {})})
    if not 2 <= len(lines) <= 12:
        print(f"[경고] 대사 수가 이상해서 장면을 버립니다 ({len(lines)}줄)")
        return None

    summary = str(out.get("summary") or "").strip()[:200] or f"{' · '.join(cast_keys)}의 대화"
    scene = {
        "id": uuid.uuid4().hex[:12],
        "ts": now.isoformat(timespec="seconds"),
        "hour": now.hour,
        "location": str(out.get("location") or "사내 어딘가").strip()[:40],
        "participants": cast_keys,
        "arc_id": arc.get("id") if arc else None,
        "lines": lines,
        "narration": str(out.get("narration") or "").strip()[:300],
        "summary": summary,
    }
    with open(DIALOGUES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(scene, ensure_ascii=False) + "\n")

    if arc:
        beat = str(out.get("arc_beat") or "").strip() or summary
        data = load_arcs()
        for a in data["arcs"]:
            if a.get("id") == arc.get("id"):
                a.setdefault("beats", []).append({"ts": scene["ts"], "text": beat[:200]})
                a["beats"] = a["beats"][-MAX_BEATS_PER_ARC:]
                a["last_scene_at"] = scene["ts"]
        _save_json(ARCS_PATH, data)

    _update_relationships(cast, out.get("relationship") or [], summary, now)
    for c, k in zip(cast, cast_keys):
        others = ", ".join(x for x in cast_keys if x != k)
        characters.append_character_history(c["name"], {
            "timestamp": scene["ts"], "location": scene["location"],
            "activity": f"{others}와 대화", "state": "대화 중", "narrative": summary,
        })

    title = arc.get("title") if arc else None
    print(f"[스토리] 장면 생성: {summary}")
    return {
        "discord": _format_scene(scene, title, kakao=False),
        "kakao": _format_scene(scene, title, kakao=True),
        "participants": cast_keys,
        "summary": summary,
    }


# ===================================================================== 작가 회의
def maybe_run_writers_room(roster: list[dict], force: bool = False) -> bool:
    """
    하루 한 번(WRITERS_ROOM_HOUR) 또는 줄거리가 하나도 없을 때 작가 회의를 연다.
    그날 장면 + 바깥 세상 변화를 보고 줄거리를 진행/종결/새로 띄운다. 실행했으면 True.
    """
    if not STORY_ENABLED:
        return False
    now = now_kst()
    today = now.strftime("%Y-%m-%d")
    data = load_arcs()
    bootstrap = not _active_arcs(data)
    if not (force or bootstrap or (now.hour == WRITERS_ROOM_HOUR and data.get("last_writers_room") != today)):
        return False

    print("[스토리] 작가 회의 시작" + (" (첫 줄거리 만들기)" if bootstrap else ""))
    signals.refresh_daily_topic()
    keys = _display_keys(roster)
    today_scenes = _recent_scenes(limit=30, since=now.replace(hour=0, minute=0, second=0, microsecond=0))
    arcs_desc = []
    for a in data["arcs"]:
        if a.get("status") == "resolved":
            continue
        arcs_desc.append(
            f"- id={a['id']} | {a['title']} | 상태={a.get('status', 'active')} | 시작={a.get('started', '')[:10]} | "
            f"등장={', '.join(a.get('cast', []))}\n  전제: {a.get('premise', '')}\n"
            f"  지금까지: {a.get('summary_so_far', '')}\n"
            f"  최근 전개: {' / '.join(b['text'] for b in a.get('beats', [])[-6:])}\n"
            f"  다음 떡밥: {a.get('next_hook', '')}"
        )

    system = (
        "너는 가상의 회사 세계(포링푸드와 라이벌 회사들)를 장기 연재 드라마로 끌고 가는 메인 작가다. "
        "현실의 회사처럼 바깥 세상의 변화가 사람들 일상과 사건에 영향을 주게 만든다."
    )
    user = (
        "[등장 가능한 인물] (cast에는 이 이름을 그대로 써라)\n"
        + "\n".join(f"- {k}: {c['company']} {c['dept']} {c['rank']} / {c.get('outer_persona', '')}" for k, c in keys.items())
        + f"\n\n[지난 이야기 요약]\n{data.get('previously') or '아직 없음'}\n\n"
        "[진행 중인 줄거리]\n" + ("\n".join(arcs_desc) or "- 없음 (새로 2~3개 만들어라)") + "\n\n"
        "[오늘 있었던 장면들]\n" + ("\n".join(f"- {s['ts'][11:16]} {s['summary']}" for s in today_scenes) or "- 없음")
        + f"\n\n{signals.format_block(signals.collect(include_factory=False))}\n\n"
        "할 일:\n"
        "1. 진행 중인 줄거리마다 오늘 장면을 반영해 summary_so_far와 next_hook(다음에 일어날 일 한 줄)을 갱신해라. "
        "충분히 무르익었으면 status를 climax로, 결말이 났으면 resolved로 바꿔라(시작한 지 10일 넘은 건 정리 방향으로).\n"
        f"2. 진행 중(active/climax) 줄거리가 2개 미만이면 새로 만들어 2~{MAX_ACTIVE_ARCS - 1}개를 유지해라. 새 줄거리 중 "
        "하나는 [바깥 세상 변화]에서 의미 있는 게 있으면 거기서 출발해라(예: 공장 이상 → 생산 차질 책임 공방, "
        "사내 보안 → 보안팀 비상, 월말 → 마감 전쟁). 회사 안 사건, 사내 연애/우정, 라이벌 회사와의 신경전 등 "
        "종류를 섞어라. 등장인물은 2~4명.\n"
        "3. previously: 지금까지의 큰 흐름을 600자 이내로 압축 요약(오래된 일은 짧게).\n"
        "4. today_digest: 오늘 포링푸드에 있었던 일을 3~4문장으로.\n"
        "5. 실존 인물/정치/혐오 소재는 쓰지 마라.\n"
        "반드시 JSON으로만 응답:\n"
        '{"arcs": [{"id": "기존 id 또는 new", "title": "제목", "cast": ["이름"], "premise": "전제", '
        '"status": "active|climax|resolved", "summary_so_far": "지금까지", "next_hook": "다음 떡밥"}], '
        '"previously": "...", "today_digest": "..."}'
    )
    out = _llm_json(system, user, temperature=0.8, timeout=WRITERS_TIMEOUT_SEC)
    if not out:
        return False

    data = load_arcs()  # 회의 중 장면이 추가됐을 수 있으니 다시 읽는다
    by_id = {a["id"]: a for a in data["arcs"]}
    for item in out.get("arcs") or []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        cast = [k for k in item.get("cast", []) if k in keys]
        arc = by_id.get(str(item.get("id")))
        if arc is None:
            if len(cast) < 2:
                continue
            arc = {"id": f"arc-{uuid.uuid4().hex[:6]}", "started": now.isoformat(timespec="seconds"), "beats": []}
            data["arcs"].append(arc)
            by_id[arc["id"]] = arc
            print(f"[스토리] 새 줄거리: {item['title']}")
        arc["title"] = str(item["title"])[:60]
        if len(cast) >= 2:
            arc["cast"] = cast
        for field, limit in (("premise", 300), ("summary_so_far", 500), ("next_hook", 200)):
            if item.get(field):
                arc[field] = str(item[field])[:limit]
        status = item.get("status")
        if status in ("active", "climax", "resolved"):
            if status == "resolved" and arc.get("status") != "resolved":
                arc["resolved_at"] = now.isoformat(timespec="seconds")
                print(f"[스토리] 줄거리 종결: {arc['title']}")
            arc["status"] = status
        arc.setdefault("status", "active")

    # 종결된 줄거리는 보관함으로, 진행 중은 최대 MAX_ACTIVE_ARCS개
    data["archived"] = (data["archived"] + [a for a in data["arcs"] if a.get("status") == "resolved"])[-MAX_ARCHIVED_ARCS:]
    active = [a for a in data["arcs"] if a.get("status") != "resolved"]
    data["arcs"] = active[:MAX_ACTIVE_ARCS]
    if out.get("previously"):
        data["previously"] = str(out["previously"])[:800]
    if out.get("today_digest"):
        data["digests"] = (data["digests"] + [{"date": today, "text": str(out["today_digest"])[:600]}])[-MAX_DIGESTS:]
    data["last_writers_room"] = today
    _save_json(ARCS_PATH, data)
    print(f"[스토리] 작가 회의 완료 - 진행 중 줄거리 {len(data['arcs'])}개")
    return True


# ===================================================================== 일지/조회용
def story_block_for(name: str) -> str:
    """매시 일지 프롬프트에 넣을 "진행 중인 줄거리" 블록. 그 인물이 얽힌 줄거리를 우선한다."""
    if not STORY_ENABLED:
        return ""
    arcs = _active_arcs(load_arcs())
    if not arcs:
        return ""
    arcs.sort(key=lambda a: 0 if any(name == k.split("(")[0] for k in a.get("cast", [])) else 1)
    lines = []
    for a in arcs[:3]:
        involved = " (너도 얽혀 있음)" if any(name == k.split("(")[0] for k in a.get("cast", [])) else ""
        latest = a.get("beats", [])[-1]["text"] if a.get("beats") else a.get("premise", "")
        lines.append(f"- {a['title']}{involved}: {latest}")
    today = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
    mine = _recent_scenes(limit=2, since=today, name=name)
    block = "\n[회사에서 진행 중인 이야기]\n" + "\n".join(lines) + "\n"
    if mine:
        block += "[오늘 네가 직접 겪은 장면]\n" + "\n".join(f"- {s['summary']}" for s in mine) + "\n"
    block += ("- 위 이야기를 알고 있는 사람으로서 일지에 1~2문장 정도 자연스럽게 반영해라. "
              "진행 중인 사건과 모순되는 내용은 쓰지 마라.\n")
    return block


def story_so_far(character: str = "") -> str:
    """대화 중 "포링푸드 요즘 무슨 일 있어?" 질문용 요약."""
    data = load_arcs()
    parts = []
    if data.get("previously"):
        parts.append(f"[지난 이야기] {data['previously']}")
    arcs = _active_arcs(data)
    if arcs:
        parts.append("[진행 중인 이야기]")
        for a in arcs:
            parts.append(f"- {a['title']} ({', '.join(a.get('cast', []))}): {a.get('summary_so_far') or a.get('premise', '')}"
                         + (f" / 다음: {a['next_hook']}" if a.get("next_hook") else ""))
    if data.get("digests"):
        d = data["digests"][-1]
        parts.append(f"[{d['date']} 하루 요약] {d['text']}")
    scenes = _recent_scenes(limit=5, since=now_kst() - timedelta(days=3), name=character or None)
    if scenes:
        parts.append("[최근 장면]")
        parts.extend(f"- {s['ts'][5:16].replace('T', ' ')} {', '.join(s['participants'])}: {s['summary']}" for s in scenes)
    return "\n".join(parts) if parts else "아직 쌓인 이야기가 없어요."
