"""debate/debate.py - 독립 토론 스크립트를 가짜 OpenAI 호환 서버로 끝까지 돌려 본다."""
import importlib.util
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debate", "debate.py")


def _load():
    spec = importlib.util.spec_from_file_location("debate_script", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fake_server(monkeypatch):
    calls = []
    fail = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            if fail["n"] > 0:
                fail["n"] -= 1
                self.send_response(429)
                self.end_headers()
                self.wfile.write(b'{"error":"rate"}')
                return
            system = body["messages"][0]["content"]
            if "기록자" in system:
                text = "요약: 다들 맞섰다."
            elif "사회자" in system:
                text = "B님, 비용 문제는 어떻게 보세요?"
            else:
                me = system.split("'")[1]
                text = f"{me}: 저는 {len(calls)}번째로 말합니다."  # 이름표는 스크립트가 떼어야 한다
            out = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("DEBATE_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setenv("DEBATE_API_KEY", "k")
    yield calls, fail
    srv.shutdown()


def _cfg():
    return {"topic": "주 4일제", "model": "m", "summary_every": 4, "moderator_every": 5,
            "participants": [{"name": "A", "stance": "찬성"}, {"name": "B", "stance": "반대"},
                             {"name": "C", "stance": "중립", "model": "groq", "reasoning_effort": "low"}]}


def test_debate_runs_resumes_and_keeps_context_bounded(fake_server, tmp_path, monkeypatch):
    calls, _ = fake_server
    d = _load()
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    paths = {"transcript": str(tmp_path / "t.md"), "state": str(tmp_path / "t.json")}
    state = d.run(_cfg(), paths, max_turns=20, delay=0, resume=False, wrap_up=True)
    people = [t for t in state["transcript"] if t["speaker"] != "사회자"]
    assert len(people) == 20
    assert all(a["speaker"] != b["speaker"] for a, b in zip(people, people[1:]))  # 연달아 말하지 않음
    assert not any(t["text"].startswith(t["speaker"] + ":") for t in people)       # 이름표는 뗀다
    assert any(t["speaker"] == "사회자" for t in state["transcript"])
    # 사회자가 B를 지목하면 다음은 B
    i = next(i for i, t in enumerate(state["transcript"]) if t["speaker"] == "사회자")
    assert state["transcript"][i + 1]["speaker"] == "B"
    assert state["summary"] and state["summarized_upto"] > 0
    groq = [c for c in calls if c["model"] == "groq"]
    assert groq and all(c.get("reasoning_effort") == "low" for c in groq)
    # 문맥은 요약 + 최근 RECENT마디만 (횟수 제한이 없어도 프롬프트가 끝없이 길어지지 않는다)
    last_user = [c for c in calls if "다음 발언" in c["messages"][-1]["content"]][-1]["messages"][-1]["content"]
    assert last_user.count("\n") < d.RECENT + 20
    md = open(paths["transcript"], encoding="utf-8").read()
    assert "# 토론: 주 4일제" in md and "## 정리" in md
    # 이어하기
    state2 = d.run(_cfg(), paths, max_turns=3, delay=0, resume=True, wrap_up=False)
    assert len([t for t in state2["transcript"] if t["speaker"] != "사회자"]) == 23


def test_debate_retries_rate_limit(fake_server, monkeypatch):
    calls, fail = fake_server
    d = _load()
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    fail["n"] = 2
    assert d.chat("m", "너는 'A'이다", "u").startswith("A:")
    assert len(calls) == 3


def test_pick_speaker_rules():
    d = _load()
    ps = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
    assert d.pick_speaker(ps, [])["name"] == "A"
    assert d.pick_speaker(ps, [{"speaker": "A", "text": "C님 생각은?"}])["name"] == "C"
    assert d.pick_speaker(ps, [{"speaker": "A", "text": "x"}, {"speaker": "B", "text": "y"}])["name"] == "C"
    assert d.pick_speaker(ps, [{"speaker": "A", "text": "A가 맞다"}])["name"] != "A"
