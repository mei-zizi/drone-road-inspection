/* ======== 无人机道路智能巡检系统 - 前端核心逻辑 ======== */

// ======== 应用状态 ========
const App = {
  data: null,
  defects: [],
  initialDefects: [],  // 初始数据备份（用于重置）
  charts: {},
  chartModes: { typeBar: 'bar', sevPie: 'pie' },  // 图表展示模式
  mapPoints: [],       // 地图上可交互的点位
  uploadedFiles: [],   // 已上传文件签名（用于查重）
  agentOnline: false,  // 智能体在线状态
};

// ======== 智能体 API 调用模块 ========
// 当页面由 server.py 提供服务时，智能体在线；离线时自动降级为本地分析
// 所有请求带超时兜底（AbortController），避免网络异常时一直转圈
const AgentAPI = {
  async request(path, options, timeoutMs) {
    const opts = options || {};
    const ms = timeoutMs || 8000;                    // 默认8秒超时
    const ctrl = new AbortController();
    const timer = setTimeout(function () { ctrl.abort(); }, ms);
    try {
      const res = await fetch(path, Object.assign({
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
      }, opts));
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  },
  async checkStatus() {
    try {
      const r = await this.request('/api/status', { method: 'GET' }, 3000); // 状态检测3秒快速失败
      App.agentOnline = !!(r && r.success);
      return r;
    } catch (e) {
      App.agentOnline = false;
      return null;
    }
  },
  async analyze(rows, filename) {
    return this.request('/api/analyze', {
      method: 'POST',
      body: JSON.stringify({ rows: rows, filename: filename }),
    });
  },
  async analyzeImage(filename, width, height) {
    return this.request('/api/analyze_image', {
      method: 'POST',
      body: JSON.stringify({ filename: filename, width: width, height: height }),
    });
  },
  async runInspection() {
    return this.request('/api/run_inspection', {
      method: 'POST',
      body: '{}',
    }, 60000);
  },
  async latestResults() {
    return this.request('/api/latest_results', { method: 'GET' });
  },
};

// ======== 常量配置 ========
const SEV_COLORS = { '严重': '#ef4444', '中等': '#f59e0b', '轻微': '#22c55e' };
const TYPE_COLORS = {
  '坑洼': '#f97316', '裂缝': '#8b5cf6', '车辙': '#3b82f6',
  '沉陷': '#ec4899', '边缘破损': '#14b8a6',
};
const FEATURES = [
  {
    icon: '&#128205;', color: '#2563eb', title: '路径规划',
    desc: '接收巡检任务，自动规划弓字形飞行路径与航点',
    detail: '采用割草机式（Boustrophedon）覆盖航线规划算法。根据道路中心线、路面宽度与拍摄重叠率要求，自动生成弓字形往返覆盖航线与航点序列：沿中心线按航向步距推进，在每个横断面生成往返拍摄航点，保证影像重叠率 ≥ 80%，确保巡检无漏拍区域。算法结合飞行高度与相机视场角计算航点间距与拍摄间隔，输出完整航点坐标序列供飞控执行。',
  },
  {
    icon: '&#128247;', color: '#06b6d4', title: '影像采集',
    desc: '控制无人机沿航点采集路面航拍影像',
    detail: '无人机沿规划航点自主飞行，到达每个拍摄航点后定点悬停拍摄路面影像。采集过程严格控制飞行高度（保持地面分辨率 GSD 一致）与拍摄间隔，保证相邻影像具有足够的航向/旁向重叠率。每帧影像记录拍摄时刻的 GPS 坐标、航向角与桩号位置，为后续病害地理定位提供参考基准。巡检全程监控电量与信号状态，异常时自动触发返航保护。',
  },
  {
    icon: '&#128269;', color: '#8b5cf6', title: '病害识别',
    desc: 'AI连通域分析，识别坑洼/裂缝/车辙/沉陷/边缘破损',
    detail: '基于连通域分析的路面病害识别算法。对航拍影像依次执行灰度化、自适应二值化、形态学开闭运算滤波去噪，再通过连通域标记提取候选病害区域。结合区域面积、长宽比、形状因子等几何特征，将候选区域分类为坑洼、裂缝、车辙、沉陷、边缘破损五类病害；并依据面积与深度估算结果判定严重程度（严重/中等/轻微），输出病害类型、位置与识别置信度。',
  },
  {
    icon: '&#128204;', color: '#f59e0b', title: '坐标记录',
    desc: '像素坐标反算WGS84地理坐标，记录桩号位置',
    detail: '病害像素坐标到地理坐标的精确反算。利用拍摄航点的 WGS84 经纬度、飞行高度、相机内参与姿态角，将影像中病害区域的像素坐标反投影为地面坐标，实现病害的精准地理定位。同时根据病害在道路中心线上的投影位置，换算为工程桩号（K里程+偏移量），便于养护人员按桩号快速定位病害现场，实现"影像—坐标—桩号"三位一体的病害档案记录。',
  },
  {
    icon: '&#9888;',  color: '#ef4444', title: '智能避障',
    desc: '检测航段障碍物冲突，自动规划绕行航点',
    detail: '前瞻探测式智能避障策略。巡检前加载已知障碍物（高压线、信号塔、树木等）位置数据，飞行过程中对每一航段进行冲突检测：判断航段线段与障碍物安全圆是否相交。一旦发现冲突，自动在障碍物前方生成绕行航点（沿法线方向偏移安全距离），插入新航段绕过障碍后回归原航线，在保证飞行安全的同时不漏拍关键巡检区域。',
  },
  {
    icon: '&#128196;', color: '#22c55e', title: '报告生成',
    desc: '汇总检测数据，一键生成规范检修报告',
    detail: '巡检结束后自动汇总全流程检测数据，生成规范化道路检修报告。报告包含：任务概述、巡检执行情况（航点/影像/避障统计）、病害检测汇总（严重程度分布、类型分布、里程分布）、严重病害重点标记清单、病害明细清单与分级维修建议。支持 Markdown / HTML / JSON 多格式一键导出与打印，直接对接道路养护管理部门的检修工作流程。',
  },
  {
    icon: '&#9733;',  color: '#eab308', title: '重点标记',
    desc: '严重路面病害自动重点标记，优先处置',
    detail: '严重病害自动重点标记机制。当病害识别结果判定为严重等级（大面积坑洼、深层沉陷、结构性裂缝等）时，系统自动为该病害打上重点标记（★），在地图可视化中以星标+光晕高亮显示，并在检修报告中单独列出"严重病害重点标记清单"，附带紧急维修建议（48小时内处置）。确保高危病害优先纳入处置计划，防范道路安全事故。',
  },
];
const MAX_FILE_SIZE = 20 * 1024 * 1024;
const MAX_FILE_COUNT = 10;
const ALLOWED_EXTS = ['xlsx', 'xls', 'csv', 'png', 'jpg', 'jpeg'];
const FLOW_STEPS = [
  { title: '任务接收', desc: '接收巡检任务，解析道路信息与巡检参数' },
  { title: '路径规划', desc: '割草机式覆盖航线与航点自动生成' },
  { title: '起飞巡航', desc: '沿航点飞行，实时监控飞行状态' },
  { title: '影像采集', desc: '定点拍摄路面影像，保证重叠率' },
  { title: 'AI 识别', desc: '连通域分析识别五类路面病害' },
  { title: '智能避障', desc: '探测航段冲突，自动规划绕行' },
  { title: '报告生成', desc: '汇总检测数据，输出检修报告' },
];
const EXCEL_COL_MAP = {
  type: ['病害类型', '类型', 'type', '缺陷类型'],
  severity: ['严重程度', '等级', 'severity', '严重度'],
  lon: ['经度', 'lon', 'longitude', 'lng'],
  lat: ['纬度', 'lat', 'latitude'],
  area: ['面积', 'area', '面积m2', '面积(m2)'],
  station: ['桩号', 'station', '里程'],
};

// ======== 初始化 ========
function init() {
  App.data = window.INSPECTION_DATA || { mission: {}, defects: [], centerline: [], obstacles: [] };
  App.defects = App.data.defects || [];
  App.initialDefects = JSON.parse(JSON.stringify(App.defects));
  renderFeatures();
  renderFlowSteps();
  setupUpload();
  renderStatsCards();
  renderCharts();
  drawMap();
  renderMapLegend();
  renderReport();
  bindExportButtons();
  setupChartActions();
  setupChartToggles();
  setupAgentStatus();
  setupScrollAnimations();
  setupNavHighlight();
  console.log('[系统] 初始化完成，加载病害数据 ' + App.defects.length + ' 处');
}

// ======== 智能体状态检测 ========
async function setupAgentStatus() {
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  const info = await AgentAPI.checkStatus();
  if (App.agentOnline && info) {
    dot.className = 'status-dot online';
    text.textContent = '智能体在线';
    text.title = info.agent + ' ' + info.server_version + ' | Python ' + info.python_version +
      '\n任务：' + info.mission_id + ' | 道路：' + info.road_name;
    console.log('[智能体] 已连接：' + info.agent + ' ' + info.server_version);
    // 智能体已就绪，等待用户操作时按需调用（不再首屏自动拉取，保证首屏秒开）
  } else {
    dot.className = 'status-dot offline';
    text.textContent = '智能体离线（本地模式）';
    text.title = '未连接巡检智能体服务，上传分析将使用本地降级模式\n启动方式：运行 python-embed\\python.exe server.py';
    console.log('[智能体] 服务未连接，进入本地降级模式');
  }
}

// ======== 加载智能体最新巡检数据（用户点击触发，不阻塞首屏） ========
async function loadLatestResults() {
  const btn = document.getElementById('btnLoadResults');
  if (!btn) return;
  if (!App.agentOnline) {
    showStatus('error', '✘ 巡检智能体未连接，请确认服务已启动');
    return;
  }
  btn.disabled = true;
  const original = btn.innerHTML;
  btn.innerHTML = '&#8987; 加载中...';
  btn.classList.add('btn-loading');
  try {
    const res = await AgentAPI.latestResults();
    if (res.success && res.data && res.data.defects && res.data.defects.length > 0) {
      App.data = res.data;
      App.defects = res.data.defects || [];
      App.initialDefects = JSON.parse(JSON.stringify(App.defects));
      refreshAll();
      showStatus('success', '✔ 已加载智能体最新巡检结果：病害 ' + App.defects.length + ' 处（来源：' + (res.file || '') + '）');
    } else {
      showStatus('info', 'ℹ 暂无历史巡检结果，可点击"一键全流程巡检"生成数据');
    }
  } catch (err) {
    showStatus('error', '✘ 加载巡检数据失败（请求超时或服务异常）：' + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
    btn.classList.remove('btn-loading');
  }
}

// ======== 智能体日志面板 ========
function showAgentLog(lines, title) {
  const panel = document.getElementById('agentLogPanel');
  const body = document.getElementById('agentLogBody');
  if (!lines || lines.length === 0) return;
  body.innerHTML = lines.map(l => '<div class="log-line">' + escapeHtml(l) + '</div>').join('');
  panel.style.display = 'block';
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  body.scrollTop = body.scrollHeight;
}
function hideAgentLog() {
  document.getElementById('agentLogPanel').style.display = 'none';
}
function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
// 简写：HTML 转义（防 XSS）
function esc(s) { return escapeHtml(s); }
// 安全数字格式化：非数字/缺失值兜底为 0，避免 toFixed 抛 TypeError 导致报告渲染崩溃
function num(v, digits) { return (Number(v) || 0).toFixed(digits || 2); }

// ======== 功能卡片 ========
function renderFeatures() {
  const grid = document.getElementById('featuresGrid');
  grid.innerHTML = FEATURES.map((f, i) =>
    '<div class="feature-card clickable" data-idx="' + i + '" title="点击查看详细说明">' +
    '<div class="feature-icon" style="background:' + f.color + '">' + f.icon + '</div>' +
    '<h4>' + f.title + '</h4><p>' + f.desc + '</p>' +
    '<span class="feature-more">查看详情 &#10142;</span></div>'
  ).join('');
  grid.querySelectorAll('.feature-card.clickable').forEach(card => {
    card.addEventListener('click', () => showFeatureDetail(FEATURES[+card.getAttribute('data-idx')]));
  });
}

// ======== 功能详情模态框 ========
function showFeatureDetail(f) {
  let overlay = document.getElementById('featureModalOverlay');
  if (!overlay) {                       // 首次调用时动态创建模态框DOM
    overlay = document.createElement('div');
    overlay.id = 'featureModalOverlay';
    overlay.className = 'feature-modal-overlay';
    overlay.innerHTML =
      '<div class="feature-modal" role="dialog" aria-modal="true">' +
      '<button class="feature-modal-close" id="featureModalClose" aria-label="关闭">&times;</button>' +
      '<div class="feature-modal-icon" id="featureModalIcon"></div>' +
      '<h3 class="feature-modal-title" id="featureModalTitle"></h3>' +
      '<p class="feature-modal-desc" id="featureModalDesc"></p>' +
      '<div class="feature-modal-divider"></div>' +
      '<p class="feature-modal-detail" id="featureModalDetail"></p>' +
      '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) hideFeatureDetail(); });
    document.getElementById('featureModalClose').addEventListener('click', hideFeatureDetail);
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && overlay.classList.contains('show')) hideFeatureDetail();
    });
  }
  document.getElementById('featureModalIcon').innerHTML = f.icon;
  document.getElementById('featureModalIcon').style.background = f.color;
  document.getElementById('featureModalTitle').textContent = f.title;
  document.getElementById('featureModalDesc').textContent = f.desc;
  document.getElementById('featureModalDetail').textContent = f.detail;
  overlay.classList.add('show');
  document.body.style.overflow = 'hidden';   // 弹窗时锁定背景滚动
}

