"""서버 상태 MCP: 부하는 호스트 기준 - 호스트의 village-ctl.sh top으로 게스트별·프로세스별 CPU"""
import importlib.util
import os
import stat

import pytest

pytest.importorskip("mcp")
SPEC = importlib.util.spec_from_file_location("server_status_mcp", os.path.join(os.path.dirname(__file__), "..", "scripts", "server_status_mcp.py"))
ss = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ss)

TOP = "cores 4\nguest vm:110:win10 200.0\nguest ct:102:etc 20.0\nproc vm:110:win10 kvm 200.0\nproc ct:102:etc python3 20.0\n"


def test_parse_top():
    t = ss.parse_top(TOP)
    assert t["cores"] == 4 and t["guests"][0] == {"kind": "vm", "id": "110", "name": "win10", "cpu_percent": 200.0}
    assert t["processes"][1] == {"guest": "ct:102:etc", "name": "python3", "cpu_percent": 20.0}
    assert ss.parse_top("village-ctl: 허용되지 않은 명령: top") is None


def test_host_load_over_ssh(tmp_path, monkeypatch):
    fake = tmp_path / "ssh"
    fake.write_text("#!/bin/sh\n[ \"$(eval echo \\${$#})\" = top ] || exit 2\nprintf '%s' \"$FAKE_TOP\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_TOP", TOP)
    monkeypatch.delenv("STATUS_HOST_SSH", raising=False)
    monkeypatch.setattr(ss, "_dotenv", lambda key: "root@zeeps" if key == "PORING_LOADSHED_ZEEPS_SSH" else "")
    d = ss.get_host_load()
    assert d["available"] and d["cores"] == 4 and d["guests"][0]["machine_percent"] == 50.0
    assert "win10(가상머신 110) 50%" in d["summary"] and "kvm@win10 50%" in d["summary"]
    brief = ss.get_system_status()["host_load"]
    assert brief["available"] and brief["guests"][0] == {"guest": "vm:110:win10", "machine_percent": 50.0}

    monkeypatch.setattr(ss, "_dotenv", lambda key: "")
    assert ss.get_host_load()["available"] is False and "설정되지 않음" in ss.get_host_load()["reason"]
