# 토론 시뮬레이터 (debate)

정해 둔 인물들이 한 주제로 **횟수 제한 없이** 토론하는 독립 스크립트다. 디스코드 봇(이 저장소의 나머지)과는 코드도 설정도 공유하지 않는다.
이 폴더만 복사해 가서 써도 된다. 표준 라이브러리만 쓰므로 설치할 패키지가 없다 (Python 3.9+).

## 실행

```bash
cd debate
cp debate.example.json debate.json           # 주제/참가자 고치기
export DEBATE_BASE_URL=http://localhost:4000 # LiteLLM 등 OpenAI 호환 주소 (/v1 있어도 됨)
export DEBATE_API_KEY=sk-...                 # 키 (없으면 생략)

python3 debate.py debate.json                # Ctrl+C로 멈출 때까지 계속
python3 debate.py debate.json --turns 30     # 30마디까지만
python3 debate.py debate.json --resume       # 멈춘 토론 이어서
python3 debate.py debate.json --topic "AI가 일자리를 빼앗는가" --delay 3
```

- 주소/키는 `DEBATE_BASE_URL`/`DEBATE_API_KEY`, 없으면 `LITELLM_BASE_URL`/`LITELLM_API_KEY`를 쓴다.
  `debate/.env` 파일에 적어 둬도 된다 (`.env`는 git에 안 올라간다).
- 기록은 설정 파일 옆 `runs/`에 저장된다: `주제.md`(읽는 용), `주제.json`(이어하기용). 발언마다 저장하니 언제 멈춰도 남는다.
- Ctrl+C로 멈추면 그때까지의 토론을 사회자 모델이 정리해서 기록 끝에 붙인다 (`--no-summary`면 생략).

## 설정 (debate.json)

| 키 | 뜻 |
|---|---|
| `topic` | 토론 주제 |
| `participants` | 참가자 2명 이상 - `name`, `persona`(어떤 사람인지), `stance`(입장), `speech`(말투, 선택), `model`(이 사람만 다른 모델, 선택), `reasoning_effort`(추론 모델이면 `low`, 선택), `temperature`(선택) |
| `model` | 기본 모델 (LiteLLM의 model_name) |
| `moderator_model` | 요약/사회자 모델 (없으면 `model`) |
| `turn_length` | 한 번에 말하는 길이 (기본 "2~4문장") |
| `delay` | 발언 사이 쉬는 초 (무료 키 분당 한도 대비, 기본 2) |
| `summary_every` | 이만큼 말할 때마다 앞부분을 요약으로 압축 (기본 8) |
| `moderator_every` | 이만큼 말할 때마다 사회자가 새 쟁점을 던짐 (0이면 사회자 없음, 기본 10) |

## 어떻게 돌아가나

- 참가자마다 자기 LLM 턴: 자기 페르소나/입장/말투 + 지금까지의 요약 + 최근 12마디만 보고 다음 말을 정한다.
- 다음 발언자: 직전 발언에서 이름이 불린 사람이 먼저, 아니면 가장 오래 말 안 한 사람 (연달아 말하지 않음).
- 횟수 제한이 없어도 문맥이 끝없이 길어지지 않는다: 최근 12마디만 원문, 그 이전은 요약으로 압축.
- 같은 말이 맴돌지 않게 사회자가 주기적으로 덜 다뤄진 쟁점을 던지고 한 명을 지목한다.
- LLM 오류(429/5xx/연결 끊김)는 기다렸다가 다시 부르고, 재시도 끝에도 5번 연속 실패하면 멈춘다 (`--resume`으로 이어하기).

## 무료 키 한도

한 마디 = LLM 1회, 요약/사회자는 몇 마디에 1회. 하루 500회 한도라면 대략 400마디 정도.
분당 한도가 작은 키는 `delay`를 늘리거나 참가자마다 다른 모델(`model`)을 나눠 주면 된다.
