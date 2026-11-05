"""Download only public, publisher-hosted development data; never used by main.py."""
from __future__ import annotations

import hashlib
import json
import argparse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ClinicalScribe-Research/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="显式更新上游版本；默认重放已锁定的下载清单")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / "data/download_manifest.json"
    if manifest_path.exists() and not args.refresh:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            path = (ROOT / entry["file"]).resolve()
            if not path.is_relative_to(RAW.resolve()):
                raise ValueError("下载路径超出 data/raw")
            body = path.read_bytes() if path.exists() else get(entry["url"])
            if hashlib.sha256(body).hexdigest() != entry["sha256"]:
                raise ValueError("固定版本校验失败：" + entry["file"])
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            print("verified", entry["file"], flush=True)
        return
    records = []

    def save(name: str, url: str, license_name: str, dataset: str) -> None:
        path = RAW / name
        path.parent.mkdir(parents=True, exist_ok=True)
        body = get(url)
        path.write_bytes(body)
        records.append({"file": str(path.relative_to(ROOT)).replace("\\", "/"),
                        "url": url, "dataset": dataset, "license": license_name,
                        "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                        "retrieved_at": datetime.now(timezone.utc).isoformat()})
        print(name, len(body), flush=True)

    hf = json.loads(get("https://huggingface.co/api/datasets/FreedomIntelligence/CMB"))
    revision = hf["sha"]
    base = f"https://huggingface.co/datasets/FreedomIntelligence/CMB/resolve/{revision}"
    save("cmb_clin.json", base + "/CMB-Clin/CMB-Clin-qa.json", "Apache-2.0 (publisher declaration)", "CMB-Clin")
    save("CMB_DATA_CARD.md", base + "/README.md", "Apache-2.0", "CMB-Clin")
    gh = json.loads(get("https://api.github.com/repos/FreedomIntelligence/CMB/commits/main"))["sha"]
    save("CMB_LICENSE.txt", f"https://raw.githubusercontent.com/FreedomIntelligence/CMB/{gh}/LICENSE", "Apache-2.0", "CMB-Clin")
    mimic = "https://physionet.org/files/mimic-iv-demo/2.2/"
    for filename in ["patients.csv.gz", "admissions.csv.gz", "d_labitems.csv.gz", "labevents.csv.gz"]:
        save("mimic_demo/" + filename, mimic + "hosp/" + filename, "ODbL-1.0", "MIMIC-IV Demo 2.2")
    for filename in ["LICENSE.txt", "README.txt", "SHA256SUMS.txt"]:
        save("mimic_demo/" + filename, mimic + filename, "ODbL-1.0", "MIMIC-IV Demo 2.2")
    manifest = {"purpose": "offline development only; not an approved competition retrieval corpus",
                "cmb_hf_revision": revision, "cmb_repository_revision": gh, "files": records}
    (ROOT / "data" / "download_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
