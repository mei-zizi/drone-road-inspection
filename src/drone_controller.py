# -*- coding: utf-8 -*-
"""
无人机控制与影像采集模块
控制无人机沿规划航点飞行，在每个拍摄航点采集路面影像，
途中集成避障系统执行基础避障，记录飞行遥测与避障事件。
"""
import json
import math
import os
import time
from typing import List

from models import (InspectionMission, Waypoint, GeoPoint, ImageFrame,
                    Obstacle, MissionStatus, gen_id)
from obstacle_avoidance import ObstacleAvoidanceSystem

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
AERIAL_RAW_DIR = os.path.join(DATA_DIR, "aerial_raw")

# 无人机飞行速度（米/秒）
FLIGHT_SPEED = 5.0


def load_obstacles_from_map(road_map_path: str) -> List[Obstacle]:
    """从道路地图读取已知障碍物列表"""
    with open(road_map_path, "r", encoding="utf-8") as f:
        road_map = json.load(f)
    obstacles = []
    for o in road_map.get("known_obstacles", []):
        obstacles.append(Obstacle(
            position=GeoPoint(lon=o["lon"], lat=o["lat"], alt=o.get("height_m", 20.0)),
            radius=o.get("radius_m", 5.0),
            obstacle_type=o.get("type", "unknown"),
        ))
    return obstacles


def _find_raw_frame_for_waypoint(wp: Waypoint, frame_index: dict) -> dict:
    """根据航点位置匹配最接近的航拍原始影像帧"""
    best = None
    best_dist = float("inf")
    for fm in frame_index.get("frames", []):
        fp = fm["position"]
        dlat = (fp["lat"] - wp.position.lat) * 111320.0
        dlon = (fp["lon"] - wp.position.lon) * 111320.0 * math.cos(math.radians(wp.position.lat))
        d = math.sqrt(dlat ** 2 + dlon ** 2)
        if d < best_dist:
            best_dist = d
            best = fm
    return best if best_dist < 25.0 else None


