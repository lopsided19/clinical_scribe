"""Read published competition pages and their explicitly linked public assets. No login or scanning."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/official_site_audit"
BASE = "https://bjcsaiic.zsyqkj.com/"


class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []
        self.scripts = []
        self.text = []
        self.current = None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.current = {"href": attrs.get("href"), "text": ""}
        if tag == "script":
            if attrs.get("src"):
                self.scripts.append(attrs["src"])
            self.skip += 1
        if tag == "style": self.skip += 1

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.anchors.append(self.current)
            self.current = None
        if tag in {"script", "style"}: self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if self.current is not None: self.current["text"] += data.strip()
        if not self.skip and data.strip(): self.text.append(data.strip())


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=35) as response:
        return response.read(), response.headers.get("Content-Type", "")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"checked_at": datetime.now(timezone.utc).isoformat(), "pages": []}
    for name, path in [("home", ""), ("topic8", "topics/2026/8"), ("resources", "resources")]:
        url = urljoin(BASE, path)
        try:
            raw, mime = fetch(url)
            text = raw.decode("utf-8")
            (OUT / f"{name}.html").write_text(text, encoding="utf-8")
            parser = Parser(); parser.feed(text)
            plain = "\n".join(parser.text)
            (OUT / f"{name}.txt").write_text(plain, encoding="utf-8")
            links = [{"url": urljoin(url, x["href"]), "text": x["text"]} for x in parser.anchors if x["href"]]
            entry = {"name": name, "url": url, "sha256": hashlib.sha256(raw).hexdigest(), "content_type": mime,
                     "links": links, "scripts": [urljoin(url, s) for s in parser.scripts]}
            manifest["pages"].append(entry)
            print(json.dumps({"name": name, "bytes": len(raw), "text": plain[:22000], "links": links, "scripts": entry["scripts"]}, ensure_ascii=False), flush=True)
        except Exception as exc:
            manifest["pages"].append({"name": name, "url": url, "error": type(exc).__name__ + ": " + str(exc)})
            print(name, type(exc).__name__, str(exc), flush=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
