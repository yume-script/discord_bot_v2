"""
테스트 공통 설정 - 진짜 디스코드/LLM 없이 포링푸드 가상 세계를 돌린다.

- 상태 폴더(PORING_FOOD_STATE_DIR)를 임시 폴더로 바꾸고, 테스트마다 비운다.
- LLM 호출(requests.post)은 fake_llm 픽스처가 가로챈다: 테스트가 (시스템 프롬프트, 사용자 프롬프트) -> dict 함수를 넣는다.
- 디스코드 전송은 붙이지 않는다 (runtime에 보낼 곳이 없으면 print만 한다).
"""
import os
import shutil
import sys
import tempfile

_STATE = tempfile.mkdtemp(prefix="poring_test_")
os.environ.setdefault("DISCORD_BOT_TOKEN", "test")
os.environ["PORING_FOOD_STATE_DIR"] = _STATE
os.environ["LLM_USAGE_DB_PATH"] = os.path.join(_STATE, "llm_usage.db")
os.environ.setdefault("LITELLM_API_KEY", "test")
os.environ.setdefault("LITELLM_BASE_URL", "http://llm.invalid")
os.environ.setdefault("PORING_AGENT_CHANNEL_ID", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """테스트마다 빈 세계에서 시작한다."""
    for name in os.listdir(_STATE):
        path = os.path.join(_STATE, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    from poring_food import store, characters, semantic
    store._ready.clear()
    semantic._cache.clear()
    semantic._pending.clear()
    characters._TYPE_CACHE.clear()
    yield


@pytest.fixture
def state_dir():
    return _STATE


class _Resp:
    status_code = 200

    def __init__(self, content):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture
def fake_llm(monkeypatch):
    """fake_llm(handler): handler(system, user) -> dict (LLM이 돌려줄 JSON). 호출 기록은 .calls."""
    import poring_food.llm as llm

    class Fake:
        calls: list = []
        handler = staticmethod(lambda system, user: {})

        def __call__(self, handler):
            self.handler = handler
            return self

    fake = Fake()

    def post(url, json=None, headers=None, timeout=None, **kw):  # noqa: A002
        system, user = json["messages"][0]["content"], json["messages"][-1]["content"]
        fake.calls.append((system, user))
        return _Resp(_dumps(fake.handler(system, user) or {}))

    fake.calls = []
    monkeypatch.setattr(llm.requests, "post", post)
    return fake


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False)
