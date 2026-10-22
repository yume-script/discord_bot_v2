# 브런치 요일별 연재 → EPUB (brunch_epub)

브런치 첫 화면의 요일 탭(`https://brunch.co.kr/#mon#PUBLISH_TIME` … `#sun#PUBLISH_TIME`) 최신글 목록을 저장하고,
해당 요일 글을 EPUB 한 권으로 묶는 독립 스크립트다. 디스코드 봇과는 코드를 공유하지 않는다.

## 준비

```bash
pip install requests beautifulsoup4 lxml
pip install playwright && playwright install chromium   # 목록 수집용 (--api-url 을 쓰면 불필요)
```

## 실행

```bash
cd brunch_epub
python3 brunch_epub.py                    # 오늘(한국 시간) 요일: 목록 갱신 + 새 글 EPUB
python3 brunch_epub.py --day fri          # 금요일
python3 brunch_epub.py --all-days --list-only   # 7개 요일 목록만 갱신
python3 brunch_epub.py --day mon --limit 10 --include-exported   # 이미 묶은 글 포함, 최신 10편
python3 brunch_epub.py -v                 # 목록을 가져온 API 주소를 출력
```

- 목록: `data/<요일>.json` - 글 주소, 처음 본 시각, EPUB에 넣은 시각(`exported`). 실행할 때마다 합쳐서 쌓인다.
- EPUB: `out/brunch_<요일>_<YYYYMMDD>.epub` - 아직 안 넣은 글만, 오래된 글 → 최신 글 순서. 이미지 포함 (`--no-images`로 제외).
- 매일 자동으로: `crontab -e` → `10 23 * * * cd /경로/brunch_epub && python3 brunch_epub.py`

## 목록을 가져오는 방식

브런치 첫 화면은 자바스크립트로 그려서 requests로는 목록이 비어 있다.

1. 기본은 Playwright로 페이지를 열고, 페이지가 부르는 `api.brunch.co.kr` JSON 응답 중 요일 코드/`PUBLISH_TIME`이 들어간 것에서 글 주소를 뽑는다.
   못 찾으면 화면 링크(`/@작가/번호`)를 긁는다 (이때는 다른 추천 목록 글이 섞일 수 있다).
2. `-v`로 실제 API 주소를 한 번 확인한 뒤 `--api-url 'https://api.brunch.co.kr/...{DAY}...'`
   (또는 환경변수 `BRUNCH_LIST_API`)로 지정하면 브라우저 없이 requests만으로 돈다. `{day}`=mon, `{DAY}`=MON.

본문은 글 페이지(`https://brunch.co.kr/@작가/번호`)의 `div.wrap_body`를 쓴다.
개인 소장용으로만 쓰고, 요청 사이 간격(`--delay`, 기본 1초)을 너무 줄이지 않는다.
