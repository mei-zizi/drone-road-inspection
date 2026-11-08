/**
 * 无人机道路检修智能体 - 配置与数据模型
 */
const Config = {
  // 道路参数（单位：米）
  road: {
    length: 600,        // 道路长度
    width: 14,          // 道路宽度（双向四车道简化）
    laneCount: 4,
  },
  // 无人机参数
  drone: {
    cruiseSpeed: 8,       // 巡航速度 m/s
    altitude: 30,         // 巡航高度 m
    turnSpeed: 4,         // 转弯速度
    batteryCapacity: 100, // 电量 %
    batteryDrainRate: 0.15, // 每秒耗电 %
    safeBattery: 20,      // 安全返航电量
  },
  // 相机参数
  camera: {
    fovH: 78,    // 水平视场角（度）
    fovV: 62,    // 垂直视场角（度）
    footprintWidth: 24,  // 地面覆盖宽度 m（由高度和FOV计算，此处直接给定）
    footprintLength: 18, // 地面覆盖长度 m
    overlap: 0.6,        // 影像重叠率
  },
  // 巡检参数
  inspection: {
    captureInterval: 7.2, // 采图间隔 m = footprintLength * (1-overlap)
    lateralStep: 9.6,     // 侧向步距 m = footprintWidth * (1-overlap)
    gpsError: 0.8,        // GPS 定位误差 m
    detectionConfidenceBase: 0.85, // 基础识别置信度
  },
  // 避障参数
  obstacle: {
    detectionRange: 45,   // 前方探测距离 m
    safetyMargin: 6,      // 安全绕行余量 m
  },
  // 仿真参数
  sim: {
    tickRate: 30,         // 每秒仿真帧数
    baseSpeedMultiplier: 1, // 基础速度倍率
  },
  // 病害类型定义
  damageTypes: {
    pothole:    { label: '坑洼',   color: '#f85149', icon: '⬤' },
    crack:      { label: '裂缝',   color: '#bc8cff', icon: '╱' },
    rut:        { label: '车辙',   color: '#db6d28', icon: '━' },
    settlement: { label: '沉降',   color: '#d29922', icon: '◐' },
  },
  // 严重程度定义
  severities: {
    minor:    { label: '轻微', color: '#3fb950', threshold: 0.3 },
    moderate: { label: '中等', color: '#d29922', threshold: 0.6 },
    severe:   { label: '严重', color: '#f85149', threshold: 1.01 },
  },
};

/** 工具函数 */
const Utils = {
  /** 两点间距离 */
  dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); },
  /** 两点间角度 */
  angle(a, b) { return Math.atan2(b.y - a.y, b.x - a.x); },
  /** 线性插值 */
  lerp(a, b, t) { return a + (b - a) * t; },
  /** 点到线段的距离及最近点 */
  pointToSegment(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y;
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return { dist: Utils.dist(p, a), nearest: a };
    let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq;
    t = Math.max(0, Math.min(1, t));
    const nearest = { x: a.x + t * dx, y: a.y + t * dy };
    return { dist: Utils.dist(p, nearest), nearest };
  },
  /** 线段是否与圆相交 */
  segmentIntersectsCircle(a, b, center, radius) {
    return Utils.pointToSegment(center, a, b).dist < radius;
  },
  /** 生成 [min, max] 范围随机数 */
  rand(min, max) { return min + Math.random() * (max - min); },
  /** 生成随机整数 */
  randInt(min, max) { return Math.floor(Utils.rand(min, max + 1)); },
  /** 从数组随机取一个 */
  pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; },
  /** 坐标格式化（模拟GPS：基准点+偏移） */
  formatGPS(x, y) {
    const baseLat = 39.9042, baseLon = 116.4074; // 模拟北京坐标基准
    const lat = baseLat + (y / 111320);
    const lon = baseLon + (x / (111320 * Math.cos(baseLat * Math.PI / 180)));
    return `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  },
  /** 时间格式化 */
  formatTime(seconds) {
    const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  },
  /** 唯一ID */
  uid(prefix) { return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`; },
};

/** 巡检任务模型 */
class InspectionTask {
  constructor(opts = {}) {
    this.taskId = opts.taskId || Utils.uid('TASK');
    this.roadName = opts.roadName || '示范路段';
    this.roadLength = opts.roadLength || Config.road.length;
    this.roadWidth = opts.roadWidth || Config.road.width;
    this.startTime = null;
    this.endTime = null;
    this.status = 'pending'; // pending | running | completed
  }
}

/** 病害记录模型 */
class DamageRecord {
  constructor(type, severity, x, y, size, confidence, imageId) {
    this.recordId = Utils.uid('DMG');
    this.type = type;             // pothole | crack | rut | settlement
    this.severity = severity;     // minor | moderate | severe
    this.x = x;                   // 病害坐标X（含GPS误差）
    this.y = y;                   // 病害坐标Y
    this.trueX = x;               // 真实坐标（仿真用）
    this.trueY = y;
    this.size = size;             // 估算尺寸 m
    this.confidence = confidence; // 识别置信度
    this.imageId = imageId;       // 关联影像ID
    this.timestamp = new Date();
    this.isPriority = severity === 'severe'; // 严重病害重点标记
  }
  get typeLabel() { return Config.damageTypes[this.type].label; }
  get severityLabel() { return Config.severities[this.severity].label; }
  get color() { return Config.damageTypes[this.type].color; }
  get gpsString() { return Utils.formatGPS(this.x, this.y); }
}

/** 无人机状态模型 */
class DroneState {
  constructor() {
    this.x = 0; this.y = 0;
    this.altitude = 0;
    this.heading = 0;          // 航向角（弧度）
    this.battery = Config.drone.batteryCapacity;
    this.state = 'idle';       // idle | taking_off | flying | capturing | avoiding | landing | done
    this.speed = 0;
    this.totalDistance = 0;
    this.flightTime = 0;
    this.imagesCaptured = 0;
  }
  get batteryPercent() { return Math.max(0, this.battery); }
  get isLowBattery() { return this.battery <= Config.drone.safeBattery; }
  reset() {
    Object.assign(this, new DroneState());
  }
}

/** 航点模型 */
class Waypoint {
  constructor(x, y, action = 'fly') {
    this.x = x; this.y = y;
    this.action = action; // fly | capture | takeoff | land
    this.visited = false;
    this.captureDone = false;
  }
}