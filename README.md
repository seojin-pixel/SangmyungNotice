# 공지왔수뭉

상명대학교 천안캠퍼스 공지를 **대신 읽고 정리해 주는** 비공식 공지 모음 사이트입니다.
로그인 없이 동작하고, 내 정보와 관심사, 저장한 공지는 사용자 브라우저에만 저장됩니다.

## 다른 공지 알림과 다른 점

| 기능 | 설명 | 만드는 곳 |
|---|---|---|
| 3줄 요약 | 본문과 이미지를 AI가 읽어 요약하고 대상·기간·방법·문의처를 뽑음 | `crawler/enrich.py` |
| 해당 여부 | 학년·학적을 입력하면 대상이 아닌 공지를 흐리게 표시 | enrich(본문) + postprocess(제목) |
| 변경 묶기 | '기한 연장', '변경' 공지를 원래 공지와 묶고 바뀐 마감일 표시 | `crawler/postprocess.py` |
| 캘린더 | 사이트 안 '공지왔수뭉 캘린더'(시작~마감, 반복 안함/매주/매월/매년, 브라우저에 저장)와 구글 캘린더 보내기, 카테고리·캠퍼스별 구독 피드(`web/cal/*.ics`) | index.html + postprocess |
| 기간별 | 오늘·이번 주·다음 주·이번 달·직접 선택한 기간의 마감과 꼭 볼 공지, 단톡방용 글 복사 | `web/index.html` |
| 시기별 강조 | 월별 키워드, 또는 `web/seasons.json`(학사일정)에 맞춰 관련 공지에 가산점 | index.html |
| 혜택 | 장학금·상금·지원금 공지와 금액 합계 | enrich + postprocess |
| 게시판 통합 | `crawler/sources.json`에 학과·기숙사 게시판 추가 | `crawler/crawl.py` |

## 실행 순서

```bash
pip install requests beautifulsoup4 anthropic
python crawler/crawl.py --pages 10        # 1) 목록 수집 → web/notices.json
python crawler/bodydates.py --max 60      # 1-1) 본문 텍스트에서 마감일 찾기 (API 키 불필요)
export ANTHROPIC_API_KEY=sk-ant-...        # (선택) AI 요약용
python crawler/enrich.py --max 30         # 2) 새 공지만 AI 정리 → crawler/cache/ai.json
python crawler/postprocess.py             # 3) 합치기, 변경 묶기, 금액, 캘린더
cd web && python -m http.server 8000      # http://localhost:8000
```

API 키가 없어도 2단계만 건너뛰고 나머지는 동작합니다. 이 경우 요약은 없고, 마감일은 본문 텍스트와 제목에서 찾습니다(이미지 속 날짜는 못 읽음). 마감일 우선순위는 AI > 본문 텍스트 > 제목입니다.
마감일을 못 찾은 공지는 사용자가 '날짜 정해서 추가'로 직접 날짜를 고를 수 있습니다(브라우저에만 저장).

## 비용

`enrich.py`는 한 번 정리한 공지를 캐시에 저장해서 새 공지만 처리합니다. 하루 10~20건 정도라 저렴한 모델(기본값 `claude-haiku-4-5`, 환경변수 `GONGJIKOK_MODEL`로 변경 가능)이면 한 달에 몇천 원 수준입니다. `--max`로 하루 상한을 정할 수 있습니다.

## 게시판 추가 (학과 공지 등)

`crawler/sources.json`에 같은 형식으로 추가합니다. 상명대 홈페이지와 같은 시스템의 게시판(주소가 `....do`이고 `?mode=list`로 목록이 나오는 곳)이면 그대로 동작합니다.

```json
{"id": "cs", "name": "학과 공지", "url": "학과 게시판 주소(…/notice.do)", "campus": "천안", "category": "학사", "pages": 3}
```

## 학사일정 연동

`web/seasons.example.json`을 참고해서 **학교 학사일정의 실제 날짜**로 `web/seasons.json`을 만들면, 해당 기간에 관련 키워드 공지가 위로 올라옵니다. 파일이 없으면 월별 일반 키워드를 씁니다.

## 무료 배포 (GitHub Pages + Actions)

1. 이 폴더를 GitHub 저장소로 올립니다.
2. Settings → Pages → Build and deployment에서 Source를 `Deploy from a branch`, 브랜치를 `main`, 폴더를 `/ (root)`로 고릅니다. 루트의 `index.html`이 `web/`으로 넘겨줘서 `https://<아이디>.github.io/<저장소 이름>/` 주소로 바로 열려요.
3. (선택) Settings → Secrets → Actions에 `ANTHROPIC_API_KEY`를 등록합니다.
4. Actions 탭에서 "공지 수집"을 한 번 수동으로 실행합니다. 이후에는 하루 5번 자동으로 실행됩니다.

## 주의

- 요약, 마감일, 대상, 금액은 자동 정리 결과라 틀릴 수 있습니다. 화면에 "원문 확인" 안내를 유지해 주세요.
- 학교 서버에 부담이 없도록 요청 사이에 1초씩 쉬고, 수집은 하루 몇 번만 합니다.
- 학교 로고는 쓰지 말고, "비공식" 표기를 유지해 주세요.
- 개인 이메일 같은 담당자 개인정보는 요약에 넣지 않도록 프롬프트에 적어 두었습니다.

## 마감 알림 메일 켜기

사이트(정적 페이지)만으로는 메일을 보낼 수 없어서, 무료 서비스 두 개를 연결합니다.

1. **Supabase**(무료)에서 프로젝트를 만들고, SQL Editor에 `supabase/schema.sql`을 붙여넣어 실행합니다.
2. `web/config.example.js`를 `web/config.js`로 복사하고 Supabase의 Project URL과 **anon** 키를 넣습니다. (service_role 키는 절대 넣지 마세요.)
3. **메일 발송 계정**: Gmail을 쓰면 구글 계정 → 보안 → 2단계 인증 → 앱 비밀번호를 만듭니다.
4. GitHub 저장소 → Settings → Secrets and variables → Actions에 아래를 등록합니다.
   - Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`(service_role 키), `SMTP_HOST`(smtp.gmail.com), `SMTP_PORT`(465), `SMTP_USER`, `SMTP_PASS`(앱 비밀번호), `MAIL_FROM`
   - Variables: `SITE_URL`(배포된 사이트 주소)
5. Actions 탭에서 "마감 알림 메일"을 한 번 수동 실행해 확인 메일이 오는지 봅니다. 이후 매일 아침 8시쯤 자동으로 보냅니다.

동작 방식: 처음 등록하면 확인 메일이 가고, 링크를 눌러야 알림이 시작됩니다(남의 주소 도용 방지). 같은 일정·같은 시점은 한 번만 보냅니다. 메일 아래 링크로 해지하면 주소가 바로 삭제됩니다.

테스트: `python crawler/send_alerts.py --dry-run --fixture crawler/fixtures/subscriptions.json --today 2026-11-22`

**개인정보**: 이메일을 저장하므로 사이트에 개인정보 처리 안내(수집 항목: 이메일·알림 설정, 목적: 마감 알림, 보관: 해지 시 즉시 삭제)를 두는 걸 권장합니다.
