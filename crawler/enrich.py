"""
공지왔수뭉 AI 정리기 — 공지 본문(텍스트 + 이미지)을 읽어 요약·대상·기간·금액을 뽑습니다.

- 본문이 이미지뿐인 공지도 Claude가 이미지를 직접 읽습니다(별도 OCR 불필요).
- 한 번 정리한 공지는 crawler/cache/ai.json 에 저장해 다시 비용을 쓰지 않습니다.
- ANTHROPIC_API_KEY 가 없으면 아무것도 하지 않고 넘어갑니다.

사용법:
    pip install requests beautifulsoup4 anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python crawler/enrich.py --max 30
환경변수 GONGJIKOK_MODEL 로 모델을 바꿀 수 있습니다(기본: claude-haiku-4-5, 저렴한 모델 권장).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "notices.json"
CACHE = Path(__file__).resolve().parent / "cache" / "ai.json"
MODEL = os.environ.get("GONGJIKOK_MODEL", "claude-haiku-4-5")
MAX_IMAGES = 4

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": {"type": "string"}, "description": "핵심 3줄. 각 줄 40자 안팎, ~해요체. 이미지 속 내용도 반영"},
        "imageOnly": {"type": "boolean", "description": "본문에 텍스트 없이 이미지만 있었으면 true"},
        "target": {"type": ["string", "null"], "description": "누가 대상인지 한 줄"},
        "period": {"type": ["string", "null"], "description": "신청/진행 기간 원문 그대로 짧게"},
        "start": {"type": ["string", "null"], "description": "신청·진행 시작일 YYYY-MM-DD. 게시기간 아님. 없으면 null"},
        "deadline": {"type": ["string", "null"], "description": "신청 마감일 YYYY-MM-DD(신청이 없으면 행사일). 게시기간 아님. 없으면 null"},
        "how": {"type": ["string", "null"], "description": "신청 방법 한 줄"},
        "contact": {"type": ["string", "null"], "description": "부서명과 전화번호. 개인 이메일은 넣지 말 것"},
        "eligibility": {
            "type": "object",
            "properties": {
                "grades": {"type": "array", "items": {"type": "integer"}, "description": "대상 학년. 제한 없으면 []"},
                "status": {"type": "array", "items": {"type": "string", "enum": ["재학", "휴학", "졸업예정", "졸업생", "대학원", "신입생"]}, "description": "대상 학적. 제한 없으면 []"},
                "note": {"type": ["string", "null"], "description": "기타 자격(예: 이공계열, 평점 3.5 이상)"},
            },
            "required": ["grades", "status", "note"],
        },
        "money": {
            "type": ["object", "null"],
            "properties": {
                "max_won": {"type": ["integer", "null"], "description": "한 사람이 받을 수 있는 최대 금액(원). 불명확하면 null"},
                "label": {"type": "string", "description": "예: '대상 70만원', '등록금 전액'"},
                "kind": {"type": "string", "enum": ["장학금", "상금", "지원금", "활동비", "기타"]},
            },
            "required": ["max_won", "label", "kind"],
        },
        "changes": {"type": ["string", "null"], "description": "변경/연장 공지라면 무엇이 바뀌었는지 한 줄"},
    },
    "required": ["summary", "target", "period", "start", "deadline", "how", "contact", "eligibility", "money", "changes"],
}

PROMPT = """다음은 상명대학교 공지사항입니다. 대학생이 클릭하지 않고도 핵심을 알 수 있도록 정리해 주세요.
- 이미지 안의 글자도 읽어서 반영하세요.
- 본문에 없는 내용은 지어내지 말고 null 로 두세요.
- '게시기간'은 마감일이 아닙니다.
- 오늘 날짜: {today}

제목: {title}
게시일: {date} / 캠퍼스: {campus} / 분류: {category}

본문 텍스트:
{body}
"""


def fetch_body(session, url: str) -> tuple[str, list[tuple[str, bytes]]]:
    from bs4 import BeautifulSoup

    r = session.get(url, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    # 본문 영역 추정: 편집기 이미지가 있거나 가장 텍스트가 긴 div
    cands = soup.select('[class*="board-view-content"], [class*="view-con"], [class*="fr-view"], .board-contents, article')
    node = max(cands, key=lambda x: len(x.get_text()), default=None) if cands else None
    if node is None:
        divs = soup.find_all("div")
        node = max(divs, key=lambda d: len(d.find_all("p")) + len(d.select('img[src*="editorImage"]')) * 3, default=soup)
    text = re.sub(r"\n{3,}", "\n\n", node.get_text("\n", strip=True))[:6000]
    images = []
    for img in node.find_all("img")[:MAX_IMAGES]:
        src = img.get("src") or ""
        if not src or src.startswith("data:") or "ic-" in src:
            continue
        try:
            ir = session.get(urljoin(url, src), timeout=20)
            ctype = ir.headers.get("content-type", "image/jpeg").split(";")[0]
            if ctype.startswith("image/") and len(ir.content) < 4_500_000:
                images.append((ctype, ir.content))
        except Exception:
            pass
    return text, images


def ask_claude(client, notice: dict, text: str, images) -> dict:
    content = []
    for ctype, data in images:
        content.append({"type": "image", "source": {"type": "base64", "media_type": ctype, "data": base64.b64encode(data).decode()}})
    content.append({"type": "text", "text": PROMPT.format(
        today=time.strftime("%Y-%m-%d"), title=notice["title"], date=notice["date"],
        campus=notice["campus"], category=notice["category"], body=text or "(텍스트 없음, 이미지만 있음)")})
    msg = client.messages.create(
        model=MODEL, max_tokens=1200,
        tools=[{"name": "save", "description": "정리 결과 저장", "input_schema": SCHEMA}],
        tool_choice={"type": "tool", "name": "save"},
        messages=[{"role": "user", "content": content}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("no tool output")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=100, help="한 번에 새로 정리할 최대 건수(비용 제한)")
    a = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY 없음 → AI 정리 건너뜀", file=sys.stderr)
        return
    import anthropic
    import requests

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    data = json.loads(DATA.read_text(encoding="utf-8"))
    client = anthropic.Anthropic()
    s = requests.Session()
    s.headers["User-Agent"] = "GongjiKok/1.0 (+student project; notice aggregator)"
    todo = [n for n in data["notices"] if str(n["no"]) not in cache][: a.max]
    for i, n in enumerate(todo, 1):
        try:
            text, images = fetch_body(s, n["url"])
            cache[str(n["no"])] = ask_claude(client, n, text, images) | {"model": MODEL}
            print(f"[{i}/{len(todo)}] {n['no']} 정리 완료 (이미지 {len(images)}장)", file=sys.stderr)
        except Exception as e:  # 한 건 실패해도 나머지는 계속
            print(f"[{i}/{len(todo)}] {n['no']} 실패: {e}", file=sys.stderr)
        time.sleep(1)
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
