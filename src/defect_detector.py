# -*- coding: utf-8 -*-
"""
病害识别模块
对采集的路面影像进行自动分析，识别坑洼、裂缝、车辙、沉陷、边缘破损等病害，
计算病害几何参数（面积/长度/宽度/深度），评定严重程度并重点标记严重病害，
将像素位置换算为病害地理坐标。
"""
import copy
import math
from collections import deque
from typing import List, Tuple, Dict

from models import (InspectionMission, ImageFrame, Defect, GeoPoint,
                    DefectType, SeverityLevel, gen_id)

# 像素编码 → 病害类型映射
PIXEL_TYPE_MAP = {
    1: DefectType.CRACK,
    2: DefectType.POT_HOLE,
    3: DefectType.RUTTING,
    4: DefectType.SETTLEMENT,
    5: DefectType.EDGE_BREAK,
}
DEFECT_PIXELS = set(PIXEL_TYPE_MAP.keys())

# 每像素格代表的实际路面尺寸（米）
PIXEL_SCALE_M = 0.5


def find_connected_components(grid: List[List[int]]) -> List[Dict]:
    """
    连通域分析：找出影像中同一病害类型的连通像素块。
    使用8邻域洪泛填充，返回每个连通域的类型、像素列表、包围盒。
    """
    h = len(grid)
    w = len(grid[0]) if h > 0 else 0
    visited = [[False] * w for _ in range(h)]
    components = []

    for y in range(h):
        for x in range(w):
            if visited[y][x] or grid[y][x] not in DEFECT_PIXELS:
                continue
            pixel_type = grid[y][x]
            # BFS 洪泛填充（deque.popleft 为 O(1)，避免 list.pop(0) 的 O(n) 队头弹出开销）
            queue = deque([(x, y)])
            visited[y][x] = True
            pixels = []
            min_x, max_x = x, x
            min_y, max_y = y, y
            while queue:
                cx, cy = queue.popleft()
                pixels.append((cx, cy))
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = cx + dx, cy + dy
                        if (0 <= nx < w and 0 <= ny < h and not visited[ny][nx]
                                and grid[ny][nx] == pixel_type):
                            visited[ny][nx] = True
                            queue.append((nx, ny))
            components.append({
                "type": pixel_type,
                "pixels": pixels,
                "count": len(pixels),
                "bbox": (min_x, min_y, max_x, max_y),
            })
    return components


def classify_severity(defect_type: DefectType, area_m2: float,
                      length_m: float, width_m: float, depth_mm: float) -> SeverityLevel:
    """
    病害严重程度分级评定（参照城市道路养护规范，适配航拍像素尺度）：
    - 坑洼: 面积>2.0m² 或深度>40mm 为严重；面积>0.5m² 为中等；否则轻微
    - 裂缝: 长度>6.0m 为严重；长度>2.0m 为中等；否则轻微
      （航拍像素分辨率0.5m，裂缝实际宽度无法精确测量，以长度为主要分级依据）
    - 车辙: 长度>10m 为严重；>5m 为中等
    - 沉陷: 面积>3.0m² 为严重；>1.0m² 为中等
    - 边缘破损: 面积>0.5m² 为严重；>0.2m² 为中等
    """
    if defect_type == DefectType.POT_HOLE:
        if area_m2 > 2.0 or depth_mm > 40:
            return SeverityLevel.CRITICAL
        if area_m2 > 0.5:
            return SeverityLevel.MODERATE
        return SeverityLevel.MINOR

    if defect_type == DefectType.CRACK:
        if length_m > 6.0:
            return SeverityLevel.CRITICAL
        if length_m > 2.0:
            return SeverityLevel.MODERATE
        return SeverityLevel.MINOR

    if defect_type == DefectType.RUTTING:
        if length_m > 10.0:
            return SeverityLevel.CRITICAL
        if length_m > 5.0:
            return SeverityLevel.MODERATE
        return SeverityLevel.MINOR

    if defect_type == DefectType.SETTLEMENT:
        if area_m2 > 3.0:
            return SeverityLevel.CRITICAL
        if area_m2 > 1.0:
            return SeverityLevel.MODERATE
        return SeverityLevel.MINOR

    if defect_type == DefectType.EDGE_BREAK:
        if area_m2 > 0.5:
            return SeverityLevel.CRITICAL
        if area_m2 > 0.2:
            return SeverityLevel.MODERATE
        return SeverityLevel.MINOR

    return SeverityLevel.MINOR


