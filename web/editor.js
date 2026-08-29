/* PromptForge skeleton editor — COCO-18 joint editing on a <canvas>.
   One figure per character; drag joints, right-click to toggle a joint,
   per-figure mirror / scale / dutch / nudge. Emits normalised frames. */

const COCO18 = {
  names: ['nose','neck','R-sho','R-elb','R-wri','L-sho','L-elb','L-wri',
          'R-hip','R-kne','R-ank','L-hip','L-kne','L-ank','R-eye','L-eye','R-ear','L-ear'],
  limbs: [[1,2],[1,5],[2,3],[3,4],[5,6],[6,7],[1,8],[8,9],[9,10],[1,11],
          [11,12],[12,13],[1,0],[0,14],[14,16],[0,15],[15,17]],
  colors: [[255,0,0],[255,85,0],[255,170,0],[255,255,0],[170,255,0],[85,255,0],
           [0,255,0],[0,255,85],[0,255,170],[0,255,255],[0,170,255],[0,85,255],
           [0,0,255],[85,0,255],[170,0,255],[255,0,255],[255,0,170],[255,0,85]],
  lrPairs: [[2,5],[3,6],[4,7],[8,11],[9,12],[10,13],[14,15],[16,17]],
};

// a plausible standing figure, normalised, centred at cx
function standingPts(cx) {
  const P = {0:[cx,.12],1:[cx,.22],2:[cx-.05,.23],3:[cx-.07,.36],4:[cx-.08,.49],
    5:[cx+.05,.23],6:[cx+.07,.36],7:[cx+.08,.49],8:[cx-.04,.52],9:[cx-.045,.72],
    10:[cx-.05,.93],11:[cx+.04,.52],12:[cx+.045,.72],13:[cx+.05,.93],
    14:[cx-.02,.10],15:[cx+.02,.10],16:[cx-.05,.11],17:[cx+.05,.11]};
  const pts = [];
  for (let i = 0; i < 18; i++) pts.push([P[i][0], P[i][1], 1]);
  return pts;
}

function ptsToFlat(pts) {
  const f = [];
  for (const [x, y, c] of pts) f.push(x, y, c ? 1.0 : 0.0);
  return f;
}
function flatToPts(flat) {
  const pts = [];
  for (let i = 0; i < 54; i += 3) pts.push([flat[i], flat[i + 1], flat[i + 2] > 0 ? 1 : 0]);
  while (pts.length < 18) pts.push([0, 0, 0]);
  return pts;
}

