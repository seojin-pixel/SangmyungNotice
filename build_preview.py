"""web/index.html에 notices.json을 내장한 단일 파일(dist/gongjikok.html)을 만듭니다."""
import json, re, pathlib
root = pathlib.Path(__file__).parent
html = (root/"web/index.html").read_text(encoding="utf-8")
data = json.dumps(json.loads((root/"web/notices.json").read_text(encoding="utf-8")), ensure_ascii=False).replace("</", "<\\/")
html = re.sub(r"/\*SNAPSHOT\*/.*?/\*END\*/", lambda m: data, html, flags=re.S)
html = html.replace('fetch("notices.json"', 'fetch("__none__.json"')
html = html.replace('<script src="config.js" onerror="this.remove()"></script>\n', '')  # 미리보기엔 알림 서버 설정 없음  # 단일 파일에선 내장 데이터만 사용
(root/"dist").mkdir(exist_ok=True)
(root/"dist/gongjikok.html").write_text(html, encoding="utf-8")
print("dist/gongjikok.html")
