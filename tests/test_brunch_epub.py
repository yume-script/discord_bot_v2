"""브런치 EPUB 저장기 - 네트워크 없이 목록 파싱/상태 병합/EPUB 생성을 확인한다."""
import os
import sys
import zipfile

from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brunch_epub"))

import brunch_epub as be  # noqa: E402

ARTICLE_HTML = """<html><head>
<meta property="og:title" content="첫 번째 이야기 &amp; 시작">
<meta name="article:author" content="홍길동">
<meta property="article:published_time" content="2026-10-19T09:00:00+09:00">
</head><body>
<div class="wrap_body">
  <h4>소제목</h4>
  <p>본문 첫 줄<br>둘째 줄</p>
  <div class="wrap_img_float"><img data-src="//img1.daumcdn.net/a.jpg" onclick="x()"></div>
  <script>alert(1)</script>
  <custom-tag>안쪽 글자</custom-tag>
</div></body></html>"""


def test_links_from_json_finds_nested_articles():
    data = {"data": {"list": [
        {"article": {"profileId": "writer1", "no": 12, "title": "a"}},
        {"userId": "@@abc", "articleNo": "3"},
        {"link": "https://brunch.co.kr/@writer2/7"},
        {"profileId": "writer1", "no": 12},  # 중복
    ]}}
    assert be.links_from_json(data) == [
        "https://brunch.co.kr/@writer1/12",
        "https://brunch.co.kr/@@abc/3",
        "https://brunch.co.kr/@writer2/7",
    ]


def test_links_from_html():
    page = '<a href="/@kim/5">x</a><a href="https://brunch.co.kr/@lee/10">y</a><a href="/@kim">작가</a>'
    assert be.links_from_html(page) == ["https://brunch.co.kr/@kim/5", "https://brunch.co.kr/@lee/10"]


def test_merge_list_keeps_export_marks_and_order():
    state = {"articles": [{"url": "u1", "first_seen": "old", "exported": "2026-10-01"}]}
    added = be.merge_list(state, ["u2", "u1"])
    assert added == 1
    assert [a["url"] for a in state["articles"]] == ["u2", "u1"]
    assert state["articles"][1]["exported"] == "2026-10-01"


def test_parse_article_and_build_epub(tmp_path, monkeypatch):
    art = be.parse_article(ARTICLE_HTML, "https://brunch.co.kr/@hong/1")
    assert art["title"] == "첫 번째 이야기 & 시작"
    assert art["author"] == "홍길동"
    assert art["body"].find("script") is None
    img = art["body"].find("img")
    assert img.attrs == {"src": "https://img1.daumcdn.net/a.jpg", "alt": ""}

    monkeypatch.setattr(be, "download_image", lambda src, session: (b"\xff\xd8fake", "image/jpeg"))
    out = be.build_epub(str(tmp_path / "t.epub"), "테스트 책", [art])

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert names[0] == "mimetype"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert "OEBPS/images/img0001.jpg" in names
        # 모든 XML 파일이 잘 짜인 XML이어야 리더가 연다
        for n in names:
            if n.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                etree.fromstring(z.read(n))
        ch = z.read("OEBPS/ch001.xhtml").decode()
        assert "<br/>" in ch and "안쪽 글자" in ch and "images/img0001.jpg" in ch
        assert "onclick" not in ch and "alert" not in ch
