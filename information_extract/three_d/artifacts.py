"""Repository-relative artifact IO; independent of historical diagnostic scripts."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]


def resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def relative(path):
    return resolve(path).resolve().relative_to(ROOT).as_posix()


def portable(value):
    if isinstance(value, dict):
        return {portable(k): portable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [portable(v) for v in value]
    if isinstance(value, str):
        normalized = value.replace('\\', '/')
        prefix = ROOT.as_posix() + '/'
        if normalized.lower().startswith(prefix.lower()):
            return normalized[len(prefix):]
    return value


def read(path):
    return json.loads(resolve(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(resolve(path).read_bytes()).hexdigest()


def write(path, data):
    path = resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(portable(data), ensure_ascii=False, indent=2,
                               allow_nan=False) + '\n', encoding='utf-8')


def source_manifest(paths):
    return {relative(p): sha(p) for p in paths}


def verify_sources(manifest):
    for path, digest in manifest.items():
        if sha(path) != digest:
            raise ValueError('Source hash mismatch: ' + path)
