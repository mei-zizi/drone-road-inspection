/**
 * 路径规划与避障模块
 * 路径规划：割草机式（boustrophedon）覆盖路径，确保路面全覆盖
 * 避障：前方探测 → 计算绕行航点 → 回归原航线
 */
class PathPlanner {
  /**
   * 规划巡检航线
   * @param {InspectionTask} task 巡检任务
   * @returns {{ waypoints: Waypoint[], passes: number, totalLength: number }}
   */
  plan(task) {
    const { roadLength: L, roadWidth: W } = task;
    const fw = Config.camera.footprintWidth;
    const fl = Config.camera.footprintLength;
    const lateralStep = Config.inspection.lateralStep;
    const captureInterval = Config.inspection.captureInterval;

    // 计算扫描线条数（覆盖整个路面宽度）
    const passCount = Math.max(2, Math.ceil(W / lateralStep));
    // 航线横向位置：从道路一侧均匀分布到另一侧
    const startX = -W / 2 + (W - (passCount - 1) * lateralStep) / 2;

    const waypoints = [];
    // 起飞点（道路起点外侧）
    waypoints.push(new Waypoint(startX, -15, 'takeoff'));

    let totalLength = 0;
    let prev = null;

    for (let pass = 0; pass < passCount; pass++) {
      const x = startX + pass * lateralStep;
      const goingNorth = pass % 2 === 0; // 偶数pass向北飞，奇数向南飞（往复）
      const yStart = goingNorth ? 0 : L;
      const yEnd = goingNorth ? L : 0;

      // 横向移动到本条扫描线起点（转弯航点）
      const lineStart = new Waypoint(x, yStart, 'fly');
      waypoints.push(lineStart);
      if (prev) totalLength += Utils.dist(prev, lineStart);
      prev = lineStart;

      // 沿扫描线生成采图航点
      const dir = goingNorth ? 1 : -1;
      for (let y = yStart + dir * captureInterval; goingNorth ? y <= yEnd : y >= yEnd; y += dir * captureInterval) {
        const clampedY = Math.max(0, Math.min(L, y));
        const wp = new Waypoint(x, clampedY, 'capture');
        waypoints.push(wp);
        totalLength += Utils.dist(prev, wp);
        prev = wp;
        if (clampedY === yEnd || clampedY === yStart) break;
      }
      // 确保末端有采图点
      const endWp = new Waypoint(x, yEnd, 'capture');
      if (prev.y !== yEnd) {
        waypoints.push(endWp);
        totalLength += Utils.dist(prev, endWp);
        prev = endWp;
      }
    }

    // 降落点（返回道路起点附近）
    const landWp = new Waypoint(startX, -15, 'land');
    waypoints.push(landWp);
    if (prev) totalLength += Utils.dist(prev, landWp);

    return { waypoints, passes: passCount, totalLength };
  }
}

/**
 * 避障模块 - 前方路径探测与绕行规划
 */
class ObstacleAvoidance {
  /**
   * 检测当前航段是否有障碍物，如有则生成绕行航点
   * @param {{x,y}} from 当前位置
   * @param {{x,y}} to 目标航点
   * @param {RoadEnvironment} env 环境
   * @returns {{ hasObstacle: boolean, obstacle: object|null, detour: Waypoint[] }}
   */
  checkAndPlanDetour(from, to, env) {
    const obstacle = env.findObstacleOnPath(from, to);
    if (!obstacle) {
      return { hasObstacle: false, obstacle: null, detour: [] };
    }

    // 计算绕行路径：在障碍物两侧选择更优的一侧绕行
    const detour = this.calculateDetour(from, to, obstacle);
    return { hasObstacle: true, obstacle, detour };
  }

  /**
   * 计算绕行航点
   * 策略：垂直于当前航线方向偏移，绕过障碍物后回归航线
   */
  calculateDetour(from, to, obstacle) {
    const pathAngle = Utils.angle(from, to);
    // 垂直方向（左侧法线）
    const perpAngle = pathAngle + Math.PI / 2;

    // 障碍物中心在航线上的投影点
    const proj = Utils.pointToSegment(obstacle, from, to);
    const nearest = proj.nearest;

    // 绕行距离 = 障碍物半径 + 安全余量
    const detourDist = obstacle.radius + Config.obstacle.safetyMargin;

    // 判断障碍物偏向航线哪一侧，选择反向绕行
    const dx = obstacle.x - nearest.x;
    const dy = obstacle.y - nearest.y;
    const cross = Math.cos(perpAngle) * dx + Math.sin(perpAngle) * dy;
    const side = cross >= 0 ? -1 : 1; // 向障碍物反侧绕

    // 绕行航点1：障碍物前方，侧向偏移
    const offsetBefore = Utils.dist(from, nearest) - detourDist * 0.5;
    const p1 = {
      x: from.x + Math.cos(pathAngle) * Math.max(0, offsetBefore) + Math.cos(perpAngle) * side * detourDist,
      y: from.y + Math.sin(pathAngle) * Math.max(0, offsetBefore) + Math.sin(perpAngle) * side * detourDist,
    };
    // 绕行航点2：障碍物后方，侧向偏移
    const offsetAfter = Utils.dist(from, nearest) + detourDist * 0.5;
    const p2 = {
      x: from.x + Math.cos(pathAngle) * offsetAfter + Math.cos(perpAngle) * side * detourDist,
      y: from.y + Math.sin(pathAngle) * offsetAfter + Math.sin(perpAngle) * side * detourDist,
    };

    return [
      new Waypoint(p1.x, p1.y, 'fly'),
      new Waypoint(p2.x, p2.y, 'fly'),
    ];
  }

  /**
   * 连续避障检查：递归检查绕行路径是否仍有障碍物（最多两层）
   */
  planSafeRoute(from, to, env, depth = 0) {
    if (depth >= 2) return [to]; // 最多两层绕行，避免死循环

    const { hasObstacle, obstacle, detour } = this.checkAndPlanDetour(from, to, env);
    if (!hasObstacle) return [to];

    // 对绕行航点递归检查
    const result = [];
    let current = from;
    for (const wp of detour) {
      const sub = this.planSafeRoute(current, wp, env, depth + 1);
      result.push(...sub.slice(0, -1)); // 除最后一个外都加入
      current = sub[sub.length - 1];
    }
    // 从最后一个绕行点到目标
    const final = this.planSafeRoute(current, to, env, depth + 1);
    result.push(...final.slice(0, -1));
    result.push(to);
    return result;
  }
}