# -*- coding: utf-8 -*-
"""
无人机道路巡检智能体 - 核心数据模型
定义巡检任务、航点、病害、影像帧、报告等数据结构
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Tuple
import time
import uuid


class DefectType(Enum):
    """路面病害类型"""
    POT_HOLE = "坑洼"          # 坑洞/坑洼
    CRACK = "裂缝"             # 纵向/横向/龟裂
    RUTTING = "车辙"           # 车辙变形
    PATCH = "修补痕迹"         # 历史修补区（需复查）
    SETTLEMENT = "沉陷"        # 路面沉陷
    EDGE_BREAK = "边缘破损"    # 路肩边缘破损


class SeverityLevel(Enum):
    """病害严重程度分级"""
    CRITICAL = "严重"          # 严重病害 - 重点标记，需立即处置
    MODERATE = "中等"          # 中等病害 - 需计划维修
    MINOR = "轻微"             # 轻微病害 - 记录观察


class MissionStatus(Enum):
    """巡检任务状态"""
    PENDING = "待执行"
    PLANNING = "路径规划中"
    FLYING = "巡检飞行中"
    ANALYZING = "影像分析中"
    REPORTING = "报告生成中"
    COMPLETED = "已完成"
    ABORTED = "已中止"


@dataclass
class GeoPoint:
    """地理坐标点（经纬度 + 海拔）"""
    lon: float          # 经度
    lat: float          # 纬度
    alt: float = 50.0   # 海拔/飞行高度（米）

    def to_dict(self) -> Dict:
        return {"lon": round(self.lon, 7), "lat": round(self.lat, 7), "alt": round(self.alt, 2)}

    def distance_to(self, other: "GeoPoint") -> float:
        """估算两点间平面距离（米），使用简化平面近似"""
        import math
        dlat = (other.lat - self.lat) * 111320.0
        dlon = (other.lon - self.lon) * 111320.0 * math.cos(math.radians(self.lat))
        return math.sqrt(dlat ** 2 + dlon ** 2)


@dataclass
class Waypoint:
    """飞行航点"""
    index: int                  # 航点序号
    position: GeoPoint          # 航点坐标
    action: str = "capture"     # 到达后动作: capture(拍摄) / hover(悬停) / turn(转向)
    visited: bool = False       # 是否已到达
    obstacle_detected: bool = False  # 该航点附近是否检测到障碍物

    def to_dict(self) -> Dict:
        return {
            "index": self.index,
            "position": self.position.to_dict(),
            "action": self.action,
            "visited": self.visited,
            "obstacle_detected": self.obstacle_detected,
        }


@dataclass
class Obstacle:
    """障碍物"""
    position: GeoPoint          # 障碍物坐标
    radius: float               # 影响半径（米）
    obstacle_type: str = "unknown"  # 类型: building/tree/wire/vehicle
    detected_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {
            "position": self.position.to_dict(),
            "radius": round(self.radius, 2),
            "type": self.obstacle_type,
        }


@dataclass
class ImageFrame:
    """采集的影像帧"""
    frame_id: str                       # 帧唯一ID
    waypoint_index: int                 # 对应航点序号
    position: GeoPoint                  # 拍摄时无人机坐标
    timestamp: float                    # 拍摄时间戳
    image_data: Optional[list] = None   # 影像像素数据（模拟）
    file_path: str = ""                 # 影像文件路径

    def to_dict(self) -> Dict:
        return {
            "frame_id": self.frame_id,
            "waypoint_index": self.waypoint_index,
            "position": self.position.to_dict(),
            "timestamp": round(self.timestamp, 3),
            "file_path": self.file_path,
        }


@dataclass
class Defect:
    """路面病害记录"""
    defect_id: str                  # 病害唯一ID
    defect_type: DefectType         # 病害类型
    severity: SeverityLevel         # 严重程度
    position: GeoPoint              # 病害中心坐标
    area_m2: float                  # 病害面积（平方米）
    length_m: float = 0.0           # 病害长度（米，裂缝类）
    width_m: float = 0.0            # 病害宽度（米）
    depth_mm: float = 0.0           # 病害深度（毫米，坑洼类）
    confidence: float = 0.0         # 识别置信度 0~1
    detected_in_frame: str = ""     # 检出影像帧ID
    is_key_marked: bool = False     # 是否重点标记（严重病害）
    description: str = ""           # 病害描述
    repair_suggestion: str = ""     # 维修建议

    def to_dict(self) -> Dict:
        return {
            "defect_id": self.defect_id,
            "type": self.defect_type.value,
            "severity": self.severity.value,
            "is_key_marked": self.is_key_marked,
            "position": self.position.to_dict(),
            "area_m2": round(self.area_m2, 3),
            "length_m": round(self.length_m, 2),
            "width_m": round(self.width_m, 2),
            "depth_mm": round(self.depth_mm, 1),
            "confidence": round(self.confidence, 3),
            "detected_in_frame": self.detected_in_frame,
            "description": self.description,
            "repair_suggestion": self.repair_suggestion,
        }


@dataclass
class InspectionMission:
    """巡检任务"""
    mission_id: str                     # 任务ID
    road_name: str                      # 道路名称
    road_grade: str = "城市主干道"       # 道路等级
    start_point: GeoPoint = field(default_factory=lambda: GeoPoint(0, 0))
    end_point: GeoPoint = field(default_factory=lambda: GeoPoint(0, 0))
    road_width_m: float = 12.0          # 路面宽度（米）
    flight_altitude: float = 30.0       # 巡检飞行高度（米）
    capture_interval_m: float = 20.0    # 拍摄间隔（米）
    overlap_rate: float = 0.6           # 影像重叠率
    status: MissionStatus = MissionStatus.PENDING
    created_at: float = field(default_factory=time.time)
    waypoints: List[Waypoint] = field(default_factory=list)
    obstacles: List[Obstacle] = field(default_factory=list)
    frames: List[ImageFrame] = field(default_factory=list)
    defects: List[Defect] = field(default_factory=list)
    avoidance_events: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "mission_id": self.mission_id,
            "road_name": self.road_name,
            "road_grade": self.road_grade,
            "start_point": self.start_point.to_dict(),
            "end_point": self.end_point.to_dict(),
            "road_width_m": self.road_width_m,
            "flight_altitude": self.flight_altitude,
            "capture_interval_m": self.capture_interval_m,
            "overlap_rate": self.overlap_rate,
            "status": self.status.value,
            "created_at": self.created_at,
            "waypoints": [w.to_dict() for w in self.waypoints],
            "obstacles": [o.to_dict() for o in self.obstacles],
            "frames": [f.to_dict() for f in self.frames],
            "defects": [d.to_dict() for d in self.defects],
            "avoidance_events": self.avoidance_events,
        }


def gen_id(prefix: str = "") -> str:
    """生成唯一ID"""
    return f"{prefix}{uuid.uuid4().hex[:8]}"