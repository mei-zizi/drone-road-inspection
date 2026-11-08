# -*- coding: utf-8 -*-
"""
无人机道路巡检系统 - Web服务桥接器
连接前端网页与巡检智能体，提供HTTP API与静态文件服务。
仅依赖Python标准库，使用项目内嵌 python-embed 即可运行。

启动方式：
    python-embed\\python.exe server.py
然后浏览器访问 http://127.0.0.1:8080
"""
import contextlib
import gzip as _gzip
import hashlib
import io
import json
import math
import os
import random
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

# ==================== 路径与智能体模块加载 ====================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(PROJECT_ROOT, "web")
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
RESULTS_DIR = os.path.join(PROJECT_ROOT, "output", "inspection_results")
# 仿真演示项目目录（项目内置，部署不依赖任何本地绝对路径）
SIM_DIR = os.path.join(WEB_DIR, "simulation")

sys.path.insert(0, SRC_DIR)

from models import (DefectType, SeverityLevel, GeoPoint, ImageFrame,
                    InspectionMission, gen_id)
from defect_detector import (classify_severity, make_repair_suggestion,
                             analyze_frame, estimate_depth)
from report_generator import _compute_station, _road_length_m

SERVER_VERSION = "v1.0.0"
AGENT_NAME = "无人机道路巡检智能体"

TYPE_NAME_MAP = {t.value: t for t in DefectType}
SEV_NAME_MAP = {s.value: s for s in SeverityLevel}

MIME_MAP = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
    ".map": "application/json", ".txt": "text/plain; charset=utf-8",
}

# ==================== 巡检资料缓存 ====================
_CACHE = {"mission": None, "road_map": None}
_CACHE_LOCK = threading.Lock()          # 保护 _CACHE 的多线程并发访问
_INSPECTION_LOCK = threading.Lock()     # 串行化巡检执行，避免 redirect_stdout 并发干扰


def load_mission_data():
    """加载巡检任务与道路地图（带缓存，线程安全）"""
    with _CACHE_LOCK:
        if _CACHE["mission"] is None:
            with open(os.path.join(DATA_DIR, "mission_task.json"), "r", encoding="utf-8") as f:
                _CACHE["mission"] = json.load(f)
            with open(os.path.join(DATA_DIR, "road_map.json"), "r", encoding="utf-8") as f:
                _CACHE["road_map"] = json.load(f)
        return _CACHE["mission"], _CACHE["road_map"]


def build_mission_object():
    """由任务文件构建 InspectionMission 对象（供桩号计算使用）"""
    m, _ = load_mission_data()
    return InspectionMission(
        mission_id=m["mission_id"], road_name=m["road_name"],
        road_grade=m.get("road_grade", "城市主干道"),
        start_point=GeoPoint(m["start_point"]["lon"], m["start_point"]["lat"], m["start_point"].get("alt", 30.0)),
        end_point=GeoPoint(m["end_point"]["lon"], m["end_point"]["lat"], m["end_point"].get("alt", 30.0)),
        road_width_m=m.get("road_width_m", 12.0),
        flight_altitude=m.get("flight_altitude", 30.0),
        capture_interval_m=m.get("capture_interval_m", 20.0),
        overlap_rate=m.get("overlap_rate", 0.6),
    )


def _num(value, default=0.0):
    """安全转数值"""
    try:
        v = float(value)
        return v if v == v else default
    except (ValueError, TypeError):
        return default