function hideFeatureDetail() {
  const overlay = document.getElementById('featureModalOverlay');
  if (!overlay) return;
  overlay.classList.remove('show');
  document.body.style.overflow = '';
}

// ======== 技术流程步骤 ========
function renderFlowSteps() {
  const wrap = document.getElementById('flowSteps');
  wrap.innerHTML = FLOW_STEPS.map((s, i) =>
    '<div class="flow-step">' +
    '<div class="flow-num">' + (i + 1) + '</div>' +
    '<h4>' + s.title + '</h4><p>' + s.desc + '</p></div>' +
    (i < FLOW_STEPS.length - 1 ? '<div class="flow-arrow">&#10142;</div>' : '')
  ).join('');
}

// ======== 统计计算 ========
function computeStats() {
  const defs = App.defects;
  const byType = {}, bySev = { '严重': 0, '中等': 0, '轻微': 0 };
  let totalArea = 0;
  defs.forEach(d => {
    byType[d.type] = (byType[d.type] || 0) + 1;
    if (bySev[d.severity] !== undefined) bySev[d.severity]++;
    totalArea += d.area_m2 || 0;
  });
  return {
    total: defs.length,
    critical: bySev['严重'], moderate: bySev['中等'], minor: bySev['轻微'],
    totalArea: Math.round(totalArea * 100) / 100,
    byType, bySev,
    frames: App.data.total_frames || 0,
    waypoints: App.data.total_waypoints || 0,
    roadLen: App.data.road_length_m || 0,
  };
}

function renderStatsCards() {
  const s = computeStats();
  const m = App.data.mission || {};
  document.getElementById('statsCards').innerHTML = [
    card(s.total, '病害总数', ''),
    card(s.critical, '严重病害', 'red'),
    card(s.moderate, '中等病害', 'amber'),
    card(s.minor, '轻微病害', 'green'),
    card(s.totalArea + ' m²', '病害总面积', ''),
    card(s.frames, '采集影像帧数', ''),
  ].join('');
  function card(v, label, cls) {
    return '<div class="stat-card"><div class="stat-value ' + cls + '">' + v +
           '</div><div class="stat-label">' + label + '</div></div>';
  }
}