def estimate_depth(defect_type: DefectType, area_m2: float) -> float:
    """估算病害深度（毫米）：坑洼按面积估算，其他类型深度记0"""
    if defect_type == DefectType.POT_HOLE:
        # 经验估算：面积越大深度倾向越大，10~60mm
        return min(60.0, 10.0 + area_m2 * 60.0)
    if defect_type == DefectType.SETTLEMENT:
        return min(50.0, 5.0 + area_m2 * 15.0)
    return 0.0


def pixel_to_geo(frame: ImageFrame, px: int, py: int, grid_w: int, grid_h: int) -> GeoPoint:
    """将影像像素坐标换算为地理坐标（基于拍摄位置与像素比例尺）"""
    # 像素格中心相对于影像中心的偏移（米）
    # 影像坐标系：x 向东，y 向北（第0行在北侧）
    offset_x_m = (px - (grid_w - 1) / 2.0) * PIXEL_SCALE_M
    offset_y_m = ((grid_h - 1) / 2.0 - py) * PIXEL_SCALE_M
    # 转为经纬度偏移
    dlon = offset_x_m / (111320.0 * math.cos(math.radians(frame.position.lat)))
    dlat = offset_y_m / 111320.0
    return GeoPoint(
        lon=round(frame.position.lon + dlon, 7),
        lat=round(frame.position.lat + dlat, 7),
        alt=frame.position.alt,
    )


def make_repair_suggestion(defect_type: DefectType, severity: SeverityLevel) -> str:
    """根据病害类型与严重程度生成维修建议"""
    base = {
        DefectType.POT_HOLE: "清理坑洞松散物，热拌沥青混合料填补压实",
        DefectType.CRACK: "裂缝清理后灌缝胶密封处理",
        DefectType.RUTTING: "铣刨车辙部位，重铺沥青面层",
        DefectType.SETTLEMENT: "注浆抬升或局部翻修基层",
        DefectType.EDGE_BREAK: "修复路肩，边缘加固处理",
    }
    sug = base.get(defect_type, "现场复查后确定维修方案")
    if severity == SeverityLevel.CRITICAL:
        sug = "【紧急】" + sug + "，建议48小时内处置并设置警示标志"
    elif severity == SeverityLevel.MODERATE:
        sug += "，建议纳入近期维修计划（2周内）"
    else:
        sug += "，持续观察，纳入常规养护"
    return sug


def analyze_frame(frame: ImageFrame, min_pixels: int = 2) -> List[Defect]:
    """分析单帧影像，识别并记录该帧中的全部病害"""
    if not frame.image_data:
        return []

    grid = frame.image_data
    h = len(grid)
    w = len(grid[0]) if h > 0 else 0
    if h == 0 or w == 0:
        return []

    components = find_connected_components(grid)
    defects = []

    for comp in components:
        if comp["count"] < min_pixels:
            continue  # 过滤过小噪声

        defect_type = PIXEL_TYPE_MAP[comp["type"]]
        pixels = comp["pixels"]
        min_x, min_y, max_x, max_y = comp["bbox"]

        # 几何参数计算
        area_m2 = comp["count"] * (PIXEL_SCALE_M ** 2)
        bbox_w_m = (max_x - min_x + 1) * PIXEL_SCALE_M
        bbox_h_m = (max_y - min_y + 1) * PIXEL_SCALE_M

        if defect_type == DefectType.CRACK:
            # 裂缝：长度取包围盒长边，宽度按面积/长度估算
            length_m = max(bbox_w_m, bbox_h_m)
            width_m = area_m2 / max(length_m, 0.01)
        elif defect_type == DefectType.RUTTING:
            length_m = bbox_w_m  # 车辙沿道路纵向
            width_m = bbox_h_m
        else:
            length_m = max(bbox_w_m, bbox_h_m)
            width_m = min(bbox_w_m, bbox_h_m)

        depth_mm = estimate_depth(defect_type, area_m2)

        # 严重程度评定
        severity = classify_severity(defect_type, area_m2, length_m, width_m, depth_mm)

        # 病害中心地理坐标
        cx_px = sum(p[0] for p in pixels) / len(pixels)
        cy_px = sum(p[1] for p in pixels) / len(pixels)
        defect_pos = pixel_to_geo(frame, cx_px, cy_px, w, h)

        # 置信度：基于连通域像素数（越大越可信），上限0.98
        confidence = min(0.98, 0.5 + comp["count"] * 0.05)

        is_key = (severity == SeverityLevel.CRITICAL)

        desc = (f"{defect_type.value}：面积{area_m2:.2f}m²，"
                f"长{length_m:.1f}m×宽{width_m:.2f}m"
                + (f"，估算深度{depth_mm:.0f}mm" if depth_mm > 0 else ""))

        defect = Defect(
            defect_id=gen_id("DEF-"),
            defect_type=defect_type,
            severity=severity,
            position=defect_pos,
            area_m2=area_m2,
            length_m=length_m,
            width_m=width_m,
            depth_mm=depth_mm,
            confidence=confidence,
            detected_in_frame=frame.frame_id,
            is_key_marked=is_key,
            description=desc,
            repair_suggestion=make_repair_suggestion(defect_type, severity),
        )
        defects.append(defect)

    return defects


