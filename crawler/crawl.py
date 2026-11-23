"""
공지왔수뭉 크롤러 — 상명대학교 통합 공지(https://www.smu.ac.kr/kor/life/notice.do)를 수집해
web/notices.json 으로 저장합니다.

사용법:
    pip install requests beautifulsoup4
    python crawler/crawl.py              # 기본 10페이지(약 100건) 수집
    python crawler/crawl.py --pages 30   # 더 많이
    python crawler/crawl.py --from-tsv crawler/snapshot.tsv   # 오프라인 스냅샷으로 JSON 생성

학교 서버에 부담을 주지 않도록 페이지 사이에 1초씩 쉬고, 하루 몇 번만 돌리는 것을 권장합니다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "https://www.smu.ac.kr/kor/life/notice.do"
LIST_URL = "{base}?mode=list&articleLimit={limit}&article.offset={offset}"
VIEW_URL = "{base}?mode=view&articleNo={no}"
SOURCES = Path(__file__).resolve().parent / "sources.json"
KST = timezone(timedelta(hours=9))
OUT = Path(__file__).resolve().parent.parent / "web" / "notices.json"

CAMPUSES = ("천안", "서울", "상명")
CATEGORIES = ("학사", "일반", "비교과", "등록/장학", "학생생활", "사회봉사", "글로벌", "진로취업")


# ---------------------------------------------------------------- 마감일 추출
def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _infer_year(month: int, posted: date) -> int:
    # 게시일보다 6개월 이상 앞선 월이면 다음 해로 본다 (예: 11월 게시, "1/5까지" → 내년 1월)
    return posted.year + 1 if month < posted.month - 6 else posted.year


def extract_deadline(title: str, posted: date) -> date | None:
    """제목에서 마감/행사 날짜를 추정합니다. 범위면 끝 날짜를 씁니다."""
    t = title
    # 1) 2026. 11. 23. / 2026.11.23 / 2026-11-23
    full = [
        _mk(int(y), int(m), int(d))
        for y, m, d in re.findall(r"(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})", t)
    ]
    full = [d for d in full if d]
    # 2) 범위: 11/23~27, 10.15(목)~10.16(금), 11/23~12/2
    rng = re.search(
        r"(\d{1,2})\s*[./]\s*(\d{1,2})\.?\s*(?:\([^)]*\))?\s*[~∼\-]\s*(?:(\d{1,2})\s*[./]\s*)?(\d{1,2})",
        t,
    )
    if rng:
        m1, d1, m2, d2 = rng.groups()
        m = int(m2 or m1)
        dd = _mk(_infer_year(m, posted), m, int(d2))
        if dd:
            return dd
    # 3) ~11/15까지, ~11.15
    until = re.search(r"[~∼]\s*(\d{1,2})\s*[./]\s*(\d{1,2})", t)
    if until:
        m, d = map(int, until.groups())
        dd = _mk(_infer_year(m, posted), m, d)
        if dd:
            return dd
    if full:
        return max(full)
    # 4) 11월 30일까지
    kor = re.search(r"(\d{1,2})월\s*(\d{1,2})일", t)
    if kor:
        m, d = map(int, kor.groups())
        return _mk(_infer_year(m, posted), m, d)
    return None


# ---------------------------------------------------------------- HTML 파싱
def parse_list(html: str) -> list[dict]:
    """목록 페이지 HTML → 공지 리스트.
    상명대 CMS의 클래스명이 바뀌어도 버티도록, 글 링크(articleNo)를 기준으로
    가장 가까운 목록 항목(li/tr)의 텍스트에서 정규식으로 필드를 뽑습니다."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    items: dict[int, dict] = {}
    for a in soup.select('a[href*="articleNo="]'):
        href = a.get("href", "")
        if "mode=view" not in href:
            continue
        m = re.search(r"articleNo=(\d+)", href)
        if not m:
            continue
        no = int(m.group(1))
        row = a.find_parent(["li", "tr"]) or a.parent
        # 제목 + 메타가 함께 들어있는 단위까지 올라가기
        while row is not None and "작성일" not in row.get_text() and row.parent is not None:
            if row.name in ("ul", "table", "body"):
                break
            row = row.find_parent(["li", "tr", "div", "dl"])
        if row is None:
            continue
        text = " ".join(row.get_text(" ", strip=True).split())

        title = (a.get("title") or a.get_text(" ", strip=True)).strip()
        title = re.sub(r"^(새글|new)\s*", "", title, flags=re.I)
        rec = items.setdefault(no, {"no": no})
        # 캠퍼스/카테고리 링크("천안 [비교과]")와 제목 링크가 따로 있을 수 있음
        cc = re.match(r"^(천안|서울|상명)\s*\[([^\]]+)\]$", title)
        if cc:
            rec["campus"], rec["category"] = cc.groups()
            continue
        if len(title) > len(rec.get("title", "")):
            rec["title"] = title

        if "campus" not in rec:
            cm = re.search(r"(천안|서울|상명)\s*\[(" + "|".join(map(re.escape, CATEGORIES)) + r")\]", text)
            if cm:
                rec["campus"], rec["category"] = cm.groups()
        dm = re.search(r"작성일\s*:?\s*(\d{4}[.\-]\d{2}[.\-]\d{2})", text)
        if dm:
            rec["date"] = dm.group(1).replace(".", "-")
        vm = re.search(r"조회수?\s*:?\s*([\d,]+)", text)
        if vm:
            rec["views"] = int(vm.group(1).replace(",", ""))
        wm = re.search(r"작성자\s*:?\s*(\S+)", text)
        if wm:
            rec["author"] = wm.group(1)
        rec["attachments"] = max(rec.get("attachments", 0), len(row.select('a[href*="mode=download"]')))
        cls = " ".join(row.get("class", []))
        if re.search(r"notice|top|fixed|pin", cls, re.I) or "상단고정" in text:
            rec["pinned"] = True
    return [r for r in items.values() if r.get("title") and r.get("date")]