// ======== 上传模块 ========
function setupUpload() {
  const area = document.getElementById('uploadArea');
  const input = document.getElementById('fileInput');
  area.addEventListener('click', () => input.click());
  area.addEventListener('dragover', e => { e.preventDefault(); area.classList.add('dragover'); });
  area.addEventListener('dragleave', () => area.classList.remove('dragover'));
  area.addEventListener('drop', e => {
    e.preventDefault(); area.classList.remove('dragover');
    handleFiles(e.dataTransfer.files);
  });
  input.addEventListener('change', () => handleFiles(input.files));
}

function handleFiles(files) {
  if (!files || files.length === 0) return;
  const fileArr = Array.from(files);
  // 数量上限校验
  if (App.uploadedFiles.length + fileArr.length > MAX_FILE_COUNT) {
    showStatus('error', '✘ 上传文件数量超过上限（最多 ' + MAX_FILE_COUNT + ' 个），当前已上传 ' + App.uploadedFiles.length + ' 个');
    return;
  }
  fileArr.forEach(file => {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!ALLOWED_EXTS.includes(ext)) {
      showStatus('error', '✘ 文件 "' + file.name + '" 格式不支持，仅支持 ' + ALLOWED_EXTS.join(' / '));
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      showStatus('error', '✘ 文件 "' + file.name + '" 超过20MB限制（当前 ' + (file.size / 1048576).toFixed(1) + 'MB）');
      return;
    }
    if (file.size === 0) {
      showStatus('error', '✘ 文件 "' + file.name + '" 为空文件，无法解析');
      return;
    }
    // 重复文件检测（文件名+大小签名）
    const sig = file.name + '|' + file.size;
    if (App.uploadedFiles.includes(sig)) {
      showStatus('error', '✘ 文件 "' + file.name + '" 已上传过，请勿重复上传');
      return;
    }
    App.uploadedFiles.push(sig);
    if (['xlsx', 'xls', 'csv'].includes(ext)) {
      parseExcelFile(file);
    } else {
      handleImageFile(file);
    }
  });
}

function parseExcelFile(file) {
  showStatus('info', '⏳ 正在读取文件 "' + file.name + '" ...');
  showProgress(file.name, 5);
  const reader = new FileReader();
  reader.onprogress = function (e) {
    if (e.lengthComputable) updateProgress(5 + e.loaded / e.total * 65);
  };
  reader.onload = async function (e) {
    updateProgress(75);
    try {
      const wb = XLSX.read(e.target.result, { type: 'array' });
      const ws = wb.Sheets[wb.SheetNames[0]];
      const rows = XLSX.utils.sheet_to_json(ws);
      if (rows.length === 0) {
        showStatus('error', '✘ 文件 "' + file.name + '" 内容为空，无有效数据行');
        hideProgress(); return;
      }
      // 列名匹配
      const cols = Object.keys(rows[0]);
      const matched = {};
      for (const [key, candidates] of Object.entries(EXCEL_COL_MAP)) {
        matched[key] = cols.find(c => candidates.some(k =>
          String(c).toLowerCase().includes(String(k).toLowerCase())));
      }
      if (!matched.type) {
        showStatus('error', '✘ 文件 "' + file.name + '" 缺少必要列（需包含"病害类型"列）。检测到列：' + cols.join('、'));
        hideProgress(); return;
      }
      updateProgress(88);
      if (App.agentOnline) {
        // —— 在线模式：调用巡检智能体进行病害复核分析 ——
        try {
          const res = await AgentAPI.analyze(rows, file.name);
          updateProgress(100);
          if (res.success && res.defects && res.defects.length > 0) {
            res.defects.forEach(d => App.defects.push(d));
            renderDataPreview(rows, file.name);
            const st = res.stats || {};
            showStatus('success', '✔ 智能体分析完成："' + file.name + '" 复核有效病害 ' + res.defects.length + ' 处' +
              (st.adjusted ? '，严重程度修正 ' + st.adjusted + ' 处' : '') +
              (st.critical ? '，严重病害 ' + st.critical + ' 处已重点标记★' : ''));
            showAgentLog(res.agent_log, '病害复核分析');
            refreshAll();
          } else {
            showStatus('error', '✘ 智能体分析"' + file.name + '"未检出有效病害：' + (res.message || '数据无效'));
            if (res.agent_log) showAgentLog(res.agent_log, '病害复核分析');
          }
        } catch (apiErr) {
          showStatus('error', '✘ 智能体调用失败：' + apiErr.message + '，已降级为本地解析');
          parseExcelLocal(rows, matched, file.name);
        }
      } else {
        // —— 离线降级模式：本地解析 ——
        parseExcelLocal(rows, matched, file.name);
      }
      hideProgress();
    } catch (err) {
      showStatus('error', '✘ 文件 "' + file.name + '" 解析失败：' + err.message);
      hideProgress();
    }
  };
  reader.onerror = function () {
    showStatus('error', '✘ 文件 "' + file.name + '" 读取失败，请重试');
    hideProgress();
  };
  reader.readAsArrayBuffer(file);
}

// 离线降级：本地解析Excel病害数据
function parseExcelLocal(rows, matched, fileName) {
  let added = 0, skipped = 0;
  rows.forEach((row, i) => {
    const type = String(row[matched.type] || '').trim();
    const sevRaw = matched.severity ? String(row[matched.severity] || '').trim() : '';
    if (!type) { skipped++; return; }
    // 本地严重程度推断（面积≥5m²判严重，≥1m²判中等）
    const area = matched.area ? (parseFloat(row[matched.area]) || 1.0) : 1.0;
    let sev = sevRaw;
    if (!['严重', '中等', '轻微'].includes(sev)) {
      sev = area >= 5 ? '严重' : area >= 1 ? '中等' : '轻微';
    }
    const lon = matched.lon ? parseFloat(row[matched.lon]) : NaN;
    const lat = matched.lat ? parseFloat(row[matched.lat]) : NaN;
    const cl = App.data.centerline || [];
    let useLon = lon, useLat = lat;
    if (isNaN(useLon) || isNaN(useLat)) {
      if (cl.length >= 2) {
        const t = (i + 1) / Math.max(rows.length, 1);
        const seg = Math.min(Math.floor(t * (cl.length - 1)), cl.length - 2);
        const lt = t * (cl.length - 1) - seg;
        useLon = cl[seg].lon + (cl[seg + 1].lon - cl[seg].lon) * lt;
        useLat = cl[seg].lat + (cl[seg + 1].lat - cl[seg].lat) * lt;
      } else {
        useLon = 113.264; useLat = 23.130;
      }
    }
    const d = {
      defect_id: 'UP-' + String(Date.now()).slice(-6) + '-' + (i + 1),
      type: type, severity: sev, is_key_marked: sev === '严重',
      lon: useLon, lat: useLat,
      area_m2: area,
      length_m: Math.max(1, Math.sqrt(area)),
      width_m: Math.max(0.1, area / Math.max(1, Math.sqrt(area))),
      depth_mm: 0,
      confidence: 0.80, detected_in_frame: '上传数据:' + fileName,
      repair_suggestion: sev === '严重' ? '紧急维修，48小时内处置' :
        sev === '中等' ? '纳入近期维修计划（2周内）' : '常规养护持续观察',
      description: type + '（上传数据·本地解析）',
      station: matched.station ? String(row[matched.station]) : 'K0+000',
    };
    App.defects.push(d);
    added++;
  });
  if (added > 0) {
    renderDataPreview(rows, fileName);
    showStatus('success', '✔ 文件 "' + fileName + '" 本地解析成功：新增病害 ' + added + ' 条' +
      (skipped > 0 ? '，跳过无效行 ' + skipped + ' 条' : ''));
    refreshAll();
  } else {
    showStatus('error', '✘ 文件 "' + fileName + '" 未解析到有效病害数据');
  }
}

