import os
import random
from datetime import datetime

from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from . import bookoasis
from . import loader
from . import processor
from . import generator
from . import notifier
from . import checker
from . import characters
from . import signals
from . import story
from .config import INTERACTION_HOURS

# [신규] 애순이가 매시간 스포트라이트(=디스코드/카톡으로 방송)를 독점하지 않고, 깨어있는
# 다른 인물에게도 가중치 랜덤으로 돌아가게 한다. LLM 호출/메시지는 시간당 1건으로 그대로
# 유지된다(주인공만 바뀜) - .env의 AESUN_SPOTLIGHT_WEIGHT로 애순이 등장 비중 조정 가능.
AESUN_SPOTLIGHT_WEIGHT = float(os.getenv("AESUN_SPOTLIGHT_WEIGHT", "0.4"))


def _pick_spotlight(roster: list, states: dict, rnd: random.Random, aesun_awake: bool):
    """이번 시간 방송 주인공을 고른다. "애순이"(문자열) 또는 로스터 항목(dict)을 반환."""
    others_awake = [
        c for c in roster
        if c["name"] != "애순이" and states.get(c["id"], {}).get("state") != "자는 중"
    ]
    candidates: list[tuple] = []
    if aesun_awake:
        candidates.append(("애순이", AESUN_SPOTLIGHT_WEIGHT))
    if others_awake:
        remaining = max(0.0, 1.0 - (AESUN_SPOTLIGHT_WEIGHT if aesun_awake else 0.0))
        share = remaining / len(others_awake)
        for c in others_awake:
            candidates.append((c, share if share > 0 else 1.0))  # 애순이가 못 나오는 시간엔 균등 배분

    if not candidates:
        return "애순이"  # 아무도 없으면 안전하게 애순이로

    total = sum(w for _, w in candidates)
    r = rnd.uniform(0, total)
    upto = 0.0
    for cand, w in candidates:
        upto += w
        if upto >= r:
            return cand
    return candidates[-1][0]


def _run_world_tick(aesun_status: dict) -> None:
    """
    [취침 중 전용] 애순이가 자는 시간엔 스포트라이트 로테이션 없이, 상태 갱신과 상호작용만
    수행한다 (그 시간대엔 다른 인물들도 대부분 자고 있어서 로테이션 후보가 거의 없다).
    """
    now = datetime.now()
    rnd = random.Random(now.strftime("%Y-%m-%d-%H"))

    roster = characters.load_roster()
    if not roster:
        print("[경고] 조직도를 못 읽어서 다인물 틱을 건너뜁니다.")
        return
    roster_map = characters.roster_by_id(roster)

    aesun_state = {
        "location": aesun_status.get("location"),
        "activity": aesun_status.get("activity"),
        "state": aesun_status.get("state"),
        "updated_at": now.isoformat(),
    }
    states = characters.update_all_states(roster, rnd, aesun_state=aesun_state)
    print(f"[다인물] {len(states)}명 상태 갱신 완료")
    _run_world_tick_common(roster, roster_map, states, rnd)


def _run_world_tick_common(roster: list, roster_map: dict, states: dict, rnd: random.Random,
                            exclude_name: str | None = None) -> None:
    """
    [공용] 애순이 제외 인물들의 상태를 히스토리에 남기고, 지정 시각(INTERACTION_HOURS)에만
    상호작용을 시도한다. exclude_name이 주어지면 그 사람은 이미 별도로(스포트라이트 리포트로)
    히스토리에 기록했으니 여기서 중복 기록하지 않는다.
    """
    now = datetime.now()

    for cid, info in states.items():
        name = info.get("name", cid)
        if name == "애순이" or name == exclude_name:
            continue
        characters.append_character_history(name, {
            "timestamp": now.isoformat(),
            "time_tag": processor.get_time_tag(),
            "location": info.get("location"),
            "activity": info.get("activity"),
            "state": info.get("state"),
        })

    # [신규] 연재 드라마 장면 - 정해진 시각(PORING_SCENE_HOURS)마다 인물들이 실제로 대화하는 장면을
    # 만들어 디스코드/카톡에 올린다. 이 기능이 켜져 있으면 예전 "우연한 마주침" 요약은 쓰지 않는다.
    if story.STORY_ENABLED:
        if now.hour in story.SCENE_HOURS:
            try:
                scene = story.run_scene(roster, states, rnd)
            except Exception as e:  # noqa: BLE001
                print(f"[에러] 장면 생성 실패: {e}")
                scene = None
            if scene:
                notifier.send_to_discord(scene["discord"])
                notifier.send_to_local_bot(scene["kakao"])
                print(f"[스토리] 장면 전송 완료: {scene['summary']}")
        return

    if now.hour not in INTERACTION_HOURS:
        return

    pair = characters.find_colocated_pair(states, rnd)
    if not pair:
        print("[다인물] 이번 시각엔 같은 장소에 있는 인물 쌍이 없어 상호작용을 건너뜁니다.")
        return

    print(f"[다인물] 상호작용 시도: {pair[0]} x {pair[1]}")
    result = characters.generate_interaction(pair, states, roster_map)
    if not result:
        print("[다인물] 상호작용 생성 실패 (LLM 미설정이거나 오류)")
        return

    episode_text = (
        f"👥 [{result['location']}에서 우연히] {result['participants'][0]} x {result['participants'][1]}\n"
        f"{result['episode']}"
    )
    for name in result["participants"]:
        characters.append_character_history(name, {
            "timestamp": now.isoformat(),
            "time_tag": processor.get_time_tag(),
            "location": result["location"],
            "activity": f"{result['participants'][1] if name == result['participants'][0] else result['participants'][0]}와 상호작용",
            "state": "상호작용 중",
            "narrative": result["episode"],
        })

    notifier.send_to_discord(episode_text)
    notifier.send_to_local_bot(episode_text)
    print(f"[다인물] 상호작용 전송 완료: {episode_text}")


