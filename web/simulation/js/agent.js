/**
 * 无人机道路检修智能体 - 主控编排与UI交互
 * RoadInspectionAgent: 编排完整巡检流程（任务接收→路径规划→飞行巡检→
 *   影像采集→病害识别→避障→报告生成）
 */
class RoadInspectionAgent {
  constructor() {
    this.task = null;
    this.env = null;
    this.planner = new PathPlanner();
    this.analyzer = new ImageAnalyzer();
    this.reportGenerator = new ReportGenerator();
    this.controller = null;
    this.waypoints = [];
    this.flightInfo = { plannedLength: 0, passes: 0, avoidCount: 0, currentWp: 0, totalWp: 0 };
    this.damageRecords = [];
    this.reportHtml = null;
    this.speedMultiplier = 4;
    this.isRunning = false;

    // DOM 引用
    this.dom = {
      canvas: document.getElementById('mainCanvas'),
      agentStatus: document.getElementById('agentStatus'),
      droneState: document.getElementById('droneState'),
      droneCoord: document.getElementById('droneCoord'),
      batteryFill: document.getElementById('batteryFill'),
      statDistance: document.getElementById('statDistance'),
      statTime: document.getElementById('statTime'),
      statImages: document.getElementById('statImages'),
      statDamages: document.getElementById('statDamages'),
      damageList: document.getElementById('damageList'),
      logList: document.getElementById('logList'),
      btnStart: document.getElementById('btnStart'),
      btnPause: document.getElementById('btnPause'),
      btnReset: document.getElementById('btnReset'),
      btnReport: document.getElementById('btnReport'),
      speedSlider: document.getElementById('speedSlider'),
      speedLabel: document.getElementById('speedLabel'),
      reportModal: document.getElementById('reportModal'),
      reportBody: document.getElementById('reportBody'),
      btnCloseReport: document.getElementById('btnCloseReport'),
    };

    this.renderer = new Renderer(this.dom.canvas);
    this.initTask();
    this.bindEvents();
    this.startRenderLoop();
  }

  /** 初始化巡检任务 */
  initTask() {
    this.task = new InspectionTask({ roadName: '城北快速路 K0+000 ~ K0+600 段' });
    this.env = new RoadEnvironment(this.task);
    this.damageRecords = [];
    this.reportHtml = null;

    // 路径规划
    const planResult = this.planner.plan(this.task);
    this.waypoints = planResult.waypoints;
    this.flightInfo = {
      plannedLength: planResult.totalLength,
      passes: planResult.passes,
      avoidCount: 0,
      currentWp: 0,
      totalWp: this.waypoints.length,
    };

    // 无人机控制器
    this.controller = new DroneController(this.env, this.analyzer, {
      onLog: (level, msg) => this.addLog(level, msg),
      onDamageDetected: (records) => this.onDamageDetected(records),
      onObstacleAvoided: (obs, count) => {
        this.flightInfo.avoidCount = count;
      },
      onCapture: (imageId, x, y) => {
        this.renderer.addCaptureFlash(x, y);
      },
      onStateChange: (state, droneState) => {
        this.updateDroneStatusUI(state, droneState);
      },
      onComplete: () => this.onInspectionComplete(),
    });
    this.controller.loadRoute(this.waypoints);

    this.updateStatusBadge('待命');
    this.addLog('info', `巡检任务已创建：${this.task.roadName}`);
    this.addLog('info', `路径规划完成：${this.flightInfo.passes} 条扫描航线，` +
      `${this.waypoints.filter(w => w.action === 'capture').length} 个采图航点，` +
      `规划总航程 ${this.flightInfo.plannedLength.toFixed(0)}m`);
    const gtStats = this.env.getGroundTruthStats();
    this.addLog('info', `环境仿真就绪：路面预设 ${gtStats.total} 处病害` +
      `（严重${gtStats.severe}/中等${gtStats.moderate}/轻微${gtStats.minor}），` +
      `${this.env.obstacles.length} 个飞行障碍物`);
    this.updateStatsUI();
    this.updateDamageListUI();
  }

