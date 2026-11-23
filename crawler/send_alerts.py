"""
공지왔수뭉 마감 알림 메일 — 매일 아침 한 번 실행 (GitHub Actions: .github/workflows/alerts.yml)

하는 일
1. 처음 등록한 사람에게 '메일 주소 확인' 메일을 한 번 보냄 (확인 전에는 알림을 안 보냄)
2. 확인된 사람마다 오늘 알려야 할 마감을 모아 하루 한 통으로 보냄
   - 내 캘린더에 추가한 일정 (반복 일정 포함)
   - 관심 카테고리·키워드에 맞는 공지 마감
   - 사용자가 고른 시점(당일 / 1일 전 / 3일 전 / 1주일 전)에 맞는 것만
3. 같은 일정·같은 시점은 두 번 보내지 않음 (sent_log)

필요한 환경변수 (GitHub → Settings → Secrets and variables → Actions)
  SUPABASE_URL, SUPABASE_SERVICE_KEY   : Supabase 프로젝트 주소와 service_role 키 (절대 사이트에 넣지 말 것)
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM : 메일 발송 계정 (예: Gmail + 앱 비밀번호)
  SITE_URL                             : 배포된 사이트 주소 (확인·해지 링크용)

테스트:  python crawler/send_alerts.py --dry-run --fixture crawler/fixtures/subscriptions.json --today 2026-11-22
"""
from __future__ import annotations

import argparse
import html
import json
import os
import smtplib
import sys
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTICES = ROOT / "web" / "notices.json"
KST = timezone(timedelta(hours=9))
LEAD_TEXT = {0: "오늘 마감", 1: "내일 마감", 3: "3일 뒤 마감", 7: "1주일 뒤 마감"}
DEFAULT_CATS = ["학사", "등록/장학"]
BRAND = "공지왔수뭉"


# ------------------------------------------------------------------ 날짜 계산
def _shift(d: date, rep: str, k: int) -> date | None:
    if rep == "weekly":
        return d + timedelta(days=7 * k)
    if rep == "monthly":
        y, m = divmod(d.month - 1 + k, 12)
        try:
            return d.replace(year=d.year + y, month=m + 1)
        except ValueError:
            return None
    if rep == "yearly":
        try:
            return d.replace(year=d.year + k)
        except ValueError:
            return None
    return d if k == 0 else None


def next_end(s: str, e: str, rep: str, today: date) -> date | None:
    """반복을 고려해서 오늘 이후(오늘 포함) 가장 가까운 마감일"""
    s0, e0 = date.fromisoformat(s), date.fromisoformat(e)
    span = e0 - s0
    if not rep or rep == "none":
        return e0 if e0 >= today else None
    for k in range(0, 800):
        st = _shift(s0, rep, k)
        if st is None:
            continue
        if st + span >= today:
            return st + span
    return None


# ------------------------------------------------------------------ 알릴 거리 모으기
def collect(sub: dict, notices: list[dict], today: date) -> list[dict]:
    leads = set(sub.get("leads") or [1])
    out: dict[str, dict] = {}

    def add(key: str, title: str, end: date | None, url: str | None, kind: str, extra: str = ""):
        if end is None:
            return
        left = (end - today).days
        if left in leads:
            k = f"{key}@{end.isoformat()}"
            out[k] = {"key": k, "title": title, "end": end, "left": left, "url": url, "kind": kind, "extra": extra}

    if sub.get("want_calendar", True):
        for it in sub.get("items") or []:
            if not it.get("e"):
                continue
            kind = "내 일정" if it.get("custom") else "공지"
            # 같은 공지가 캘린더와 관심 공지 양쪽으로 들어와도 한 번만 보내도록 키를 맞춤
            key = f"notice:{it.get('key')}" if kind == "공지" else f"cal:{it.get('key')}"
            add(key, it.get("t") or "일정", next_end(it.get("s") or it["e"], it["e"], it.get("rep", "none"), today),
                it.get("url"), kind)

    if sub.get("want_notices", True):
        cats = sub.get("categories") or DEFAULT_CATS
        kws = [k.lower() for k in (sub.get("keywords") or [])]
        camp = sub.get("campus") or "천안"
        for n in notices:
            if not n.get("deadline") or n.get("supersededBy"):
                continue
            if camp != "전체" and n.get("campus") not in (camp, "상명"):
                continue
            hay = (n["title"] + " " + " ".join((n.get("ai") or {}).get("summary") or [])).lower()
            if n.get("category") in cats or any(k in hay for k in kws):
                add(f"notice:{n['no']}", n["title"], date.fromisoformat(n["deadline"]), n.get("url"), "공지")

    return sorted(out.values(), key=lambda x: (x["left"], x["title"]))


