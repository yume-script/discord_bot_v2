# -*- coding: utf-8 -*-
"""
브런치(brunch.co.kr) 요일별 연재 최신글 -> EPUB 저장기.

[하는 일]
1) 요일 탭(https://brunch.co.kr/#mon#PUBLISH_TIME 등)의 최신글 목록을 모아
   `data/<요일>.json`에 저장한다 (이미 본 글은 합쳐서 계속 쌓는다).
2) 해당 요일 목록에서 아직 EPUB으로 만들지 않은 글을 받아
   `out/brunch_<요일>_<날짜>.epub` 한 권으로 묶는다 (글 1편 = 1장, 이미지 포함).

[목록을 어떻게 가져오나]
브런치 첫 화면은 자바스크립트로 그리는 페이지라 requests로 받으면 목록이 비어 있다.
- 기본: Playwright(헤드리스 크로미움)로 페이지를 열고, 페이지가 부르는 api.brunch.co.kr
  JSON 응답에서 글 주소를 뽑는다. JSON에서 못 찾으면 화면의 링크(/@작가/번호)를 긁는다.
  어떤 API 주소를 썼는지 --verbose로 찍어 주니, 한 번 확인한 뒤에는 아래처럼 바꿀 수 있다.
- --api-url: 그 API 주소를 직접 requests로 부른다(Playwright 불필요).
  `{day}`(mon), `{DAY}`(MON) 자리표시자를 쓸 수 있다.

[실행 예]
  python3 brunch_epub.py                 # 오늘(한국 시간) 요일
  python3 brunch_epub.py --day fri       # 금요일
  python3 brunch_epub.py --all-days --list-only   # 7개 요일 목록만 갱신
  python3 brunch_epub.py --day mon --include-exported --limit 10
"""
import argparse
import html
import json
import mimetypes
import os
import re
import sys
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from lxml import etree
from lxml import html as lxml_html

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_KO = {"mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금", "sat": "토", "sun": "일"}
KST = timezone(timedelta(hours=9))
BASE = "https://brunch.co.kr"
LIST_URL = BASE + "/#{day}#PUBLISH_TIME"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ko-KR,ko;q=0.9",
}
# https://brunch.co.kr/@작가id/123  (작가 id에 @가 하나 더 붙는 내부 id(@@abc)도 있다)
ARTICLE_RE = re.compile(r"(?:https?:)?(?://brunch\.co\.kr)?/(@@?[A-Za-z0-9_.\-]+)/(\d+)(?![\d/])")
HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- 목록 수집

def article_url(author, no):
    author = author if author.startswith("@") else "@" + author
    return f"{BASE}/{author}/{int(no)}"