  /** 绑定UI事件 */
  bindEvents() {
    this.dom.btnStart.addEventListener('click', () => this.startInspection());
    this.dom.btnPause.addEventListener('click', () => this.togglePause());
    this.dom.btnReset.addEventListener('click', () => this.resetInspection());
    this.dom.btnReport.addEventListener('click', () => this.showReport());
    this.dom.btnCloseReport.addEventListener('click', () => {
      this.dom.reportModal.classList.remove('show');
    });
    this.dom.speedSlider.addEventListener('input', (e) => {
      this.speedMultiplier = parseInt(e.target.value);
      this.dom.speedLabel.textContent = this.speedMultiplier + 'x';
    });
  }

  /** 开始巡检 */
  startInspection() {
    if (this.isRunning) return;
    this.isRunning = true;
    this.task.startTime = new Date();
    this.task.status = 'running';
    this.controller.start();
    this.dom.btnStart.disabled = true;
    this.dom.btnPause.disabled = false;
    this.updateStatusBadge('巡检中', '#1f6feb');
  }

  /** 暂停/恢复 */
  togglePause() {
    if (!this.isRunning) return;
    if (this.controller.isPaused) {
      this.controller.resume();
      this.dom.btnPause.textContent = '⏸ 暂停';
      this.updateStatusBadge('巡检中', '#1f6feb');
    } else {
      this.controller.pause();
      this.dom.btnPause.textContent = '▶ 继续';
      this.updateStatusBadge('已暂停', '#d29922');
    }
  }

  /** 重置 */
  resetInspection() {
    this.isRunning = false;
    this.dom.logList.innerHTML = '';
    this.dom.btnStart.disabled = false;
    this.dom.btnPause.disabled = true;
    this.dom.btnPause.textContent = '⏸ 暂停';
    this.dom.btnReport.disabled = true;
    this.dom.reportModal.classList.remove('show');
    this.initTask();
  }

  /** 巡检完成 */
  onInspectionComplete() {
    this.isRunning = false;
    this.task.endTime = new Date();
    this.task.status = 'completed';

    // 生成巡检报告
    this.flightInfo.currentWp = this.controller.currentWpIndex;
    this.reportHtml = this.reportGenerator.generate(
      this.task, this.controller.state, this.damageRecords, this.flightInfo, this.env
    );

    this.dom.btnPause.disabled = true;
    this.dom.btnReport.disabled = false;
    this.updateStatusBadge('巡检完成', '#3fb950');

    const severe = this.damageRecords.filter(d => d.isPriority).length;
    this.addLog('ok', `═══ 巡检报告已生成 ═══`);
    this.addLog('info', `共发现病害 ${this.damageRecords.length} 处，其中严重病害 ${severe} 处已重点标记`);
    this.addLog('info', `飞行距离 ${this.controller.state.totalDistance.toFixed(0)}m，` +
      `采集影像 ${this.controller.state.imagesCaptured} 张，避障 ${this.flightInfo.avoidCount} 次`);
    this.addLog('info', '点击 [📋 查看报告] 查看完整道路检修报告');

    // 自动弹出报告
    setTimeout(() => this.showReport(), 800);
  }

  /** 病害检测回调 */
  onDamageDetected(records) {
    this.damageRecords.push(...records);
    this.updateDamageListUI();
    this.updateStatsUI();
    // 严重病害即时告警
    for (const r of records) {
      if (r.isPriority) {
        this.addLog('alert', `🚨 严重病害告警：${r.typeLabel} 位于 ${r.gpsString}，已重点标记！`);
      }
    }
  }

  /** 显示报告 */
  showReport() {
    if (!this.reportHtml) return;
    this.dom.reportBody.innerHTML = this.reportHtml;
    this.dom.reportModal.classList.add('show');
  }

