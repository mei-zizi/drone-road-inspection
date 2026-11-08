# -*- coding: utf-8 -*-
"""
基础避障模块
巡检途中检测航点附近障碍物，执行基础避障策略：
1. 障碍物探测：检查当前航点与下一航点连线是否穿越已知障碍物影响区
2. 避障策略：绕行偏移（向道路另一侧平移安全距离后继续前往目标航点）
3. 紧急悬停：若无法安全绕行则悬停告警
"""
import math
from typing import List, Optional, Tuple

from models import Obstacle, GeoPoint, Waypoint

# 安全裕度（米）：在障碍物影响半径外额外保持的安全距离
SAFETY_MARGIN = 5.0
# 绕行偏移量（米）：向法线方向平移的距离
DETOUR_OFFSET = 10.0


def point_to_segment_distance(p: GeoPoint, a: GeoPoint, b: GeoPoint) -> float:
    """计算点 p 到线段 ab 的最短距离（米）"""
    # 转为局部平面坐标（米）
    lat0 = a.lat
    px = (p.lon - a.lon) * 111320.0 * math.cos(math.radians(lat0))
    py = (p.lat - a.lat) * 111320.0
    ax, ay = 0.0, 0.0
    bx = (b.lon - a.lon) * 111320.0 * math.cos(math.radians(lat0))
    by = (b.lat - a.lat) * 111320.0

    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-9:
        return math.sqrt(px * px + py * py)

    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    closest_x = ax + t * dx
    closest_y = ay + t * dy
    return math.sqrt((px - closest_x) ** 2 + (py - closest_y) ** 2)


def check_path_segment(current: GeoPoint, target: GeoPoint,
                       obstacles: List[Obstacle]) -> Optional[Obstacle]:
    """
    检查当前点到目标航点的航段是否穿越任何障碍物影响区。
    返回第一个冲突的障碍物，无冲突返回 None。
    """
    for obs in obstacles:
        dist = point_to_segment_distance(obs.position, current, target)
        if dist < obs.radius + SAFETY_MARGIN:
            return obs
    return None


def compute_detour_waypoint(current: GeoPoint, target: GeoPoint,
                            obstacle: Obstacle,
                            all_obstacles: Optional[List[Obstacle]] = None) -> Optional[GeoPoint]:
    """
    计算绕行航点：将目标航点向远离障碍物一侧的法线方向偏移。
    返回绕行航点坐标；若几何条件不允许安全绕行则返回 None（触发悬停）。
    all_obstacles: 全部已知障碍物，用于验证绕行点不落入其他障碍物影响区。
    """
    # 航段方向向量
    lat0 = current.lat
    dx = (target.lon - current.lon) * 111320.0 * math.cos(math.radians(lat0))
    dy = (target.lat - current.lat) * 111320.0
    seg_len = math.sqrt(dx * dx + dy * dy)
    if seg_len < 1e-6:
        return None

    # 法线方向（垂直于航段）
    nx, ny = -dy / seg_len, dx / seg_len

    # 障碍物相对于当前点的方向
    ox = (obstacle.position.lon - current.lon) * 111320.0 * math.cos(math.radians(lat0))
    oy = (obstacle.position.lat - current.lat) * 111320.0

    # 选择远离障碍物一侧的法线方向
    dot = nx * ox + ny * oy
    if dot > 0:
        nx, ny = -nx, -ny

    # 偏移量 = 障碍物半径 + 安全裕度 + 额外绕行距离
    offset = obstacle.radius + SAFETY_MARGIN + DETOUR_OFFSET
    dlat = ny * offset / 111320.0
    dlon = nx * offset / (111320.0 * math.cos(math.radians(target.lat)))

    detour = GeoPoint(
        lon=round(target.lon + dlon, 7),
        lat=round(target.lat + dlat, 7),
        alt=target.alt,
    )

    # 验证绕行航点对所有已知障碍物的安全性（避免绕行点落入其他障碍物影响区）
    check_list = all_obstacles if all_obstacles else [obstacle]
    for obs in check_list:
        if detour.distance_to(obs.position) < obs.radius + SAFETY_MARGIN:
            return None  # 绕行后仍不安全，触发悬停
    return detour


class ObstacleAvoidanceSystem:
    """避障系统：封装探测与绕行决策"""

    def __init__(self, obstacles: List[Obstacle]):
        self.obstacles = obstacles
        self.events: List[dict] = []

    def navigate(self, current: GeoPoint, target: GeoPoint) -> Tuple[GeoPoint, dict]:
        """
        导航决策：检查航段安全性，必要时返回绕行航点。
        返回 (实际前往的航点, 事件记录)。
        事件类型:
          - clear: 航段安全，直飞
          - detour: 检测到障碍物，绕行
          - hover: 无法安全绕行，原地悬停告警
        """
        conflict = check_path_segment(current, target, self.obstacles)
        if conflict is None:
            event = {"type": "clear", "from": current.to_dict(), "to": target.to_dict()}
            return target, event

        # 尝试绕行
        detour = compute_detour_waypoint(current, target, conflict, self.obstacles)
        if detour is not None:
            event = {
                "type": "detour",
                "obstacle": conflict.to_dict(),
                "from": current.to_dict(),
                "original_target": target.to_dict(),
                "detour_point": detour.to_dict(),
                "message": f"检测到障碍物({conflict.obstacle_type})，已规划绕行航点",
            }
            self.events.append(event)
            return detour, event

        # 无法绕行 → 悬停
        event = {
            "type": "hover",
            "obstacle": conflict.to_dict(),
            "from": current.to_dict(),
            "target": target.to_dict(),
            "message": f"检测到障碍物({conflict.obstacle_type})且无法安全绕行，执行悬停告警",
        }
        self.events.append(event)
        return current, event