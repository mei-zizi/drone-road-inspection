/**
 * 画布渲染模块 - 可视化巡检仿真过程
 * 渲染内容：道路、航线、已巡检区域、病害标记、障碍物、无人机、相机覆盖范围
 */
class Renderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.padding = 40;
    this.animTime = 0;
    this.captureFlash = []; // 采图闪光效果
    this.resize();
    window.addEventListener('resize', () => this.resize());
  }

  resize() {
    const rect = this.canvas.parentElement.getBoundingClientRect();
    this.canvas.width = rect.width * devicePixelRatio;
    this.canvas.height = rect.height * devicePixelRatio;
    this.canvas.style.width = rect.width + 'px';
    this.canvas.style.height = rect.height + 'px';
    this.ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    this.viewW = rect.width;
    this.viewH = rect.height;
  }

  /** 世界坐标 → 屏幕坐标 */
  worldToScreen(wx, wy, env) {
    const worldW = 90;   // 世界宽度（含两侧障碍物区域）
    const worldH = env.roadLength + 50;
    const scaleX = (this.viewW - this.padding * 2) / worldW;
    const scaleY = (this.viewH - this.padding * 2) / worldH;
    const scale = Math.min(scaleX, scaleY);
    const offsetX = this.viewW / 2;
    const offsetY = this.padding;
    return {
      x: offsetX + wx * scale,
      y: offsetY + (wy + 25) * scale, // y=-25 映射到顶部
      scale
    };
  }

  /** 添加采图闪光 */
  addCaptureFlash(x, y) {
    this.captureFlash.push({ x, y, life: 1 });
  }

  /** 主渲染入口 */
  render(env, drone, waypoints, damageRecords, flightInfo) {
    const ctx = this.ctx;
    this.animTime += 0.016;
    ctx.clearRect(0, 0, this.viewW, this.viewH);

    // 背景
    ctx.fillStyle = '#0a0e14';
    ctx.fillRect(0, 0, this.viewW, this.viewH);

    this.drawGrid(env);
    this.drawRoad(env);
    this.drawScannedAreas(env);
    this.drawPlannedPath(waypoints, env);
    this.drawGroundTruthDamage(env);
    this.drawObstacles(env);
    this.drawDetectedDamage(damageRecords, env);
    this.drawDrone(drone, env);
    this.drawCaptureFlash(env);
    this.drawInfoBar(env, drone, flightInfo);
  }

  /** 网格背景 */
  drawGrid(env) {
    const ctx = this.ctx;
    ctx.strokeStyle = '#161b22';
    ctx.lineWidth = 1;
    const step = 50; // 每50米一条网格线
    for (let y = -25; y <= env.roadLength + 25; y += step) {
      const p1 = this.worldToScreen(-45, y, env);
      const p2 = this.worldToScreen(45, y, env);
      ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
    }
    // 距离标注
    ctx.fillStyle = '#3d444d';
    ctx.font = '10px monospace';
    for (let y = 0; y <= env.roadLength; y += 100) {
      const p = this.worldToScreen(-44, y, env);
      ctx.fillText(`${y}m`, p.x, p.y + 4);
    }
  }

  /** 绘制道路 */
  drawRoad(env) {
    const ctx = this.ctx;
    const halfW = env.roadWidth / 2;
    // 路面
    const tl = this.worldToScreen(-halfW, -25, env);
    const br = this.worldToScreen(halfW, env.roadLength + 25, env);
    const roadGrad = ctx.createLinearGradient(tl.x, 0, br.x, 0);
    roadGrad.addColorStop(0, '#1c2128');
    roadGrad.addColorStop(0.5, '#21262d');
    roadGrad.addColorStop(1, '#1c2128');
    ctx.fillStyle = roadGrad;
    ctx.fillRect(tl.x, tl.y, br.x - tl.x, br.y - tl.y);
    // 路边缘线
    ctx.strokeStyle = '#484f58';
    ctx.lineWidth = 2;
    const e1 = this.worldToScreen(-halfW, -25, env);
    const e2 = this.worldToScreen(-halfW, env.roadLength + 25, env);
    const e3 = this.worldToScreen(halfW, -25, env);
    const e4 = this.worldToScreen(halfW, env.roadLength + 25, env);
    ctx.beginPath(); ctx.moveTo(e1.x, e1.y); ctx.lineTo(e2.x, e2.y); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(e3.x, e3.y); ctx.lineTo(e4.x, e4.y); ctx.stroke();
    // 车道分隔线（虚线）
    ctx.strokeStyle = '#3d444d';
    ctx.lineWidth = 1;
    ctx.setLineDash([8, 8]);
    const laneStep = env.roadWidth / 4;
    for (let i = 1; i < 4; i++) {
      const lx = -halfW + i * laneStep;
      if (i === 2) { // 中央双黄线
        ctx.setLineDash([]);
        ctx.strokeStyle = '#9e6a03';
        ctx.lineWidth = 1.5;
        const c1 = this.worldToScreen(lx - 1, -25, env);
        const c2 = this.worldToScreen(lx - 1, env.roadLength + 25, env);
        const c3 = this.worldToScreen(lx + 1, -25, env);
        const c4 = this.worldToScreen(lx + 1, env.roadLength + 25, env);
        ctx.beginPath(); ctx.moveTo(c1.x, c1.y); ctx.lineTo(c2.x, c2.y); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(c3.x, c3.y); ctx.lineTo(c4.x, c4.y); ctx.stroke();
        ctx.strokeStyle = '#3d444d';
        ctx.lineWidth = 1;
        ctx.setLineDash([8, 8]);
      } else {
        const l1 = this.worldToScreen(lx, -25, env);
        const l2 = this.worldToScreen(lx, env.roadLength + 25, env);
        ctx.beginPath(); ctx.moveTo(l1.x, l1.y); ctx.lineTo(l2.x, l2.y); ctx.stroke();
      }
    }
    ctx.setLineDash([]);
    // 起点终点标注
    ctx.fillStyle = '#58a6ff';
    ctx.font = 'bold 11px sans-serif';
    const sp = this.worldToScreen(0, 0, env);
    ctx.fillText('起点 K0+000', sp.x - 35, sp.y - 6);
    const ep = this.worldToScreen(0, env.roadLength, env);
    ctx.fillText(`终点 K0+${env.roadLength}`, ep.x - 35, ep.y - 6);
  }

  /** 已巡检覆盖区域 */
  drawScannedAreas(env) {
    const ctx = this.ctx;
    ctx.fillStyle = 'rgba(63,185,80,0.10)';
    ctx.strokeStyle = 'rgba(63,185,80,0.25)';
    ctx.lineWidth = 1;
    for (const area of env.scannedAreas) {
      const tl = this.worldToScreen(area.cx - area.fw / 2, area.cy - area.fl / 2, env);
      const br = this.worldToScreen(area.cx + area.fw / 2, area.cy + area.fl / 2, env);
      ctx.fillRect(tl.x, tl.y, br.x - tl.x, br.y - tl.y);
      ctx.strokeRect(tl.x, tl.y, br.x - tl.x, br.y - tl.y);
    }
  }

  /** 规划航线 */
  drawPlannedPath(waypoints, env) {
    const ctx = this.ctx;
    if (!waypoints || waypoints.length < 2) return;
    // 航线连线
    ctx.strokeStyle = 'rgba(88,166,255,0.4)';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    const first = this.worldToScreen(waypoints[0].x, waypoints[0].y, env);
    ctx.moveTo(first.x, first.y);
    for (let i = 1; i < waypoints.length; i++) {
      const p = this.worldToScreen(waypoints[i].x, waypoints[i].y, env);
      ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();
    ctx.setLineDash([]);
    // 采图航点
    for (const wp of waypoints) {
      if (wp.action !== 'capture') continue;
      const p = this.worldToScreen(wp.x, wp.y, env);
      ctx.fillStyle = wp.captureDone ? 'rgba(63,185,80,0.8)' : 'rgba(88,166,255,0.5)';
      ctx.beginPath();
      ctx.arc(p.x, p.y, wp.captureDone ? 2.5 : 2, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /** 真实病害（半透明显示地面真值，供对比） */
  drawGroundTruthDamage(env) {
    const ctx = this.ctx;
    for (const d of env.groundTruthDamage) {
      const p = this.worldToScreen(d.x, d.y, env);
      const r = Math.max(3, d.size * p.scale * 0.35);
      ctx.strokeStyle = d.detected ? 'rgba(188,140,255,0.15)' : 'rgba(188,140,255,0.35)';
      ctx.lineWidth = 1;
      if (d.type === 'crack' || d.type === 'rut') {
        // 线状病害
        const len = Math.max(6, d.size * p.scale * 0.4);
        const dx = Math.cos(d.orientation) * len / 2;
        const dy = Math.sin(d.orientation) * len / 2;
        ctx.beginPath();
        ctx.moveTo(p.x - dx, p.y - dy);
        ctx.lineTo(p.x + dx, p.y + dy);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }

  /** 障碍物 */
  drawObstacles(env) {
    const ctx = this.ctx;
    for (const obs of env.obstacles) {
      const p = this.worldToScreen(obs.x, obs.y, env);
      const r = obs.radius * p.scale;
      // 危险区域（半透明圆）
      ctx.fillStyle = 'rgba(210,153,34,0.08)';
      ctx.beginPath();
      ctx.arc(p.x, p.y, r + Config.obstacle.safetyMargin * p.scale, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = 'rgba(210,153,34,0.4)';
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.setLineDash([]);
      // 障碍物本体
      ctx.fillStyle = 'rgba(210,153,34,0.25)';
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#d29922';
      ctx.lineWidth = 1.5;
      ctx.stroke();
      // 图标
      ctx.font = `${Math.max(12, r)}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(obs.icon, p.x, p.y);
      ctx.textAlign = 'left';
      ctx.textBaseline = 'alphabetic';
    }
  }

  /** 已检测病害标记 */
  drawDetectedDamage(records, env) {
    const ctx = this.ctx;
    for (const d of records) {
      const p = this.worldToScreen(d.x, d.y, env);
      const isSevere = d.severity === 'severe';
      const baseR = Math.max(5, d.size * p.scale * 0.4);
      // 严重病害脉冲光环
      if (isSevere) {
        const pulse = (Math.sin(this.animTime * 4) + 1) / 2;
        ctx.strokeStyle = `rgba(248,81,73,${0.2 + pulse * 0.3})`;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(p.x, p.y, baseR + 6 + pulse * 8, 0, Math.PI * 2);
        ctx.stroke();
      }
      // 标记圆
      ctx.fillStyle = d.color + '44';
      ctx.beginPath();
      ctx.arc(p.x, p.y, baseR, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = d.color;
      ctx.lineWidth = isSevere ? 2.5 : 1.5;
      ctx.stroke();
      // 中心点
      ctx.fillStyle = d.color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
      ctx.fill();
      // 严重病害标注
      if (isSevere) {
        ctx.fillStyle = '#f85149';
        ctx.font = 'bold 10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('⚠', p.x, p.y - baseR - 6);
        ctx.textAlign = 'left';
      }
    }
  }

  /** 无人机与相机覆盖 */
  drawDrone(drone, env) {
    const ctx = this.ctx;
    const p = this.worldToScreen(drone.x, drone.y, env);
    // 相机覆盖范围
    if (drone.altitude > 5) {
      const fw = Config.camera.footprintWidth / 2;
      const fl = Config.camera.footprintLength / 2;
      const tl = this.worldToScreen(drone.x - fw, drone.y - fl, env);
      const br = this.worldToScreen(drone.x + fw, drone.y + fl, env);
      ctx.fillStyle = 'rgba(88,166,255,0.08)';
      ctx.fillRect(tl.x, tl.y, br.x - tl.x, br.y - tl.y);
      ctx.strokeStyle = 'rgba(88,166,255,0.5)';
      ctx.lineWidth = 1;
      ctx.strokeRect(tl.x, tl.y, br.x - tl.x, br.y - tl.y);
      // 十字准星
      ctx.strokeStyle = 'rgba(88,166,255,0.3)';
      ctx.beginPath();
      ctx.moveTo(tl.x, p.y); ctx.lineTo(br.x, p.y);
      ctx.moveTo(p.x, tl.y); ctx.lineTo(p.x, br.y);
      ctx.stroke();
    }
    // 无人机本体
    const droneR = 10;
    // 旋翼
    ctx.strokeStyle = '#58a6ff';
    ctx.lineWidth = 1.5;
    const rotorAngles = [0, Math.PI / 2, Math.PI, Math.PI * 1.5];
    for (const a of rotorAngles) {
      const rx = p.x + Math.cos(a + this.animTime * 8) * droneR;
      const ry = p.y + Math.sin(a + this.animTime * 8) * droneR;
      ctx.beginPath();
      ctx.arc(rx, ry, 3, 0, Math.PI * 2);
      ctx.stroke();
    }
    // 机身
    ctx.fillStyle = drone.state === 'capturing' ? '#d29922' : '#1f6feb';
    ctx.beginPath();
    ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#58a6ff';
    ctx.lineWidth = 2;
    ctx.stroke();
    // 航向指示
    if (drone.speed > 0) {
      const hx = p.x + Math.cos(drone.heading) * 16;
      const hy = p.y + Math.sin(drone.heading) * 16;
      ctx.strokeStyle = '#58a6ff';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(hx, hy);
      ctx.stroke();
      // 箭头
      ctx.beginPath();
      ctx.moveTo(hx, hy);
      ctx.lineTo(hx - Math.cos(drone.heading - 0.4) * 5, hy - Math.sin(drone.heading - 0.4) * 5);
      ctx.moveTo(hx, hy);
      ctx.lineTo(hx - Math.cos(drone.heading + 0.4) * 5, hy - Math.sin(drone.heading + 0.4) * 5);
      ctx.stroke();
    }
  }

  /** 采图闪光效果 */
  drawCaptureFlash(env) {
    const ctx = this.ctx;
    this.captureFlash = this.captureFlash.filter(f => f.life > 0);
    for (const f of this.captureFlash) {
      const p = this.worldToScreen(f.x, f.y, env);
      ctx.strokeStyle = `rgba(255,255,255,${f.life * 0.6})`;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(p.x, p.y, (1 - f.life) * 30 + 5, 0, Math.PI * 2);
      ctx.stroke();
      f.life -= 0.05;
    }
  }

  /** 底部信息条 */
  drawInfoBar(env, drone, flightInfo) {
    const ctx = this.ctx;
    const barY = this.viewH - 24;
    ctx.fillStyle = 'rgba(13,17,23,0.85)';
    ctx.fillRect(0, barY, this.viewW, 24);
    ctx.fillStyle = '#8b949e';
    ctx.font = '11px monospace';
    const info = [
      `高度: ${drone.altitude.toFixed(0)}m`,
      `速度: ${drone.speed.toFixed(1)}m/s`,
      `电量: ${drone.batteryPercent.toFixed(0)}%`,
      `航点: ${Math.min(flightInfo.currentWp + 1, flightInfo.totalWp)}/${flightInfo.totalWp}`,
    ];
    ctx.fillText(info.join('  |  '), 12, barY + 16);
  }
}