def _maybe_run_writers_room() -> None:
    """
    [신규] 연재 드라마 - 하루 한 번(또는 줄거리가 하나도 없을 때) 작가 회의로 줄거리를 갱신한다.
    회차 맨 앞에서 돌려서, 같은 회차의 일지/장면이 갱신된 줄거리를 바로 쓰게 한다.
    """
    if not story.STORY_ENABLED:
        return
    try:
        roster = characters.load_roster()
        if roster:
            story.maybe_run_writers_room(roster)
    except Exception as e:  # noqa: BLE001 - 작가 회의가 실패해도 이번 회차 나머지는 계속
        print(f"[에러] 작가 회의 실패: {e}")


def main():
    """
    애순이 봇 메인 파이프라인
    1. 스케줄 확인 및 생산량 통계/서버 상태 점검
    2. 자는 중이면 상태 업데이트 후 종료
    3. 깨어있으면 날씨/공장상태/이슈 생성 - 그리고 [신규] 이번 시간 방송 주인공을
       애순이 또는 깨어있는 다른 인물 중에서 가중치 랜덤으로 고른다(스포트라이트 로테이션)
    4. 다인물 시뮬레이션(상태 갱신/상호작용)도 같이 굴린다
    """
    _maybe_run_writers_room()

    # 1. 현재 스케줄 및 상태 확인
    location, activity, focus, state, is_sleeping = processor.get_aesun_detailed_schedule()
    time_tag = processor.get_time_tag()
    now = datetime.now()
    now_str = now.isoformat()

    # [통계] 생산량/영업 판매수량 및 오늘의 기분 가져오기
    prod_count, progress_rate = processor.get_production_stats()
    stats = (prod_count, progress_rate)
    sales_count = processor.get_sales_stats()
    mood = processor.get_daily_mood()

    print(f"[통계] 현재 생산량: {prod_count}건 ({progress_rate}%) / 영업 판매수량: {sales_count}건")
    print(f"[감정] 오늘의 애순이: {mood}")

    # 2. 취침 중일 경우 처리 (기존과 동일 - 스포트라이트 로테이션 없음)
    if is_sleeping:
        print(f"[정보] 현재 애순이는 자는 시간입니다 ({state}). 상태 파일만 업데이트합니다.")
        status_payload = {
            "timestamp": now_str,
            "time_tag": time_tag,
            "title": "애순이는 취침 중",
            "location": location,
            "activity": activity,
            "narrative": f"지금은 애순이가 {location}에서 {activity} 시간입니다. 건드리지 마세요.",
            "ragnarok_status": "캐릭터는 마을에서 자동 낚시 중이거나 휴식 중",
            "cynical_thought": "잠은 죽어서 자는 거라지만, 내일 출근하려면 지금 자야 한다.",
            "full_report": f"[{time_tag}] 애순이는 현재 자는 중입니다... Zzz",
            "state": state
        }
        notifier.save_to_file(status_payload)
        notifier.append_to_history(status_payload)
        _run_world_tick(status_payload)
        return

    # 3. 깨어있는 시간일 경우: 전체 파이프라인 실행
    print(f"[정보] 애순이 활동 시작: {state} 모드")

    factory_msg, is_factory_ok = checker.check_poring_factory_status()
    print(f"[체크] 포링푸드 공장 상태: {factory_msg}")

    org_data, _, persona_data = loader.load_resources()

    print("[1/4] 광주 실시간 날씨 조회 중...")
    weather_info = processor.fetch_gwangju_weather()
    signals.remember_weather(weather_info)  # 장면/작가 회의가 "바깥 세상 변화"로 다시 쓴다

    # [신규] 스포트라이트 로테이션 - 다인물 상태를 먼저 갱신하고 그중에서 이번 시간 주인공을 뽑는다
    # [변경] 이슈 생성보다 먼저 뽑는다 - 애순이가 주인공일 때만 애순이 담당 업무(북오아시스 서고)
    # 소식을 이슈/보고서에 넣어야 해서. (주인공 선택은 이슈 내용과 무관해서 순서를 바꿔도 같다)
    rnd = random.Random(now.strftime("%Y-%m-%d-%H"))
    roster = characters.load_roster()
    roster_map = characters.roster_by_id(roster) if roster else {}
    aesun_light_state = {"location": location, "activity": activity, "state": state, "updated_at": now_str}
    states = characters.update_all_states(roster, rnd, aesun_state=aesun_light_state) if roster else {}
    spotlight = _pick_spotlight(roster, states, rnd, aesun_awake=True) if roster else "애순이"

    is_aesun_spotlight = not isinstance(spotlight, dict)
    narrator_name = "애순이" if is_aesun_spotlight else spotlight["name"]
    print(f"[스포트라이트] 이번 시간 주인공: {narrator_name}")

    # [신규] 애순이 겸직 - 사내 자료실(북오아시스) 점검. 애순이가 주인공일 때만 점검해서, 신간 소식이
    # 다른 인물 방송 시간에 소리 없이 "이미 본 것"으로 처리돼 버리지 않게 한다.
    bookoasis_block = ""
    if is_aesun_spotlight:
        print("[1.5/4] 사내 자료실(북오아시스) 점검 중...")
        bookoasis_block = bookoasis.build_prompt_block(bookoasis.check_bookoasis())

    # [신규] 연재 드라마의 진행 중인 줄거리를 일지에도 반영한다 (이번 시간 주인공 기준)
    story_block = story.story_block_for(narrator_name)

    print("[2/4] 조직도 기반 동적 이슈 생성 중...")
    dynamic_issue = processor.generate_dynamic_issue(
        org_data, weather_info, factory_status=factory_msg, our_count=prod_count,
        bookoasis_block=bookoasis_block, story_block=story_block,
    )

    if is_aesun_spotlight:
        print("[3/4] 애순이 시점으로 보고서 변환 중...")
        report_data = generator.generate_aesun_report(
            dynamic_issue, time_tag, org_data, persona_data, weather_info, stats, mood, sales_count,
            bookoasis_block=bookoasis_block, story_block=story_block,
        )
        status_payload = {
            "timestamp": now_str,
            "time_tag": time_tag,
            "title": dynamic_issue.get('title', '오늘의 사건'),
            "location": location,
            "activity": activity,
            "weather": weather_info,
            "factory_status": factory_msg,
            "state": state,
            "mood": mood,
            **report_data
        }
        notifier.save_to_file(status_payload)
        notifier.append_to_history(status_payload)
    else:
        char = spotlight
        cinfo = states.get(char["id"], {})
        c_location = cinfo.get("location", "")
        c_activity = cinfo.get("activity", "")
        c_state = cinfo.get("state", "")
        c_mood = processor.get_daily_mood()

        print(f"[3/4] {narrator_name} 시점으로 보고서 변환 중...")
        report_data = generator.generate_generic_character_report(
            char, dynamic_issue, time_tag, weather_info, c_mood, c_location, c_activity, c_state,
            story_block=story_block,
        )
        # 애순이 자신은 방송 주인공이 아니어도 위치/활동은 계속 가볍게 갱신해둔다
        # (get_current_status("애순이")가 항상 최신 위치를 보여줄 수 있게)
        notifier.save_to_file({
            "timestamp": now_str, "time_tag": time_tag,
            "location": location, "activity": activity, "state": state,
        })
        characters.append_character_history(narrator_name, {
            "timestamp": now_str, "time_tag": time_tag,
            "location": c_location, "activity": c_activity, "state": c_state,
            "title": dynamic_issue.get('title', '오늘의 사건'),
            "narrative": report_data.get("narrative"),
        })

    # 4. 다인물 시뮬레이션(배경 상태 히스토리 + 상호작용) - 스포트라이트 인물은 이미 기록했으니 제외
    if roster:
        _run_world_tick_common(roster, roster_map, states, rnd,
                                exclude_name=None if is_aesun_spotlight else narrator_name)

    # 5. 결과 전송
    if report_data:
        print("[4/4] 결과 전송 중...")
        full_text = report_data["full_report"]
        target_phrase = f"💬 {narrator_name}의 한마디"
        if target_phrase in full_text:
            spacer = "\u200b" * 500  # 카톡 "더보기" 접힘용 (f-string 밖에서 만들어 Python 3.11에서도 동작)
            katalk_msg = full_text.replace(target_phrase, f"\n━━━━━━━━━━━━━━\n{spacer}\n{target_phrase}")
        else:
            katalk_msg = full_text

        notifier.send_to_discord(full_text)
        notifier.send_to_local_bot(katalk_msg)

        print(f"\n=== [전송 완료: {narrator_name}] ===\n{full_text}\n")
    else:
        print("[오류] 보고서 생성에 실패하여 전송을 취소합니다.")


if __name__ == "__main__":
    main()