function handleImageFile(file) {
  if (!file.type.startsWith('image/')) {
    showStatus('error', '✘ 文件 "' + file.name + '" 不是有效的图片文件');
    return;
  }
  const reader = new FileReader();
  reader.onload = function (e) {
    const gallery = document.getElementById('imageGallery');
    const img = document.createElement('img');
    img.src = e.target.result;
    img.title = file.name;
    img.alt = file.name;
    gallery.appendChild(img);
    // 调用智能体进行航拍影像病害识别
    const probe = new Image();
    probe.onload = async function () {
      if (App.agentOnline) {
        showStatus('info', '⏳ 智能体正在识别航拍影像 "' + file.name + '" ...');
        try {
          const res = await AgentAPI.analyzeImage(file.name, probe.naturalWidth, probe.naturalHeight);
          if (res.success && res.defects && res.defects.length > 0) {
            res.defects.forEach(d => App.defects.push(d));
            const st = res.stats || {};
            showStatus('success', '✔ 智能体识别"' + file.name + '"完成：检出病害 ' + res.defects.length + ' 处' +
              (st.critical ? '，严重 ' + st.critical + ' 处已重点标记★' : ''));
            showAgentLog(res.agent_log, '航拍影像识别');
            refreshAll();
          } else {
            showStatus('info', 'ℹ 智能体识别"' + file.name + '"完成：未检出明显病害');
            if (res.agent_log) showAgentLog(res.agent_log, '航拍影像识别');
          }
        } catch (apiErr) {
          showStatus('error', '✘ 智能体影像识别失败：' + apiErr.message);
        }
      } else {
        showStatus('success', '✔ 航拍影像 "' + file.name + '" 上传成功（离线模式，未执行智能识别）');
      }
    };
    probe.src = e.target.result;
  };
  reader.readAsDataURL(file);
}

function showStatus(type, msg) {
  const box = document.getElementById('uploadStatus');
  const div = document.createElement('div');
  div.className = 'status-msg ' + type;
  div.textContent = msg;
  box.appendChild(div);
  setTimeout(() => div.remove(), 8000);
}

// ======== 上传进度条 ========
function showProgress(fileName, pct) {
  const wrap = document.getElementById('uploadProgress');
  document.getElementById('progressFileName').textContent = '📄 ' + fileName;
  wrap.style.display = 'block';
  updateProgress(pct || 0);
}
function updateProgress(pct) {
  document.getElementById('progressFill').style.width = pct + '%';
  document.getElementById('progressPercent').textContent = Math.round(pct) + '%';
}
function hideProgress() {
  setTimeout(() => { document.getElementById('uploadProgress').style.display = 'none'; }, 800);
}

// ======== 数据预览 ========
function renderDataPreview(rows, fileName) {
  const preview = document.getElementById('dataPreview');
  const previewRows = rows.slice(0, 5);
  const cols = Object.keys(rows[0]);
  let html = '<div class="preview-header">📋 数据预览：' + fileName +
    '（显示前 ' + previewRows.length + ' 行 / 共 ' + rows.length + ' 行）</div>';
  html += '<div class="preview-table-wrap"><table class="preview-table"><thead><tr>';
  cols.forEach(c => html += '<th>' + c + '</th>');
  html += '</tr></thead><tbody>';
  previewRows.forEach(row => {
    html += '<tr>';
    cols.forEach(c => {
      const val = row[c] !== undefined ? row[c] : '';
      const safe = escapeHtml(String(val));
      html += '<td title="' + safe + '">' + safe + '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  preview.innerHTML = html;
  preview.style.display = 'block';
}

// ======== 数据刷新 ========
function refreshAll() {
  renderStatsCards();
  Object.values(App.charts).forEach(c => c && c.destroy());
  App.charts = {};
  renderCharts();
  drawMap();
  renderReport();
}

// ======== 图表渲染 ========
function renderCharts() {
  renderTypeChart();
  renderSeverityChart();
  renderMileageChart();
}

// ======== 图表切换控件 ========
function setupChartToggles() {
  document.querySelectorAll('.chart-toggle').forEach(toggle => {
    toggle.querySelectorAll('.toggle-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const target = toggle.getAttribute('data-target');
        const mode = btn.getAttribute('data-mode');
        if (App.chartModes[target] === mode) return;
        App.chartModes[target] = mode;
        toggle.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        // 仅重绘目标图表
        if (App.charts[target]) { App.charts[target].destroy(); delete App.charts[target]; }
        if (target === 'typeBar') renderTypeChart();
        else if (target === 'sevPie') renderSeverityChart();
      });
    });
  });
}

// 渐变色辅助函数
function makeVerticalGradient(ctx, color, areaHeight) {
  const h = areaHeight || 300;
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, color);
  grad.addColorStop(1, color + '22');
  return grad;
}

function renderTypeChart() {
  const s = computeStats();
  const types = Object.keys(s.byType);
  const counts = types.map(t => s.byType[t]);
  const colors = types.map(t => TYPE_COLORS[t] || '#64748b');
  const canvas = document.getElementById('typeBarChart');
  const mode = App.chartModes.typeBar || 'bar';
  if (mode === 'pie') {
    App.charts.typeBar = new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: types,
        datasets: [{
          data: counts,
          backgroundColor: colors.map(c => c + 'dd'),
          borderColor: '#fff', borderWidth: 3, hoverOffset: 12,
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '52%',
        animation: { animateRotate: true, duration: 1000, easing: 'easeOutQuart' },
        plugins: {
          legend: { position: 'right', labels: { padding: 14, font: { size: 13 }, usePointStyle: true, pointStyle: 'circle' } },
          tooltip: {
            backgroundColor: '#0f172a', padding: 12, cornerRadius: 8,
            callbacks: {
              label: ctx => {
                const tot = ctx.dataset.data.reduce((a, b) => a + b, 0) || 1;
                return ctx.label + '：' + ctx.parsed + ' 处（' + (ctx.parsed / tot * 100).toFixed(1) + '%）';
              }
            }
          }
        }
      },
      plugins: [centerTextPlugin]
    });
    return;
  }
  const ctx2d = canvas.getContext('2d');
  App.charts.typeBar = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: types,
      datasets: [{
        label: '病害数量',
        data: counts,
        backgroundColor: colors.map(c => makeVerticalGradient(ctx2d, c, 300)),
        borderColor: colors,
        borderWidth: 2,
        borderRadius: 8,
        maxBarThickness: 64,
        hoverBackgroundColor: colors,
        hoverBorderWidth: 3,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 1200, easing: 'easeOutQuart' },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0f172a', padding: 12, cornerRadius: 8,
          callbacks: {
            label: ctx => {
              const tot = ctx.dataset.data.reduce((a, b) => a + b, 0) || 1;
              return ctx.parsed.y + ' 处（' + (ctx.parsed.y / tot * 100).toFixed(1) + '%）';
            }
          }
        }
      },
      scales: {
        y: { beginAtZero: true, ticks: { stepSize: 1 }, grid: { color: '#f1f5f9' } },
        x: { grid: { display: false }, ticks: { font: { size: 13 } } }
      }
    }
  });
}

// 中心文字插件（饼图显示总数）
const centerTextPlugin = {
  id: 'centerText',
  afterDraw(chart) {
    if (chart.config.type !== 'doughnut') return;
    const data = chart.data.datasets[0].data;
    const total = data.reduce((a, b) => a + b, 0);
    const { ctx, chartArea } = chart;
    const cx = (chartArea.left + chartArea.right) / 2;
    const cy = (chartArea.top + chartArea.bottom) / 2;
    ctx.save();
    ctx.font = 'bold 30px "Microsoft YaHei",sans-serif';
    ctx.fillStyle = '#0f172a';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(total, cx, cy - 10);
    ctx.font = '13px "Microsoft YaHei",sans-serif';
    ctx.fillStyle = '#64748b';
    ctx.fillText('病害总数', cx, cy + 16);
    ctx.restore();
  }
};