# ------------------------------------------------------------------ 메일 만들기
def digest_mail(sub: dict, items: list[dict], site: str) -> tuple[str, str, str]:
    groups: dict[int, list[dict]] = {}
    for it in items:
        groups.setdefault(it["left"], []).append(it)
    subject = f"[{BRAND}] " + " · ".join(f"{LEAD_TEXT[k]} {len(v)}건" for k, v in sorted(groups.items()))
    unsub = f"{site}?unsubscribe={sub['token']}"
    text_lines, html_parts = [], []
    for left, its in sorted(groups.items()):
        text_lines.append(f"■ {LEAD_TEXT[left]}")
        html_parts.append(f'<h3 style="margin:18px 0 6px;font-size:15px;color:#4b22c9">{LEAD_TEXT[left]}</h3><ul style="margin:0;padding-left:18px">')
        for it in its:
            when = f"{it['end'].month}/{it['end'].day}"
            text_lines.append(f"- [{it['kind']}] {it['title']} ({when}{', ' + it['extra'] if it['extra'] else ''})" + (f"\n  {it['url']}" if it["url"] else ""))
            title = html.escape(it["title"])
            link = f'<a href="{html.escape(it["url"])}" style="color:#1e1532">{title}</a>' if it["url"] else title
            extra = f" · {html.escape(it['extra'])}" if it["extra"] else ""
            html_parts.append(f'<li style="margin:4px 0"><span style="font-size:12px;color:#6b3ff2;font-weight:600">[{it["kind"]}]</span> {link} <span style="color:#6c6485;font-size:12px">{when}{extra}</span></li>')
        html_parts.append("</ul>")
    foot = f"공지왔수뭉은 상명대학교 공식 서비스가 아닌 학생 프로젝트예요. 날짜는 자동으로 찾은 값이라 꼭 원문을 확인해 주세요.\n알림 그만 받기: {unsub}"
    text = "\n".join(text_lines) + "\n\n" + foot
    body = f'''<div style="font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;max-width:560px;color:#1e1532">
      <div style="background:#6b3ff2;color:#fff;border-radius:14px;padding:16px 18px;font-size:18px;font-weight:700">{BRAND} 마감 알림</div>
      {''.join(html_parts)}
      <p style="margin-top:22px;font-size:12px;color:#6c6485;line-height:1.6">공지왔수뭉은 상명대학교 공식 서비스가 아닌 학생 프로젝트예요. 날짜는 자동으로 찾은 값이라 꼭 원문을 확인해 주세요.<br>
      <a href="{html.escape(site)}" style="color:#6b3ff2">사이트 열기</a> · <a href="{html.escape(unsub)}" style="color:#6c6485">알림 그만 받기</a></p></div>'''
    return subject, text, body


def confirm_mail(sub: dict, site: str) -> tuple[str, str, str]:
    link = f"{site}?confirm={sub['token']}"
    subject = f"[{BRAND}] 마감 알림 메일 주소를 확인해 주세요"
    text = f"아래 링크를 누르면 마감 알림이 시작돼요.\n{link}\n\n직접 신청하지 않았다면 이 메일은 무시하세요. 아무 메일도 더 가지 않아요."
    body = f'''<div style="font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;max-width:560px;color:#1e1532">
      <p style="font-size:16px;font-weight:700">{BRAND} 마감 알림을 신청하셨나요?</p>
      <p><a href="{html.escape(link)}" style="display:inline-block;background:#6b3ff2;color:#fff;padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:700">메일 주소 확인하기</a></p>
      <p style="font-size:12px;color:#6c6485">직접 신청하지 않았다면 이 메일은 무시하세요. 아무 메일도 더 가지 않아요.</p></div>'''
    return subject, text, body


