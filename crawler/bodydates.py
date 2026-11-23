"""
공지왔수뭉 본문 마감일 찾기 — API 키 없이 공지 본문 텍스트에서 신청·마감 날짜를 찾습니다.

- '신청', '접수', '마감', '제출', '기간', '까지' 같은 단어가 있는 줄에서 날짜를 찾고,
  그중 가장 늦은 날짜를 마감일로 봅니다. '게시기간', '작성일' 줄은 무시합니다.
- 본문이 이미지뿐인 공지는 여기서 못 찾습니다(그건 enrich.py의 AI가 읽습니다).
- 결과는 crawler/cache/body.json 에 저장되고, 한 번 본 공지는 다시 받지 않습니다.

사용법:  python crawler/bodydates.py --max 60
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl import extract_deadline  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "notices.json"
CACHE = Path(__file__).resolve().parent / "cache" / "body.json"

KEY = re.compile(r"신청|접수|마감|제출|모집\s*기간|기간|까지|기한|일시|일정")
SKIP = re.compile(r"게시\s*기간|작성일|등록일|수정일|조회")


def range_start(chunk: str, posted: date) -> date | None:
    """'2026. 11. 16.(월) ~ 11. 23.' 같은 범위에서 앞쪽 날짜"""
    m = re.search(r"(?:(20\d{2})\s*[.\-/년]\s*)?(\d{1,2})\s*[./월]\s*(\d{1,2})\s*일?\.?\s*(?:\([^)]*\))?\s*(?:\d{1,2}:\d{2})?\s*[~∼\-]", chunk)
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        return date(int(y) if y else (posted.year + 1 if int(mo) < posted.month - 6 else posted.year), int(mo), int(d))
    except ValueError:
        return None


def find_deadline(text: str, posted: date) -> tuple[str | None, str | None, str | None]:
    """본문 텍스트 → (마감일, 시작일, 근거가 된 줄)"""
    best: tuple[date, str] | None = None
    lines = [l.strip() for l in re.split(r"[\n\r]+", text) if l.strip()]
    for i, line in enumerate(lines):
        if SKIP.search(line) or not KEY.search(line):
            continue
        # 날짜가 다음 줄에 이어지는 경우("신청기간" / "2026. 11. 16. ~ 11. 23.")도 함께 본다
        chunk = line if re.search(r"\d", line) else line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
        d = extract_deadline(chunk, posted)
        if not d or d < posted or (d - posted).days > 200:
            continue
        if best is None or d > best[0]:
            st = range_start(chunk, posted)
            best = (d, chunk[:120], st if st and st <= d else None)
    return (best[0].isoformat(), best[2].isoformat() if best[2] else None, best[1]) if best else (None, None, None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=60)
    a = ap.parse_args()
    import requests
    from enrich import fetch_body

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    data = json.loads(DATA.read_text(encoding="utf-8"))
    s = requests.Session()
    s.headers["User-Agent"] = "GongjiKok/1.0 (+student project; notice aggregator)"
    key = lambda n: str(n["no"]) if n.get("source", "main") == "main" else f"{n['source']}:{n['no']}"
    todo = [n for n in data["notices"] if key(n) not in cache][: a.max]
    for i, n in enumerate(todo, 1):
        try:
            text, _ = fetch_body(s, n["url"])
            text = re.sub(r"(?m)^\s*(작성자|작성일|게시기간|조회수?).*$", "", text)
            posted = date.fromisoformat(n["date"])
            dl, st, evidence = find_deadline(text, posted)
            cache[key(n)] = {"deadline": dl, "start": st, "evidence": evidence, "hasText": len(text) > 30}
            print(f"[{i}/{len(todo)}] {n['no']} → {dl}", file=sys.stderr)
        except Exception as e:
            print(f"[{i}/{len(todo)}] {n['no']} 실패: {e}", file=sys.stderr)
        time.sleep(1)
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
