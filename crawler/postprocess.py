"""
공지왔수뭉 후처리 — crawl.py, enrich.py 다음에 실행합니다.

1. AI 정리 결과(cache/ai.json)를 공지에 합치고, 본문에서 찾은 마감일로 D-day를 보정
2. 제목만으로도 '대상 학년/학적'과 '금액'을 추정 (AI 정리가 없는 공지용)
3. '기한 연장', '변경', '재안내' 공지를 원래 공지와 같은 묶음으로 연결
4. 마감일 캘린더 구독 파일(web/cal/*.ics) 생성
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "notices.json"
CACHE = Path(__file__).resolve().parent / "cache" / "ai.json"
BODY = Path(__file__).resolve().parent / "cache" / "body.json"
CAL_DIR = ROOT / "web" / "cal"

CAT_SLUG = {"학사": "academic", "등록/장학": "scholarship", "비교과": "extracurricular", "진로취업": "career",
            "학생생활": "life", "일반": "general", "사회봉사": "volunteer", "글로벌": "global"}

CHANGE_RE = re.compile(r"기한\s*연장|기간\s*연장|일정\s*변경|날짜\s*수정|모집조건\s*변경|변경|재안내|재공지|연장|수정")


# ------------------------------------------------------------ 대상 추정 (제목)
def eligibility_from_title(t: str) -> dict:
    grades = sorted({int(g) for g in re.findall(r"([1-4])\s*학년", t)})
    status = []
    if re.search(r"대학원", t):
        status.append("대학원")
    if re.search(r"졸업\s*예정", t):
        status.append("졸업예정")
    if re.search(r"신입생|새내기", t):
        status.append("신입생")
    if re.search(r"휴학생", t):
        status.append("휴학")
    return {"grades": grades, "status": status, "note": None}


# ------------------------------------------------------------ 금액 추정 (제목)
def money_from_title(t: str) -> dict | None:
    amounts = []
    for num, unit in re.findall(r"(\d[\d,]*)\s*(만\s*원|만원|원)", t):
        v = int(num.replace(",", ""))
        amounts.append(v * 10000 if "만" in unit else v)
    if not amounts:
        return None
    kind = "상금" if re.search(r"공모|대회|상금|경진", t) else "장학금" if "장학" in t else "지원금"
    m = max(amounts)
    label = f"{m // 10000:,}만원" if m >= 10000 else f"{m:,}원"
    if re.search(r"총\s*" + re.escape(str(m // 10000)), t.replace(",", "")):
        # '총 140만원'은 전체 상금 규모라 1인 금액 합계에서 뺀다
        return {"max_won": None, "label": "총 " + label, "kind": kind}
    return {"max_won": m, "label": label, "kind": kind}


# ------------------------------------------------------------ 변경 공지 묶기
def norm_tokens(t: str) -> set[str]:
    t = re.sub(r"(\d{4})\s*학년도\s*(제\s*)?(\d)\s*학기", r"\1-\3", t)
    t = re.sub(r"\([^)]*(연장|변경|수정|마감|재)[^)]*\)", " ", t)
    t = CHANGE_RE.sub(" ", t)
    t = re.sub(r"개최|안내|및|참가\s*신청|참가신청|신청|결과물\s*제출|모집|드립니다|공지", " ", t)
    t = re.sub(r"[\[\]()「」『』'\"·,.~]", " ", t)
    return {w for w in t.split() if len(w) >= 2}


def build_threads(notices: list[dict]) -> None:
    by_date = sorted(notices, key=lambda n: (n["date"], n["no"]))
    toks = {n["no"]: norm_tokens(n["title"]) for n in by_date}
    parent: dict[int, int] = {}
    for i, n in enumerate(by_date):
        if not CHANGE_RE.search(n["title"]):
            continue
        best, best_sim = None, 0.0
        d_n = datetime.strptime(n["date"], "%Y-%m-%d")
        for m in by_date[:i]:
            if m["campus"] != n["campus"] or m.get("source") != n.get("source"):
                continue
            if (d_n - datetime.strptime(m["date"], "%Y-%m-%d")).days > 90:
                continue
            a, b = toks[n["no"]], toks[m["no"]]
            if not a or not b:
                continue
            sim = len(a & b) / len(a | b)
            if sim > best_sim:
                best, best_sim = m, sim
        if best and best_sim >= 0.6:
            parent[n["no"]] = parent.get(best["no"], best["no"])
    groups: dict[int, list[int]] = {}
    for child, root in parent.items():
        groups.setdefault(root, [root]).append(child)
    idx = {n["no"]: n for n in notices}
    for root, members in groups.items():
        members = sorted(set(members), key=lambda no: (idx[no]["date"], no))
        for no in members:
            idx[no]["thread"] = members           # 오래된 것 → 최신 순
        for prev, cur in zip(members, members[1:]):
            a, b = idx[prev].get("deadline"), idx[cur].get("deadline")
            if a and b and a != b:
                idx[cur]["deadlineChange"] = [a, b]
        idx[members[-1]]["isLatestInThread"] = True
        for no in members[:-1]:
            idx[no]["supersededBy"] = members[-1]


# ------------------------------------------------------------ 캘린더 (.ics)
def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 73:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def write_ics(path: Path, name: str, items: list[dict]) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//gongjikok//KO", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{ics_escape(name)}", "X-WR-TIMEZONE:Asia/Seoul", "REFRESH-INTERVAL;VALUE=DURATION:PT6H"]
    for n in items:
        d = date.fromisoformat(n["deadline"])
        st = date.fromisoformat(n["start"]) if n.get("start") else d
        desc = "\n".join((n.get("ai") or {}).get("summary") or []) + f"\n\n원문: {n['url']}\n(공지왔수뭉 · 마감일은 자동 추정, 원문 확인 필수)"
        lines += ["BEGIN:VEVENT", f"UID:{n.get('source', 'main')}-{n['no']}@gongjikok", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{st:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                  fold(f"SUMMARY:{ics_escape(n['title'])}"), fold(f"DESCRIPTION:{ics_escape(desc)}"),
                  fold(f"URL:{n['url']}"),
                  "BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY", "DESCRIPTION:내일 마감", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def main() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    body = json.loads(BODY.read_text(encoding="utf-8")) if BODY.exists() else {}
    notices = data["notices"]
    for n in notices:  # 다시 실행해도 결과가 같도록 이전 후처리 값 초기화
        for k in ("thread", "deadlineChange", "isLatestInThread", "supersededBy", "start"):
            n.pop(k, None)
        if n.get("deadlineSource") in ("body", "ai"):
            n["deadline"] = n.get("titleDeadline")
        else:
            n["titleDeadline"] = n.get("deadline")
    for n in notices:
        key = str(n["no"]) if n.get("source", "main") == "main" else f"{n['source']}:{n['no']}"
        ai = cache.get(key)
        n.pop("ai", None)
        n["deadlineSource"] = "title" if n.get("deadline") else None
        # 우선순위: AI(이미지까지 읽음) > 본문 텍스트 > 제목
        b = body.get(key) or {}
        if b.get("deadline"):
            n["deadline"], n["deadlineSource"] = b["deadline"], "body"
            n["start"] = b.get("start")
        if ai:
            n["ai"] = {k: ai.get(k) for k in ("summary", "target", "period", "how", "contact", "changes", "imageOnly")}
            if ai.get("deadline"):
                n["deadline"], n["deadlineSource"] = ai["deadline"], "ai"
                n["start"] = ai.get("start") if ai.get("start") and ai["start"] <= ai["deadline"] else None
        elig = (ai or {}).get("eligibility") or eligibility_from_title(n["title"])
        n["eligibility"] = elig if (elig.get("grades") or elig.get("status") or elig.get("note")) else None
        n["money"] = (ai or {}).get("money") or money_from_title(n["title"])
        if n["money"] is None and n["category"] == "등록/장학" and re.search(r"장학생\s*선발|장학금.*신청|장학생.*신청", n["title"]):
            n["money"] = {"max_won": None, "label": "장학금", "kind": "장학금"}
    build_threads(notices)

    CAL_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    dated = [n for n in notices if n.get("deadline") and n["deadline"] >= cutoff and not n.get("supersededBy")]
    write_ics(CAL_DIR / "all.ics", "공지왔수뭉 · 전체 마감일", dated)
    feeds = [{"file": "all.ics", "name": "전체", "count": len(dated)}]
    for cat, slug in CAT_SLUG.items():
        items = [n for n in dated if n["category"] == cat]
        write_ics(CAL_DIR / f"{slug}.ics", f"공지왔수뭉 · {cat}", items)
        feeds.append({"file": f"{slug}.ics", "name": cat, "count": len(items)})
    for camp, slug in (("천안", "cheonan"), ("서울", "seoul")):
        items = [n for n in dated if n["campus"] in (camp, "상명")]
        write_ics(CAL_DIR / f"{slug}.ics", f"공지왔수뭉 · {camp}캠퍼스", items)
        feeds.append({"file": f"{slug}.ics", "name": f"{camp}캠퍼스", "count": len(items)})
    data["calendars"] = feeds
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    t = sum(1 for n in notices if n.get("thread"))
    print(f"AI 정리 {sum(1 for n in notices if n.get('ai'))}건, 묶인 공지 {t}건, 금액 {sum(1 for n in notices if n.get('money'))}건, 캘린더 {len(feeds)}개")


if __name__ == "__main__":
    main()
