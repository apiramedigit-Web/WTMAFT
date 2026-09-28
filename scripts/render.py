"""Render data/report_dataset.json into the standalone HTML report (CSS, JS and data inline)."""
import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = BASE / "scripts" / "report_template.html"
DATASET = BASE / "data" / "report_dataset.json"
OUT = BASE / "output" / "weekly_top_moving_asin_backend_keyword_fine_tuning_report.html"
BS = chr(92)  # backslash, built at runtime so no editor can collapse the escape


def render(ds, out_path):
    payload = json.dumps(ds, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    # Keep the JSON inert inside <script>: no "</script>" or "<!--" can terminate/open markup.
    payload = payload.replace("</", "<" + BS + "/").replace("<!--", "<" + BS + "u0021--")
    html = TEMPLATE.read_text(encoding="utf-8")
    assert html.count("__REPORT_DATA__") == 1
    out_path.write_text(html.replace("__REPORT_DATA__", payload), encoding="utf-8", newline="\n")
    return out_path


def main():
    out = render(json.loads(DATASET.read_text(encoding="utf-8")), OUT)
    print(out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