function renderSeverityChart() {
  const s = computeStats();
  const canvas = document.getElementById('severityPieChart');
  const mode = App.chartModes.sevPie || 'pie';
  if (mode === 'bar') {
    const sevs = ['严重', '中等', '轻微'];
    const colors = sevs.map(x => SEV_COLORS[x]);
    App.charts.sevPie = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: sevs,
        datasets: [{
          label: '病害数量',
          data: [s.critical, s.moderate, s.minor],
          backgroundColor: colors.map(c => makeVerticalGradient(canvas.getContext('2d'), c, 300)),
          borderColor: colors, borderWidth: 2, borderRadius: 8, maxBarThickness: 72,
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: { duration: 1000, easing: 'easeOutQuart' },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#0f172a', padding: 12, cornerRadius: 8,
            callbacks: {
              label: ctx => {
                const tot = ctx.dataset.data.reduce((a, b) => a + b, 0) || 1;
                return ctx.parsed.y + ' 处（' + (ctx.parsed.y / tot * 100).toFixed(1) + '%）';
              }
            }
          }
        },
        scales: {
          y: { beginAtZero: true, ticks: { stepSize: 1 }, grid: { color: '#f1f5f9' } },
          x: { grid: { display: false }, ticks: { font: { size: 13 } } }
        }
      }
    });
    return;
  }
  App.charts.sevPie = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: ['严重', '中等', '轻微'],
      datasets: [{
        data: [s.critical, s.moderate, s.minor],
        backgroundColor: [SEV_COLORS['严重'] + 'dd', SEV_COLORS['中等'] + 'dd', SEV_COLORS['轻微'] + 'dd'],
        borderColor: ['#fff', '#fff', '#fff'],
        borderWidth: 3,
        hoverOffset: 12,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: '58%',
      animation: { animateRotate: true, duration: 1200, easing: 'easeOutQuart' },
      plugins: {
        legend: { position: 'bottom', labels: { padding: 18, font: { size: 13 }, usePointStyle: true, pointStyle: 'circle' } },
        tooltip: {
          backgroundColor: '#0f172a', padding: 12, cornerRadius: 8,
          callbacks: {
            label: ctx => {
              const total = ctx.dataset.data.reduce((a, b) => a + b, 0) || 1;
              return ctx.label + '：' + ctx.parsed + ' 处（' + (ctx.parsed / total * 100).toFixed(1) + '%）';
            }
          }
        }
      }
    },
    plugins: [centerTextPlugin]
  });
}

function renderMileageChart() {
  const s = computeStats();
  const roadLen = App.data.road_length_m || 1400;
  const segLen = 200;
  const nSeg = Math.max(1, Math.ceil(roadLen / segLen));
  const labels = [], crit = [], mod = [], minor = [];
  for (let i = 0; i < nSeg; i++) {
    const sm = i * segLen, em = Math.min((i + 1) * segLen, roadLen);
    labels.push('K' + Math.floor(sm / 1000) + '+' + String(Math.round(sm) % 1000).padStart(3, '0') +
      '~K' + Math.floor(em / 1000) + '+' + String(Math.round(em) % 1000).padStart(3, '0'));
    crit.push(0); mod.push(0); minor.push(0);
  }
  App.defects.forEach(d => {
    const sm = stationToM(d.station);
    const idx = Math.min(Math.floor(sm / segLen), nSeg - 1);
    if (d.severity === '严重') crit[idx]++;
    else if (d.severity === '中等') mod[idx]++;
    else minor[idx]++;
  });
  App.charts.mileage = new Chart(document.getElementById('mileageChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: '严重', data: crit, backgroundColor: SEV_COLORS['严重'] + 'cc', borderColor: SEV_COLORS['严重'], borderWidth: 1, borderRadius: 4 },
        { label: '中等', data: mod, backgroundColor: SEV_COLORS['中等'] + 'cc', borderColor: SEV_COLORS['中等'], borderWidth: 1, borderRadius: 4 },
        { label: '轻微', data: minor, backgroundColor: SEV_COLORS['轻微'] + 'cc', borderColor: SEV_COLORS['轻微'], borderWidth: 1, borderRadius: 4 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 1000, easing: 'easeOutQuart' },
      plugins: {
        legend: { position: 'bottom', labels: { padding: 16, font: { size: 13 }, usePointStyle: true, pointStyle: 'rectRounded' } },
        tooltip: { backgroundColor: '#0f172a', padding: 12, cornerRadius: 8 }
      },
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: { font: { size: 11 } } },
        y: { stacked: true, beginAtZero: true, ticks: { stepSize: 1 }, grid: { color: '#f1f5f9' } }
      }
    }
  });
}

function stationToM(st) {
  try {
    const parts = String(st).replace('K', '').split('+');
    return parseInt(parts[0]) * 1000 + parseInt(parts[1] || '0');
  } catch (e) { return 0; }
}

// ======== 地图可视化 ========
function drawMap() {
  const canvas = document.getElementById('mapCanvas');
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const cl = App.data.centerline || [];
  const defs = App.defects;
  const obs = App.data.obstacles || [];
  if (cl.length === 0) return;

  // 计算坐标范围
  const allPts = cl.concat(defs.map(d => ({ lon: d.lon, lat: d.lat })));
  let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
  allPts.forEach(p => {
    minLon = Math.min(minLon, p.lon); maxLon = Math.max(maxLon, p.lon);
    minLat = Math.min(minLat, p.lat); maxLat = Math.max(maxLat, p.lat);
  });
  const padX = (maxLon - minLon) * 0.15 || 0.001;
  const padY = (maxLat - minLat) * 0.15 || 0.001;
  minLon -= padX; maxLon += padX; minLat -= padY; maxLat += padY;
  const toX = lon => 50 + (lon - minLon) / (maxLon - minLon) * (W - 100);
  const toY = lat => H - 50 - (lat - minLat) / (maxLat - minLat) * (H - 100);

  ctx.clearRect(0, 0, W, H);
  // 背景网格
  ctx.strokeStyle = '#f1f5f9'; ctx.lineWidth = 1;
  for (let x = 0; x < W; x += 50) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
  for (let y = 0; y < H; y += 50) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

  // 道路（宽带 + 中心线）
  const clPx = cl.map(p => ({ x: toX(p.lon), y: toY(p.lat) }));
  ctx.strokeStyle = '#cbd5e1'; ctx.lineWidth = 22; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.beginPath(); clPx.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)); ctx.stroke();
  ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 18;
  ctx.beginPath(); clPx.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)); ctx.stroke();
  // 中心虚线
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.setLineDash([10, 8]);
  ctx.beginPath(); clPx.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)); ctx.stroke();
  ctx.setLineDash([]);

  // 起终点标记
  drawEndpoint(ctx, clPx[0].x, clPx[0].y, '起点', '#22c55e');
  drawEndpoint(ctx, clPx[clPx.length - 1].x, clPx[clPx.length - 1].y, '终点', '#ef4444');

  // 障碍物标记
  App.mapPoints = [];
  obs.forEach(o => {
    const x = toX(o.lon), y = toY(o.lat);
    ctx.fillStyle = '#fbbf24'; ctx.strokeStyle = '#d97706'; ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, y - 7); ctx.lineTo(x + 6, y + 5); ctx.lineTo(x - 6, y + 5);
    ctx.closePath(); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#92400e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(o.type, x, y + 18);
    App.mapPoints.push({ x, y, r: 10, data: { kind: '障碍物', type: o.type, note: o.note || '' } });
  });

  // 病害点位
  defs.forEach(d => {
    const x = toX(d.lon), y = toY(d.lat);
    const color = SEV_COLORS[d.severity] || '#64748b';
    const r = d.is_key_marked ? 8 : 5;
    // 光晕（严重病害）
    if (d.severity === '严重') {
      ctx.fillStyle = color + '33';
      ctx.beginPath(); ctx.arc(x, y, r + 6, 0, Math.PI * 2); ctx.fill();
    }
    ctx.fillStyle = color; ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    // 严重病害星标
    if (d.is_key_marked) {
      ctx.fillStyle = '#fff'; ctx.font = 'bold 9px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText('★', x, y + 0.5);
    }
    App.mapPoints.push({
      x, y, r: r + 4,
      data: {
        kind: '病害', id: d.defect_id, type: d.type, severity: d.severity,
        station: d.station, area: d.area_m2, lon: d.lon, lat: d.lat,
        suggestion: d.repair_suggestion,
      }
    });
  });

  // 鼠标交互
  canvas.onmousemove = function (e) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
    const mx = (e.clientX - rect.left) * scaleX, my = (e.clientY - rect.top) * scaleY;
    let hit = null;
    for (const p of App.mapPoints) {
      const dx = mx - p.x, dy = my - p.y;
      if (dx * dx + dy * dy <= p.r * p.r) { hit = p; break; }
    }
    const tip = document.getElementById('mapTooltip');
    if (hit) {
      const d = hit.data;
      let html = '';
      if (d.kind === '病害') {
        html = '<b>' + escapeHtml(d.id) + '</b> [' + escapeHtml(d.type) + ' · ' + escapeHtml(d.severity) + ']<br>' +
          '桩号：' + escapeHtml(d.station) + ' | 面积：' + escapeHtml(String(d.area)) + ' m²<br>' +
          '坐标：(' + escapeHtml(String(d.lon)) + ', ' + escapeHtml(String(d.lat)) + ')<br>' + escapeHtml(d.suggestion);
      } else {
        html = '<b>障碍物</b> [' + escapeHtml(d.type) + ']<br>' + escapeHtml(d.note || '');
      }
      tip.innerHTML = html;
      tip.style.display = 'block';
      const wrap = canvas.parentElement.getBoundingClientRect();
      tip.style.left = (e.clientX - wrap.left + 14) + 'px';
      tip.style.top = (e.clientY - wrap.top + 14) + 'px';
    } else {
      tip.style.display = 'none';
    }
  };
  canvas.onmouseleave = () => { document.getElementById('mapTooltip').style.display = 'none'; };
}