class PoseEditor {
  constructor(canvas, onChange) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.onChange = onChange || (() => {});
    this.figures = [];          // [{pts: [[x,y,c]*18]}]  — facing-neutral, canonical
    this.yaw = 0; this.pitch = 0;   // facing sliders, -1..1; a display + export transform
    this.drag = null;           // {fi, ji}
    this.hist = []; this.redoStack = [];
    canvas.addEventListener('pointerdown', e => this._down(e));
    canvas.addEventListener('pointermove', e => this._move(e));
    window.addEventListener('pointerup', () => this._up());
    canvas.addEventListener('contextmenu', e => { e.preventDefault(); this._toggle(e); });
  }

  setCount(n, libFrame) {
    // keep existing figures; add/remove to reach n
    if (libFrame && libFrame.people && libFrame.people.length === n) {
      this.figures = libFrame.people.map(p => ({ pts: flatToPts(p.pose_keypoints_2d || []) }));
    } else {
      while (this.figures.length < n)
        this.figures.push({ pts: standingPts(n === 1 ? 0.5 : (this.figures.length ? 0.72 : 0.30)) });
      this.figures.length = n;
    }
    this._snap(); this.draw(); this._emit();
  }

  loadFrame(frame) {
    if (!frame || !frame.people || !frame.people.length) return;
    this.figures = frame.people.map(p => ({ pts: flatToPts(p.pose_keypoints_2d || []) }));
    this._snap(); this.draw(); this._emit();
  }

  setOrient(yaw, pitch) {
    this.yaw = Math.max(-1, Math.min(1, +yaw || 0));
    this.pitch = Math.max(-1, Math.min(1, +pitch || 0));
    this.draw(); this._emit();
  }

  // --- yaw/pitch fake-3D, mirror of pose_ops.orient() (backend re-applies it) ---
  _yawSx(y) {
    const th = y >= 0 ? y * Math.PI : y * (Math.PI / 2);
    const sx = Math.cos(th);
    return Math.abs(sx) >= 0.12 ? sx : (sx < 0 ? -0.12 : 0.12);
  }
  _pitchSy(p) { return Math.max(0.4, Math.cos(p * 55 * Math.PI / 180)); }
  _headDx(y) { return Math.sin(y >= 0 ? y * Math.PI : y * (Math.PI / 2)) * 0.03; }
  _centroid(pts) {
    const v = pts.filter(p => p[2]);
    if (!v.length) return [0.5, 0.5];
    return [v.reduce((a, p) => a + p[0], 0) / v.length, v.reduce((a, p) => a + p[1], 0) / v.length];
  }
  _orientPts(pts) {
    if (!this.yaw && !this.pitch) return pts;
    const [cx, cy] = this._centroid(pts);
    const sx = this._yawSx(this.yaw), sy = this._pitchSy(this.pitch), hdx = this._headDx(this.yaw);
    const head = new Set([0, 14, 15, 16, 17]);
    return pts.map(([x, y, c], i) => c
      ? [cx + (x - cx) * sx + (head.has(i) ? hdx : 0), cy + (y - cy) * sy, c]
      : [x, y, c]);
  }
  _unorientPt(nx, ny, pts, ji) {
    if (!this.yaw && !this.pitch) return [nx, ny];
    const [cx, cy] = this._centroid(pts);
    const sx = this._yawSx(this.yaw), sy = this._pitchSy(this.pitch);
    const hdx = new Set([0, 14, 15, 16, 17]).has(ji) ? this._headDx(this.yaw) : 0;
    return [cx + (nx - cx - hdx) / sx, cy + (ny - cy) / sy];
  }

  getFrame(w, h) {
    return {
      people: this.figures.map(f => ({
        pose_keypoints_2d: ptsToFlat(f.pts),
        face_keypoints_2d: null, hand_left_keypoints_2d: null, hand_right_keypoints_2d: null,
      })),
      canvas_width: w, canvas_height: h,
    };
  }

  // ---- per-figure transforms ----
  _xf(fi, fn) {
    const f = this.figures[fi]; if (!f) return;
    f.pts = f.pts.map(([x, y, c]) => c ? [...fn(x, y), c] : [x, y, c]);
    this._snap(); this.draw(); this._emit();
  }
  mirror(fi) {
    const f = this.figures[fi]; if (!f) return;
    f.pts = f.pts.map(([x, y, c]) => [1 - x, y, c]);
    for (const [a, b] of COCO18.lrPairs) { const t = f.pts[a]; f.pts[a] = f.pts[b]; f.pts[b] = t; }
    this._snap(); this.draw(); this._emit();
  }
  scale(fi, s) { this._xf(fi, (x, y) => [(x - 0.5) * s + 0.5, (y - 0.5) * s + 0.5]); }
  nudge(fi, dx, dy) { this._xf(fi, (x, y) => [x + dx, y + dy]); }
  dutch(fi, deg, aspect) {
    const r = deg * Math.PI / 180, cs = Math.cos(r), sn = Math.sin(r);
    this._xf(fi, (x, y) => {
      const px = (x - 0.5) * aspect, py = (y - 0.5);
      return [0.5 + (px * cs - py * sn) / aspect, 0.5 + (px * sn + py * cs)];
    });
  }

  // ---- history ----
  _snap() { this.hist.push(JSON.stringify(this.figures)); if (this.hist.length > 40) this.hist.shift(); this.redoStack = []; }
  undo() { if (this.hist.length < 2) return; this.redoStack.push(this.hist.pop()); this.figures = JSON.parse(this.hist[this.hist.length - 1]); this.draw(); this._emit(); }
  redo() { const s = this.redoStack.pop(); if (!s) return; this.hist.push(s); this.figures = JSON.parse(s); this.draw(); this._emit(); }

  // ---- pointer ----
  _norm(e) {
    const r = this.cv.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  }
  _hit(nx, ny) {
    let best = null, bd = 0.035;
    this.figures.forEach((f, fi) => this._orientPts(f.pts).forEach(([x, y, c], ji) => {
      if (!c) return;
      const d = Math.hypot(x - nx, y - ny);
      if (d < bd) { bd = d; best = { fi, ji }; }
    }));
    return best;
  }
  _down(e) { const [nx, ny] = this._norm(e); const h = this._hit(nx, ny); if (h) { this.drag = h; this.cv.setPointerCapture(e.pointerId); } }
  _move(e) {
    if (!this.drag) return;
    let [nx, ny] = this._norm(e);
    nx = Math.max(0, Math.min(1, nx)); ny = Math.max(0, Math.min(1, ny));
    const f = this.figures[this.drag.fi];
    let [cx, cy] = this._unorientPt(nx, ny, f.pts, this.drag.ji);
    cx = Math.max(0, Math.min(1, cx)); cy = Math.max(0, Math.min(1, cy));
    f.pts[this.drag.ji] = [cx, cy, 1];
    this.draw();
  }
  _up() { if (this.drag) { this.drag = null; this._snap(); this._emit(); } }
  _toggle(e) {
    const [nx, ny] = this._norm(e);
    const h = this._hit(nx, ny);
    if (h) { this.figures[h.fi].pts[h.ji][2] = 0; }         // remove
    else {                                                   // add nearest missing on nearest figure
      let fi = 0, bd = 9;
      this.figures.forEach((f, i) => {
        const [cx] = this._centroid(this._orientPts(f.pts));
        const d = Math.abs(cx - nx); if (d < bd) { bd = d; fi = i; }
      });
      const miss = this.figures[fi].pts.findIndex(p => !p[2]);
      if (miss >= 0) {
        const [cx, cy] = this._unorientPt(nx, ny, this.figures[fi].pts, miss);
        this.figures[fi].pts[miss] = [Math.max(0, Math.min(1, cx)), Math.max(0, Math.min(1, cy)), 1];
      }
    }
    this._snap(); this.draw(); this._emit();
  }

  // ---- draw ----
  draw() {
    const { ctx, cv } = this, W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#0c0d10'; ctx.fillRect(0, 0, W, H);
    this.figures.forEach((f, fi) => {
      const tint = fi === 0 ? '#4fc3f7' : '#ffb74d';
      const pts = this._orientPts(f.pts);
      COCO18.limbs.forEach(([a, b], li) => {
        const p1 = pts[a], p2 = pts[b];
        if (!p1[2] || !p2[2]) return;
        const col = COCO18.colors[li % 18];
        ctx.strokeStyle = `rgb(${col[0]},${col[1]},${col[2]})`;
        ctx.lineWidth = 5; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(p1[0] * W, p1[1] * H); ctx.lineTo(p2[0] * W, p2[1] * H); ctx.stroke();
      });
      pts.forEach(([x, y, c], ji) => {
        if (!c) return;
        ctx.beginPath(); ctx.arc(x * W, y * H, 4.5, 0, 7);
        ctx.fillStyle = '#fff'; ctx.fill();
        ctx.strokeStyle = tint; ctx.lineWidth = 2; ctx.stroke();
      });
    });
  }
  _emit() { this.onChange(); }
}

window.PoseEditor = PoseEditor;
window.COCO18 = COCO18;
