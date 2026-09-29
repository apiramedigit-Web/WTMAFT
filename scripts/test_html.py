"""Static HTML validation of the rendered dashboard (stdlib parser; no network).
Checks: doctype/lang/charset/viewport/title, balanced non-void tags, unique ids, every tab link
targets an existing section, embedded JSON parses, and no secret-shaped strings.
Output: evidence/12_html_validation.json
"""
import datetime as dt
import html.parser
import json
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
HTML = BASE / "output" / "weekly_top_moving_asin_backend_keyword_fine_tuning_report.html"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Checker(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.ids, self.hrefs, self.sections = [], [], {}, [], set()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids[a["id"]] = self.ids.get(a["id"], 0) + 1
        if tag == "section" and a.get("id"):
            self.sections.add(a["id"])
        if tag == "a" and (a.get("href") or "").startswith("#"):
            self.hrefs.append(a["href"][1:])
        if tag == "html":
            self.meta["lang"] = a.get("lang")
        if tag == "meta" and a.get("charset"):
            self.meta["charset"] = a["charset"].lower()
        if tag == "meta" and a.get("name") == "viewport":
            self.meta["viewport"] = True
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            self.errors.append(f"unexpected </{tag}> at {self.getpos()} (open: {self.stack[-1] if self.stack else None})")
            if any(t == tag for t, _ in self.stack):
                while self.stack and self.stack[-1][0] != tag:
                    self.stack.pop()
                self.stack.pop()
            return
        self.stack.pop()


def main(path=HTML):
    src = path.read_text(encoding="utf-8")
    c = Checker()
    c.feed(src)
    c.close()
    res = []

    def check(name, ok, detail=""):
        res.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": str(detail)[:400]})
        print(f'{"PASS" if ok else "FAIL"}  {name}  {"" if ok else detail}')

    check("<!DOCTYPE html> first", src.lstrip().lower().startswith("<!doctype html>"))
    check("html lang, meta charset utf-8, viewport, title",
          c.meta.get("lang") == "en" and c.meta.get("charset") == "utf-8" and c.meta.get("viewport")
          and re.search(r"<title>[^<]+</title>", src), c.meta)
    check("all non-void tags balanced", not c.errors and not c.stack, (c.errors[:3], c.stack[:3]))
    dup = {k: v for k, v in c.ids.items() if v > 1}
    check("element ids unique", not dup, dup)
    missing = [h for h in c.hrefs if h not in c.sections]
    check("every tab link targets an existing section (incl. #s13 Full Optimization)",
          not missing and "s13" in c.hrefs and "s13" in c.sections, missing)
    m = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', src, re.S)
    try:
        d = json.loads(m.group(1))
        ok = "alert_monitoring" in d and "monitoring_performance" in d
    except Exception as e:  # noqa: BLE001
        ok, d = False, e
    check("embedded JSON parses and carries monitoring data", ok)
    secrets = re.findall(r"ya29\.[A-Za-z0-9_-]{20,}|1//0[A-Za-z0-9_-]{20,}|GOCSPX-[A-Za-z0-9_-]{10,}|re_[A-Za-z0-9]{8,}_", src)
    check("no token / client-secret / API-key shaped strings in the dashboard", not secrets, secrets[:2])
    f = sum(r["result"] == "FAIL" for r in res)
    (BASE / "evidence" / "12_html_validation.json").write_text(json.dumps(
        {"validated_at": dt.datetime.now().isoformat(timespec="seconds"), "file": str(path.relative_to(BASE)),
         "bytes": len(src.encode()), "passed": len(res) - f, "failed": f, "checks": res}, indent=1), encoding="utf-8")
    print(f"{len(res) - f} PASS / {f} FAIL")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
