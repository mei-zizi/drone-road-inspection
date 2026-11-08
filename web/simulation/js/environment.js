/**
 * 环境仿真模块 - 模拟道路、路面病害、飞行障碍物
 * 坐标系：x 为道路横向（-width/2 ~ width/2），y 为道路纵向（0 ~ length）
 */
class RoadEnvironment {
  constructor(task) {
    this.task = task;
    this.roadLength = task.roadLength;
    this.roadWidth = task.roadWidth;
    this.groundTruthDamage = [];  // 真实病害（地面真值）
    this.obstacles = [];          // 飞行障碍物
    this.scannedAreas = [];       // 已巡检覆盖区域
    this.generate();
  }

  /** 生成仿真环境：病害 + 障碍物 */
  generate() {
    this.generateDamage();
    this.generateObstacles();
  }

  /** 生成路面病害（地面真值） */
  generateDamage() {
    this.groundTruthDamage = [];
    const count = Utils.randInt(18, 28);
    const types = Object.keys(Config.damageTypes);
    for (let i = 0; i < count; i++) {
      const type = Utils.pick(types);
      const x = Utils.rand(-this.roadWidth / 2 + 1, this.roadWidth / 2 - 1);
      const y = Utils.rand(10, this.roadLength - 10);
      // 根据病害类型确定尺寸与严重度
      let size, severityScore;
      switch (type) {
        case 'pothole':
          size = Utils.rand(0.2, 1.6);
          severityScore = size / 1.6;
          break;
        case 'crack':
          size = Utils.rand(1, 10); // 裂缝长度
          severityScore = Utils.rand(0.1, 1.0);
          break;
        case 'rut':
          size = Utils.rand(3, 15); // 车辙长度
          severityScore = Utils.rand(0.2, 0.9);
          break;
        case 'settlement':
          size = Utils.rand(2, 8); // 沉降区域直径
          severityScore = Utils.rand(0.4, 1.0);
          break;
      }
      const severity = this.classifySeverity(severityScore);
      this.groundTruthDamage.push({
        id: Utils.uid('GT'),
        type, severity, x, y, size,
        severityScore,
        detected: false,   // 是否已被识别
        orientation: Utils.rand(0, Math.PI * 2), // 裂缝/车辙方向
      });
    }
  }

  /** 严重度分级 */
  classifySeverity(score) {
    if (score >= 0.65) return 'severe';
    if (score >= 0.35) return 'moderate';
    return 'minor';
  }

  /** 生成飞行障碍物（树木、电线杆、广告牌、飞鸟等） */
  generateObstacles() {
    this.obstacles = [];
    const count = Utils.randInt(5, 9);
    const kinds = [
      { kind: 'tree',    label: '树木',   icon: '🌳', radiusMin: 4, radiusMax: 7 },
      { kind: 'pole',    label: '电线杆', icon: '🗼', radiusMin: 3, radiusMax: 5 },
      { kind: 'sign',    label: '广告牌', icon: '🪧', radiusMin: 5, radiusMax: 8 },
      { kind: 'bird',    label: '飞鸟',   icon: '🐦', radiusMin: 3, radiusMax: 5 },
      { kind: 'crane',   label: '吊车',   icon: '🏗️', radiusMin: 6, radiusMax: 10 },
    ];
    for (let i = 0; i < count; i++) {
      const k = Utils.pick(kinds);
      // 障碍物分布在道路两侧或上方
      const side = Math.random() > 0.5 ? 1 : -1;
      const x = side * Utils.rand(this.roadWidth / 2 + 2, this.roadWidth / 2 + 18);
      const y = Utils.rand(30, this.roadLength - 30);
      const radius = Utils.rand(k.radiusMin, k.radiusMax);
      this.obstacles.push({
        id: Utils.uid('OBS'),
        kind: k.kind, label: k.label, icon: k.icon,
        x, y, radius,
        altitude: k.kind === 'bird' ? Utils.rand(20, 40) : Utils.rand(0, 15),
      });
    }
  }

  /** 获取相机覆盖范围内的病害（模拟影像识别的输入） */
  getDamageInFootprint(cx, cy, fw, fl, heading) {
    // 简化处理：以相机中心为矩形中心（航向对齐道路方向）
    const halfW = fw / 2, halfL = fl / 2;
    return this.groundTruthDamage.filter(d => {
      return Math.abs(d.x - cx) <= halfW && Math.abs(d.y - cy) <= halfL;
    });
  }

  /** 标记病害为已检测 */
  markDetected(damageId) {
    const d = this.groundTruthDamage.find(g => g.id === damageId);
    if (d) d.detected = true;
  }

  /** 记录已巡检覆盖区域 */
  addScannedArea(cx, cy, fw, fl) {
    this.scannedAreas.push({ cx, cy, fw, fl, time: Date.now() });
  }

  /** 检测某线段路径上的最近障碍物 */
  findObstacleOnPath(from, to, extraMargin = 0) {
    const range = Config.obstacle.detectionRange;
    let closest = null, closestDist = Infinity;
    for (const obs of this.obstacles) {
      // 只考虑探测范围内的障碍物
      const distToFrom = Utils.dist(from, obs);
      if (distToFrom > range + obs.radius) continue;
      const clearance = obs.radius + Config.obstacle.safetyMargin + extraMargin;
      if (Utils.segmentIntersectsCircle(from, to, obs, clearance)) {
        if (distToFrom < closestDist) {
          closest = obs;
          closestDist = distToFrom;
        }
      }
    }
    return closest;
  }

  /** 获取统计：真实病害数量（按严重度） */
  getGroundTruthStats() {
    const stats = { total: this.groundTruthDamage.length, minor: 0, moderate: 0, severe: 0 };
    for (const d of this.groundTruthDamage) stats[d.severity]++;
    return stats;
  }
}