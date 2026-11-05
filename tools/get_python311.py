"""Optional isolated Python 3.11 verification runtime; never installs globally."""
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
PUBLISHED_MD5 = "6d9aa08531d48fcc261ba667e2df17c4"

if __name__ == "__main__":
    target = ROOT / "artifacts/python311"
    target.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=90) as response:
        raw = response.read()
    if hashlib.md5(raw).hexdigest() != PUBLISHED_MD5:
        raise ValueError("Python official download integrity mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for name in archive.namelist():
            if not (target / name).resolve().is_relative_to(target.resolve()):
                raise ValueError("Invalid archive path")
        archive.extractall(target)
    (target / "python311._pth").write_text("python311.zip\n.\n../..\n", encoding="utf-8")
    (target / "download_provenance.json").write_text(json.dumps({"url": URL, "sha256": hashlib.sha256(raw).hexdigest(),
        "purpose": "isolated compatibility tests; not packaged for submission", "version": "3.11.9"}, indent=2), encoding="utf-8")
    print(target / "python.exe")
