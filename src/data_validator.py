# -*- coding: utf-8 -*-
"""
航拍数据完整性校验模块（兜底机制）
在巡检流程关键节点校验航拍数据完整性，遇到残缺数据时触发兜底：
自动提示"数据不全，无法生成完整检修报告"，中止流程，不瞎编结果。

三道校验关卡：
  关卡1（飞行前）：校验航拍原始数据目录/索引/帧文件的结构完整性
  关卡2（采集后）：校验无人机实际采集影像的有效性
  关卡3（报告前）：报告生成前的最终数据完整性确认
"""
import json
import os
import time
from typing import List, Optional

from models import InspectionMission

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
AERIAL_RAW_DIR = os.path.join(DATA_DIR, "aerial_raw")
RESULTS_DIR = os.path.join(PROJECT_ROOT, "output", "inspection_results")

# ---------- 校验阈值 ----------
# 有效帧占比低于此值判定为数据残缺，触发兜底中止
MIN_VALID_RATIO = 0.85
# 影像网格最小行数（低于此值视为无效影像数据）
MIN_GRID_ROWS = 4
# 单帧影像网格最小列数
MIN_GRID_COLS = 4

# 兜底提示语（统一口径，不瞎编结果）
FALLBACK_MESSAGE = "数据不全，无法生成完整检修报告"


class IntegrityReport:
    """数据完整性校验报告"""

    def __init__(self, checkpoint: str):
        self.checkpoint = checkpoint            # 校验关卡名称
        self.passed = True                      # 是否通过
        self.errors: List[str] = []             # 致命问题（触发兜底中止）
        self.warnings: List[str] = []           # 警告（记录但不中止）
        self.checked_frames = 0                 # 检查的帧总数
        self.valid_frames = 0                   # 有效帧数
        self.missing_files: List[str] = []      # 缺失文件帧ID
        self.corrupt_frames: List[str] = []     # 损坏/无法解析帧ID
        self.empty_frames: List[str] = []       # 影像数据为空帧ID
        self.valid_ratio = 1.0                  # 有效帧占比
        self.checked_at = time.time()           # 校验时间

    def fail(self, message: str):
        """记录致命问题"""
        self.passed = False
        self.errors.append(message)

    def warn(self, message: str):
        """记录警告"""
        self.warnings.append(message)

    def to_dict(self) -> dict:
        return {
            "checkpoint": self.checkpoint,
            "passed": self.passed,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.checked_at)),
            "errors": self.errors,
            "warnings": self.warnings,
            "checked_frames": self.checked_frames,
            "valid_frames": self.valid_frames,
            "valid_ratio": round(self.valid_ratio, 4),
            "missing_files": self.missing_files,
            "corrupt_frames": self.corrupt_frames,
            "empty_frames": self.empty_frames,
        }


def _is_valid_grid(grid) -> bool:
    """判断影像网格数据是否有效"""
    if not grid or not isinstance(grid, list):
        return False
    if len(grid) < MIN_GRID_ROWS:
        return False
    first_row = grid[0]
    if not first_row or not isinstance(first_row, list) or len(first_row) < MIN_GRID_COLS:
        return False
    return True


# ==================== 关卡1：飞行前原始数据校验 ====================

def validate_raw_data(aerial_raw_dir: Optional[str] = None) -> IntegrityReport:
    """
    校验航拍原始数据目录的结构完整性：
    - 目录与索引文件是否存在且可解析
    - 索引引用的每个帧文件是否存在、可解析、影像数据有效
    - 有效帧占比是否达标
    """
    report = IntegrityReport("关卡1-飞行前原始数据校验")
    aerial_dir = aerial_raw_dir or AERIAL_RAW_DIR

    # 1. 目录存在性
    if not os.path.isdir(aerial_dir):
        report.fail(f"航拍原始数据目录不存在：{aerial_dir}")
        return report

    # 2. 索引文件存在性与可解析性
    index_path = os.path.join(aerial_dir, "_index.json")
    if not os.path.exists(index_path):
        report.fail(f"航拍数据索引文件缺失：{index_path}")
        return report
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception as e:
        report.fail(f"航拍数据索引文件损坏，无法解析：{e}")
        return report

    frames_meta = index.get("frames", [])
    if not frames_meta or not isinstance(frames_meta, list):
        report.fail("航拍数据索引为空或格式异常，无任何影像帧记录")
        return report

    # 3. 逐帧校验
    for fm in frames_meta:
        report.checked_frames += 1
        frame_id = fm.get("frame_id", "未知帧")
        frame_file = fm.get("file", "")

        # 帧文件存在性
        if not frame_file:
            report.missing_files.append(frame_id)
            continue
        frame_path = os.path.join(aerial_dir, frame_file)
        if not os.path.exists(frame_path):
            report.missing_files.append(frame_id)
            continue

        # 帧文件可解析性
        try:
            with open(frame_path, "r", encoding="utf-8") as f:
                frame_data = json.load(f)
        except Exception:
            report.corrupt_frames.append(frame_id)
            continue

        # 影像数据有效性
        grid = frame_data.get("image_data")
        if not _is_valid_grid(grid):
            report.empty_frames.append(frame_id)
            continue

        report.valid_frames += 1

    # 4. 有效帧占比
    report.valid_ratio = (report.valid_frames / report.checked_frames
                          if report.checked_frames else 0.0)

    # 5. 汇总判定
    if report.missing_files:
        shown = ", ".join(report.missing_files[:5])
        suffix = " 等" if len(report.missing_files) > 5 else ""
        report.fail(f"缺失影像帧文件 {len(report.missing_files)} 个（{shown}{suffix}），"
                    f"对应路段无航拍数据覆盖")
    if report.corrupt_frames:
        shown = ", ".join(report.corrupt_frames[:5])
        suffix = " 等" if len(report.corrupt_frames) > 5 else ""
        report.fail(f"影像帧文件损坏无法解析 {len(report.corrupt_frames)} 个（{shown}{suffix}）")
    if report.empty_frames:
        shown = ", ".join(report.empty_frames[:5])
        suffix = " 等" if len(report.empty_frames) > 5 else ""
        report.fail(f"影像帧数据为空或格式异常 {len(report.empty_frames)} 个（{shown}{suffix}）")

    if report.checked_frames > 0 and report.valid_ratio < MIN_VALID_RATIO:
        report.fail(f"有效影像帧占比仅 {report.valid_ratio:.1%}"
                    f"（{report.valid_frames}/{report.checked_frames}），"
                    f"低于最低要求 {MIN_VALID_RATIO:.0%}，数据残缺")

    if report.passed and report.warnings:
        pass  # 仅有警告时通过

    return report


