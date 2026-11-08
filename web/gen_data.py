# -*- coding: utf-8 -*-
"""从巡检结果JSON生成前端数据文件 web/data.js"""
import json
import os
import math

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_PATH = os.path.join(PROJECT_ROOT, "output", "inspection_results",
                           "MISSION-20261106-001_巡检结果.json")
ROAD_MAP_PATH = os.path.join(PROJECT_ROOT, "data", "road_map.json")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.js")

with open(RESULT_PATH, "r", encoding="utf-8") as f:
    result = json.load(f)
with open(ROAD_MAP_PATH, "r", encoding="utf-8") as f:
    road_map = json.load(f)

mission = {
    "mission_id": result["mission_id"],
    "road_name": result["road_name"],
    "road_grade": result["road_grade"],
    "start_point": result["start_point"],
    "end_point": result["end_point"],
    "road_width_m": result["road_width_m"],
    "flight_altitude": result["flight_altitude"],
    "capture_interval_m": result["capture_interval_m"],
    "overlap_rate": result["overlap_rate"],
    "status": result["status"],
}

defects = []
for d in result["defects"]:
    defects.append({
        "defect_id": d["defect_id"],
        "type": d["type"],
        "severity": d["severity"],
        "is_key_marked": d["is_key_marked"],
        "lon": d["position"]["lon"],
        "lat": d["position"]["lat"],
        "area_m2": d["area_m2"],
        "length_m": d["length_m"],
        "width_m": d["width_m"],
        "depth_mm": d["depth_mm"],
        "confidence": d["confidence"],
        "detected_in_frame": d["detected_in_frame"],
        "repair_suggestion": d["repair_suggestion"],
        "description": d["description"],
    })

# 计算桩号
sp, ep = mission["start_point"], mission["end_point"]
dx = (ep["lon"] - sp["lon"]) * 111320.0 * math.cos(math.radians(sp["lat"]))
dy = (ep["lat"] - sp["lat"]) * 111320.0
road_len = math.sqrt(dx * dx + dy * dy)

for d in defects:
    px = (d["lon"] - sp["lon"]) * 111320.0 * math.cos(math.radians(sp["lat"]))
    py = (d["lat"] - sp["lat"]) * 111320.0
    t = max(0.0, min(1.0, (px * dx + py * dy) / (road_len * road_len)))
    dist = t * road_len
    d["station"] = "K%d+%03d" % (int(dist // 1000), int(round(dist)) % 1000)

obstacles = [{"type": o["type"], "lon": o["lon"], "lat": o["lat"],
              "radius_m": o["radius_m"], "note": o["note"]}
             for o in road_map["known_obstacles"]]

output = {
    "mission": mission,
    "defects": defects,
    "centerline": road_map["road_centerline"],
    "obstacles": obstacles,
    "avoidance_events": result.get("avoidance_events", []),
    "total_waypoints": len(result.get("waypoints", [])),
    "visited_waypoints": sum(1 for w in result.get("waypoints", []) if w.get("visited")),
    "total_frames": len(result.get("frames", [])),
    "road_length_m": round(road_len, 1),
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write("window.INSPECTION_DATA = ")
    f.write(json.dumps(output, ensure_ascii=False, indent=1))
    f.write(";\n")

print("data.js generated: %d defects, road length %.1fm" % (len(defects), road_len))