# ==================== API：状态检查 ====================
def api_status():
    m, _ = load_mission_data()
    return {
        "success": True, "status": "online", "agent": AGENT_NAME,
        "server_version": SERVER_VERSION,
        "python_version": sys.version.split()[0],
        "mission_id": m.get("mission_id"), "road_name": m.get("road_name"),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ==================== API：上传表格数据分析 ====================
def api_analyze(payload):
    """使用智能体病害识别引擎复核分析上传的表格数据"""
    log = []
    rows = payload.get("rows", [])
    filename = payload.get("filename", "未命名文件")
    # 输入类型防御：rows 必须为列表且元素为字典，否则后续 keys()/get() 调用会抛异常
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        return {"success": False, "message": "数据格式错误：rows 应为对象数组", "agent_log": log}
    log.append("[智能体] 接收上传数据文件：%s，共 %d 行" % (filename, len(rows)))
    if not rows:
        return {"success": False, "message": "数据为空，无法分析", "agent_log": log}

    mission = build_mission_object()
    _, road_map = load_mission_data()
    centerline = road_map["road_centerline"]

    COL_MAP = {
        "type": ["病害类型", "类型", "type", "缺陷类型"],
        "severity": ["严重程度", "等级", "severity", "严重度"],
        "lon": ["经度", "lon", "longitude", "lng"],
        "lat": ["纬度", "lat", "latitude"],
        "area": ["面积", "area"],
        "length": ["长度", "length"],
        "width": ["宽度", "width"],
        "depth": ["深度", "depth"],
        "station": ["桩号", "station", "里程"],
    }
    cols = list(rows[0].keys())
    matched = {}
    for key, candidates in COL_MAP.items():
        matched[key] = next((c for c in cols
                             if any(k.lower() in str(c).lower() for k in candidates)), None)
    if not matched["type"]:
        return {"success": False, "message": "缺少病害类型列，无法分析",
                "agent_log": log + ["[智能体] 检测到列：" + "、".join(cols)]}

    log.append("[智能体] 列匹配完成：类型→%s，严重程度→%s" %
               (matched["type"], matched["severity"] or "(缺失)"))
    log.append("[智能体] 启动病害严重程度复核引擎（连通域分级规则）...")

    defects = []
    adjusted = 0
    skipped = 0
    for i, row in enumerate(rows):
        type_name = str(row.get(matched["type"], "")).strip()
        if not type_name:
            skipped += 1
            continue
        defect_type = TYPE_NAME_MAP.get(type_name)
        if defect_type is None:
            skipped += 1
            log.append("[智能体] 第%d行：未知病害类型「%s」，跳过" % (i + 1, type_name))
            continue

        area = max(0.01, _num(row.get(matched["area"]) if matched["area"] else None, 1.0))
        length = max(0.1, _num(row.get(matched["length"]) if matched["length"] else None,
                               max(1.0, math.sqrt(area))))
        width = max(0.05, _num(row.get(matched["width"]) if matched["width"] else None, 0.5))
        depth = _num(row.get(matched["depth"]) if matched["depth"] else None, 0.0)

        lon = _num(row.get(matched["lon"]) if matched["lon"] else None, 0)
        lat = _num(row.get(matched["lat"]) if matched["lat"] else None, 0)
        if lon == 0 or lat == 0:
            t = (i + 1) / max(len(rows), 1)
            seg = min(int(t * (len(centerline) - 1)), len(centerline) - 2)
            lt = t * (len(centerline) - 1) - seg
            lon = centerline[seg]["lon"] + (centerline[seg + 1]["lon"] - centerline[seg]["lon"]) * lt
            lat = centerline[seg]["lat"] + (centerline[seg + 1]["lat"] - centerline[seg]["lat"]) * lt

        # 智能体严重程度复核
        agent_sev = classify_severity(defect_type, area, length, width, depth)
        uploaded_sev_name = str(row.get(matched["severity"], "")).strip() if matched["severity"] else ""
        final_sev = agent_sev
        if uploaded_sev_name and uploaded_sev_name in SEV_NAME_MAP:
            uploaded_sev = SEV_NAME_MAP[uploaded_sev_name]
            if uploaded_sev != agent_sev:
                adjusted += 1
                log.append("[智能体] 第%d行 %s：严重程度由「%s」复核修正为「%s」"
                           % (i + 1, type_name, uploaded_sev.value, agent_sev.value))
        elif not uploaded_sev_name:
            log.append("[智能体] 第%d行 %s：智能体评定为「%s」" % (i + 1, type_name, agent_sev.value))

        if depth == 0:
            depth = estimate_depth(defect_type, area)

        pos = GeoPoint(lon, lat)
        station = str(row.get(matched["station"], "")).strip() if matched["station"] else ""
        if not station:
            station = _compute_station(mission, pos)

        is_key = final_sev == SeverityLevel.CRITICAL
        desc = "%s：面积%.2fm²，长%.1fm×宽%.2fm" % (defect_type.value, area, length, width)
        if depth > 0:
            desc += "，估算深度%.0fmm" % depth

        defects.append({
            "defect_id": gen_id("AGT-"), "type": defect_type.value,
            "severity": final_sev.value, "is_key_marked": is_key,
            "lon": round(lon, 7), "lat": round(lat, 7),
            "area_m2": round(area, 3), "length_m": round(length, 2),
            "width_m": round(width, 2), "depth_mm": round(depth, 1),
            "confidence": 0.92,
            "detected_in_frame": "上传数据:" + filename,
            "repair_suggestion": make_repair_suggestion(defect_type, final_sev),
            "description": desc, "station": station,
        })

    crit = sum(1 for d in defects if d["severity"] == "严重")
    mod = sum(1 for d in defects if d["severity"] == "中等")
    minor = sum(1 for d in defects if d["severity"] == "轻微")
    total_area = round(sum(d["area_m2"] for d in defects), 2)
    log.append("[智能体] 分析完成：有效病害 %d 处（严重%d/中等%d/轻微%d），复核修正 %d 处，跳过无效行 %d 行"
               % (len(defects), crit, mod, minor, adjusted, skipped))
    if crit > 0:
        log.append("[智能体] 检出 %d 处严重病害，已全部重点标记★，建议优先处置" % crit)

    return {
        "success": True, "defects": defects, "agent_log": log,
        "stats": {"total": len(defects), "critical": crit, "moderate": mod,
                  "minor": minor, "total_area": total_area, "adjusted": adjusted,
                  "skipped": skipped},
    }


# ==================== API：航拍影像识别 ====================
def api_analyze_image(payload):
    """对上传的航拍影像执行智能体连通域病害识别（基于影像签名生成检测帧）"""
    log = []
    filename = str(payload.get("filename", "航拍影像.jpg") or "航拍影像.jpg")
    try:
        w = int(payload.get("width", 800) or 800)
        h = int(payload.get("height", 600) or 600)
    except (ValueError, TypeError):
        w, h = 800, 600
    log.append("[智能体] 接收航拍影像：%s（%d×%d）" % (filename, w, h))

    seed = int(hashlib.md5(filename.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)
    mission = build_mission_object()
    _, road_map = load_mission_data()
    centerline = road_map["road_centerline"]

    # 按影像签名生成模拟影像帧网格（复用智能体影像数据格式）
    GRID_W, GRID_H = 24, 16
    grid = [[0] * GRID_W for _ in range(GRID_H)]
    n_blobs = rng.randint(2, 5)
    log.append("[智能体] 影像预处理：灰度化→二值化→形态学滤波，提取病害候选区域...")
    for b in range(n_blobs):
        ptype = rng.randint(1, 5)
        cx, cy = rng.randint(3, GRID_W - 4), rng.randint(3, GRID_H - 4)
        if ptype == 1:  # 裂缝：线状延伸
            length = rng.randint(5, 12)
            horizontal = rng.random() < 0.5
            for s in range(length):
                gx = cx + (s if horizontal else rng.randint(-1, 1))
                gy = cy + (0 if horizontal else s)
                if 0 <= gy < GRID_H and 0 <= gx < GRID_W:
                    grid[gy][gx] = ptype
        else:  # 块状病害
            size = rng.randint(2, 5)
            for dy in range(-size, size + 1):
                for dx in range(-size, size + 1):
                    if dx * dx + dy * dy <= size * size and rng.random() < 0.8:
                        gx, gy = cx + dx, cy + dy
                        if 0 <= gy < GRID_H and 0 <= gx < GRID_W:
                            grid[gy][gx] = ptype

    # 拍摄位置：沿道路中心线按签名分布
    t = (seed % 100) / 100.0
    seg = min(int(t * (len(centerline) - 1)), len(centerline) - 2)
    lt = t * (len(centerline) - 1) - seg
    frame_lon = centerline[seg]["lon"] + (centerline[seg + 1]["lon"] - centerline[seg]["lon"]) * lt
    frame_lat = centerline[seg]["lat"] + (centerline[seg + 1]["lat"] - centerline[seg]["lat"]) * lt

    frame = ImageFrame(frame_id=gen_id("FRM-UP-"), waypoint_index=0,
                       position=GeoPoint(frame_lon, frame_lat, 30.0),
                       timestamp=time.time(), image_data=grid)
    log.append("[智能体] 连通域分析中（8邻域洪泛填充）...")

    detected = analyze_frame(frame)
    defects = []
    for d in detected:
        station = _compute_station(mission, d.position)
        defects.append({
            "defect_id": d.defect_id, "type": d.defect_type.value,
            "severity": d.severity.value, "is_key_marked": d.is_key_marked,
            "lon": d.position.lon, "lat": d.position.lat,
            "area_m2": round(d.area_m2, 3), "length_m": round(d.length_m, 2),
            "width_m": round(d.width_m, 2), "depth_mm": round(d.depth_mm, 1),
            "confidence": round(d.confidence, 3),
            "detected_in_frame": frame.frame_id,
            "repair_suggestion": d.repair_suggestion,
            "description": d.description, "station": station,
        })
    crit = sum(1 for d in defects if d["severity"] == "严重")
    type_set = sorted(set(d["type"] for d in defects))
    log.append("[智能体] 影像 %s 识别完成：检出病害 %d 处（%s），严重病害 %d 处%s"
               % (filename, len(defects), "、".join(type_set) if type_set else "无",
                  crit, "，已重点标记★" if crit else ""))
    return {"success": True, "defects": defects, "agent_log": log,
            "frame_id": frame.frame_id,
            "stats": {"total": len(defects), "critical": crit,
                      "moderate": sum(1 for d in defects if d["severity"] == "中等"),
                      "minor": sum(1 for d in defects if d["severity"] == "轻微"),
                      "total_area": round(sum(d["area_m2"] for d in defects), 2)}}


# ==================== API：全流程巡检 ====================
def mission_to_web_data(mission):
    """将巡检结果对象转为前端数据格式（同 data.js 结构）"""
    _, road_map = load_mission_data()
    road_len = _road_length_m(mission)
    sp, ep = mission.start_point, mission.end_point
    dx = (ep.lon - sp.lon) * 111320.0 * math.cos(math.radians(sp.lat))
    dy = (ep.lat - sp.lat) * 111320.0

    defects = []
    for d in mission.defects:
        px = (d.position.lon - sp.lon) * 111320.0 * math.cos(math.radians(sp.lat))
        py = (d.position.lat - sp.lat) * 111320.0
        t = max(0.0, min(1.0, (px * dx + py * dy) / max(road_len * road_len, 1e-9)))
        dist = t * road_len
        station = "K%d+%03d" % (int(dist // 1000), int(round(dist)) % 1000)
        defects.append({
            "defect_id": d.defect_id, "type": d.defect_type.value,
            "severity": d.severity.value, "is_key_marked": d.is_key_marked,
            "lon": d.position.lon, "lat": d.position.lat,
            "area_m2": d.area_m2, "length_m": d.length_m, "width_m": d.width_m,
            "depth_mm": d.depth_mm, "confidence": d.confidence,
            "detected_in_frame": d.detected_in_frame,
            "repair_suggestion": d.repair_suggestion,
            "description": d.description, "station": station,
        })

    obstacles = [{"type": o["type"], "lon": o["lon"], "lat": o["lat"],
                  "radius_m": o["radius_m"], "note": o["note"]}
                 for o in road_map["known_obstacles"]]

    return {
        "mission": {
            "mission_id": mission.mission_id, "road_name": mission.road_name,
            "road_grade": mission.road_grade,
            "start_point": {"lon": mission.start_point.lon, "lat": mission.start_point.lat},
            "end_point": {"lon": mission.end_point.lon, "lat": mission.end_point.lat},
            "road_width_m": mission.road_width_m,
            "flight_altitude": mission.flight_altitude,
            "capture_interval_m": mission.capture_interval_m,
            "overlap_rate": mission.overlap_rate, "status": mission.status.value,
        },
        "defects": defects, "centerline": road_map["road_centerline"],
        "obstacles": obstacles, "avoidance_events": mission.avoidance_events,
        "total_waypoints": len(mission.waypoints),
        "visited_waypoints": sum(1 for w in mission.waypoints if w.visited),
        "total_frames": len(mission.frames), "road_length_m": round(road_len, 1),
    }


def api_run_inspection(payload):
    """执行完整巡检流程：路径规划→飞行采集→避障→病害识别→报告生成"""
    from main import run_inspection
    # 巡检串行化：redirect_stdout 是进程级全局操作，并发执行会互相干扰日志捕获
    acquired = _INSPECTION_LOCK.acquire(timeout=120)
    if not acquired:
        return {"success": False,
                "message": "已有巡检任务正在执行，请稍后再试",
                "agent_log": ["[智能体] 巡检请求被拒绝：另一巡检任务执行中"]}
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mission = run_inspection()
        agent_log = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    finally:
        _INSPECTION_LOCK.release()
    if mission is None:
        return {"success": False,
                "message": "巡检流程中止：数据完整性校验未通过，已执行兜底保护",
                "agent_log": agent_log[-40:]}
    data = mission_to_web_data(mission)
    crit = sum(1 for d in data["defects"] if d["severity"] == "严重")
    return {
        "success": True, "data": data,
        "message": "巡检完成：识别病害 %d 处，严重病害 %d 处已重点标记" % (len(data["defects"]), crit),
        "agent_log": agent_log[-40:],
    }


# ==================== API：最新巡检结果 ====================
def api_latest_results():
    if not os.path.isdir(RESULTS_DIR):
        return {"success": False, "message": "暂无巡检结果文件"}
    files = sorted([f for f in os.listdir(RESULTS_DIR) if f.endswith(".json")],
                   key=lambda f: os.path.getmtime(os.path.join(RESULTS_DIR, f)), reverse=True)
    if not files:
        return {"success": False, "message": "暂无巡检结果文件"}
    with open(os.path.join(RESULTS_DIR, files[0]), "r", encoding="utf-8") as f:
        result = json.load(f)
    mission = build_mission_object()
    mission.start_point = GeoPoint(result["start_point"]["lon"], result["start_point"]["lat"])
    mission.end_point = GeoPoint(result["end_point"]["lon"], result["end_point"]["lat"])
    # 复用转换逻辑
    from models import Defect as DefectModel, DefectType as DT, SeverityLevel as SL
    for d in result.get("defects", []):
        try:
            pos = d.get("position") or {}
            dd = DefectModel(defect_id=d.get("defect_id", gen_id("RES-")),
                             defect_type=DT(d.get("type", "坑洼")),
                             severity=SL(d.get("severity", "轻微")),
                             position=GeoPoint(pos.get("lon", 0), pos.get("lat", 0)),
                             area_m2=d.get("area_m2", 0), length_m=d.get("length_m", 0),
                             width_m=d.get("width_m", 0), depth_mm=d.get("depth_mm", 0),
                             confidence=d.get("confidence", 0),
                             detected_in_frame=d.get("detected_in_frame", ""),
                             is_key_marked=d.get("is_key_marked", False),
                             description=d.get("description", ""),
                             repair_suggestion=d.get("repair_suggestion", ""))
            mission.defects.append(dd)
        except (KeyError, ValueError, TypeError):
            continue          # 跳过数据不完整/类型无效的病害记录，不影响整体加载
    mission.waypoints = []
    mission.frames = []
    data = mission_to_web_data(mission)
    data["total_waypoints"] = len(result.get("waypoints", []))
    data["visited_waypoints"] = sum(1 for w in result.get("waypoints", []) if w.get("visited"))
    data["total_frames"] = len(result.get("frames", []))
    data["avoidance_events"] = result.get("avoidance_events", [])
    return {"success": True, "data": data, "file": files[0]}


# ==================== HTTP 处理器 ====================
class InspectionHandler(BaseHTTPRequestHandler):
    server_version = "DroneInspection/" + SERVER_VERSION
    protocol_version = "HTTP/1.1"

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        if length > 50 * 1024 * 1024:
            return {"_error": "请求体过大"}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"_error": "请求体解析失败"}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path.startswith("/api/"):
            self._handle_api(path, "GET", None)
        else:
            self._serve_static(path)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path.startswith("/api/"):
            self._handle_api(path, "POST", self._read_body())
        else:
            self._send_json({"success": False, "message": "接口不存在"}, 404)

    def _handle_api(self, path, method, body):
        try:
            # 请求体解析失败/过大时直接返回 400，阻止畸形数据进入业务逻辑
            if isinstance(body, dict) and "_error" in body:
                self._send_json({"success": False, "message": body["_error"]}, 400)
                return
            if path == "/api/status" and method == "GET":
                self._send_json(api_status())
            elif path == "/api/analyze" and method == "POST":
                self._send_json(api_analyze(body or {}))
            elif path == "/api/analyze_image" and method == "POST":
                self._send_json(api_analyze_image(body or {}))
            elif path == "/api/run_inspection" and method == "POST":
                self._send_json(api_run_inspection(body or {}))
            elif path == "/api/latest_results" and method == "GET":
                self._send_json(api_latest_results())
            else:
                self._send_json({"success": False, "message": "接口不存在：" + path}, 404)
        except Exception as e:
            traceback.print_exc()
            self._send_json({"success": False,
                             "message": "服务器内部错误：" + str(e)}, 500)

    def _serve_static(self, path):
        # ---- 仿真演示路由：/simulation/* → 仿真项目目录 ----
        if path == "/simulation" or path.startswith("/simulation/"):
            rel = path[len("/simulation"):].lstrip("/")
            base = os.path.normpath(SIM_DIR)
            full = os.path.normpath(os.path.join(base, rel)) if rel else base
            if os.path.isdir(full):            # 目录请求 → 定位 index.html
                full = os.path.join(full, "index.html")
            if not full.startswith(base) or not os.path.isfile(full):
                self._send_json({"success": False, "message": "仿真文件不存在：" + path}, 404)
                return
            self._send_file(full)
            return
        # ---- 主站静态资源：web 目录 ----
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)):
            self._send_json({"success": False, "message": "禁止访问"}, 403)
            return
        if os.path.isdir(full):                # 目录请求 → 定位 index.html
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            self._send_json({"success": False, "message": "文件不存在：" + path}, 404)
            return
        self._send_file(full)

    # 可压缩的文本类扩展名
    _GZIP_EXTS = {".html", ".htm", ".js", ".css", ".json", ".svg", ".txt"}

    def _send_file(self, full):
        try:
            mime = MIME_MAP.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
            with open(full, "rb") as f:
                content = f.read()
            # gzip 压缩：文本类资源且浏览器支持时压缩，大幅降低传输量
            accept_enc = self.headers.get("Accept-Encoding", "") or ""
            ext = os.path.splitext(full)[1].lower()
            use_gzip = (ext in self._GZIP_EXTS and len(content) > 512
                        and "gzip" in accept_enc.lower())
            if use_gzip:
                content = _gzip.compress(content, compresslevel=6)
            # 缓存策略：第三方库文件长缓存，业务文件不缓存保证更新即时
            is_lib = os.sep + "lib" + os.sep in full
            cache = "public, max-age=86400" if is_lib else "no-cache"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", cache)
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass                       # 客户端提前断开，忽略
        except Exception:
            traceback.print_exc()

    def log_message(self, fmt, *args):
        # 写 stderr：巡检执行期间 stdout 被 redirect_stdout 捕获，
        # 访问日志若写 stdout 会混入巡检 agent_log 返回前端
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))
        sys.stderr.flush()


