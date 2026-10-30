# 좋은 글귀 (Good Quotes)

BookOasis 홈 화면에 **좋은 글귀**와 **배경 이미지**를 보여주는 위젯 플러그인입니다.
[colaiuta77/detail_studio](https://github.com/colaiuta77/detail_studio) 1.0.1의 "홈 좋은 글귀" 기능만 따로 떼어 만들었습니다. 상세페이지와 데일리 픽은 포함하지 않습니다.

| 항목 | 내용 |
| --- | --- |
| 플러그인 ID | `good_quotes` |
| 버전 | 1.0.0 |
| 지원 세션 | 일반·성인 도서, 오디오북, 비디오 |
| 외부 의존성 | 없음 (표준 라이브러리 + 코어 Flask) |
| 라이선스 | GNU AGPL v3 (`LICENSE`) |

## 기능

- **글귀** — 기본 공급원은 [Korean Advice API](https://github.com/gwongibeom/korean-advice-open-api)이며 키 없이 바로 동작합니다. ZenQuotes·PositiveQuotation 영문 공급원을 켜면 가중치(기본 60:35:5)에 따라 섞어 보여줍니다. 공급원이 모두 실패하면 내장 문구를 표시합니다.
- **배경 이미지** — 그라데이션(기본), The Met 오픈 액세스(CC0, 키 불필요), Unsplash, Pexels 중에서 고릅니다. 배경은 1~24시간(기본 3시간)마다 바뀌고, 배경 변경 버튼을 누르면 바로 새로 받습니다.
- **위젯 도구** — 다른 글귀, 언어 전환(`En` → `한` → `En.한`), 배경 변경, 출처 보기 버튼이 있습니다.
- **글꼴** — 한국어·영어·저자의 글꼴과 크기를 각각 고릅니다. 글꼴은 코어에 설치된 목록에서 고르며, 긴 문구는 카드 높이에 맞게 자동으로 줄입니다.
- **DeepL 사전 번역 (선택)** — 영문 공급원 문구를 관리자가 미리 한국어로 번역해 저장합니다. 홈 화면에서는 번역 API를 호출하지 않습니다.

## 설치

이 폴더를 BookOasis의 다음 위치에 복사합니다.

```text
plugins/metadata/good_quotes/
  __init__.py
  good_quotes.py
  quotes.py
  quote_http.py
  quote_media.py
  quote_store.py
  quote_translation.py
  VERSION
  dashboard.html
  dashboard.css
  dashboard.js
  settings.html
  settings.css
  settings.js
```

1. 플러그인 관리에서 플러그인을 다시 불러온 뒤 **좋은 글귀 · 홈 위젯**을 활성화합니다.
2. **내 설정 → 홈 화면 플러그인 배치 모드**를 켭니다.
3. 홈 화면의 **위젯 추가 → 좋은 글귀**를 선택합니다.
4. 필요하면 **플러그인 관리 → 좋은 글귀 · 홈 위젯** 설정에서 공급원·배경·글꼴을 바꾸고 저장합니다. 설정은 세션(일반·성인 등)마다 따로 저장됩니다.

Detail Studio와 함께 설치해도 됩니다. 두 플러그인은 설정과 저장 파일이 서로 겹치지 않습니다.

## 저장 위치

- 영문 공급원 원문과 DeepL 번역은 `plugins/metadata/good_quotes/data/quotes.sqlite3`에 저장합니다. 처음 사용할 때 자동으로 만들어지므로 이 폴더에 쓰기 권한이 필요합니다. 업데이트할 때는 `data/` 폴더를 지우지 말고 그대로 두세요.
- 배경 이미지 파일은 디스크에 저장하지 않습니다. URL과 출처만 서버 메모리에 보관합니다.
- API 키(Unsplash·Pexels·DeepL)는 코어의 플러그인 설정에만 저장하며 홈 화면 응답에는 포함하지 않습니다.

## 공급원과 이용 조건

| 공급원 | 용도 | 조건 |
| --- | --- | --- |
| Korean Advice API | 한국어 글귀·저자 | [LICENSE](https://github.com/gwongibeom/korean-advice-open-api/blob/main/LICENSE): 비상업적 무료 사용, 출처 표기 |
| ZenQuotes | 영문 글귀 | [이용 조건](https://docs.zenquotes.io/terms-and-conditions/) |
| PositiveQuotation | 영문 속담 | [API 문서](https://positivequotation.com/developers/public-domain-quotes-api) |
| DeepL | 영문 → 한국어 사전 번역 | [API 안내](https://www.deepl.com/en/developers) |
| The Met | 공개 영역(CC0) 작품 배경 | [Open Access 정책](https://www.metmuseum.org/ko/policies/image-resources) |
| Unsplash | 무료 사진 배경 (Access Key 필요) | [라이선스](https://unsplash.com/license) |
| Pexels | 사진 배경 (API 키 필요) | [라이선스](https://www.pexels.com/license/) |

ZenQuotes·PositiveQuotation·Unsplash·Pexels는 설정에서 이용 조건 확인 스위치를 켜야 동작합니다.

## 출처

[Detail Studio](https://github.com/colaiuta77/detail_studio)(AGPL-3.0)의 글귀 코드(`quotes.py`, `quote_*.py`)와 위젯·설정 UI를 가져와 다음을 바꿨습니다.

- 단독 플러그인 `good_quotes`로 분리하고, 데일리 픽 선택 로직은 뺐습니다.
- 요청 헤더(`X-Good-Quotes`), CSS 클래스(`gq-`), 저장 키 이름을 바꿔 Detail Studio와 겹치지 않게 했습니다.
