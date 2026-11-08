/**
 * 报告生成模块 - 巡检结束后汇总全部检测数据，生成道路检修报告
 * 包含：任务概要、飞行统计、病害汇总、重点病害标记、检修建议
 */
class ReportGenerator {
  /**
   * 生成巡检报告 HTML
   * @param {InspectionTask} task 任务
   * @param {DroneState} droneState 无人机最终状态
   * @param {DamageRecord[]} damageRecords 病害记录
   * @param {object} flightInfo 飞行信息 { plannedLength, passes, avoidCount }
   * @param {RoadEnvironment} env 环境（用于覆盖率统计）
   */
  generate(task, droneState, damageRecords, flightInfo, env) {
    const stats = this.computeStats(damageRecords);
    const coverage = this.computeCoverage(env);
    const priorityDamages = damageRecords.filter(d => d.isPriority);

    const html = [];
    // ===== 任务概要 =====
    html.push(`<div class="report-section">`);
    html.push(`<h4>一、任务概要</h4>`);
    html.push(`<table class="report-table">`);
    html.push(`<tr><th>任务编号</th><td>${task.taskId}</td><th>道路名称</th><td>${task.roadName}</td></tr>`);
    html.push(`<tr><th>巡检路段</th><td>${task.roadLength}m × ${task.roadWidth}m</td><th>巡检状态</th><td><span style="color:var(--green)">✓ 已完成</span></td></tr>`);
    html.push(`<tr><th>开始时间</th><td>${task.startTime ? this.fmtDate(task.startTime) : '--'}</td><th>结束时间</th><td>${task.endTime ? this.fmtDate(task.endTime) : '--'}</td></tr>`);
    html.push(`</table>`);
    html.push(`</div>`);

    // ===== 汇总卡片 =====
    html.push(`<div class="report-section">`);
    html.push(`<h4>二、巡检成果汇总</h4>`);
    html.push(`<div class="summary-cards">`);
    html.push(this.summaryCard(stats.total, '发现病害(处)', stats.total > 0 ? 'var(--yellow)' : 'var(--text)'));
    html.push(this.summaryCard(stats.severe, '严重病害(处)', 'var(--red)'));
    html.push(this.summaryCard(droneState.imagesCaptured, '采集影像(张)', 'var(--accent)'));
    html.push(this.summaryCard(coverage.toFixed(0) + '%', '路面覆盖率', coverage >= 90 ? 'var(--green)' : 'var(--yellow)'));
    html.push(`</div>`);
    html.push(`</div>`);

    // ===== 飞行统计 =====
    html.push(`<div class="report-section">`);
    html.push(`<h4>三、飞行统计</h4>`);
    html.push(`<table class="report-table">`);
    html.push(`<tr><th>实际飞行距离</th><td>${droneState.totalDistance.toFixed(0)} m</td><th>规划航线长度</th><td>${flightInfo.plannedLength.toFixed(0)} m</td></tr>`);
    html.push(`<tr><th>飞行时间</th><td>${Utils.formatTime(droneState.flightTime)}</td><th>扫描航线数</th><td>${flightInfo.passes} 条</td></tr>`);
    html.push(`<tr><th>巡航高度</th><td>${Config.drone.altitude} m</td><th>巡航速度</th><td>${Config.drone.cruiseSpeed} m/s</td></tr>`);
    html.push(`<tr><th>剩余电量</th><td>${droneState.batteryPercent.toFixed(0)}%</td><th>避障次数</th><td>${flightInfo.avoidCount} 次</td></tr>`);
    html.push(`</table>`);
    html.push(`</div>`);

    // ===== 病害分类统计 =====
    html.push(`<div class="report-section">`);
    html.push(`<h4>四、病害分类统计</h4>`);
    html.push(`<table class="report-table">`);
    html.push(`<tr><th>病害类型</th><th>轻微</th><th>中等</th><th>严重</th><th>合计</th></tr>`);
    for (const type of Object.keys(Config.damageTypes)) {
      const typeRecords = damageRecords.filter(d => d.type === type);
      const minor = typeRecords.filter(d => d.severity === 'minor').length;
      const moderate = typeRecords.filter(d => d.severity === 'moderate').length;
      const severe = typeRecords.filter(d => d.severity === 'severe').length;
      if (typeRecords.length === 0) continue;
      html.push(`<tr><td><span style="color:${Config.damageTypes[type].color}">●</span> ${Config.damageTypes[type].label}</td>` +
        `<td>${minor}</td><td>${moderate}</td>` +
        `<td style="color:var(--red);font-weight:600">${severe}</td>` +
        `<td><b>${typeRecords.length}</b></td></tr>`);
    }
    html.push(`<tr style="color:var(--text-dim)"><td>合计</td><td>${stats.minor}</td><td>${stats.moderate}</td>` +
      `<td style="color:var(--red)">${stats.severe}</td><td><b>${stats.total}</b></td></tr>`);
    html.push(`</table>`);
    html.push(`</div>`);

    // ===== 重点病害（严重） =====
    html.push(`<div class="report-section">`);
    html.push(`<h4>五、⚠ 严重病害重点标记（需优先检修）</h4>`);
    if (priorityDamages.length === 0) {
      html.push(`<p style="color:var(--text-dim);font-size:12px">本次巡检未发现严重病害。</p>`);
    } else {
      html.push(`<table class="report-table">`);
      html.push(`<tr><th>#</th><th>病害类型</th><th>严重度</th><th>估算尺寸</th><th>GPS坐标</th><th>置信度</th><th>关联影像</th></tr>`);
      priorityDamages.forEach((d, i) => {
        html.push(`<tr class="severe-row">`);
        html.push(`<td>${i + 1}</td>`);
        html.push(`<td><span style="color:${d.color}">●</span> ${d.typeLabel}</td>`);
        html.push(`<td><span class="sev-badge severe">⚠ ${d.severityLabel}</span></td>`);
        html.push(`<td>${d.size.toFixed(1)} m</td>`);
        html.push(`<td style="font-family:monospace;font-size:11px">${d.gpsString}</td>`);
        html.push(`<td>${(d.confidence * 100).toFixed(0)}%</td>`);
        html.push(`<td>${d.imageId}</td>`);
        html.push(`</tr>`);
      });
      html.push(`</table>`);
    }
    html.push(`</div>`);

    // ===== 全部病害明细 =====
    html.push(`<div class="report-section">`);
    html.push(`<h4>六、病害检测明细（全部 ${damageRecords.length} 处）</h4>`);
    html.push(`<table class="report-table">`);
    html.push(`<tr><th>#</th><th>类型</th><th>严重度</th><th>尺寸</th><th>GPS坐标</th><th>置信度</th><th>影像</th><th>标记</th></tr>`);
    damageRecords.forEach((d, i) => {
      html.push(`<tr class="${d.severity === 'severe' ? 'severe-row' : ''}">`);
      html.push(`<td>${i + 1}</td>`);
      html.push(`<td><span style="color:${d.color}">●</span> ${d.typeLabel}</td>`);
      html.push(`<td><span class="sev-badge ${d.severity}">${d.severityLabel}</span></td>`);
      html.push(`<td>${d.size.toFixed(1)} m</td>`);
      html.push(`<td style="font-family:monospace;font-size:11px">${d.gpsString}</td>`);
      html.push(`<td>${(d.confidence * 100).toFixed(0)}%</td>`);
      html.push(`<td>${d.imageId}</td>`);
      html.push(`<td>${d.isPriority ? '<span style="color:var(--red);font-weight:700">⚠ 重点</span>' : '--'}</td>`);
      html.push(`</tr>`);
    });
    html.push(`</table>`);
    html.push(`</div>`);

    // ===== 检修建议 =====
    html.push(`<div class="report-section">`);
    html.push(`<h4>七、检修建议</h4>`);
    const recs = this.generateRecommendations(stats, priorityDamages, damageRecords);
    for (const r of recs) {
      html.push(`<div class="recommendation ${r.critical ? 'critical' : ''}">${r.text}</div>`);
    }
    html.push(`</div>`);

    // ===== 报告尾注 =====
    html.push(`<div style="text-align:center;color:var(--text-dim);font-size:11px;margin-top:16px">`);
    html.push(`本报告由无人机道路检修智能体自动生成 · ${this.fmtDate(new Date())}`);
    html.push(`</div>`);

    return html.join('');
  }