# ==================== 关卡2：采集后影像有效性校验 ====================

def validate_captured_frames(mission: InspectionMission) -> IntegrityReport:
    """
    校验无人机实际采集的影像帧有效性：
    - 是否采集到影像
    - 每帧影像数据是否有效
    - 有效帧占拍摄航点比例是否达标
    """
    report = IntegrityReport("关卡2-采集后影像有效性校验")

    if not mission.waypoints:
        report.fail("无规划航点，巡检任务异常")
        return report

    capture_waypoints = [w for w in mission.waypoints if w.action == "capture"]
    if not capture_waypoints:
        report.fail("无拍摄航点，无法采集影像")
        return report

    if not mission.frames:
        report.fail("巡检飞行未采集到任何影像帧，无法进行病害分析")
        return report

    # 逐帧校验采集影像
    for frame in mission.frames:
        report.checked_frames += 1
        if _is_valid_grid(frame.image_data):
            report.valid_frames += 1
        else:
            report.empty_frames.append(frame.frame_id)

    # 有效帧占拍摄航点比例
    expected = len(capture_waypoints)
    report.valid_ratio = report.valid_frames / expected if expected else 0.0

    if report.empty_frames:
        shown = ", ".join(report.empty_frames[:5])
        suffix = " 等" if len(report.empty_frames) > 5 else ""
        report.fail(f"采集影像中 {len(report.empty_frames)} 帧无有效数据"
                    f"（{shown}{suffix}），对应位置路面情况未知")

    if report.valid_ratio < MIN_VALID_RATIO:
        report.fail(f"有效采集覆盖率仅 {report.valid_ratio:.1%}"
                    f"（{report.valid_frames}/{expected} 拍摄航点），"
                    f"低于最低要求 {MIN_VALID_RATIO:.0%}，存在巡检盲区")

    return report


# ==================== 关卡3：报告生成前最终校验 ====================

def validate_before_report(mission: InspectionMission) -> IntegrityReport:
    """
    报告生成前的最终数据完整性确认：
    - 影像帧与分析结果是否齐全
    - 全部帧是否均被有效分析
    """
    report = IntegrityReport("关卡3-报告生成前最终校验")

    if not mission.frames:
        report.fail("无采集影像数据，无法生成检修报告")
        return report

    # 确认影像数据有效性
    valid = 0
    empty_ids = []
    for f in mission.frames:
        if _is_valid_grid(f.image_data):
            valid += 1
        else:
            empty_ids.append(f.frame_id)

    report.checked_frames = len(mission.frames)
    report.valid_frames = valid
    report.valid_ratio = valid / len(mission.frames) if mission.frames else 0.0

    if empty_ids:
        shown = ", ".join(empty_ids[:5])
        suffix = " 等" if len(empty_ids) > 5 else ""
        report.fail(f"仍有 {len(empty_ids)} 帧影像无有效数据（{shown}{suffix}），"
                    f"对应路段病害情况无法确认，不可生成完整报告")

    if report.valid_ratio < MIN_VALID_RATIO:
        report.fail(f"有效影像数据占比仅 {report.valid_ratio:.1%}，"
                    f"{FALLBACK_MESSAGE}")

    return report


# ==================== 兜底：保存校验失败日志 ====================

def save_fallback_log(mission: InspectionMission, reports: List[IntegrityReport]) -> str:
    """
    兜底触发时保存校验失败日志至项目文件，记录失败原因，
    便于排查数据问题，同时留痕证明未生成报告的原因。
    """
    os.makedirs(RESULTS_DIR, exist_ok=True)
    log_data = {
        "mission_id": mission.mission_id,
        "road_name": mission.road_name,
        "fallback_message": FALLBACK_MESSAGE,
        "report_generated": False,
        "triggered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "validation_reports": [r.to_dict() for r in reports],
    }
    log_path = os.path.join(RESULTS_DIR, f"{mission.mission_id}_数据校验失败日志.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=1)
    return log_path


def print_fallback_banner(report: IntegrityReport):
    """打印兜底提示横幅"""
    print("\n" + "!" * 70)
    print(f"  ⚠ {FALLBACK_MESSAGE}")
    print("!" * 70)
    print(f"  校验关卡：{report.checkpoint}")
    for err in report.errors:
        print(f"  ✗ {err}")
    for warn in report.warnings:
        print(f"  ⚠ {warn}")
    print(f"  已校验帧数：{report.checked_frames}，有效帧数：{report.valid_frames}，"
          f"有效占比：{report.valid_ratio:.1%}")
    print("  智能体已中止流程，未生成检修报告，不基于残缺数据输出结果。")
    print("!" * 70)