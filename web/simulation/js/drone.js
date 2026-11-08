/**
 * 无人机控制与影像识别模块
 * DroneController: 飞行控制、航点跟踪、影像采集触发
 * ImageAnalyzer: 影像病害识别、严重度分级、重点标记
 */

/** 影像识别模块（模拟AI识别引擎） */
class ImageAnalyzer {
  constructor() {
    this.imageCounter = 0;
  }

  /**
   * 分析单帧影像，识别路面病害
   * @param {number} cx 相机中心X
   * @param {number} cy 相机中心Y
   * @param {RoadEnvironment} env 环境仿真
   * @returns {{ imageId: string, records: DamageRecord[] }}
   */
  analyze(cx, cy, env) {
    this.imageCounter++;
    const imageId = `IMG_${String(this.imageCounter).padStart(4, '0')}`;
    const fw = Config.camera.footprintWidth;
    const fl = Config.camera.footprintLength;

    // 获取该帧覆盖范围内的真实病害
    const candidates = env.getDamageInFootprint(cx, cy, fw, fl, 0);
    const records = [];

    for (const gt of candidates) {
      if (gt.detected) continue; // 已识别过的不再重复记录

      // 检测概率：病害越大、越严重越容易被识别
      const sizeFactor = Math.min(1, gt.size / 2.0);
      const sevFactor = gt.severity === 'severe' ? 1.0 : gt.severity === 'moderate' ? 0.9 : 0.75;
      const detectProb = Config.inspection.detectionConfidenceBase * (0.5 + 0.5 * sizeFactor) * sevFactor;

      if (Math.random() > detectProb) continue; // 漏检

      // 模拟GPS定位误差
      const gpsErr = Config.inspection.gpsError;
      const detX = gt.x + Utils.rand(-gpsErr, gpsErr);
      const detY = gt.y + Utils.rand(-gpsErr, gpsErr);

      // 识别置信度（含随机波动）
      const confidence = Math.min(0.99, detectProb + Utils.rand(-0.08, 0.08));

      const record = new DamageRecord(
        gt.type, gt.severity, detX, detY, gt.size, confidence, imageId
      );
      record.trueX = gt.x;
      record.trueY = gt.y;
      records.push(record);
      env.markDetected(gt.id);
    }

    // 小概率误检（假阳性）
    if (Math.random() < 0.06 && records.length < 3) {
      const falseType = Utils.pick(Object.keys(Config.damageTypes));
      const fx = cx + Utils.rand(-fw / 3, fw / 3);
      const fy = cy + Utils.rand(-fl / 3, fl / 3);
      const record = new DamageRecord(
        falseType, 'minor', fx, fy, Utils.rand(0.2, 0.6), Utils.rand(0.5, 0.65), imageId
      );
      record.isFalsePositive = true;
      records.push(record);
    }

    return { imageId, records };
  }

  reset() { this.imageCounter = 0; }
}

/** 无人机飞行控制器 */
class DroneController {
  constructor(env, imageAnalyzer, callbacks = {}) {
    this.env = env;
    this.analyzer = imageAnalyzer;
    this.callbacks = callbacks; // onLog, onDamageDetected, onObstacleAvoided, onStateChange, onCapture
    this.state = new DroneState();
    this.waypoints = [];
    this.currentWpIndex = 0;
    this.detourQueue = [];       // 避障插入的临时航点队列
    this.currentTarget = null;   // 当前飞向的目标点
    this.captureHoldTimer = 0;   // 采图悬停计时
    this.takeoffTimer = 0;
    this.landingTimer = 0;
    this.obstacleAvoidance = new ObstacleAvoidance();
    this.avoidCount = 0;
    this.isRunning = false;
    this.isPaused = false;
  }

  /** 加载航线 */
  loadRoute(waypoints) {
    this.waypoints = waypoints;
    this.currentWpIndex = 0;
    this.detourQueue = [];
    // 初始位置为起飞点
    const takeoffWp = waypoints[0];
    this.state.x = takeoffWp.x;
    this.state.y = takeoffWp.y;
    this.state.altitude = 0;
  }

  /** 开始飞行 */
  start() {
    this.isRunning = true;
    this.isPaused = false;
    this.setState('taking_off');
    this.takeoffTimer = 0;
    this.log('info', '无人机起飞中...爬升至巡航高度 30m');
  }

  /** 暂停/恢复 */
  pause() { this.isPaused = true; this.log('warn', '巡检已暂停'); }
  resume() { this.isPaused = false; this.log('info', '巡检恢复'); }

  /** 主更新循环 */
  update(dt) {
    if (!this.isRunning || this.isPaused) return;
    const s = this.state;

    // 电量消耗
    s.battery -= Config.drone.batteryDrainRate * dt;
    s.flightTime += dt;

    switch (s.state) {
      case 'taking_off':
        this.updateTakeoff(dt);
        break;
      case 'flying':
        this.updateFlying(dt);
        break;
      case 'capturing':
        this.updateCapturing(dt);
        break;
      case 'landing':
        this.updateLanding(dt);
        break;
    }
  }

  updateTakeoff(dt) {
    const s = this.state;
    this.takeoffTimer += dt;
    // 3秒爬升到巡航高度
    s.altitude = Math.min(Config.drone.altitude, (this.takeoffTimer / 3) * Config.drone.altitude);
    if (this.takeoffTimer >= 3) {
      s.altitude = Config.drone.altitude;
      this.log('ok', `已到达巡航高度 ${Config.drone.altitude}m，开始执行巡检航线`);
      this.setState('flying');
      this.advanceToNextTarget();
    }
  }

