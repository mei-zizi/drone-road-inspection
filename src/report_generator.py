# -*- coding: utf-8 -*-
"""
报告生成模块（规范提交版）
汇总巡检全部检测数据，生成结构化道路检修报告（Markdown格式），
含桩号定位、病害里程分布、分类养护方案、养护维修计划与总体结论，
同时保存机器可读的JSON结果数据，报告中重点标记严重路面病害。
"""
import json
import math
import os
import time
from typing import Dict, List

from models import (InspectionMission, Defect, GeoPoint, SeverityLevel,
                    DefectType, MissionStatus)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
REPORTS_DIR = os.path.join(OUTPUT_DIR, "reports")
RESULTS_DIR = os.path.join(OUTPUT_DIR, "inspection_results")


# ==================== 工具函数 ====================

def _fmt_ts(ts: float) -> str:
    """时间戳格式化"""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return str(ts)


def _severity_badge(severity: SeverityLevel) -> str:
    """严重程度标记符号"""
    if severity == SeverityLevel.CRITICAL:
        return "🔴 严重"
    if severity == SeverityLevel.MODERATE:
        return "🟡 中等"
    return "🟢 轻微"


def _road_length_m(mission: InspectionMission) -> float:
    """计算道路起终点直线距离（米）"""
    dx = (mission.end_point.lon - mission.start_point.lon) * 111320.0 * \
         math.cos(math.radians(mission.start_point.lat))
    dy = (mission.end_point.lat - mission.start_point.lat) * 111320.0
    return math.sqrt(dx * dx + dy * dy)