  /** 计算统计 */
  computeStats(records) {
    const stats = { total: records.length, minor: 0, moderate: 0, severe: 0 };
    for (const r of records) stats[r.severity]++;
    return stats;
  }

  /** 计算路面覆盖率（估算） */
  computeCoverage(env) {
    const roadArea = env.roadLength * env.roadWidth;
    // 合并已扫描区域面积（简化：用扫描面积/路面面积，上限100%）
    const scannedArea = env.scannedAreas.length *
      Config.camera.footprintWidth * Config.camera.footprintLength * Config.camera.overlap;
    return Math.min(100, (scannedArea / roadArea) * 100);
  }

  /** 生成检修建议 */
  generateRecommendations(stats, priorityDamages, allDamages) {
    const recs = [];
    if (priorityDamages.length > 0) {
      recs.push({
        critical: true,
        text: `🔴 <b>紧急</b>：发现 ${priorityDamages.length} 处严重病害，建议 48 小时内安排紧急维修，` +
          `防止病害扩展引发交通安全事故。严重病害已全部重点标记并记录精确 GPS 坐标。`
      });
    }
    const moderate = allDamages.filter(d => d.severity === 'moderate');
    if (moderate.length > 0) {
      recs.push({
        critical: false,
        text: `🟡 <b>建议</b>：发现 ${moderate.length} 处中等病害，建议两周内纳入常规养护计划，` +
          `避免中等病害恶化升级为严重病害。`
      });
    }
    const minor = allDamages.filter(d => d.severity === 'minor');
    if (minor.length > 0) {
      recs.push({
        critical: false,
        text: `🟢 <b>跟踪</b>：发现 ${minor.length} 处轻微病害，建议录入道路病害台账定期观测，` +
          `下次巡检时重点复查变化趋势。`
      });
    }
    if (stats.total === 0) {
      recs.push({ critical: false, text: `✅ 本次巡检未发现明显路面病害，道路状况良好，建议保持正常巡检周期。` });
    }
    // 按类型补充建议
    const potholes = allDamages.filter(d => d.type === 'pothole');
    if (potholes.length >= 3) {
      recs.push({ critical: false, text: `ℹ️ 坑洼类病害较集中（${potholes.length} 处），建议排查该路段排水及路基状况，从根源治理。` });
    }
    return recs;
  }

  summaryCard(num, label, color) {
    return `<div class="summary-card"><div class="num" style="color:${color}">${num}</div><div class="lbl">${label}</div></div>`;
  }

  fmtDate(d) {
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
}