def _defects_mergeable(d1: Defect, d2: Defect, dist_threshold: float = 3.0) -> bool:
    """判断两病害是否为同一病害的跨帧重复检出（同类型且位置相近）"""
    if d1.defect_type != d2.defect_type:
        return False
    return d1.position.distance_to(d2.position) < dist_threshold


def deduplicate_defects(defects: List[Defect]) -> List[Defect]:
    """跨帧去重：合并重叠影像中的同一病害，保留严重程度最高、置信度最高的记录"""
    if not defects:
        return []
    # 按严重程度排序：严重 > 中等 > 轻微
    sev_order = {SeverityLevel.CRITICAL: 0, SeverityLevel.MODERATE: 1, SeverityLevel.MINOR: 2}
    sorted_defs = sorted(defects, key=lambda d: (sev_order[d.severity], -d.confidence))

    result = []
    for d in sorted_defs:
        merged = False
        for r in result:
            if _defects_mergeable(d, r):
                # 合并：取更大的面积/长度，更新帧号记录
                r.area_m2 = max(r.area_m2, d.area_m2)
                r.length_m = max(r.length_m, d.length_m)
                r.width_m = max(r.width_m, d.width_m)
                r.depth_mm = max(r.depth_mm, d.depth_mm)
                r.confidence = max(r.confidence, d.confidence)
                if d.detected_in_frame not in r.detected_in_frame:
                    r.detected_in_frame += f",{d.detected_in_frame}"
                merged = True
                break
        if not merged:
            # 存入浅拷贝：后续合并修改的是结果副本，不污染调用方传入的原始对象
            result.append(copy.copy(d))
    return result


def analyze_all_frames(mission: InspectionMission) -> InspectionMission:
    """对全部采集影像执行病害识别分析，跳过无效帧并记录"""
    print(f"[病害识别] 开始分析 {len(mission.frames)} 帧影像...")
    all_defects = []
    skipped_frames = []  # 记录因数据无效而跳过的帧
    for frame in mission.frames:
        if not frame.image_data or not isinstance(frame.image_data, list) or len(frame.image_data) == 0:
            skipped_frames.append(frame.frame_id)
            continue
        frame_defects = analyze_frame(frame)
        all_defects.extend(frame_defects)
        if frame_defects:
            types = set(d.defect_type.value for d in frame_defects)
            print(f"[病害识别] 帧 {frame.frame_id}: 检出 {len(frame_defects)} 处病害（{', '.join(types)}）")

    # 跨帧去重
    mission.defects = deduplicate_defects(all_defects)

    # 重新编号
    for i, d in enumerate(mission.defects):
        d.defect_id = f"DEF-{i + 1:03d}"

    critical = sum(1 for d in mission.defects if d.severity == SeverityLevel.CRITICAL)
    moderate = sum(1 for d in mission.defects if d.severity == SeverityLevel.MODERATE)
    minor = sum(1 for d in mission.defects if d.severity == SeverityLevel.MINOR)

    print(f"[病害识别] 分析完成：共识别病害 {len(mission.defects)} 处"
          f"（严重 {critical} 处【已重点标记】，中等 {moderate} 处，轻微 {minor} 处）")
    if skipped_frames:
        shown = ", ".join(skipped_frames[:5])
        suffix = " 等" if len(skipped_frames) > 5 else ""
        print(f"[病害识别] ⚠ 跳过无效影像帧 {len(skipped_frames)} 帧（{shown}{suffix}），"
              f"对应路段病害情况未知")
    return mission