# ------------------------------------------------------------------ 외부 연결
class Store:
    """Supabase REST (service_role 키로 서버에서만 사용)"""
    def __init__(self, url: str, key: str):
        import requests
        self.s = requests.Session()
        self.base = url.rstrip("/") + "/rest/v1"
        self.s.headers.update({"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})

    def subscriptions(self) -> list[dict]:
        r = self.s.get(f"{self.base}/subscriptions", params={"select": "*"}, timeout=20)
        r.raise_for_status()
        return r.json()

    def sent_keys(self, token: str) -> set[tuple[str, int]]:
        r = self.s.get(f"{self.base}/sent_log", params={"select": "item_key,lead", "token": f"eq.{token}"}, timeout=20)
        r.raise_for_status()
        return {(x["item_key"], x["lead"]) for x in r.json()}

    def log_sent(self, token: str, items: list[dict]) -> None:
        rows = [{"token": token, "item_key": it["key"], "lead": it["left"]} for it in items]
        self.s.post(f"{self.base}/sent_log", data=json.dumps(rows), timeout=20,
                    headers={"Prefer": "resolution=ignore-duplicates"}).raise_for_status()

    def mark_confirm_sent(self, token: str) -> None:
        self.s.patch(f"{self.base}/subscriptions", params={"token": f"eq.{token}"},
                     data=json.dumps({"confirm_sent_at": datetime.now(timezone.utc).isoformat()}), timeout=20).raise_for_status()


class FixtureStore:
    """--fixture 로 넘긴 JSON 파일로 테스트"""
    def __init__(self, path: Path):
        self.data = json.loads(path.read_text(encoding="utf-8"))
        self.log: list[tuple] = []

    def subscriptions(self): return self.data
    def sent_keys(self, token): return {(k, l) for t, k, l in self.log if t == token}
    def log_sent(self, token, items): self.log += [(token, it["key"], it["left"]) for it in items]
    def mark_confirm_sent(self, token): pass


class Mailer:
    def __init__(self, dry: bool):
        self.dry = dry
        self.host, self.port = os.environ.get("SMTP_HOST", ""), int(os.environ.get("SMTP_PORT", "465"))
        self.user, self.pw = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASS", "")
        self.sender = os.environ.get("MAIL_FROM") or self.user
        self.conn = None

    def send(self, to: str, subject: str, text: str, body: str) -> None:
        if self.dry:
            print(f"\n=== [DRY] to={to}\n제목: {subject}\n{text}\n")
            return
        msg = MIMEMultipart("alternative")
        msg["Subject"], msg["From"], msg["To"] = subject, f"{BRAND} <{self.sender}>", to
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(body, "html", "utf-8"))
        if self.conn is None:
            if self.port == 465:
                self.conn = smtplib.SMTP_SSL(self.host, self.port, timeout=30)
            else:
                self.conn = smtplib.SMTP(self.host, self.port, timeout=30)
                self.conn.starttls()
            self.conn.login(self.user, self.pw)
        self.conn.sendmail(self.sender, [to], msg.as_string())

    def close(self):
        if self.conn:
            self.conn.quit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="메일을 보내지 않고 내용만 출력")
    ap.add_argument("--fixture", type=Path, help="Supabase 대신 JSON 파일에서 구독 정보 읽기")
    ap.add_argument("--today", help="기준 날짜(YYYY-MM-DD), 테스트용")
    a = ap.parse_args()

    today = date.fromisoformat(a.today) if a.today else datetime.now(KST).date()
    site = os.environ.get("SITE_URL", "https://example.github.io/gongjiwatsumung/")
    notices = json.loads(NOTICES.read_text(encoding="utf-8"))["notices"] if NOTICES.exists() else []
    if a.fixture:
        store = FixtureStore(a.fixture)
    else:
        url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            print("SUPABASE_URL / SUPABASE_SERVICE_KEY 가 없어 알림을 건너뜀", file=sys.stderr)
            return
        store = Store(url, key)
    mailer = Mailer(a.dry_run)
    sent = confirms = 0
    try:
        for sub in store.subscriptions():
            if not sub.get("confirmed"):
                if not sub.get("confirm_sent_at"):
                    mailer.send(sub["email"], *confirm_mail(sub, site))
                    store.mark_confirm_sent(sub["token"])
                    confirms += 1
                continue
            items = collect(sub, notices, today)
            done = store.sent_keys(sub["token"])
            items = [it for it in items if (it["key"], it["left"]) not in done]
            if not items:
                continue
            mailer.send(sub["email"], *digest_mail(sub, items, site))
            store.log_sent(sub["token"], items)
            sent += 1
    finally:
        mailer.close()
    print(f"{today} 알림 메일 {sent}통, 확인 메일 {confirms}통", file=sys.stderr)


if __name__ == "__main__":
    main()
