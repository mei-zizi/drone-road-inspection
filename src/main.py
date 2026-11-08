# -*- coding: utf-8 -*-
"""
无人机道路巡检智能体 - 主程序入口
编排完整道路检修流程，含数据完整性兜底机制：
1. 接收道路巡检任务，读取项目内巡检资料与航拍原始数据
2. 【关卡1】校验航拍原始数据完整性 → 残缺则兜底中止
3. 规划无人机飞行巡检路径
4. 控制无人机采集路面影像（途中基础避障）
5. 【关卡2】校验采集影像有效性 → 残缺则兜底中止
6. 通过影像识别道路坑洼、裂缝等病害
7. 【关卡3】报告生成前最终校验 → 残缺则兜底中止
8. 汇总全部检测数据，生成道路检修报告并保存至项目文件

兜底原则：遇到残缺数据自动提示"数据不全，无法生成完整检修报告"，不瞎编结果。

运行方式：
    python src/main.py
"""
import os
import sys
import time

# 确保可导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import InspectionMission, MissionStatus
from path_planner import load_mission_from_file, plan_flight_path
from drone_controller import DroneController, load_obstacles_from_map
from defect_detector import analyze_all_frames
from report_generator import save_report
from data_generator import generate_aerial_raw, AERIAL_RAW_DIR
from data_validator import (validate_raw_data, validate_captured_frames,
                            validate_before_report, save_fallback_log,
                            print_fallback_banner, FALLBACK_MESSAGE)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MISSION_FILE = os.path.join(DATA_DIR, "mission_task.json")
ROAD_MAP_FILE = os.path.join(DATA_DIR, "road_map.json")


def run_inspection():
    """执行整套道路巡检流程（含数据完整性兜底）"""
    start_time = time.time()
    validation_reports = []  # 收集各关卡校验报告，兜底时一并写入日志
    print("=" * 70)
    print("  无人机道路巡检智能体 - 启动道路检修流程")
    print("=" * 70)

    # ===== 步骤1：接收巡检任务，读取巡检资料 =====
    print("\n【步骤1】接收道路巡检任务，读取项目内巡检资料...")
    if not os.path.exists(MISSION_FILE):
        print(f"[错误] 未找到巡检任务文件：{MISSION_FILE}")
        return None
    if not os.path.exists(ROAD_MAP_FILE):
        print(f"[错误] 未找到道路地图文件：{ROAD_MAP_FILE}")
        return None

    mission = load_mission_from_file(MISSION_FILE)
    print(f"[任务接收] 任务 {mission.mission_id}：{mission.road_name}")
    print(f"[任务接收] 道路等级：{mission.road_grade}，路宽 {mission.road_width_m}m，"
          f"飞行高度 {mission.flight_altitude}m")

    # 读取/生成航拍原始数据
    if not os.path.exists(os.path.join(AERIAL_RAW_DIR, "_index.json")):
        print("[数据准备] 未检测到航拍原始数据，开始生成模拟航拍数据...")
        generate_aerial_raw()
    else:
        print(f"[数据准备] 已读取项目内航拍原始数据：{AERIAL_RAW_DIR}")

    # ===== 关卡1：飞行前原始数据完整性校验 =====
    print("\n【关卡1】校验航拍原始数据完整性...")
    cp1 = validate_raw_data()
    validation_reports.append(cp1)
    if not cp1.passed:
        print_fallback_banner(cp1)
        log_path = save_fallback_log(mission, validation_reports)
        print(f"  校验失败日志已保存：{log_path}")
        mission.status = MissionStatus.ABORTED
        return None
    print(f"[关卡1] 通过：{cp1.checked_frames} 帧影像数据完整有效"
          f"（占比 {cp1.valid_ratio:.1%}）")

    # ===== 步骤2：规划飞行巡检路径 =====
    print("\n【步骤2】规划无人机飞行巡检路径...")
    mission = plan_flight_path(mission, ROAD_MAP_FILE)

    # ===== 步骤3：控制无人机飞行采集影像（含避障） =====
    print("\n【步骤3】控制无人机执行巡检飞行，采集路面影像...")
    obstacles = load_obstacles_from_map(ROAD_MAP_FILE)
    print(f"[避障准备] 已加载 {len(obstacles)} 个已知障碍物信息")
    controller = DroneController(mission, obstacles)
    mission = controller.execute_flight()

    # ===== 关卡2：采集后影像有效性校验 =====
    print("\n【关卡2】校验采集影像有效性...")
    cp2 = validate_captured_frames(mission)
    validation_reports.append(cp2)
    if not cp2.passed:
        print_fallback_banner(cp2)
        log_path = save_fallback_log(mission, validation_reports)
        print(f"  校验失败日志已保存：{log_path}")
        mission.status = MissionStatus.ABORTED
        return None
    print(f"[关卡2] 通过：{cp2.valid_frames}/{cp2.checked_frames} 帧采集影像有效"
          f"（覆盖率 {cp2.valid_ratio:.1%}）")

    # ===== 步骤4：影像病害识别 =====
    print("\n【步骤4】通过影像识别道路坑洼、裂缝等病害...")
    mission = analyze_all_frames(mission)

    # ===== 关卡3：报告生成前最终校验 =====
    print("\n【关卡3】报告生成前最终数据完整性校验...")
    cp3 = validate_before_report(mission)
    validation_reports.append(cp3)
    if not cp3.passed:
        print_fallback_banner(cp3)
        log_path = save_fallback_log(mission, validation_reports)
        print(f"  校验失败日志已保存：{log_path}")
        mission.status = MissionStatus.ABORTED
        return None
    print(f"[关卡3] 通过：全部 {cp3.valid_frames} 帧影像数据完整，可生成完整报告")

    # ===== 步骤5：汇总数据，生成检修报告并保存 =====
    print("\n【步骤5】汇总全部检测数据，生成道路检修报告...")
    paths = save_report(mission)

    # ===== 完成 =====
    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print("  道路巡检流程执行完成")
    print("=" * 70)
    print(f"  任务状态：{mission.status.value}")
    print(f"  采集影像：{len(mission.frames)} 帧")
    print(f"  识别病害：{len(mission.defects)} 处")
    critical = sum(1 for d in mission.defects if d.is_key_marked)
    print(f"  严重病害（重点标记）：{critical} 处")
    print(f"  避障绕行：{sum(1 for e in mission.avoidance_events if e.get('type') == 'detour')} 次")
    print(f"  检修报告：{paths['report_path']}")
    print(f"  结果数据：{paths['result_path']}")
    print(f"  耗时：{elapsed:.2f} 秒")
    print("=" * 70)

    return mission


if __name__ == "__main__":
    run_inspection()