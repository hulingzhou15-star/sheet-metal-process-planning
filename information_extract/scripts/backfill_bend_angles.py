"""Explicitly backfill the user-approved P002/P003 bend metadata only."""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT)]

from extract_app.drawing import load_drawing
from extract_app.model import ProjectStore
from extract_app.operation_fields import fill_missing_bend_angles, DEFAULT_BEND_ANGLE_DEG
from extract_app.pairing import synchronize_pairing


def without_new_metadata(project):
    value = deepcopy(project)
    value.pop('revision', None)
    value.pop('saved_at', None)
    for op in value['operations']:
        op.pop('bend_angle_deg', None)
    return value


def request(port, method, route, payload=None):
    connection = HTTPConnection('127.0.0.1', port, timeout=15)
    try:
        content = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8') if payload is not None else None
        connection.request(method, route, content, {'Content-Type': 'application/json'})
        response = connection.getresponse()
        result = json.loads(response.read())
        if response.status != 200:
            raise ValueError(result.get('error', f'HTTP {response.status}'))
        return result
    finally:
        connection.close()


def backfill(part, apply=False):
    config = json.loads((ROOT / f'configs/{part}.json').read_text(encoding='utf-8'))
    drawing = load_drawing(ROOT / config['source_file'], config)
    store = ProjectStore(ROOT, drawing, part)
    original_bytes = store.path.read_bytes()
    previous = json.loads(original_bytes)
    updated, changed = fill_missing_bend_angles(previous)
    result = {'part_id': part, 'before_revision': previous['revision'], 'angle_deg': DEFAULT_BEND_ANGLE_DEG,
              'operation_ids': changed, 'updated_count': len(changed), 'applied': False}
    if not changed or not apply:
        return result
    # Ordinary saving normalizes pairing; ensure it will not change unrelated facts.
    normalized = deepcopy(updated)
    if updated.get('workflow_version') == 2:
        synchronize_pairing(normalized)
    if normalized != updated:
        raise ValueError(f'{part} 的配对数据需另行确认；本脚本不会顺带改写其它字段。')
    backup = ROOT / f'outputs/{part}/bend_angle_backfill_revision_{previous["revision"]}'
    backup.mkdir(parents=True, exist_ok=False)
    (backup / 'project_before_backfill.json').write_bytes(original_bytes)
    if store.path.read_bytes() != original_bytes:
        raise ValueError(f'{part} 保存记录已变化，未提交补填。请重试。')
    try:
        health = request(config['port'], 'GET', '/api/health')
    except ConnectionRefusedError:
        health = None
    if health is not None:
        if health.get('module') != 'information_extract' or health.get('source_sha256') != drawing['source']['sha256']:
            raise ValueError(f'{part} 端口不是预期来源的服务，未提交。')
        saved = request(config['port'], 'POST', '/api/save', {'project': updated, 'expected_revision': previous['revision']})['project']
        result['save_method'] = 'existing_local_api'
    else:
        saved = store.save(updated, previous['revision'])['project']
        result['save_method'] = 'offline_project_store'
    if without_new_metadata(saved) != without_new_metadata(previous):
        raise ValueError(f'{part} 保存后发现超出角度字段的变化，请检查备份与新修订。')
    result.update(applied=True, after_revision=saved['revision'], backup_directory=str(backup.relative_to(ROOT)),
                  before_sha256=hashlib.sha256(original_bytes).hexdigest(),
                  after_sha256=hashlib.sha256(store.path.read_bytes()).hexdigest(),
                  created_at_utc=datetime.now(timezone.utc).isoformat())
    (backup / 'backfill_report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='仅为已明确确认的 P002/P003 补填默认折弯角度')
    parser.add_argument('--parts', nargs='+', choices=['P002', 'P003'], default=['P002', 'P003'])
    parser.add_argument('--apply', action='store_true', help='创建备份后，按正常保存流程生成新修订')
    args = parser.parse_args()
    for part in args.parts:
        print(json.dumps(backfill(part, args.apply), ensure_ascii=False), flush=True)