def links_from_json(data):
    """API JSON 어디에 있든 글 주소를 순서대로 뽑는다 (필드 이름이 바뀌어도 버티게 넓게 본다)."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            author = node.get("profileId") or node.get("userId")
            no = node.get("articleNo", node.get("no"))
            if isinstance(author, str) and author and str(no).isdigit():
                found.append(article_url(author, no))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            for m in ARTICLE_RE.finditer(node):
                found.append(article_url(m.group(1), m.group(2)))

    walk(data)
    return dedupe(found)


def links_from_html(page_html):
    return dedupe(article_url(m.group(1), m.group(2)) for m in ARTICLE_RE.finditer(page_html))


def dedupe(items):
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def fetch_list_api(api_url, day):
    url = api_url.replace("{day}", day).replace("{DAY}", day.upper())
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return links_from_json(r.json())


def fetch_list_browser(day, scrolls=3, verbose=False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("Playwright가 없습니다: pip install playwright && playwright install chromium\n"
                         "(또는 --api-url 로 API 주소를 직접 지정)")

    responses = []  # (url, json)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"], locale="ko-KR")

        def on_response(resp):
            if "api.brunch.co.kr" not in resp.url:
                return
            try:
                responses.append((resp.url, resp.json()))
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(LIST_URL.format(day=day), wait_until="networkidle", timeout=60000)
        for _ in range(scrolls):  # 더 보기(무한 스크롤) 대비
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1200)
        page_html = page.content()
        browser.close()

    # 요일 코드나 PUBLISH_TIME이 들어간 API 응답을 우선 쓴다 (첫 화면의 다른 추천 목록과 섞이지 않게).
    pat = re.compile(rf"(\b|_|=){day}(\b|_|&)|PUBLISH_TIME", re.I)
    picked = [(u, d) for u, d in responses if pat.search(u)]
    links = []
    for u, d in picked:
        got = links_from_json(d)
        if verbose:
            log(f"  [api] {u} -> {len(got)}편")
        links += got
    if links:
        return dedupe(links)
    if verbose:
        for u, _ in responses:
            log(f"  [api 후보] {u}")
        log("  요일 API를 못 찾아 화면 링크로 대신합니다 (다른 목록 글이 섞일 수 있음).")
    return links_from_html(page_html)


# ---------------------------------------------------------------- 상태 저장

def load_state(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"articles": []}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def merge_list(state, links):
    """새 목록을 상태에 합친다. 새로 본 글 수를 돌려준다. 최신 목록 순서가 앞에 오게 유지."""
    known = {a["url"]: a for a in state["articles"]}
    now = datetime.now(KST).isoformat(timespec="seconds")
    fresh = []
    for url in links:
        if url in known:
            fresh.append(known.pop(url))
        else:
            fresh.append({"url": url, "first_seen": now, "exported": None})
    added = sum(1 for a in fresh if a["first_seen"] == now)
    state["articles"] = fresh + list(known.values())
    state["updated"] = now
    return added


# ---------------------------------------------------------------- 글 본문

ALLOWED = {"p", "div", "span", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
           "ul", "ol", "li", "b", "strong", "i", "em", "u", "s", "sub", "sup", "a", "img",
           "figure", "figcaption", "pre", "code", "table", "thead", "tbody", "tr", "th", "td"}
DROP = {"script", "style", "iframe", "noscript", "button", "form", "input", "svg", "video", "audio"}


def meta(soup, *names):
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def parse_article(page_html, url):
    soup = BeautifulSoup(page_html, "lxml")
    title = (meta(soup, "og:title") or (soup.title.string if soup.title else "") or url).strip()
    author = meta(soup, "article:author", "og:article:author", "author")
    if not author:
        a = soup.select_one(".author_name, .tit_author, .wrap_author .name")
        author = a.get_text(strip=True) if a else ""
    published = meta(soup, "article:published_time", "og:regDate")
    if not published:
        d = soup.select_one(".date, .f_l.date")
        published = d.get_text(strip=True) if d else ""

    body = soup.select_one("div.wrap_body") or soup.select_one(".wrap_body_frame") or soup.find("article")
    if body is None:
        raise ValueError("본문(div.wrap_body)을 찾지 못했습니다")
    for t in body.find_all(DROP):
        t.decompose()
    for t in body.find_all(True):
        if t.name not in ALLOWED:
            t.unwrap()
            continue
        keep = {}
        if t.name == "img":
            src = t.get("data-src") or t.get("src") or ""
            keep = {"src": urljoin(url, src), "alt": t.get("alt", "")}
        elif t.name == "a" and t.get("href", "").startswith(("http", "//")):
            keep = {"href": urljoin(url, t["href"])}
        t.attrs = keep
    return {"url": url, "title": title, "author": author, "published": published, "body": body}


def fetch_article(url, session):
    r = session.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return parse_article(r.text, url)


def body_to_xhtml(body):
    """bs4 조각 -> 잘 짜인 XHTML 문자열 (EPUB 리더는 XML 파서라 <br> 같은 것도 닫혀 있어야 한다)."""
    inner = "".join(str(c) for c in body.contents).strip() or "<p></p>"
    wrapper = lxml_html.fragment_fromstring(inner, create_parent="div")
    wrapper.set("class", "body")
    return etree.tostring(wrapper, method="xml", encoding="unicode")


# ---------------------------------------------------------------- EPUB

def build_epub(path, book_title, chapters, session=None, with_images=True):
    """chapters: parse_article 결과 목록. 표준 라이브러리 zip으로 EPUB3(+toc.ncx)을 만든다."""
    book_id = "urn:uuid:" + str(uuid.uuid4())
    images = {}  # 원본 url -> (epub 내 경로, media-type, bytes)
    xhtml_files = []

    for i, ch in enumerate(chapters, 1):
        body = ch["body"]
        for img in body.find_all("img"):
            src = img.get("src", "")
            if not with_images or not src:
                img.decompose()
                continue
            if src not in images:
                data, mtype = download_image(src, session)
                if data is None:
                    img.decompose()
                    continue
                ext = mimetypes.guess_extension(mtype) or ".jpg"
                images[src] = (f"images/img{len(images) + 1:04d}{ext}", mtype, data)
            img["src"] = images[src][0]
        meta_line = " · ".join(x for x in (ch["author"], ch["published"]) if x)
        xhtml_files.append((f"ch{i:03d}.xhtml", ch["title"], XHTML.format(
            title=html.escape(ch["title"]),
            meta=html.escape(meta_line),
            url=html.escape(ch["url"]),
            body=body_to_xhtml(body),
        )))

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    manifest += [f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>'
                 for i, (name, _, _) in enumerate(xhtml_files, 1)]
    manifest += [f'<item id="img{i}" href="{p}" media-type="{m}"/>'
                 for i, (p, m, _) in enumerate(images.values(), 1)]
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(1, len(xhtml_files) + 1))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    opf = OPF.format(id=html.escape(book_id), title=html.escape(book_title), modified=now,
                     manifest="\n    ".join(manifest), spine=spine)
    nav = NAV.format(title=html.escape(book_title), items="".join(
        f'<li><a href="{n}">{html.escape(t)}</a></li>' for n, t, _ in xhtml_files))
    ncx = NCX.format(id=html.escape(book_id), title=html.escape(book_title), points="".join(
        f'<navPoint id="p{i}" playOrder="{i}"><navLabel><text>{html.escape(t)}</text></navLabel>'
        f'<content src="{n}"/></navPoint>' for i, (n, t, _) in enumerate(xhtml_files, 1)))

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        # mimetype은 맨 앞, 압축 없이 (EPUB 규격)
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        for name, _, content in xhtml_files:
            z.writestr("OEBPS/" + name, content, compress_type=zipfile.ZIP_DEFLATED)
        for p, _, data in images.values():
            z.writestr("OEBPS/" + p, data, compress_type=zipfile.ZIP_STORED)
    return path


def download_image(src, session):
    session = session or requests.Session()
    if src.startswith("//"):
        src = "https:" + src
    if urlparse(src).scheme not in ("http", "https"):
        return None, None
    try:
        r = session.get(src, headers={**HEADERS, "Referer": BASE + "/"}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        log(f"  이미지 실패: {src} ({e})")
        return None, None
    mtype = r.headers.get("Content-Type", "").split(";")[0].strip()
    if mtype not in ("image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"):
        mtype = "image/jpeg"
    return r.content, mtype


CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="ko">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{id}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:language>ko</dc:language>
    <dc:creator>brunch.co.kr</dc:creator>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    {manifest}
  </manifest>
  <spine toc="ncx">{spine}</spine>
</package>"""

NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ko">
<head><title>{title}</title></head>
<body><nav epub:type="toc" id="toc"><h1>{title}</h1><ol>{items}</ol></nav></body>
</html>"""

NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{id}"/></head>
  <docTitle><text>{title}</text></docTitle>
  <navMap>{points}</navMap>
</ncx>"""

XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ko">
<head><title>{title}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
<h1>{title}</h1>
<p class="meta">{meta}</p>
{body}
<p class="src"><a href="{url}">{url}</a></p>
</body>
</html>"""

CSS = """body { line-height: 1.7; }
h1 { font-size: 1.4em; margin-bottom: 0.2em; }
.meta, .src { color: #777; font-size: 0.85em; }
img { max-width: 100%; height: auto; display: block; margin: 1em auto; }
blockquote { border-left: 3px solid #ccc; margin-left: 0; padding-left: 1em; color: #555; }
"""


# ---------------------------------------------------------------- 실행

def today_day():
    return DAYS[datetime.now(KST).weekday()]


def refresh_list(day, args):
    links = fetch_list_api(args.api_url, day) if args.api_url else \
        fetch_list_browser(day, scrolls=args.scrolls, verbose=args.verbose)
    state_path = os.path.join(args.data_dir, f"{day}.json")
    state = load_state(state_path)
    added = merge_list(state, links)
    save_state(state_path, state)
    log(f"[{day}] 목록 {len(links)}편 (새 글 {added}편) -> {state_path}")
    return state_path, state


def export_day(day, state_path, state, args):
    targets = [a for a in state["articles"] if args.include_exported or not a.get("exported")]
    if args.limit:
        targets = targets[:args.limit]
    if not targets:
        log(f"[{day}] 새로 묶을 글이 없습니다.")
        return None
    # 목록은 최신글이 앞이므로, 책은 오래된 글부터 읽히게 뒤집는다.
    targets = list(reversed(targets))
    session = requests.Session()
    chapters, done = [], []
    for a in targets:
        try:
            chapters.append(fetch_article(a["url"], session))
            done.append(a)
            log(f"  ✓ {chapters[-1]['title']}")
        except Exception as e:
            log(f"  ✗ {a['url']} ({e})")
        time.sleep(args.delay)
    if not chapters:
        return None

    stamp = datetime.now(KST).strftime("%Y%m%d")
    out = os.path.join(args.out_dir, f"brunch_{day}_{stamp}.epub")
    title = f"브런치 {DAY_KO[day]}요일 연재 ({datetime.now(KST):%Y-%m-%d})"
    build_epub(out, title, chapters, session=session, with_images=not args.no_images)
    now = datetime.now(KST).isoformat(timespec="seconds")
    for a in done:
        a["exported"] = now
    save_state(state_path, state)
    log(f"[{day}] EPUB {len(chapters)}편 -> {out}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="브런치 요일별 최신글 목록 저장 + EPUB 생성")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--day", choices=DAYS, help="요일 (기본: 오늘, 한국 시간)")
    g.add_argument("--all-days", action="store_true", help="7개 요일 모두")
    ap.add_argument("--list-only", action="store_true", help="목록만 저장하고 EPUB은 만들지 않음")
    ap.add_argument("--include-exported", action="store_true", help="이미 EPUB에 넣은 글도 다시 넣기")
    ap.add_argument("--limit", type=int, default=0, help="최신글 N편까지만 (0=제한 없음)")
    ap.add_argument("--no-images", action="store_true", help="이미지 빼고 글만")
    ap.add_argument("--api-url", default=os.getenv("BRUNCH_LIST_API", ""),
                    help="목록 API 주소 직접 지정 ({day}/{DAY} 자리표시자). 없으면 Playwright 사용")
    ap.add_argument("--scrolls", type=int, default=3, help="Playwright에서 아래로 스크롤할 횟수")
    ap.add_argument("--delay", type=float, default=1.0, help="글 요청 사이 쉬는 초")
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--out-dir", default=os.path.join(HERE, "out"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    days = DAYS if args.all_days else [args.day or today_day()]
    for day in days:
        state_path, state = refresh_list(day, args)
        if not args.list_only:
            export_day(day, state_path, state, args)


if __name__ == "__main__":
    main()