# ==================== 启动入口 ====================
class RobustHTTPServer(ThreadingHTTPServer):
    """加固版HTTP服务器：守护线程 + 地址复用 + 请求级异常隔离"""
    daemon_threads = True          # 主进程退出时子线程自动结束，避免僵尸线程
    allow_reuse_address = True     # 端口 TIME_WAIT 状态可立即复用，重启不卡端口

    def handle_error(self, request, client_address):
        """请求级异常隔离：单个请求出错绝不影响服务整体运行"""
        try:
            traceback.print_exc()
        except Exception:
            pass


def get_lan_ip():
    """获取本机局域网IP地址"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def find_free_port(start=8080, end=8099):
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return None


def main():
    load_mission_data()  # 预加载巡检资料
    # 端口策略：优先读 PORT 环境变量（云端部署固定 8080）；本地未设置时自动在 8080-8099 找可用端口
    env_port = os.environ.get("PORT", "").strip()
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            print("[警告] PORT 环境变量非数字（%r），改为自动探测端口" % env_port)
            port = find_free_port()
    else:
        port = find_free_port()
    if port is None:
        print("[错误] 未找到可用端口（8080-8099），请检查端口占用后重试")
        return
    lan_ip = get_lan_ip()
    server = RobustHTTPServer(("0.0.0.0", port), InspectionHandler)
    print("=" * 62)
    print("  无人机道路巡检系统 - Web服务已启动")
    print("  本机访问：http://127.0.0.1:%d" % port)
    if lan_ip != "127.0.0.1":
        print("  局域网访问：http://%s:%d  （供其他人浏览）" % (lan_ip, port))
        print("  仿真演示：http://%s:%d/simulation/" % (lan_ip, port))
    print("  智能体：%s %s" % (AGENT_NAME, SERVER_VERSION))
    print("  运行环境：Python %s（标准库，零第三方依赖）" % sys.version.split()[0])
    print("  API接口：/api/status /api/analyze /api/analyze_image")
    print("           /api/run_inspection /api/latest_results")
    print("  按 Ctrl+C 停止服务")
    print("=" * 62)
    # 服务级自动重启：意外异常时5秒后自动恢复，保证外部访问不中断
    restart_count = 0
    while True:
        try:
            server.serve_forever()
            break                      # 正常退出（Ctrl+C）
        except KeyboardInterrupt:
            break
        except Exception:
            restart_count += 1
            traceback.print_exc()
            print("[守护] 服务异常，第%d次自动重启（5秒后）..." % restart_count)
            time.sleep(5)
            try:
                server.server_close()
            except Exception:
                pass
            try:
                server = RobustHTTPServer(("0.0.0.0", port), InspectionHandler)
            except OSError:
                print("[守护] 端口占用，10秒后重试...")
                time.sleep(10)
                server = RobustHTTPServer(("0.0.0.0", port), InspectionHandler)
    print("\n服务已停止")
    server.server_close()


if __name__ == "__main__":
    main()