  /** 渲染循环 */
  startRenderLoop() {
    const loop = () => {
      // 仿真更新（支持速度倍率）
      if (this.controller) {
        const dt = (1 / Config.sim.tickRate) * this.speedMultiplier;
        // 分步更新避免大dt穿透
        const steps = Math.min(5, Math.ceil(this.speedMultiplier / 2));
        const subDt = dt / steps;
        for (let i = 0; i < steps; i++) {
          this.controller.update(subDt);
        }
        this.flightInfo.currentWp = this.controller.currentWpIndex;
        this.updateStatsUI();
        this.updateBatteryUI();
      }
      // 渲染
      if (this.env && this.controller) {
        this.renderer.render(
          this.env, this.controller.state,
          this.waypoints, this.damageRecords, this.flightInfo
        );
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  // ===== UI 更新方法 =====

  updateStatusBadge(text, color) {
    this.dom.agentStatus.textContent = text;
    this.dom.agentStatus.style.color = color || '';
    this.dom.agentStatus.style.borderColor = color ? color + '66' : '';
  }

  updateDroneStatusUI(state, droneState) {
    const stateMap = {
      idle: '待命', taking_off: '起飞中', flying: '巡航飞行',
      capturing: '📸 影像采集', avoiding: '⚠ 避障机动',
      landing: '降落中', done: '已完成',
    };
    this.dom.droneState.textContent = stateMap[state] || state;
    this.dom.droneCoord.textContent =
      `位置: (${droneState.x.toFixed(1)}, ${droneState.y.toFixed(1)}) / 高度: ${droneState.altitude.toFixed(0)}m`;
  }

  updateStatsUI() {
    if (!this.controller) return;
    const s = this.controller.state;
    this.dom.statDistance.textContent = `${s.totalDistance.toFixed(0)} m`;
    this.dom.statTime.textContent = Utils.formatTime(s.flightTime);
    this.dom.statImages.textContent = `${s.imagesCaptured} 张`;
    this.dom.statDamages.textContent = `${this.damageRecords.length} 处`;
  }

  updateBatteryUI() {
    if (!this.controller) return;
    const pct = this.controller.state.batteryPercent;
    this.dom.batteryFill.style.width = pct + '%';
    if (pct > 50) {
      this.dom.batteryFill.style.background = 'var(--green)';
    } else if (pct > 25) {
      this.dom.batteryFill.style.background = 'var(--yellow)';
    } else {
      this.dom.batteryFill.style.background = 'var(--red)';
    }
  }

  updateDamageListUI() {
    const list = this.dom.damageList;
    if (this.damageRecords.length === 0) {
      list.innerHTML = '<div class="empty-hint">尚未发现病害</div>';
      return;
    }
    // 按严重度排序：严重在前
    const sorted = [...this.damageRecords].sort((a, b) => {
      const order = { severe: 0, moderate: 1, minor: 2 };
      return order[a.severity] - order[b.severity];
    });
    list.innerHTML = sorted.map(d => `
      <div class="damage-item ${d.severity === 'severe' ? 'severe' : ''}">
        <span class="sev-dot" style="background:${d.color}"></span>
        <div>
          <div class="d-type">${d.typeLabel} · ${d.severityLabel}</div>
          <div class="d-coord">${d.gpsString} | ${d.size.toFixed(1)}m</div>
        </div>
        ${d.isPriority ? '<span class="priority-tag">⚠ 重点</span>' : ''}
      </div>
    `).join('');
    // 自动滚动到底部
    list.scrollTop = list.scrollHeight;
  }

  addLog(level, msg) {
    const list = this.dom.logList;
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    const line = document.createElement('div');
    line.className = `log-line ${level}`;
    line.innerHTML = `<span class="time">${timeStr}</span>${msg}`;
    list.appendChild(line);
    // 限制日志条数
    while (list.children.length > 200) list.removeChild(list.firstChild);
    list.scrollTop = list.scrollHeight;
  }
}

// ===== 启动智能体 =====
window.addEventListener('DOMContentLoaded', () => {
  window.agent = new RoadInspectionAgent();
});