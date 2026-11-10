from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
import ezdxf


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def reference_profile(path: Path) -> dict:
    """Read only workbook names. No reading of IDs, data matching or writes."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [s.attrib["name"] for s in root.findall("{*}sheets/{*}sheet")]
    return {"file_name": path.name, "sha256_before": sha256(path), "sheet_names": names,
            "note": "仅核验结构与文件哈希；字段依据任务说明，不读取或匹配已有正式 ID。"}


def load_dxf(path: Path):
    document = ezdxf.readfile(path)
    # Audit another in-memory document: never let audit repairs alter parsed geometry.
    auditor = ezdxf.readfile(path).audit()
    audit = {"errors": [{"code": int(e.code), "message": e.message} for e in auditor.errors],
             "fixes": [{"code": int(e.code), "message": e.message} for e in auditor.fixes]}
    return document, audit
