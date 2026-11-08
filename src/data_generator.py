# -*- coding: utf-8 -*-
"""
航拍原始数据生成器
模拟无人机相机采集的路面影像原始数据，生成含病害像素特征的影像帧数据文件。
生成的数据保存至 data/aerial_raw/ 目录，供智能体读取分析。
"""
import json
import math
import os
import random
import time

# ---------- 配置 ----------
random.seed(20261106)  # 固定随机种子，保证数据可复现

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
AERIAL_RAW_DIR = os.path.join(DATA_DIR, "aerial_raw")

# 模拟影像网格尺寸（像素网格，每个单元格代表约0.5m×0.5m路面）
GRID_W = 24
GRID_H = 16

# 路面状态像素值编码
PIXEL_NORMAL = 0        # 正常路面
PIXEL_CRACK = 1         # 裂缝特征
PIXEL_POTHOLE = 2       # 坑洼特征
PIXEL_RUTTING = 3       # 车辙特征
PIXEL_SETTLEMENT = 4    # 沉陷特征
PIXEL_EDGE_BREAK = 5    # 边缘破损特征
PIXEL_SHADOW = 9        # 阴影/油污干扰（非病害）


def load_road_map():
    """读取道路地图资料"""
    with open(os.path.join(DATA_DIR, "road_map.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def load_mission():
    """读取巡检任务"""
    with open(os.path.join(DATA_DIR, "mission_task.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def lerp(a, b, t):
    return a + (b - a) * t


def point_on_centerline(centerline, t):
    """根据参数t(0~1)在道路中心线上插值取点"""
    n = len(centerline) - 1
    seg = min(int(t * n), n - 1)
    local_t = t * n - seg
    p1, p2 = centerline[seg], centerline[seg + 1]
    return {"lon": lerp(p1["lon"], p2["lon"], local_t),
            "lat": lerp(p1["lat"], p2["lat"], local_t)}


def make_crack_pattern(grid, start_x, start_y, length, direction="horizontal"):
    """在网格中绘制裂缝图案（线状暗纹）"""
    x, y = start_x, start_y
    for _ in range(length):
        if 0 <= x < GRID_W and 0 <= y < GRID_H:
            grid[y][x] = PIXEL_CRACK
        if direction == "horizontal":
            x += 1
            y += random.choice([-1, 0, 0, 1])  # 裂缝略带曲折
        elif direction == "vertical":
            y += 1
            x += random.choice([-1, 0, 0, 1])
        else:  # diagonal
            x += 1
            y += 1


def make_pothole_pattern(grid, cx, cy, radius):
    """在网格中绘制坑洼图案（块状暗斑）"""
    for y in range(max(0, cy - radius), min(GRID_H, cy + radius + 1)):
        for x in range(max(0, cx - radius), min(GRID_W, cx + radius + 1)):
            if math.sqrt((x - cx) ** 2 + (y - cy) ** 2) <= radius:
                grid[y][x] = PIXEL_POTHOLE


def make_rutting_pattern(grid, y_pos, width=2):
    """绘制车辙图案（纵向带状）"""
    for x in range(GRID_W):
        for dy in range(width):
            yy = y_pos + dy
            if 0 <= yy < GRID_H and grid[yy][x] == PIXEL_NORMAL:
                grid[yy][x] = PIXEL_RUTTING


def make_settlement_pattern(grid, cx, cy, radius):
    """绘制沉陷图案（大面积渐变暗斑）"""
    for y in range(max(0, cy - radius), min(GRID_H, cy + radius + 1)):
        for x in range(max(0, cx - radius), min(GRID_W, cx + radius + 1)):
            d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if d <= radius and grid[y][x] == PIXEL_NORMAL:
                grid[y][x] = PIXEL_SETTLEMENT


def add_noise(grid, noise_rate=0.02):
    """添加随机阴影/油污干扰噪声"""
    for y in range(GRID_H):
        for x in range(GRID_W):
            if grid[y][x] == PIXEL_NORMAL and random.random() < noise_rate:
                grid[y][x] = PIXEL_SHADOW


def generate_aerial_raw():
    """生成全部航拍原始影像帧数据"""
    os.makedirs(AERIAL_RAW_DIR, exist_ok=True)

    mission = load_mission()
    road_map = load_road_map()
    centerline = road_map["road_centerline"]
    suspect_zones = road_map["preliminary_survey"]["suspect_zones"]

    # 按拍摄间隔沿中心线生成影像帧
    road_length_m = 1200.0  # K0+000 ~ K1+200
    interval = mission["capture_interval_m"]
    num_frames = int(road_length_m / interval) + 1
    t_step = interval / road_length_m

    frames_meta = []
    base_time = time.mktime(time.strptime("2026-11-06 09:00:00", "%Y-%m-%d %H:%M:%S"))

    for i in range(num_frames):
        t = i * t_step
        pos = point_on_centerline(centerline, t)
        grid = [[PIXEL_NORMAL for _ in range(GRID_W)] for _ in range(GRID_H)]

        # 根据可疑区域注入病害特征（模拟真实路面病害分布）
        for sz in suspect_zones:
            # 计算该帧位置与可疑区域的距离
            dlat = (sz["lat"] - pos["lat"]) * 111320.0
            dlon = (sz["lon"] - pos["lon"]) * 111320.0 * math.cos(math.radians(pos["lat"]))
            dist_m = math.sqrt(dlat ** 2 + dlon ** 2)
            if dist_m < 15.0:  # 帧覆盖范围内存在可疑区域
                # 根据可疑区域注解决定病害类型
                note = sz["note"]
                cx = random.randint(6, GRID_W - 7)
                cy = random.randint(4, GRID_H - 5)
                if "坑洼" in note:
                    r = random.choice([2, 3])  # 严重坑洼更大
                    make_pothole_pattern(grid, cx, cy, r)
                    if r >= 3 and random.random() < 0.5:
                        make_pothole_pattern(grid, cx + 4, cy + 2, 2)
                elif "裂缝" in note:
                    make_crack_pattern(grid, cx, cy, random.randint(8, 14), "horizontal")
                elif "龟裂" in note:
                    # 龟裂 = 多条交织裂缝
                    for _ in range(5):
                        sx = random.randint(4, GRID_W - 8)
                        sy = random.randint(3, GRID_H - 6)
                        make_crack_pattern(grid, sx, sy, random.randint(4, 8),
                                           random.choice(["horizontal", "vertical", "diagonal"]))
                elif "沉陷" in note:
                    make_settlement_pattern(grid, cx, cy, random.choice([3, 4]))

        # 随机注入额外轻度病害（模拟巡检新发现）
        if random.random() < 0.25:
            cx = random.randint(3, GRID_W - 4)
            cy = random.randint(2, GRID_H - 3)
            r = random.choice([1, 1, 2])
            make_pothole_pattern(grid, cx, cy, r)
        if random.random() < 0.3:
            sx = random.randint(2, GRID_W // 2)
            sy = random.randint(2, GRID_H - 4)
            make_crack_pattern(grid, sx, sy, random.randint(3, 7), "vertical")
        if random.random() < 0.15:
            make_rutting_pattern(grid, random.randint(3, GRID_H - 5), 1)
        if random.random() < 0.1:
            # 边缘破损
            ex = random.randint(0, 3)
            for yy in range(random.randint(2, 5)):
                if yy < GRID_H:
                    grid[yy][ex] = PIXEL_EDGE_BREAK

        add_noise(grid)

        frame_id = f"FRM-{i:04d}"
        frame_data = {
            "frame_id": frame_id,
            "mission_id": mission["mission_id"],
            "waypoint_index": i,
            "position": {"lon": round(pos["lon"], 7), "lat": round(pos["lat"], 7),
                         "alt": mission["flight_altitude"]},
            "timestamp": round(base_time + i * 3.5, 3),  # 每3.5秒拍摄一帧
            "grid_size": {"width": GRID_W, "height": GRID_H},
            "pixel_scale_m": 0.5,  # 每像素格代表的实际路面尺寸
            "image_data": grid,
            "camera": {"model": "SIM-CAM-4K", "fov_deg": 84, "gimbal_pitch": -90},
        }
        file_path = os.path.join(AERIAL_RAW_DIR, f"{frame_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(frame_data, f, ensure_ascii=False, indent=1)

        frames_meta.append({
            "frame_id": frame_id,
            "file": f"{frame_id}.json",
            "position": frame_data["position"],
            "timestamp": frame_data["timestamp"],
        })
        print(f"[数据生成] {frame_id} @ ({pos['lon']:.6f}, {pos['lat']:.6f}) 已保存")

    # 保存影像帧索引清单
    index_path = os.path.join(AERIAL_RAW_DIR, "_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({"total_frames": len(frames_meta), "frames": frames_meta}, f,
                  ensure_ascii=False, indent=1)

    print(f"\n[数据生成] 完成：共生成 {len(frames_meta)} 帧航拍原始影像数据，保存于 {AERIAL_RAW_DIR}")
    return len(frames_meta)


if __name__ == "__main__":
    generate_aerial_raw()