def load_sources() -> list[dict]:
    if SOURCES.exists():
        return [s for s in json.loads(SOURCES.read_text(encoding="utf-8")) if s.get("enabled", True)]
    return [{"id": "main", "name": "통합공지", "url": BASE}]


def crawl_source(session, src: dict, pages: int, limit: int, delay: float) -> list[dict]:
    seen: dict[int, dict] = {}
    appear: dict[int, int] = {}
    pages = src.get("pages", pages)
    for p in range(pages):
        url = LIST_URL.format(base=src["url"], limit=limit, offset=p * limit)
        r = session.get(url, timeout=20)
        r.raise_for_status()
        rows = parse_list(r.text)
        print(f"[{src['name']} {p + 1}/{pages}] {len(rows)}건", file=sys.stderr)
        if not rows:
            break
        for row in rows:
            appear[row["no"]] = appear.get(row["no"], 0) + 1
            seen.setdefault(row["no"], {}).update(row)
        time.sleep(delay)
    # 상단 고정 공지는 모든 페이지에 반복해서 나타난다
    for no, cnt in appear.items():
        if cnt >= 2 and pages >= 2:
            seen[no]["pinned"] = True
    for r in seen.values():
        r["source"] = src["id"]
        r["sourceName"] = src["name"]
        r["url"] = VIEW_URL.format(base=src["url"], no=r["no"])
        if src.get("campus") and not r.get("campus"):
            r["campus"] = src["campus"]
        if src.get("category") and not r.get("category"):
            r["category"] = src["category"]
    return list(seen.values())


def crawl(pages: int, limit: int = 10, delay: float = 1.0) -> list[dict]:
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = "GongjiKok/1.0 (+student project; notice aggregator)"
    out: list[dict] = []
    for src in load_sources():
        try:
            out += crawl_source(s, src, pages, limit, delay)
        except Exception as e:  # 한 게시판이 실패해도 나머지는 계속
            print(f"[{src['name']}] 실패: {e}", file=sys.stderr)
    return out


def from_tsv(path: Path, pinned: set[int]) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        no, campus, cat, title, author, d, views, att = line.split("\t")
        out.append(dict(no=int(no), campus=campus, category=cat, title=title, author=author,
                        date=d, views=int(views), attachments=int(att), pinned=int(no) in pinned,
                        source="main", sourceName="통합공지"))
    return out


def finalize(rows: list[dict]) -> dict:
    seen = set()
    notices = []
    for r in rows:
        key = (r.get("source", "main"), r["no"])
        if key in seen:
            continue
        seen.add(key)
        posted = datetime.strptime(r["date"], "%Y-%m-%d").date()
        dl = extract_deadline(r["title"], posted)
        r.setdefault("campus", "상명")
        r.setdefault("category", "일반")
        r.setdefault("views", 0)
        r.setdefault("attachments", 0)
        r["pinned"] = bool(r.get("pinned"))
        r["deadline"] = dl.isoformat() if dl else None
        r.setdefault("source", "main")
        r.setdefault("sourceName", "통합공지")
        r.setdefault("url", VIEW_URL.format(base=BASE, no=r["no"]))
        notices.append(r)
    notices.sort(key=lambda x: (x["date"], x["no"]), reverse=True)
    return {"updated": datetime.now(KST).isoformat(timespec="minutes"), "source": BASE, "notices": notices}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--from-tsv", type=Path)
    ap.add_argument("--pinned", default="", help="TSV 모드에서 상단고정 글번호(쉼표 구분)")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    if a.from_tsv:
        pinned = {int(x) for x in a.pinned.split(",") if x}
        rows = from_tsv(a.from_tsv, pinned)
    else:
        rows = crawl(a.pages)
    data = finalize(rows)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {a.out} ({len(data['notices'])}건)", file=sys.stderr)


if __name__ == "__main__":
    main()
