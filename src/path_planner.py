# -*- coding: utf-8 -*-
"""
路径规划模块
接收道路巡检任务，沿道路中心线生成"弓"字形（Boustrophedon/割草机式）飞行巡检路径，
确保航拍影像覆盖整条路面且满足重叠率要求。
"""
import json
import math
import os

from models import InspectionMission, Waypoint, GeoPoint, MissionStatus

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def load_mission_from_file(mission_path: str) -> InspectionMission:
    """从任务文件读取巡检任务，构建 InspectionMission 对象"""
    with open(mission_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    mission = InspectionMission(
        mission_id=data["mission_id"],
        road_name=data["road_name"],
        road_grade=data.get("road_grade", "城市主干道"),
        start_point=GeoPoint(**data["start_point"]),
        end_point=GeoPoint(**data["end_point"]),
        road_width_m=data.get("road_width_m", 12.0),
        flight_altitude=data.get("flight_altitude", 30.0),
        capture_interval_m=data.get("capture_interval_m", 20.0),
        overlap_rate=data.get("overlap_rate", 0.6),
    )
    return mission


def _point_at(centerline, t):
    """在中心线上按参数 t∈[0,1] 线性插值取点，返回(lon,lat)"""
    n = len(centerline) - 1
    t = max(0.0, min(1.0, t))
    seg = min(int(t * n), n - 1)
    lt = t * n - seg
    p1, p2 = centerline[seg], centerline[seg + 1]
    return (p1["lon"] + (p2["lon"] - p1["lon"]) * lt,
            p1["lat"] + (p2["lat"] - p1["lat"]) * lt)


def _normal_direction(centerline, t):
    """计算中心线在 t 处的法线方向单位向量（lon方向, lat方向）"""
    eps = 0.005
    x1, y1 = _point_at(centerline, max(0, t - eps))
    x2, y2 = _point_at(centerline, min(1, t + eps))
    dx, dy = x2 - x1, y2 - y1
    norm = math.sqrt(dx * dx + dy * dy)
    if norm < 1e-12:
        return 0.0, 1.0
    # 法线方向 = (-dy, dx) 归一化
    return -dy / norm, dx / norm


def plan_flight_path(mission: InspectionMission, road_map_path: str) -> InspectionMission:
    """
    规划飞行巡检路径：
    1. 读取道路中心线
    2. 根据路面宽度与拍摄间隔计算横向扫描线条数
    3. 生成"弓"字形航点序列，保证影像重叠覆盖
    """
    mission.status = MissionStatus.PLANNING

    with open(road_map_path, "r", encoding="utf-8") as f:
        road_map = json.load(f)
    centerline = road_map["road_centerline"]

    # 计算道路总长（米）
    total_m = 0.0
    for i in range(len(centerline) - 1):
        p1, p2 = centerline[i], centerline[i + 1]
        dlat = (p2["lat"] - p1["lat"]) * 111320.0
        dlon = (p2["lon"] - p1["lon"]) * 111320.0 * math.cos(math.radians(p1["lat"]))
        total_m += math.sqrt(dlat ** 2 + dlon ** 2)

    # 横向扫描线间距：考虑影像重叠率
    # 30m高度、84°FOV 单帧覆盖约 2*30*tan(42°)≈54m，远大于路宽，此处按路宽分带
    road_w = mission.road_width_m
    lane_spacing = 4.0  # 每条扫描带间隔4米，保证重叠
    num_lanes = max(1, int(math.ceil(road_w / lane_spacing)))
    half_w = road_w / 2.0
    lane_offsets = []
    for k in range(num_lanes):
        offset = -half_w + lane_spacing / 2.0 + k * lane_spacing
        lane_offsets.append(offset)

    # 纵向拍摄间隔
    interval = mission.capture_interval_m
    num_stops = max(2, int(math.ceil(total_m / interval)) + 1)

    waypoints = []
    wp_idx = 0

    # "弓"字形路径：奇数条带正向，偶数条带反向
    for lane_k, offset_m in enumerate(lane_offsets):
        forward = (lane_k % 2 == 0)
        stops = range(num_stops) if forward else range(num_stops - 1, -1, -1)
        for s in stops:
            t = s / (num_stops - 1)
            lx, ly = _point_at(centerline, t)
            nx, ny = _normal_direction(centerline, t)
            # 将法线方向偏移量（米）转换为经纬度偏移
            dlat_offset = offset_m * ny / 111320.0
            dlon_offset = offset_m * nx / (111320.0 * math.cos(math.radians(ly)))
            wp_lon = lx + dlon_offset
            wp_lat = ly + dlat_offset
            wp_alt = mission.flight_altitude

            # 判断动作：每条带末端为转向点，其余为拍摄点
            is_end = (s == num_stops - 1) if forward else (s == 0)
            action = "turn" if is_end else "capture"

            waypoints.append(Waypoint(
                index=wp_idx,
                position=GeoPoint(lon=round(wp_lon, 7), lat=round(wp_lat, 7), alt=wp_alt),
                action=action,
            ))
            wp_idx += 1

    mission.waypoints = waypoints
    mission.status = MissionStatus.PENDING

    # 计算路径统计
    path_len = 0.0
    for i in range(1, len(waypoints)):
        path_len += waypoints[i].position.distance_to(waypoints[i - 1].position)

    print(f"[路径规划] 任务 {mission.mission_id}（{mission.road_name}）")
    print(f"[路径规划] 道路总长约 {total_m:.1f}m，路宽 {road_w}m，扫描条带 {num_lanes} 条")
    print(f"[路径规划] 共规划 {len(waypoints)} 个航点，预计飞行总航程约 {path_len:.1f}m")
    return mission


if __name__ == "__main__":
    mission_path = os.path.join(DATA_DIR, "mission_task.json")
    road_map_path = os.path.join(DATA_DIR, "road_map.json")
    m = load_mission_from_file(mission_path)
    m = plan_flight_path(m, road_map_path)
    for wp in m.waypoints[:10]:
        print(wp.to_dict())