def _compute_station(mission: InspectionMission, point: GeoPoint) -> str:
    """
    计算病害点沿道路中心线的里程桩号（K0+xxx格式）。
    将病害坐标投影到道路起终点连线上，取投影距离为里程。
    """
    road_len = _road_length_m(mission)
    if road_len < 1e-6:
        return "K0+000"
    dx = (mission.end_point.lon - mission.start_point.lon) * 111320.0 * \
         math.cos(math.radians(mission.start_point.lat))
    dy = (mission.end_point.lat - mission.start_point.lat) * 111320.0
    px = (point.lon - mission.start_point.lon) * 111320.0 * \
         math.cos(math.radians(mission.start_point.lat))
    py = (point.lat - mission.start_point.lat) * 111320.0
    t = (px * dx + py * dy) / (road_len * road_len)
    t = max(0.0, min(1.0, t))
    dist = t * road_len
    km = int(dist // 1000)
    m = int(round(dist % 1000))
    if m >= 1000:
        km += 1
        m -= 1000
    return f"K{km}+{m:03d}"


def _station_to_m(station: str) -> float:
    """将桩号字符串转为里程米数（用于排序）"""
    try:
        km, m = station.replace("K", "").split("+")
        return int(km) * 1000 + int(m)
    except Exception:
        return 0.0


def _fmt_station_range(start_m: float, end_m: float) -> str:
    """格式化里程段落范围"""
    s_km, s_m = int(start_m // 1000), int(round(start_m)) % 1000
    e_km, e_m = int(end_m // 1000), int(round(end_m)) % 1000
    return f"K{s_km}+{s_m:03d} ~ K{e_km}+{e_m:03d}"


# ==================== 分类养护方案知识库 ====================

MAINTENANCE_PLANS = {
    DefectType.POT_HOLE: {
        "title": "6.1 坑洼（坑洞）养护方案",
        "situation": "路面出现坑洞，面层材料松散脱落，严重影响行车舒适性与安全性",
        "process": [
            "① 设置施工警示标志，按规范封闭病害所在车道",
            "② 沿坑洞边缘划线切缝，切割范围超出病害边缘10~15cm至稳固处",
            "③ 铣刨/凿除坑洞内松散沥青混合料及软弱基层，清理粉尘与杂物",
            "④ 坑底及四壁涂刷乳化沥青粘层油，确保新旧面层粘结",
            "⑤ 分层回填热拌沥青混合料（AC-13/AC-16），每层厚度不超过5cm",
            "⑥ 小型压路机分层压实，压实度不低于96%，表面与原路面平齐",
            "⑦ 待混合料冷却至50℃以下后开放交通",
        ],
        "material": "热拌细粒式沥青混合料（AC-13）、乳化沥青粘层油、切缝机、小型压路机、吹风机",
        "priority": "严重坑洼影响行车安全，须48小时内紧急处置；中等坑洼2周内计划维修",
        "duration": "单处约2~4小时（视面积与深度而定）",
    },
    DefectType.CRACK: {
        "title": "6.2 裂缝养护方案",
        "situation": "路面出现纵向/横向/龟裂裂缝，水分易沿裂缝渗入基层，加速结构损坏",
        "process": [
            "① 设置施工警示标志，占道施工时引导交通",
            "② 使用裂缝开槽机沿裂缝走向开槽（宽深比约1:1，槽宽10~12mm）",
            "③ 吹风机清槽，确保槽内无灰尘、碎屑与水分",
            "④ 灌注加热型改性沥青灌缝胶，灌注至槽口略低于路面1~2mm",
            "⑤ 表面撒布细砂或石粉防粘，待冷却后开放交通",
            "⑥ 龟裂密集区域建议铣刨面层后加铺沥青罩面",
        ],
        "material": "加热型改性沥青灌缝胶、开槽机、吹风机、灌缝机、细砂",
        "priority": "严重裂缝（长度>6m）1周内处置；中等裂缝2周内计划灌缝；轻微裂缝纳入常规养护",
        "duration": "单条裂缝约30~60分钟；龟裂区域罩面约1~2天",
    },
    DefectType.RUTTING: {
        "title": "6.3 车辙养护方案",
        "situation": "轮迹带出现永久变形（车辙），雨天易积水，影响行车稳定性",
        "process": [
            "① 设置施工警示标志，封闭车辙所在车道",
            "② 测量车辙深度与范围，确定铣刨宽度（超出病害边缘15~20cm）",
            "③ 铣刨机铣刨车辙部位面层，深度至车辙底部稳固结构层",
            "④ 清理铣刨面，涂刷粘层油",
            "⑤ 回填SBS改性沥青混合料（AC-16），分层压实",
            "⑥ 压实度检测合格后开放交通",
        ],
        "material": "SBS改性沥青混合料（AC-16）、乳化沥青粘层油、铣刨机、压路机",
        "priority": "严重车辙（长度>10m）48小时内紧急铣刨重铺；中等车辙2周内计划维修",
        "duration": "单处铣刨重铺约4~8小时",
    },
    DefectType.SETTLEMENT: {
        "title": "6.4 沉陷养护方案",
        "situation": "路面局部下沉变形，可能伴随基层脱空，需探明成因后针对性处治",
        "process": [
            "① 设置警示标志，安排地质雷达/钻芯取样探明沉陷成因与范围",
            "② 若为基层脱空/压实不足：注浆加固抬升基层，注浆压力0.2~0.5MPa",
            "③ 若为基层破坏严重：局部开挖翻修基层（水泥稳定碎石），养护7天",
            "④ 基层达标后，重铺沥青面层（底层AC-25+面层AC-13），分层压实",
            "⑤ 施工期间设置临时绕行标志，养护期满开放交通",
        ],
        "material": "水泥稳定碎石（基层）、热拌沥青混合料（AC-25/AC-13）、注浆材料、地质雷达",
        "priority": "沉陷病害涉及结构安全，须48小时内紧急处置并限速通行",
        "duration": "注浆抬升约1~2天；开挖翻修基层约5~7天（含养护期）",
    },
    DefectType.EDGE_BREAK: {
        "title": "6.5 边缘破损养护方案",
        "situation": "路肩/路面边缘破损崩边，影响路面结构稳定性与排水",
        "process": [
            "① 设置施工警示标志，靠边占用应急车道或路肩",
            "② 切除破损边缘至稳固面层，形成规则垂直面",
            "③ 清理松散物，涂刷粘层油",
            "④ 回填热拌沥青混合料并压实，恢复路面边缘线形",
            "⑤ 加设路缘石或边缘加固带，防止二次破损",
            "⑥ 完善路肩排水设施，避免积水冲刷边缘",
        ],
        "material": "热拌沥青混合料、乳化沥青粘层油、路缘石/边缘加固带、切缝机",
        "priority": "严重边缘破损2周内维修；轻微破损纳入常规养护，防止扩展",
        "duration": "单处约2~3小时",
    },
}


# ==================== 统计汇总 ====================

def _station_distribution(mission: InspectionMission, defects: List[Defect],
                          segment_len: int = 200) -> List[Dict]:
    """按里程段落（默认200m一段）统计病害分布"""
    road_len = _road_length_m(mission)
    n_seg = max(1, int(math.ceil(road_len / segment_len)))
    segments = []
    for i in range(n_seg):
        start_m = i * segment_len
        end_m = min((i + 1) * segment_len, road_len)
        seg = {
            "range": _fmt_station_range(start_m, end_m),
            "total": 0, "严重": 0, "中等": 0, "轻微": 0,
            "area": 0.0, "defect_ids": [],
        }
        segments.append(seg)
    for d in defects:
        station = _compute_station(mission, d.position)
        dist = _station_to_m(station)
        idx = min(int(dist // segment_len), n_seg - 1)
        seg = segments[idx]
        seg["total"] += 1
        seg[d.severity.value] += 1
        seg["area"] += d.area_m2
        seg["defect_ids"].append(d.defect_id)
    for seg in segments:
        seg["area"] = round(seg["area"], 2)
    return segments


def compute_statistics(mission: InspectionMission) -> Dict:
    """汇总统计巡检数据"""
    defects = mission.defects
    by_type: Dict[str, int] = {}
    by_type_area: Dict[str, float] = {}
    by_severity = {"严重": 0, "中等": 0, "轻微": 0}
    total_area = 0.0
    critical_defects = []

    for d in defects:
        t = d.defect_type.value
        by_type[t] = by_type.get(t, 0) + 1
        by_type_area[t] = by_type_area.get(t, 0.0) + d.area_m2
        by_severity[d.severity.value] += 1
        total_area += d.area_m2
        if d.severity == SeverityLevel.CRITICAL:
            critical_defects.append(d)

    station_segments = _station_distribution(mission, defects)

    detour_events = [e for e in mission.avoidance_events if e.get("type") == "detour"]
    hover_events = [e for e in mission.avoidance_events if e.get("type") == "hover"]

    total_path = 0.0
    for i in range(1, len(mission.waypoints)):
        total_path += mission.waypoints[i].position.distance_to(
            mission.waypoints[i - 1].position)

    return {
        "total_defects": len(defects),
        "by_type": by_type,
        "by_type_area": by_type_area,
        "by_severity": by_severity,
        "total_defect_area_m2": round(total_area, 2),
        "critical_count": len(critical_defects),
        "critical_defects": critical_defects,
        "station_segments": station_segments,
        "total_waypoints": len(mission.waypoints),
        "visited_waypoints": sum(1 for w in mission.waypoints if w.visited),
        "total_frames": len(mission.frames),
        "path_length_m": round(total_path, 1),
        "detour_count": len(detour_events),
        "hover_count": len(hover_events),
        "obstacle_count": len(mission.obstacles),
    }


# ==================== 报告章节生成 ====================

def _section_cover(mission: InspectionMission, report_time: str) -> List[str]:
    """报告封面信息块"""
    road_len = _road_length_m(mission)
    end_station = f"K{int(road_len) // 1000}+{int(round(road_len)) % 1000:03d}"
    lines = []
    a = lines.append
    a("# 无人机道路巡检检修报告")
    a("")
    a(f"> **任务编号**：{mission.mission_id}  ")
    a(f"> **巡检道路**：{mission.road_name}（{mission.road_grade}）  ")
    a(f"> **巡检范围**：K0+000 ~ {end_station}（全长约{road_len / 1000:.1f}km）  ")
    a(f"> **执行单位**：无人机道路巡检智能体  ")
    a(f"> **报告生成时间**：{report_time}  ")
    a("> **报告版本**：正式提交版")
    a("")
    a("---")
    a("")
    return lines


def _section_overview(mission: InspectionMission) -> List[str]:
    """一、任务概述"""
    road_len = _road_length_m(mission)
    end_station = f"K{int(road_len) // 1000}+{int(round(road_len)) % 1000:03d}"
    lines = []
    a = lines.append
    a("## 一、任务概述")
    a("")
    a(f"本次巡检任务对 **{mission.road_name}** 开展无人机航拍巡检，"
      f"排查路面坑洼、裂缝、车辙、沉陷、边缘破损等病害，"
      f"为道路维护检修提供数据支撑。")
    a("")
    a("| 项目 | 内容 |")
    a("|------|------|")
    a(f"| 道路等级 | {mission.road_grade} |")
    a(f"| 路面宽度 | {mission.road_width_m} m |")
    a(f"| 巡检范围 | K0+000 ~ {end_station}（约{road_len / 1000:.1f}km） |")
    a(f"| 巡检飞行高度 | {mission.flight_altitude} m |")
    a(f"| 拍摄间隔 | {mission.capture_interval_m} m |")
    a(f"| 影像重叠率 | {mission.overlap_rate * 100:.0f}% |")
    a(f"| 起点坐标 | ({mission.start_point.lon}, {mission.start_point.lat}) |")
    a(f"| 终点坐标 | ({mission.end_point.lon}, {mission.end_point.lat}) |")
    a("")
    return lines


def _section_execution(mission: InspectionMission, stats: Dict) -> List[str]:
    """二、巡检执行情况"""
    lines = []
    a = lines.append
    a("## 二、巡检执行情况")
    a("")
    a("| 指标 | 数值 |")
    a("|------|------|")
    a(f"| 规划航点数 | {stats['total_waypoints']} |")
    a(f"| 实际到达航点 | {stats['visited_waypoints']} |")
    a(f"| 飞行总航程 | 约 {stats['path_length_m']} m |")
    a(f"| 采集影像帧数 | {stats['total_frames']} |")
    a(f"| 已知障碍物数 | {stats['obstacle_count']} |")
    a(f"| 避障绕行次数 | {stats['detour_count']} |")
    a(f"| 悬停告警次数 | {stats['hover_count']} |")
    a("")
    if mission.avoidance_events:
        a("### 避障事件记录")
        a("")
        a("| 序号 | 事件类型 | 障碍物类型 | 说明 |")
        a("|------|----------|-----------|------|")
        for i, e in enumerate(mission.avoidance_events, 1):
            etype = {"detour": "绕行", "hover": "悬停告警"}.get(e["type"], e["type"])
            obs = e.get("obstacle", {})
            obs_type = obs.get("type", "-")
            msg = e.get("message", "-")
            a(f"| {i} | {etype} | {obs_type} | {msg} |")
        a("")
    return lines


def _section_summary(mission: InspectionMission, stats: Dict) -> List[str]:
    """三、病害检测汇总"""
    lines = []
    a = lines.append
    a("## 三、病害检测汇总")
    a("")
    a(f"本次巡检共识别路面病害 **{stats['total_defects']}** 处，"
      f"病害总面积约 **{stats['total_defect_area_m2']} m²**。")
    a("")
    a("### 3.1 病害严重程度分布")
    a("")
    a("| 严重程度 | 数量 | 占比 |")
    a("|----------|------|------|")
    total = max(stats['total_defects'], 1)
    for sev in ["严重", "中等", "轻微"]:
        cnt = stats['by_severity'].get(sev, 0)
        a(f"| {sev} | {cnt} | {cnt / total * 100:.1f}% |")
    a("")
    a("### 3.2 病害类型分布")
    a("")
    a("| 病害类型 | 数量 | 面积(m²) | 面积占比 |")
    a("|----------|------|---------|---------|")
    total_area = max(stats['total_defect_area_m2'], 0.01)
    for t, cnt in sorted(stats['by_type'].items(), key=lambda x: -x[1]):
        area = stats['by_type_area'].get(t, 0.0)
        a(f"| {t} | {cnt} | {area:.2f} | {area / total_area * 100:.1f}% |")
    a("")
    a("### 3.3 病害里程分布")
    a("")
    a("| 里程段落 | 病害总数 | 严重 | 中等 | 轻微 | 病害面积(m²) |")
    a("|----------|---------|------|------|------|-------------|")
    for seg in stats['station_segments']:
        a(f"| {seg['range']} | {seg['total']} | {seg['严重']} | "
          f"{seg['中等']} | {seg['轻微']} | {seg['area']:.2f} |")
    a("")
    return lines


def _section_critical(mission: InspectionMission, stats: Dict) -> List[str]:
    """四、严重病害重点标记"""
    lines = []
    a = lines.append
    a("## 四、严重病害重点标记 ★")
    a("")
    if stats['critical_defects']:
        a(f"检出 **{stats['critical_count']}** 处严重路面病害，已全部重点标记，"
          f"建议优先安排处置：")
        a("")
        a("| 标记 | 病害编号 | 类型 | 桩号位置 | 坐标(经度,纬度) | 面积(m²) | 深度(mm) | 维修建议 |")
        a("|------|---------|------|---------|----------------|---------|---------|---------|")
        for d in stats['critical_defects']:
            station = _compute_station(mission, d.position)
            a(f"| ★ | {d.defect_id} | {d.defect_type.value} | {station} | "
              f"({d.position.lon}, {d.position.lat}) | {d.area_m2:.2f} | "
              f"{d.depth_mm:.0f} | {d.repair_suggestion} |")
        a("")
    else:
        a("本次巡检未检出严重路面病害。")
        a("")
    return lines


def _section_details(mission: InspectionMission) -> List[str]:
    """五、病害明细清单"""
    lines = []
    a = lines.append
    a("## 五、病害明细清单")
    a("")
    if mission.defects:
        a("| 编号 | 类型 | 严重程度 | 桩号位置 | 坐标(经度,纬度) | "
          "面积(m²) | 长(m) | 宽(m) | 深度(mm) | 置信度 | 检出帧 |")
        a("|------|------|---------|---------|----------------|"
          "---------|-------|-------|---------|--------|--------|")
        for d in mission.defects:
            mark = "★" if d.is_key_marked else ""
            station = _compute_station(mission, d.position)
            a(f"| {d.defect_id}{mark} | {d.defect_type.value} | "
              f"{_severity_badge(d.severity)} | {station} | "
              f"({d.position.lon}, {d.position.lat}) | {d.area_m2:.2f} | "
              f"{d.length_m:.1f} | {d.width_m:.2f} | {d.depth_mm:.0f} | "
              f"{d.confidence:.2f} | {d.detected_in_frame} |")
        a("")
    else:
        a("本次巡检未发现路面病害。")
        a("")
    return lines


def _section_maintenance_plans(mission: InspectionMission, stats: Dict) -> List[str]:
    """六、分类养护方案"""
    lines = []
    a = lines.append
    a("## 六、分类养护方案")
    a("")
    a("针对本次巡检识别的各类病害，依据《城镇道路养护技术规范》（CJJ 36），"
      "制定分类养护方案如下：")
    a("")
    for dtype, plan in MAINTENANCE_PLANS.items():
        dtype_name = dtype.value
        cnt = stats['by_type'].get(dtype_name, 0)
        if cnt == 0:
            continue
        type_defects = [d for d in mission.defects if d.defect_type == dtype]
        critical_cnt = sum(1 for d in type_defects if d.severity == SeverityLevel.CRITICAL)
        moderate_cnt = sum(1 for d in type_defects if d.severity == SeverityLevel.MODERATE)
        minor_cnt = sum(1 for d in type_defects if d.severity == SeverityLevel.MINOR)
        type_area = stats['by_type_area'].get(dtype_name, 0.0)
        stations = sorted([_compute_station(mission, d.position) for d in type_defects],
                          key=_station_to_m)
        station_range = f"{stations[0]} ~ {stations[-1]}" if stations else "-"

        a(f"### {plan['title']}")
        a("")
        a(f"**病害概况**：共检出{dtype_name} **{cnt}** 处，总面积 **{type_area:.2f} m²**，"
          f"分布桩号 {station_range}。")
        a("")
        a("| 严重程度 | 数量 |")
        a("|----------|------|")
        a(f"| 严重 | {critical_cnt} |")
        a(f"| 中等 | {moderate_cnt} |")
        a(f"| 轻微 | {minor_cnt} |")
        a("")
        a(f"**病害特征**：{plan['situation']}。")
        a("")
        a("**处治工艺**：")
        a("")
        for step in plan['process']:
            a(f"{step}  ")
        a("")
        a(f"**适用材料与设备**：{plan['material']}。")
        a("")
        a(f"**处治优先级**：{plan['priority']}。")
        a("")
        a(f"**预期工期**：{plan['duration']}。")
        a("")
        a(f"**涉及病害编号**：{', '.join(d.defect_id for d in type_defects)}")
        a("")
    return lines


def _section_repair_schedule(mission: InspectionMission, stats: Dict) -> List[str]:
    """七、养护维修计划"""
    lines = []
    a = lines.append
    a("## 七、养护维修计划")
    a("")
    a("### 7.1 优先级安排")
    a("")
    a("| 优先级 | 处置时限 | 病害数量 | 处置要求 |")
    a("|--------|---------|---------|---------|")
    crit = stats['by_severity'].get('严重', 0)
    mod = stats['by_severity'].get('中等', 0)
    minor = stats['by_severity'].get('轻微', 0)
    a(f"| 🔴 紧急处置 | 48小时内 | {crit} 处 | "
      "设置警示标志保障通行安全，按分类养护方案立即施工 |")
    a(f"| 🟡 计划维修 | 2周内 | {mod} 处 | "
      "纳入近期维修计划，按分类养护方案组织实施 |")
    a(f"| 🟢 常规养护 | 持续观察 | {minor} 处 | "
      "建档跟踪，纳入日常养护巡查，防止病害扩展 |")
    a("")
    a("### 7.2 交通组织建议")
    a("")
    a("1. 紧急处置施工期间，病害所在车道临时封闭，设置导流标志与警示灯，"
      "安排人员疏导交通。")
    a("2. 铣刨重铺等占道作业安排在交通低峰时段（夜间22:00~次日6:00）进行，"
      "减少对通行影响。")
    a("3. 沉陷病害处治期间建议限速通行（≤30km/h），必要时设置临时绕行路线。")
    a("4. 施工区域前后按规范摆放锥形标、施工标志牌，夜间加设爆闪灯。")
    a("")
    a("### 7.3 预防性养护建议")
    a("")
    a("1. 对裂缝密集路段建议开展预防性养护（雾封层/微表处），"
      "封闭微小裂缝，延缓病害扩展。")
    a("2. 车辙多发路段建议加强重载车辆管理，必要时加铺抗车辙改性沥青面层。")
    a("3. 边缘破损路段完善路肩排水设施，避免雨水冲刷加剧破损。")
    a("4. 建议每季度开展一次无人机巡检复查，动态跟踪病害发展趋势。")
    a("")
    return lines


def _section_conclusion(mission: InspectionMission, stats: Dict) -> List[str]:
    """八、总体评价与结论"""
    lines = []
    a = lines.append
    a("## 八、总体评价与结论")
    a("")
    total = max(stats['total_defects'], 1)
    crit_ratio = stats['by_severity'].get('严重', 0) / total * 100
    if crit_ratio >= 40:
        rating = "较差"
        rating_desc = ("严重病害占比超过40%，路面结构性能明显下降，"
                       "存在较大行车安全隐患，需立即开展集中维修。")
    elif crit_ratio >= 20:
        rating = "一般"
        rating_desc = ("严重病害占比较高，路面使用性能呈衰减趋势，"
                       "需尽快安排计划维修，防止病害加速扩展。")
    else:
        rating = "良好"
        rating_desc = ("严重病害占比较低，路面整体状况尚可，"
                       "按计划开展常规养护即可维持良好路况。")

    a("### 8.1 道路技术状况评价")
    a("")
    a(f"本次巡检结果表明，{mission.road_name} 路面技术状况综合评价为 **{rating}**。")
    a("")
    a(f"> {rating_desc}")
    a("")
    a("### 8.2 主要风险点")
    a("")
    risk_idx = 1
    if stats['critical_defects']:
        a(f"{risk_idx}. **行车安全风险**：{stats['critical_count']} 处严重病害"
          f"（坑洼/沉陷等）直接影响行车安全，须紧急处置。")
        risk_idx += 1
    if stats['by_type'].get('沉陷', 0) > 0:
        a(f"{risk_idx}. **结构安全风险**：检出沉陷病害，可能存在基层脱空，"
          "需钻芯复查确认结构承载能力。")
        risk_idx += 1
    if stats['by_type'].get('裂缝', 0) > 0:
        a(f"{risk_idx}. **水损害风险**：{stats['by_type'].get('裂缝', 0)} 处裂缝"
          "若不及时灌缝，雨水渗入将加速基层损坏。")
        risk_idx += 1
    a("")
    a("### 8.3 巡检结论")
    a("")
    a(f"本次无人机巡检任务已完成 {mission.road_name} 全线航拍采集与病害识别，"
      f"共检出路面病害 {stats['total_defects']} 处"
      f"（严重 {stats['critical_count']} 处、"
      f"中等 {stats['by_severity'].get('中等', 0)} 处、"
      f"轻微 {stats['by_severity'].get('轻微', 0)} 处），"
      f"病害总面积 {stats['total_defect_area_m2']} m²。")
    a("")
    a(f"建议养护管理部门依据本报告第六章分类养护方案与第七章养护维修计划，"
      f"按优先级组织实施维修，优先处置 {stats['critical_count']} 处严重病害，"
      f"确保道路安全畅通。")
    a("")
    return lines


def _section_appendix(mission: InspectionMission, stats: Dict,
                      report_time: str) -> List[str]:
    """九、附录"""
    lines = []
    a = lines.append
    a("## 九、附录")
    a("")
    a("| 附录项 | 说明 |")
    a("|--------|------|")
    a("| 航拍原始影像数据 | `data/aerial_raw/` |")
    a("| 巡检结果数据（JSON） | `output/inspection_results/` |")
    a(f"| 避障事件记录 | 共 {stats['detour_count']} 次绕行，{stats['hover_count']} 次悬停 |")
    a(f"| 影像采集覆盖率 | {stats['total_frames']} 帧 / "
      f"{stats['visited_waypoints']} 个拍摄航点 |")
    a("")
    a("---")
    a(f"*本报告由无人机道路巡检智能体自动生成 | {report_time}*")
    return lines


def generate_report(mission: InspectionMission) -> str:
    """生成 Markdown 格式道路检修报告（规范提交版）"""
    mission.status = MissionStatus.REPORTING
    stats = compute_statistics(mission)
    report_time = _fmt_ts(time.time())

    lines = []
    lines += _section_cover(mission, report_time)
    lines += _section_overview(mission)
    lines += _section_execution(mission, stats)
    lines += _section_summary(mission, stats)
    lines += _section_critical(mission, stats)
    lines += _section_details(mission)
    lines += _section_maintenance_plans(mission, stats)
    lines += _section_repair_schedule(mission, stats)
    lines += _section_conclusion(mission, stats)
    lines += _section_appendix(mission, stats, report_time)

    return "\n".join(lines)


def save_report(mission: InspectionMission) -> Dict[str, str]:
    """生成报告并保存至项目文件，返回保存路径"""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)

    report_md = generate_report(mission)
    report_path = os.path.join(REPORTS_DIR, f"{mission.mission_id}_检修报告.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"[报告生成] 检修报告已保存：{report_path}")

    mission.status = MissionStatus.COMPLETED

    result_data = mission.to_dict()
    result_path = os.path.join(RESULTS_DIR, f"{mission.mission_id}_巡检结果.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=1)
    print(f"[报告生成] 巡检结果数据已保存：{result_path}")

    return {"report_path": report_path, "result_path": result_path}