function drawEndpoint(ctx, x, y, label, color) {
  ctx.fillStyle = color; ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
  ctx.beginPath();
    ctx.moveTo(x, y - 9); ctx.lineTo(x + 7, y); ctx.lineTo(x, y + 9); ctx.lineTo(x - 7, y);
  ctx.closePath(); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#1e293b'; ctx.font = 'bold 12px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(label, x, y - 14);
}

function renderMapLegend() {
  const items = [
    { color: SEV_COLORS['严重'], label: '严重病害 ★' },
    { color: SEV_COLORS['中等'], label: '中等病害' },
    { color: SEV_COLORS['轻微'], label: '轻微病害' },
    { color: '#fbbf24', label: '障碍物', shape: 'tri' },
    { color: '#94a3b8', label: '道路中心线' },
  ];
  document.getElementById('mapLegend').innerHTML = items.map(i =>
    '<span class="legend-item">' +
    (i.shape === 'tri'
      ? '<span class="legend-dot" style="background:transparent;border-left:7px solid transparent;border-right:7px solid transparent;border-bottom:12px solid ' + i.color + ';width:0;height:0;border-radius:0"></span>'
      : '<span class="legend-dot" style="background:' + i.color + '"></span>') +
    i.label + '</span>'
  ).join('');
}

// ======== 报告生成 ========
function sevTag(sev) {
  const cls = sev === '严重' ? 'tag-critical' : sev === '中等' ? 'tag-moderate' : 'tag-minor';
  return '<span class="' + cls + '">' + sev + '</span>';
}

function renderReport() {
  document.getElementById('reportPreview').innerHTML = buildReportHTML();
}

