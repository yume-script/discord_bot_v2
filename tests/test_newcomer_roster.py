"""새 이웃(newcomers)도 로스터에서 company를 가진다 (빠지면 세계 반영이 KeyError: 'company')."""
from poring_food import characters, newcomers


def test_newcomer_has_company(monkeypatch):
    monkeypatch.setattr(characters, "ORGANIZATION_GLOB", "/nonexistent/*.json")
    monkeypatch.setattr(newcomers, "residents", lambda: [{"name": "새이웃", "rank": "주민"}])
    roster = characters.load_roster()
    assert roster and all("company" in c for c in roster)
    assert roster[-1]["company"] == newcomers.COMPANY
