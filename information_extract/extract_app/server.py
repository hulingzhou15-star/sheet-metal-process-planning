"""Local-only annotation API. All writable artifacts stay in the module root."""
from __future__ import annotations

import base64
import hashlib
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import threading
import traceback
from urllib.parse import unquote, urlparse

from .drawing import load_drawing
from .model import OPERATION_TYPES, SURFACE_SIDES, ProjectStore, validate_project, basic_check
from .pairing import synchronize_pairing
from .workflow import FEATURE_TYPES, annotation_project, compile_project
from .operation_fields import FIELD_DEFAULTS


def contained(root, relative):
    destination = (root / relative).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ValueError("文件路径必须位于本模块目录。")
    return destination


def make_handler(root, drawing, store):
    mutation_lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(fmt % args, flush=True)

        def json_response(self, value, code=200):
            content = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            try:
                path = unquote(urlparse(self.path).path)
                if path == "/api/bootstrap":
                    self.json_response({"drawing": drawing, "project": annotation_project(store.load()),
                                        "feature_types": FEATURE_TYPES,
                                        "field_defaults": FIELD_DEFAULTS,
                                        "operation_types": OPERATION_TYPES, "surface_sides": SURFACE_SIDES})
                elif path == "/api/project":
                    self.json_response({"project": annotation_project(store.load())})
                elif path == "/api/station_defaults":
                    from .process_config import existing_station_defaults
                    self.json_response(existing_station_defaults(root, store.empty["part_id"]))
                elif path == "/api/health":
                    self.json_response({"status": "ok", "module": "information_extract", "source_sha256": drawing["source"]["sha256"]})
                else:
                    if path in {"/", "/index.html"}:
                        file = root / "web/index.html"
                    elif path.startswith("/outputs/"):
                        file = contained(root / "outputs", path.removeprefix("/outputs/"))
                    else:
                        file = contained(root / "web", path.lstrip("/"))
                    if not file.is_file():
                        self.json_response({"error": "文件不存在。"}, 404)
                        return
                    content = file.read_bytes()
                    mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or file.suffix == ".js" else ""))
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(content)
            except (ValueError, OSError) as error:
                self.json_response({"error": str(error)}, 400)

        def do_POST(self):
            try:
                origin = self.headers.get("Origin")
                if origin and urlparse(origin).netloc != self.headers.get("Host"):
                    raise ValueError("请在本地工具页面中执行此操作。")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= drawing["config"]["max_request_bytes"]:
                    raise ValueError("请求为空或超过文件大小限制。")
                data = json.loads(self.rfile.read(length))
                # file_name is a display/provenance basename; source_file is the
                # configured module-relative path after repository migration.
                source_relative = drawing["config"].get("source_file") or drawing["source"].get("file_name")
                source_path = contained(root, source_relative) if source_relative else None
                if source_path and hashlib.sha256(source_path.read_bytes()).hexdigest() != drawing["source"]["sha256"]:
                    raise ValueError("源 DXF 在服务运行期间发生改变，请先保存备份，再重新启动服务核对来源。")
                for dependency in drawing.get("source_dependencies", []):
                    dependency_path = contained(root, dependency["file_name"])
                    if hashlib.sha256(dependency_path.read_bytes()).hexdigest() != dependency["sha256"]:
                        raise ValueError("补充参考图或配准清单已变化，请备份页面内容后重新启动服务。")
                route = urlparse(self.path).path
                if route == "/api/save":
                    with mutation_lock:
                        result = store.save(data["project"], data["expected_revision"])
                elif route == "/api/validate":
                    report = validate_project(data["project"], drawing)
                    result = {"report": report, **{key: report[key] for key in ("statuses", "statistics", "geometries", "regions")}}
                elif route == "/api/pairing":
                    project = deepcopy(data["project"])
                    basic_check(project, drawing)
                    result = synchronize_pairing(project)
                elif route == "/api/compile_geometry":
                    project = annotation_project(data["project"])
                    basic = validate_project(project, drawing, annotation_only=True)
                    project["geometry_batch"] = compile_project(project, drawing, basic)
                    report = validate_project(project, drawing)
                    result = {"project": project, "report": report}
                elif route == "/api/normalize_project":
                    project = annotation_project(data["project"])
                    validate_project(project, drawing, annotation_only=True)
                    result = {"project": project}
                elif route == "/api/preview":
                    from .geometry import preview_geometry, derive_region
                    op = deepcopy(data["operation"])
                    index = {s["id"]: s for s in drawing["segments"]}
                    selected = [index[sid] for sid in op.get("source_segment_ids", [])]
                    report = validate_project(data.get("project", store.load()), drawing)
                    geometry, region, issues = [], None, []
                    if data.get("project", {}).get("workflow_version") == 2:
                        self.json_response({"geometry": [], "region": None,
                                            "issues": [{"message": "标注阶段仅保存来源图元；整图解释完成后统一整理几何。"}],
                                            "statuses": report["statuses"], "statistics": report["statistics"]})
                        return
                    try:
                        geometry = preview_geometry(op, selected, drawing["config"])
                        if op.get("region"):
                            region_input = deepcopy(op["region"])
                            region_input.update(confirmed=True, parent_confirmed=True)
                            region = derive_region(op, geometry, region_input)
                    except (ValueError, KeyError, TypeError) as error:
                        issues.append({"message": str(error)})
                    result = {"geometry": geometry, "region": region, "issues": issues,
                              "statuses": report["statuses"], "statistics": report["statistics"]}
                elif route == "/api/export":
                    from .exporter import export_workbook
                    project = annotation_project(data["project"]) if data["project"].get("workflow_version") == 2 else data["project"]
                    report = validate_project(project, drawing)
                    project = deepcopy(project)
                    project["solver_settings"] = drawing["config"]["solver"]
                    result = export_workbook(project, report, contained(root, "outputs/" + project["part_id"]),
                                             mode=data.get("mode", "draft"), module_root=root)
                elif route in {"/api/import_workbook", "/api/import_operations"}:
                    from . import exporter
                    content = base64.b64decode(data["base64"], validate=True)
                    if route == "/api/import_workbook":
                        result = exporter.import_station_workbook(content)
                    else:
                        result = exporter.import_operation_workbook(content, filename=data.get("filename", "import.xlsx"))
                        result["operations"] = annotation_project({"operations": result["operations"]})["operations"]
                else:
                    self.json_response({"error": "未知接口。"}, 404)
                    return
                self.json_response(result)
            except (ValueError, KeyError, TypeError, OSError) as error:
                self.json_response({"error": str(error)}, 400)
            except Exception as error:
                traceback.print_exc()
                self.json_response({"error": f"操作未完成：{type(error).__name__}: {error}"}, 500)

    return Handler


def run(root, port=None, config_path="configs/app.json"):
    config = json.loads(contained(root, config_path).read_text(encoding="utf-8"))
    drawing = load_drawing(contained(root, config["source_file"]), config)
    store = ProjectStore(root, drawing, config["part_id"])
    server = ThreadingHTTPServer(("127.0.0.1", port if port is not None else config["port"]), make_handler(root, drawing, store))
    print(f"{config['part_id']} 标注工具：http://127.0.0.1:{server.server_address[1]}", flush=True)
    print(f"几何片段 {len(drawing['segments'])}；保存目录 {store.directory}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