function buildReportHTML() {
  const s = computeStats();
  const m = App.data.mission || {};
  const now = new Date();
  const ts = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' +
    String(now.getDate()).padStart(2, '0') + ' ' + String(now.getHours()).padStart(2, '0') + ':' +
    String(now.getMinutes()).padStart(2, '0') + ':' + String(now.getSeconds()).padStart(2, '0');
  const h = [];

  h.push('<h1>无人机道路巡检检修报告</h1>');
  h.push('<blockquote><b>任务编号</b>：' + esc(m.mission_id || '-') + '<br>' +
    '<b>巡检道路</b>：' + esc(m.road_name || '-') + '（' + esc(m.road_grade || '-') + '）<br>' +
    '<b>执行单位</b>：无人机道路巡检智能体<br>' +
    '<b>报告生成时间</b>：' + ts + '</blockquote>');
  // 摘要统计卡片
  h.push('<div class="report-summary">');
  h.push('<div class="rs-card"><div class="rs-value">' + s.total + '</div><div class="rs-label">病害总数</div></div>');
  h.push('<div class="rs-card rs-critical"><div class="rs-value">' + s.critical + '</div><div class="rs-label">严重病害</div></div>');
  h.push('<div class="rs-card rs-moderate"><div class="rs-value">' + s.moderate + '</div><div class="rs-label">中等病害</div></div>');
  h.push('<div class="rs-card rs-minor"><div class="rs-value">' + s.minor + '</div><div class="rs-label">轻微病害</div></div>');
  h.push('<div class="rs-card"><div class="rs-value">' + s.totalArea + '</div><div class="rs-label">总面积(m²)</div></div>');
  h.push('</div>');

  // 一、任务概述
  h.push('<h2>一、任务概述</h2>');
  h.push('<p>本次巡检任务对 <b>' + esc(m.road_name || '-') + '</b> 开展无人机航拍巡检，排查路面坑洼、裂缝、车辙、沉陷、边缘破损等病害，为道路维护检修提供数据支撑。</p>');
  h.push('<table><tr><th>项目</th><th>内容</th></tr>' +
    '<tr><td>道路等级</td><td>' + esc(m.road_grade || '-') + '</td></tr>' +
    '<tr><td>路面宽度</td><td>' + esc(m.road_width_m || '-') + ' m</td></tr>' +
    '<tr><td>巡检飞行高度</td><td>' + esc(m.flight_altitude || '-') + ' m</td></tr>' +
    '<tr><td>拍摄间隔</td><td>' + esc(m.capture_interval_m || '-') + ' m</td></tr>' +
    '<tr><td>影像重叠率</td><td>' + ((m.overlap_rate || 0) * 100) + '%</td></tr>' +
    '<tr><td>起点坐标</td><td>(' + num(m.start_point ? m.start_point.lon : 0, 6) + ', ' + num(m.start_point ? m.start_point.lat : 0, 6) + ')</td></tr>' +
    '<tr><td>终点坐标</td><td>(' + num(m.end_point ? m.end_point.lon : 0, 6) + ', ' + num(m.end_point ? m.end_point.lat : 0, 6) + ')</td></tr></table>');

  // 二、巡检执行情况
  h.push('<h2>二、巡检执行情况</h2>');
  h.push('<table><tr><th>指标</th><th>数值</th></tr>' +
    '<tr><td>规划航点数</td><td>' + (App.data.total_waypoints || '-') + '</td></tr>' +
    '<tr><td>实际到达航点</td><td>' + (App.data.visited_waypoints || '-') + '</td></tr>' +
    '<tr><td>采集影像帧数</td><td>' + s.frames + '</td></tr>' +
    '<tr><td>已知障碍物数</td><td>' + (App.data.obstacles || []).length + '</td></tr>' +
    '<tr><td>避障绕行次数</td><td>' + (App.data.avoidance_events || []).filter(e => e.type === 'detour').length + '</td></tr></table>');

  // 三、病害检测汇总
  h.push('<h2>三、病害检测汇总</h2>');
  h.push('<p>本次巡检共识别路面病害 <b>' + s.total + '</b> 处，病害总面积约 <b>' + s.totalArea + ' m²</b>。</p>');
  h.push('<h3>3.1 严重程度分布</h3>');
  const tot = Math.max(s.total, 1);
  h.push('<table><tr><th>严重程度</th><th>数量</th><th>占比</th></tr>' +
    ['严重', '中等', '轻微'].map(sev =>
      '<tr><td>' + sevTag(sev) + '</td><td>' + s.bySev[sev] + '</td><td>' + (s.bySev[sev] / tot * 100).toFixed(1) + '%</td></tr>'
    ).join('') + '</table>');
  h.push('<h3>3.2 病害类型分布</h3>');
  const typeRows = Object.entries(s.byType).sort((a, b) => b[1] - a[1]).map(([t, c]) =>
    '<tr><td>' + esc(t) + '</td><td>' + c + '</td><td>' + (c / tot * 100).toFixed(1) + '%</td></tr>').join('');
  h.push('<table><tr><th>病害类型</th><th>数量</th><th>占比</th></tr>' + typeRows + '</table>');

  // 四、严重病害重点标记
  const crits = App.defects.filter(d => d.severity === '严重');
  h.push('<h2>四、严重病害重点标记 ★</h2>');
  if (crits.length > 0) {
    h.push('<p>检出 <b>' + crits.length + '</b> 处严重路面病害，已全部重点标记，建议优先安排处置：</p>');
    h.push('<table><tr><th>标记</th><th>编号</th><th>类型</th><th>桩号</th><th>面积(m²)</th><th>深度(mm)</th><th>维修建议</th></tr>');
    crits.forEach(d => {
      h.push('<tr><td>★</td><td>' + esc(d.defect_id) + '</td><td>' + esc(d.type) + '</td><td>' + esc(d.station) +
        '</td><td>' + num(d.area_m2) + '</td><td>' + num(d.depth_mm, 0) + '</td><td>' + esc(d.repair_suggestion) + '</td></tr>');
    });
    h.push('</table>');
  } else {
    h.push('<p>本次巡检未检出严重路面病害。</p>');
  }

  // 五、病害明细清单
  h.push('<h2>五、病害明细清单</h2>');
  if (App.defects.length > 0) {
    h.push('<table><tr><th>编号</th><th>类型</th><th>严重程度</th><th>桩号</th><th>坐标</th><th>面积(m²)</th><th>置信度</th></tr>');
    App.defects.forEach(d => {
      h.push('<tr><td>' + esc(d.defect_id) + (d.is_key_marked ? '★' : '') + '</td><td>' + esc(d.type) +
        '</td><td>' + sevTag(d.severity) + '</td><td>' + esc(d.station) +
        '</td><td>(' + num(d.lon, 6) + ', ' + num(d.lat, 6) + ')</td><td>' + num(d.area_m2) +
        '</td><td>' + num(d.confidence) + '</td></tr>');
    });
    h.push('</table>');
  }

  // 六、维修建议
  h.push('<h2>六、维修建议</h2>');
  h.push('<ol>');
  if (s.critical > 0) h.push('<li><b>紧急处置</b>：' + s.critical + ' 处严重病害需在 <b>48小时内</b> 安排维修，处置前应设置警示标志保障通行安全。</li>');
  if (s.moderate > 0) h.push('<li><b>计划维修</b>：' + s.moderate + ' 处中等病害建议纳入 <b>近期维修计划（2周内）</b>。</li>');
  if (s.minor > 0) h.push('<li><b>常规养护</b>：' + s.minor + ' 处轻微病害持续观察，纳入常规养护。</li>');
  h.push('<li><b>预防建议</b>：对裂缝密集区域建议开展预防性养护（雾封层/微表处），延缓病害扩展。</li>');
  h.push('</ol>');

  h.push('<div class="report-footer">本报告由无人机道路巡检智能体自动生成 | ' + ts + '</div>');
  return h.join('\n');
}

// ======== 导出功能 ========
function bindExportButtons() {
  document.getElementById('btnExportMd').onclick = exportMarkdown;
  document.getElementById('btnExportHtml').onclick = exportHTML;
  document.getElementById('btnExportJson').onclick = exportJSON;
  document.getElementById('btnPrint').onclick = () => {
    const w = window.open('', '_blank');
    w.document.write('<html><head><meta charset="utf-8"><title>巡检报告打印</title><style>' +
      'body{font-family:"Microsoft YaHei",sans-serif;padding:40px;color:#1e293b}' +
      'h1{text-align:center;border-bottom:3px solid #2563eb;padding-bottom:12px}' +
      'h2{border-left:4px solid #2563eb;padding-left:10px;margin-top:24px}' +
      'table{width:100%;border-collapse:collapse;margin:10px 0;font-size:13px}' +
      'th{background:#f1f5f9}th,td{border:1px solid #e2e8f0;padding:6px 8px;text-align:left}' +
      'blockquote{background:#eff6ff;border-left:4px solid #2563eb;padding:10px 14px}' +
      '</style></head><body>' + buildReportHTML() + '</body></html>');
    w.document.close();
    setTimeout(() => w.print(), 300);
  };
}