  /** 推进到下一个目标航点（含避障检查） */
  advanceToNextTarget() {
    const s = this.state;

    // 优先消费避障临时航点
    if (this.detourQueue.length > 0) {
      this.currentTarget = this.detourQueue.shift();
      return;
    }

    if (this.currentWpIndex >= this.waypoints.length) {
      this.setState('landing');
      this.landingTimer = 0;
      this.log('info', '航线执行完毕，无人机降落中...');
      return;
    }

    const wp = this.waypoints[this.currentWpIndex];

    // 避障检查：当前位置到目标航点
    const from = { x: s.x, y: s.y };
    const to = { x: wp.x, y: wp.y };
    const route = this.obstacleAvoidance.planSafeRoute(from, to, this.env);

    if (route.length > 1) {
      // 需要绕行
      const obstacleInfo = this.env.findObstacleOnPath(from, to);
      if (obstacleInfo) {
        this.avoidCount++;
        this.log('warn', `⚠ 避障触发：前方检测到${obstacleInfo.label}(${obstacleInfo.icon})，` +
          `距离${Utils.dist(from, obstacleInfo).toFixed(0)}m，计算绕行航线 #${this.avoidCount}`);
        if (this.callbacks.onObstacleAvoided) {
          this.callbacks.onObstacleAvoided(obstacleInfo, this.avoidCount);
        }
      }
      // route 的最后一个是原目标航点，前面的都是绕行点
      const detours = route.slice(0, -1);
      this.detourQueue.push(...detours);
      this.currentTarget = this.detourQueue.shift();
    } else {
      this.currentTarget = wp;
    }
  }

  updateFlying(dt) {
    const s = this.state;
    if (!this.currentTarget) { this.advanceToNextTarget(); return; }

    const target = this.currentTarget;
    const d = Utils.dist(s, target);
    const speed = Config.drone.cruiseSpeed * dt;

    if (d <= speed) {
      // 到达目标点
      s.x = target.x; s.y = target.y;
      s.totalDistance += d;
      s.speed = 0;

      // 判断目标点类型
      if (target.action === 'capture' && !target.captureDone) {
        target.captureDone = true;
        this.setState('capturing');
        this.captureHoldTimer = 0;
      } else if (target.action === 'land') {
        this.setState('landing');
        this.landingTimer = 0;
        this.log('info', '到达降落点，无人机降落中...');
      } else {
        // 普通飞行航点或避障点，推进下一个
        // 如果当前目标来自主航线，推进索引
        if (this.detourQueue.length === 0 && this.waypoints[this.currentWpIndex] === target) {
          this.currentWpIndex++;
        }
        this.currentTarget = null;
        this.advanceToNextTarget();
      }
      return;
    }

    // 朝目标移动
    const ang = Utils.angle(s, target);
    s.x += Math.cos(ang) * speed;
    s.y += Math.sin(ang) * speed;
    s.heading = ang;
    s.totalDistance += speed;
    s.speed = Config.drone.cruiseSpeed;
  }

  updateCapturing(dt) {
    const s = this.state;
    this.captureHoldTimer += dt;
    // 悬停0.4秒完成采图
    if (this.captureHoldTimer >= 0.4) {
      this.performCapture();
      // 推进到下一个航点
      if (this.waypoints[this.currentWpIndex] === this.currentTarget) {
        this.currentWpIndex++;
      }
      this.currentTarget = null;
      this.setState('flying');
      this.advanceToNextTarget();
    }
  }

  /** 执行影像采集与识别 */
  performCapture() {
    const s = this.state;
    s.imagesCaptured++;
    const { imageId, records } = this.analyzer.analyze(s.x, s.y, this.env);

    // 记录已巡检覆盖区域
    this.env.addScannedArea(s.x, s.y, Config.camera.footprintWidth, Config.camera.footprintLength);

    if (this.callbacks.onCapture) {
      this.callbacks.onCapture(imageId, s.x, s.y);
    }

    if (records.length > 0) {
      for (const rec of records) {
        const priorityStr = rec.isPriority ? ' ⚠️[严重-重点标记]' : '';
        this.log(rec.isPriority ? 'alert' : 'ok',
          `影像 ${imageId} 识别到${rec.typeLabel}(${rec.severityLabel})` +
          ` 尺寸≈${rec.size.toFixed(1)}m 置信度${(rec.confidence * 100).toFixed(0)}%` +
          ` 坐标(${rec.gpsString})${priorityStr}`);
      }
      if (this.callbacks.onDamageDetected) {
        this.callbacks.onDamageDetected(records);
      }
    }
  }

  updateLanding(dt) {
    const s = this.state;
    this.landingTimer += dt;
    s.altitude = Math.max(0, Config.drone.altitude - (this.landingTimer / 3) * Config.drone.altitude);
    s.speed = 0;
    if (this.landingTimer >= 3) {
      s.altitude = 0;
      this.setState('done');
      this.isRunning = false;
      this.log('ok', '🛬 无人机已安全降落，巡检任务完成');
      if (this.callbacks.onComplete) this.callbacks.onComplete();
    }
  }

  setState(newState) {
    this.state.state = newState;
    if (this.callbacks.onStateChange) {
      this.callbacks.onStateChange(newState, this.state);
    }
  }

  log(level, msg) {
    if (this.callbacks.onLog) this.callbacks.onLog(level, msg);
  }

  reset() {
    this.state.reset();
    this.currentWpIndex = 0;
    this.detourQueue = [];
    this.currentTarget = null;
    this.captureHoldTimer = 0;
    this.takeoffTimer = 0;
    this.landingTimer = 0;
    this.avoidCount = 0;
    this.isRunning = false;
    this.isPaused = false;
    this.analyzer.reset();
  }
}