class DroneController:
    """无人机控制器：飞行执行、影像采集、避障集成"""

    def __init__(self, mission: InspectionMission, obstacles: List[Obstacle]):
        self.mission = mission
        self.avoidance_sys = ObstacleAvoidanceSystem(obstacles)
        self.mission.obstacles = obstacles
        self.telemetry: List[dict] = []
        self._frame_index = None
        # 采集失败记录（兜底：追踪残缺数据来源）
        self.capture_failures: List[dict] = []

    def _load_frame_index(self):
        """加载航拍原始影像索引"""
        idx_path = os.path.join(AERIAL_RAW_DIR, "_index.json")
        if os.path.exists(idx_path):
            with open(idx_path, "r", encoding="utf-8") as f:
                self._frame_index = json.load(f)
        else:
            self._frame_index = {"frames": []}

    def execute_flight(self) -> InspectionMission:
        """
        执行巡检飞行：
        依次飞往每个航点，避障系统保障航段安全，
        在拍摄航点采集路面影像（读取航拍原始数据）。
        """
        self.mission.status = MissionStatus.FLYING
        self._load_frame_index()

        waypoints = self.mission.waypoints
        if not waypoints:
            print("[无人机控制] 错误：无航点，请先规划路径")
            return self.mission

        print(f"[无人机控制] 起飞，开始执行巡检飞行，共 {len(waypoints)} 个航点")
        current_pos = waypoints[0].position
        total_dist = 0.0
        detour_count = 0
        hover_count = 0
        capture_count = 0

        for wp in waypoints:
            # 避障导航决策
            actual_target, event = self.avoidance_sys.navigate(current_pos, wp.position)

            if event["type"] == "detour":
                detour_count += 1
                wp.obstacle_detected = True
                print(f"[无人机控制] 航点{wp.index}: {event['message']}")
            elif event["type"] == "hover":
                hover_count += 1
                wp.obstacle_detected = True
                print(f"[无人机控制] 航点{wp.index}: {event['message']}")
                # 悬停后跳过该航点拍摄，继续下一航点
                self.telemetry.append({
                    "waypoint": wp.index, "event": event["type"],
                    "position": current_pos.to_dict(),
                })
                continue

            # 飞行至目标
            seg_dist = current_pos.distance_to(actual_target)
            total_dist += seg_dist
            flight_time = seg_dist / FLIGHT_SPEED

            # 到达航点（绕行时先到绕行点再到目标）
            if event["type"] == "detour":
                # 先飞到绕行点
                total_dist += actual_target.distance_to(wp.position)
                current_pos = wp.position
            else:
                current_pos = actual_target

            wp.visited = True
            self.mission.avoidance_events = self.avoidance_sys.events

            # 在拍摄航点采集影像
            if wp.action == "capture":
                frame = self._capture_image(wp)
                if frame:
                    self.mission.frames.append(frame)
                    capture_count += 1

            self.telemetry.append({
                "waypoint": wp.index, "event": event["type"],
                "position": current_pos.to_dict(),
                "seg_dist_m": round(seg_dist, 2),
                "flight_time_s": round(flight_time, 2),
            })

        self.mission.status = MissionStatus.ANALYZING
        print(f"[无人机控制] 巡检飞行完成："
              f"飞行总航程 {total_dist:.1f}m，"
              f"采集影像 {capture_count} 帧，"
              f"避障绕行 {detour_count} 次，"
              f"悬停告警 {hover_count} 次")
        if self.capture_failures:
            print(f"[无人机控制] ⚠ 采集失败 {len(self.capture_failures)} 帧，"
                  f"已记录失败原因，待数据完整性校验判定")
        return self.mission

    def _capture_image(self, wp: Waypoint) -> ImageFrame:
        """在航点采集路面影像（读取项目内航拍原始数据），失败时记录原因"""
        raw = _find_raw_frame_for_waypoint(wp, self._frame_index)
        if raw is None:
            # 无匹配原始帧：记录采集失败
            failure = {
                "waypoint_index": wp.index,
                "position": wp.position.to_dict(),
                "reason": "无匹配航拍原始影像帧",
            }
            self.capture_failures.append(failure)
            print(f"[无人机控制] 航点{wp.index}: 采集失败 - {failure['reason']}")
            frame = ImageFrame(
                frame_id=gen_id("FRM-"),
                waypoint_index=wp.index,
                position=wp.position,
                timestamp=time.time(),
                file_path="",
            )
            return frame

        # 读取对应的原始影像文件
        raw_path = os.path.join(AERIAL_RAW_DIR, raw["file"])
        image_data = None
        if os.path.exists(raw_path):
            try:
                with open(raw_path, "r", encoding="utf-8") as f:
                    raw_frame = json.load(f)
                image_data = raw_frame.get("image_data")
            except Exception as e:
                # 文件损坏无法解析：记录采集失败
                failure = {
                    "waypoint_index": wp.index,
                    "frame_id": raw["frame_id"],
                    "file": raw["file"],
                    "reason": f"影像文件损坏无法读取: {e}",
                }
                self.capture_failures.append(failure)
                print(f"[无人机控制] 航点{wp.index}: 采集失败 - {failure['reason']}")
        else:
            # 文件缺失：记录采集失败
            failure = {
                "waypoint_index": wp.index,
                "frame_id": raw["frame_id"],
                "file": raw["file"],
                "reason": "影像文件缺失",
            }
            self.capture_failures.append(failure)
            print(f"[无人机控制] 航点{wp.index}: 采集失败 - {failure['reason']}")

        frame = ImageFrame(
            frame_id=raw["frame_id"],
            waypoint_index=wp.index,
            position=GeoPoint(lon=raw["position"]["lon"], lat=raw["position"]["lat"],
                              alt=raw["position"]["alt"]),
            timestamp=raw["timestamp"],
            image_data=image_data,
            file_path=raw["file"],
        )
        return frame