function downloadFile(filename, content, mime) {
  const blob = new Blob([content], { type: mime || 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportMarkdown() {
  const s = computeStats();
  const m = App.data.mission || {};
  const now = new Date();
  const ts = now.toLocaleString('zh-CN');
  const L = [];
  L.push('# 无人机道路巡检检修报告');
  L.push('');
  L.push('> **任务编号**：' + (m.mission_id || '-'));
  L.push('> **巡检道路**：' + (m.road_name || '-'));
  L.push('> **报告生成时间**：' + ts);
  L.push('');
  L.push('## 一、任务概述');
  L.push('');
  L.push('| 项目 | 内容 |');
  L.push('|------|------|');
  L.push('| 道路等级 | ' + (m.road_grade || '-') + ' |');
  L.push('| 路面宽度 | ' + (m.road_width_m || '-') + ' m |');
  L.push('| 巡检飞行高度 | ' + (m.flight_altitude || '-') + ' m |');
  L.push('');
  L.push('## 二、病害检测汇总');
  L.push('');
  L.push('共识别病害 **' + s.total + '** 处，总面积 **' + s.totalArea + ' m²**。');
  L.push('');
  L.push('| 严重程度 | 数量 |');
  L.push('|----------|------|');
  L.push('| 严重 | ' + s.critical + ' |');
  L.push('| 中等 | ' + s.moderate + ' |');
  L.push('| 轻微 | ' + s.minor + ' |');
  L.push('');
  L.push('| 病害类型 | 数量 |');
  L.push('|----------|------|');
  Object.entries(s.byType).sort((a, b) => b[1] - a[1]).forEach(([t, c]) => L.push('| ' + t + ' | ' + c + ' |'));
  L.push('');
  L.push('## 三、病害明细清单');
  L.push('');
  L.push('| 编号 | 类型 | 严重程度 | 桩号 | 坐标 | 面积(m²) | 维修建议 |');
  L.push('|------|------|---------|------|------|---------|---------|');
  App.defects.forEach(d => {
    L.push('| ' + d.defect_id + (d.is_key_marked ? '★' : '') + ' | ' + d.type + ' | ' + d.severity +
      ' | ' + d.station + ' | (' + num(d.lon, 6) + ', ' + num(d.lat, 6) + ') | ' + num(d.area_m2) + ' | ' + d.repair_suggestion + ' |');
  });
  L.push('');
  L.push('## 四、维修建议');
  L.push('');
  if (s.critical > 0) L.push('1. **紧急处置**：' + s.critical + ' 处严重病害需48小时内维修。');
  if (s.moderate > 0) L.push('2. **计划维修**：' + s.moderate + ' 处中等病害2周内维修。');
  if (s.minor > 0) L.push('3. **常规养护**：' + s.minor + ' 处轻微病害持续观察。');
  L.push('');
  L.push('---');
  L.push('*本报告由无人机道路巡检智能体自动生成 | ' + ts + '*');
  downloadFile((m.mission_id || '巡检报告') + '_检修报告.md', L.join('\n'), 'text/markdown');
}

function exportHTML() {
  const html = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">' +
    '<title>巡检报告</title><style>' +
    'body{font-family:"Microsoft YaHei",sans-serif;max-width:900px;margin:0 auto;padding:40px;color:#1e293b;background:#f8fafc}' +
    'h1{text-align:center;border-bottom:3px solid #2563eb;padding-bottom:12px}' +
    'h2{border-left:4px solid #2563eb;padding-left:10px;margin-top:28px;color:#0f172a}' +
    'h3{color:#1d4ed8;margin-top:16px}' +
    'table{width:100%;border-collapse:collapse;margin:10px 0;font-size:13px;background:#fff}' +
    'th{background:#f1f5f9}th,td{border:1px solid #e2e8f0;padding:6px 10px;text-align:left}' +
    'tr:nth-child(even) td{background:#fafbfc}' +
    'blockquote{background:#eff6ff;border-left:4px solid #2563eb;padding:12px 16px;margin:12px 0;border-radius:0 8px 8px 0}' +
    'ol{margin:8px 0 8px 24px}p{margin:8px 0}' +
    '</style></head><body>' + buildReportHTML() + '</body></html>';
  const m = App.data.mission || {};
  downloadFile((m.mission_id || '巡检报告') + '_检修报告.html', html, 'text/html');
}

function exportJSON() {
  const m = App.data.mission || {};
  const data = JSON.stringify({
    mission: m,
    defects: App.defects,
    centerline: App.data.centerline,
    obstacles: App.data.obstacles,
    total_waypoints: App.data.total_waypoints,
    total_frames: App.data.total_frames,
    road_length_m: App.data.road_length_m,
  }, null, 2);
  downloadFile((m.mission_id || '巡检数据') + '_巡检结果.json', data, 'application/json');
}

// ======== 图表操作（一键巡检 / 模拟识别 / 重置数据） ========
function setupChartActions() {
  document.getElementById('btnSimulate').onclick = simulateDetection;
  document.getElementById('btnRefreshCharts').onclick = resetData;
  const btnRun = document.getElementById('btnRunInspection');
  if (btnRun) btnRun.onclick = runFullInspection;
  const btnLoad = document.getElementById('btnLoadResults');
  if (btnLoad) btnLoad.onclick = loadLatestResults;
  const logClose = document.getElementById('logCloseBtn');
  if (logClose) logClose.onclick = hideAgentLog;
}

// ======== 一键全流程巡检（调用智能体完整流程） ========
async function runFullInspection() {
  const btn = document.getElementById('btnRunInspection');
  if (!App.agentOnline) {
    showStatus('error', '✘ 巡检智能体未连接，无法执行全流程巡检。请先启动服务：python-embed\\python.exe server.py');
    return;
  }
  btn.disabled = true;
  const original = btn.innerHTML;
  btn.innerHTML = '&#8987; 巡检执行中...';
  showStatus('info', '⏳ 智能体正在执行全流程巡检：任务接收 → 路径规划 → 飞行采集 → 避障 → 病害识别 → 报告生成 ...');
  try {
    const res = await AgentAPI.runInspection();
    if (res.success && res.data) {
      // 用智能体返回的完整巡检数据替换当前数据
      App.data = res.data;
      App.defects = res.data.defects || [];
      App.initialDefects = JSON.parse(JSON.stringify(App.defects));
      App.uploadedFiles = [];
      document.getElementById('dataPreview').style.display = 'none';
      document.getElementById('imageGallery').innerHTML = '';
      refreshAll();
      showStatus('success', '✔ ' + (res.message || '全流程巡检完成'));
      if (res.agent_log && res.agent_log.length) showAgentLog(res.agent_log, '全流程巡检');
    } else {
      showStatus('error', '✘ 巡检中止：' + (res.message || '未知错误'));
      if (res.agent_log && res.agent_log.length) showAgentLog(res.agent_log, '全流程巡检');
    }
  } catch (err) {
    showStatus('error', '✘ 智能体调用失败：' + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
  }
}

function simulateDetection() {
  const types = Object.keys(TYPE_COLORS);
  const sevs = ['严重', '中等', '轻微'];
  const sevWeights = [0.2, 0.45, 0.35];
  const n = 1 + Math.floor(Math.random() * 3);
  const ts = Date.now().toString().slice(-6);
  for (let i = 0; i < n; i++) {
    const type = types[Math.floor(Math.random() * types.length)];
    const r = Math.random();
    let sev = '轻微', acc = 0;
    for (let j = 0; j < sevs.length; j++) {
      acc += sevWeights[j];
      if (r <= acc) { sev = sevs[j]; break; }
    }
    const m = Math.floor(Math.random() * 1400);
    App.defects.push({
      defect_id: 'SIM-' + ts + '-' + (i + 1),
      type: type, severity: sev, is_key_marked: sev === '严重',
      lon: 116.398 + Math.random() * 0.01,
      lat: 39.910 + Math.random() * 0.008,
      area_m2: Math.round((0.5 + Math.random() * 8) * 100) / 100,
      length_m: Math.round((0.5 + Math.random() * 5) * 10) / 10,
      width_m: Math.round((0.2 + Math.random() * 2) * 10) / 10,
      depth_mm: Math.floor(Math.random() * 60),
      confidence: Math.round((0.75 + Math.random() * 0.22) * 100) / 100,
      detected_in_frame: '模拟帧#' + (100 + Math.floor(Math.random() * 200)),
      repair_suggestion: sev === '严重' ? '紧急维修，48小时内处置' :
        sev === '中等' ? '纳入近期维修计划（2周内）' : '常规养护持续观察',
      description: type + '（模拟识别）',
      station: 'K' + Math.floor(m / 1000) + '+' + String(m % 1000).padStart(3, '0'),
    });
  }
  refreshAll();
  showStatus('success', '✔ 模拟AI识别完成，新增 ' + n + ' 处病害，图表与报告已动态更新');
}

function resetData() {
  App.defects = JSON.parse(JSON.stringify(App.initialDefects));
  App.uploadedFiles = [];
  document.getElementById('dataPreview').style.display = 'none';
  document.getElementById('imageGallery').innerHTML = '';
  refreshAll();
  showStatus('info', '↺ 已重置为初始巡检数据（' + App.defects.length + ' 处病害）');
}

// ======== 滚动入场动画 ========
function setupScrollAnimations() {
  const targets = document.querySelectorAll(
    '.section-title, .feature-card, .chart-card, .stat-card, .tech-card, .flow-step, .compare-wrapper, .upload-area, .map-wrapper, .report-preview'
  );
  targets.forEach(el => el.classList.add('fade-init'));
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('fade-in-up');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.08, rootMargin: '0px 0px -40px 0px' });
  targets.forEach(el => observer.observe(el));
}

// ======== 导航栏当前区块高亮 ========
function setupNavHighlight() {
  const sections = document.querySelectorAll('section[id]');
  const links = document.querySelectorAll('.nav-links a[href^="#"]');
  let ticking = false;
  window.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      let current = '';
      sections.forEach(sec => {
        if (window.scrollY >= sec.offsetTop - 130) current = sec.id;
      });
      links.forEach(link => {
        link.classList.toggle('active', link.getAttribute('href') === '#' + current);
      });
      ticking = false;
    });
  }, { passive: true });
}

// ======== 启动 ========
window.addEventListener('DOMContentLoaded', init);