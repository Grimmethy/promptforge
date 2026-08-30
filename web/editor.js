/* PromptForge skeleton editor — a small named 3-D stick rig on a <canvas>.
   Joints are body parts; bones have an editable rest length; the 4 limbs use
   FABRIK IK. Drag empty space to orbit; the orbit camera IS the exported
   facing. Projects to COCO-18 for ControlNet — keep in sync with server/core/rig.py. */

const RIG = {
  joints: ['waist', 'chest', 'head',
    'shoulder.L', 'elbow.L', 'hand.L', 'shoulder.R', 'elbow.R', 'hand.R',
    'hip.L', 'knee.L', 'foot.L', 'hip.R', 'knee.R', 'foot.R'],
  bones: [
    ['waist', 'chest', 'torso'], ['chest', 'head', 'neck'],
    ['chest', 'shoulder.L', 'clavicle'], ['shoulder.L', 'elbow.L', 'upperArm'], ['elbow.L', 'hand.L', 'forearm'],
    ['chest', 'shoulder.R', 'clavicle'], ['shoulder.R', 'elbow.R', 'upperArm'], ['elbow.R', 'hand.R', 'forearm'],
    ['waist', 'hip.L', 'hipWidth'], ['hip.L', 'knee.L', 'thigh'], ['knee.L', 'foot.L', 'shin'],
    ['waist', 'hip.R', 'hipWidth'], ['hip.R', 'knee.R', 'thigh'], ['knee.R', 'foot.R', 'shin'],
  ],
  lengthKeys: ['torso', 'neck', 'clavicle', 'upperArm', 'forearm', 'hipWidth', 'thigh', 'shin'],
  defaults: { torso: .20, neck: .16, clavicle: .09, upperArm: .15, forearm: .14, hipWidth: .07, thigh: .22, shin: .22 },
  range: { torso: [.13, .30], neck: [.09, .22], clavicle: [.05, .14], upperArm: [.09, .21], forearm: [.09, .21], hipWidth: [.04, .12], thigh: [.13, .30], shin: [.13, .30] },
  ikChains: {
    'hand.L': ['shoulder.L', 'elbow.L', 'hand.L'], 'hand.R': ['shoulder.R', 'elbow.R', 'hand.R'],
    'foot.L': ['hip.L', 'knee.L', 'foot.L'], 'foot.R': ['hip.R', 'knee.R', 'foot.R'],
  },
  // COCO-18 index -> rig joint (14..17 eyes/ears left empty)
  coco: { 0: 'head', 1: 'chest', 2: 'shoulder.R', 3: 'elbow.R', 4: 'hand.R', 5: 'shoulder.L', 6: 'elbow.L', 7: 'hand.L', 8: 'hip.R', 9: 'knee.R', 10: 'foot.R', 11: 'hip.L', 12: 'knee.L', 13: 'foot.L' },
  pretty: {
    waist: 'Waist', chest: 'Top of chest', head: 'Head',
    'shoulder.L': 'Left shoulder', 'elbow.L': 'Left elbow', 'hand.L': 'Left hand',
    'shoulder.R': 'Right shoulder', 'elbow.R': 'Right elbow', 'hand.R': 'Right hand',
    'hip.L': 'Left hip', 'knee.L': 'Left knee', 'foot.L': 'Left foot',
    'hip.R': 'Right hip', 'knee.R': 'Right knee', 'foot.R': 'Right foot',
  },
  bonePretty: { torso: 'Torso', neck: 'Neck', clavicle: 'Collarbone', upperArm: 'Upper arm', forearm: 'Forearm', hipWidth: 'Pelvis', thigh: 'Thigh', shin: 'Shin' },
};

const v = {
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  scale: (a, s) => [a[0] * s, a[1] * s, a[2] * s],
  len: a => Math.hypot(a[0], a[1], a[2]),
  norm: a => { const l = Math.hypot(a[0], a[1], a[2]) || 1e-6; return [a[0] / l, a[1] / l, a[2] / l]; },
};
const rotY = (p, a) => { const c = Math.cos(a), s = Math.sin(a); return [p[0] * c + p[2] * s, p[1], -p[0] * s + p[2] * c]; };
const rotX = (p, a) => { const c = Math.cos(a), s = Math.sin(a); return [p[0], p[1] * c - p[2] * s, p[1] * s + p[2] * c]; };
const clampLen = (k, x) => { const [lo, hi] = RIG.range[k]; return Math.min(hi, Math.max(lo, x)); };
const CAM_DIST = 1.35;

// Canonical forward depth (+z = toward camera). A neutral rig is built with it,
// and a fully-planar loaded rig is nudged to it, so the orbit camera can turn
// the figure instead of collapsing the limbs onto the spine.
// Keep mirrored with server/core/rig.py DEPTH_PRIOR / default_rig.
const DEPTH_PRIOR = {
  waist: 0, chest: 0.03, head: 0.01,
  'shoulder.L': -0.01, 'elbow.L': 0.05, 'hand.L': 0.08,
  'shoulder.R': -0.01, 'elbow.R': 0.05, 'hand.R': 0.08,
  'hip.L': -0.01, 'knee.L': 0.04, 'foot.L': 0,
  'hip.R': -0.01, 'knee.R': 0.04, 'foot.R': 0,
};
const FLAT_Z_EPS = 0.005;

function defaultRig(cx = 0) {
  const L = { ...RIG.defaults };
  const t = 0.10, top = t - L.torso, Z = DEPTH_PRIOR;
  return {
    root: 'waist',
    lengths: L,
    joints: {
      waist: [cx, t, Z.waist], chest: [cx, top, Z.chest], head: [cx, top - L.neck, Z.head],
      'shoulder.L': [cx + L.clavicle, top + .01, Z['shoulder.L']], 'elbow.L': [cx + L.clavicle, top + L.upperArm, Z['elbow.L']], 'hand.L': [cx + L.clavicle, top + L.upperArm + L.forearm, Z['hand.L']],
      'shoulder.R': [cx - L.clavicle, top + .01, Z['shoulder.R']], 'elbow.R': [cx - L.clavicle, top + L.upperArm, Z['elbow.R']], 'hand.R': [cx - L.clavicle, top + L.upperArm + L.forearm, Z['hand.R']],
      'hip.L': [cx + L.hipWidth, t, Z['hip.L']], 'knee.L': [cx + L.hipWidth, t + L.thigh, Z['knee.L']], 'foot.L': [cx + L.hipWidth, t + L.thigh + L.shin, Z['foot.L']],
      'hip.R': [cx - L.hipWidth, t, Z['hip.R']], 'knee.R': [cx - L.hipWidth, t + L.thigh, Z['knee.R']], 'foot.R': [cx - L.hipWidth, t + L.thigh + L.shin, Z['foot.R']],
    },
  };
}

// A rig with ~no depth collapses under camera yaw; give a genuinely planar rig
// the canonical depth prior. Idempotent — a rig that already has depth is left be.
function nudgeIfFlat(j) {
  const zs = Object.values(j).map(p => p[2]);
  if (zs.length && Math.max(...zs) - Math.min(...zs) < FLAT_Z_EPS)
    for (const [name, p] of Object.entries(j)) p[2] = p[2] + (DEPTH_PRIOR[name] || 0);
  return j;
}

function normalizeRig(r) {
  const base = defaultRig();
  if (!r || typeof r !== 'object') return base;
  const j = { ...base.joints };
  for (const [k, p] of Object.entries(r.joints || {}))
    if (j[k] && Array.isArray(p)) j[k] = [+p[0] || 0, +p[1] || 0, +p[2] || 0];
  const L = { ...base.lengths };
  for (const k of RIG.lengthKeys) if (r.lengths && r.lengths[k] != null) L[k] = clampLen(k, +r.lengths[k]);
  nudgeIfFlat(j);
  return { root: 'waist', joints: j, lengths: L };
}

function constrain(r) {
  const j = r.joints, L = r.lengths;
  for (const [p, c, key] of RIG.bones) {
    const d = v.sub(j[c], j[p]), n = v.len(d) || 1e-6;
    j[c] = v.add(j[p], v.scale(d, L[key] / n));
  }
  return r;
}

// FABRIK on a 3-joint chain [a,b,tip]; a stays put, bone lengths fixed
function fabrik(pts, l1, l2, target, iters = 10) {
  const A = pts[0].slice();
  const reach = l1 + l2;
  if (v.len(v.sub(target, A)) >= reach) {
    const dir = v.norm(v.sub(target, A));
    return [A, v.add(A, v.scale(dir, l1)), v.add(A, v.scale(dir, reach))];
  }
  let [a, b, t] = [A.slice(), pts[1].slice(), pts[2].slice()];
  for (let i = 0; i < iters; i++) {
    t = target.slice();
    b = v.add(t, v.scale(v.norm(v.sub(b, t)), l2));
    a = A.slice();
    b = v.add(a, v.scale(v.norm(v.sub(b, a)), l1));
    t = v.add(b, v.scale(v.norm(v.sub(t, b)), l2));
  }
  return [a, b, t];
}

class PoseEditor {
  constructor(canvas, onChange) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.onChange = onChange || (() => {});
    this.rigs = [];
    this.cam = { az: 0, el: 0, zoom: 1 };
    this.drag = null;              // {kind:'joint'|'orbit', fi, name, depth} | {kind:'orbit', x,y}
    this.hover = null;             // {kind:'joint'|'bone', fi, name}
    this._mouse = [0, 0];
    this.hist = []; this.redoStack = [];
    canvas.addEventListener('pointerdown', e => this._down(e));
    canvas.addEventListener('pointermove', e => this._move(e));
    window.addEventListener('pointerup', () => this._up());
    canvas.addEventListener('pointerleave', () => { this.hover = null; this.draw(); });
    canvas.addEventListener('contextmenu', e => { e.preventDefault(); this.resetView(); });
    canvas.addEventListener('wheel', e => this._wheel(e), { passive: false });
  }

  // scroll over a joint -> push it toward (+) / away from (-) the camera in depth
  _wheel(e) {
    if (!(this.hover && this.hover.kind === 'joint')) return;
    e.preventDefault();
    const r = this.rigs[this.hover.fi]; if (!r) return;
    const p = r.joints[this.hover.name];
    p[2] = Math.max(-0.45, Math.min(0.45, p[2] - Math.sign(e.deltaY) * 0.02));
    constrain(r);
    this._snap(); this.draw(); this._emit();
  }

  // ---------- rig set / get ----------
  setCount(n, lib) {
    const before = JSON.stringify(this.rigs);
    const libRigs = lib && (lib.rigs || (lib.rig && [lib.rig]));
    if (libRigs && libRigs.length === n) {
      this.rigs = libRigs.map(r => constrain(normalizeRig(r)));
    } else {
      while (this.rigs.length < n)
        this.rigs.push(defaultRig(n === 1 ? 0 : (this.rigs.length ? 0.22 : -0.22)));
      this.rigs.length = n;
    }
    this.draw();
    // only touch history / notify when the rig actually changed — a plain re-render
    // (outfit/name dropdown) must not spam undo or re-persist.
    if (JSON.stringify(this.rigs) !== before) { this._snap(); this._emit(); }
  }
  loadRig(data) {
    const rigs = data && (data.rigs || (data.rig && [data.rig]));
    if (!rigs || !rigs.length) return;
    const n = this.rigs.length || rigs.length;
    this.rigs = rigs.slice(0, n).map(r => constrain(normalizeRig(r)));
    while (this.rigs.length < n) this.rigs.push(defaultRig(0.22));
    this._snap(); this.draw(); this._emit();
  }
  getRigs() { return this.rigs.map(r => JSON.parse(JSON.stringify(r))); }

  getFrame(w, h) {
    return {
      people: this.rigs.map(r => {
        const proj = {}; for (const k of RIG.joints) proj[k] = this._project(r.joints[k]);
        const xs = RIG.joints.map(k => proj[k].x), ys = RIG.joints.map(k => proj[k].y);
        // Rigs carry real depth now, so a side/back view is a valid projection —
        // only blank when everything has collapsed onto a point or a line.
        const degenerate = (Math.max(...xs) - Math.min(...xs) < 0.03) ||
                           (Math.max(...ys) - Math.min(...ys) < 0.03);
        const hideNose = proj.head.z < proj.chest.z - 0.05;
        const flat = [];
        for (let i = 0; i < 18; i++) {
          const name = RIG.coco[i];
          if (name && proj[name] && !degenerate && !(i === 0 && hideNose)) {
            flat.push(Math.min(1, Math.max(0, proj[name].x)), Math.min(1, Math.max(0, proj[name].y)), 1);
          } else flat.push(0, 0, 0);
        }
        return { pose_keypoints_2d: flat, face_keypoints_2d: null, hand_left_keypoints_2d: null, hand_right_keypoints_2d: null };
      }),
      canvas_width: w, canvas_height: h,
    };
  }

  // ---------- camera ----------  (mild perspective — matches server/core/rig.py)
  _persp(qz) { return CAM_DIST / Math.max(0.35, CAM_DIST - qz); }
  _project(p) {
    const q = rotX(rotY(p, -this.cam.az), -this.cam.el);
    const f = this._persp(q[2]) * this.cam.zoom;
    return { x: 0.5 + q[0] * f, y: 0.5 + q[1] * f, z: q[2] };
  }
  _unproject(sx, sy, depth) {
    const f = this._persp(depth) * this.cam.zoom;
    const q = [(sx - 0.5) / f, (sy - 0.5) / f, depth];
    return rotY(rotX(q, this.cam.el), this.cam.az);
  }
  orbit(daz, del) {
    this.cam.az += daz;
    if (this.cam.az > Math.PI) this.cam.az -= 2 * Math.PI;
    if (this.cam.az < -Math.PI) this.cam.az += 2 * Math.PI;
    this.cam.el = Math.max(-1.35, Math.min(1.35, this.cam.el + del));
    this.draw(); this._emit();
  }
  setCam(az, el) { this.cam.az = az || 0; this.cam.el = el || 0; this.draw(); }
  resetView() { this.cam.az = 0; this.cam.el = 0; this.draw(); this._emit(); }
  exportYawPitch() {
    return { yaw: Math.max(-1, Math.min(1, this.cam.az / Math.PI)),
             pitch: Math.max(-1, Math.min(1, this.cam.el / 1.15)) };
  }

  // ---------- edits ----------
  setLength(fi, key, val) {
    const r = this.rigs[fi]; if (!r) return;
    r.lengths[key] = clampLen(key, +val);
    constrain(r); this._snap(); this.draw(); this._emit();
  }
  mirror(fi) {
    const r = this.rigs[fi]; if (!r) return;
    const j = r.joints;
    for (const k of Object.keys(j)) j[k] = [-j[k][0], j[k][1], j[k][2]];
    for (const side of [['shoulder', 'shoulder'], ['elbow', 'elbow'], ['hand', 'hand'], ['hip', 'hip'], ['knee', 'knee'], ['foot', 'foot']]) {
      const a = side[0] + '.L', b = side[1] + '.R';
      const t = j[a]; j[a] = j[b]; j[b] = t;
    }
    constrain(r); this._snap(); this.draw(); this._emit();
  }
  scaleRig(fi, s) {
    const r = this.rigs[fi]; if (!r) return;
    const c = r.joints.waist.slice();
    for (const k of Object.keys(r.joints))
      r.joints[k] = v.add(c, v.scale(v.sub(r.joints[k], c), s));
    for (const k of RIG.lengthKeys) r.lengths[k] = clampLen(k, r.lengths[k] * s);
    constrain(r); this._snap(); this.draw(); this._emit();
  }

  // ---------- history ----------
  _snap() { this.hist.push(JSON.stringify(this.rigs)); if (this.hist.length > 40) this.hist.shift(); this.redoStack = []; }
  undo() { if (this.hist.length < 2) return; this.redoStack.push(this.hist.pop()); this.rigs = JSON.parse(this.hist[this.hist.length - 1]); this.draw(); this._emit(); }
  redo() { const s = this.redoStack.pop(); if (!s) return; this.hist.push(s); this.rigs = JSON.parse(s); this.draw(); this._emit(); }

  // ---------- pointer ----------
  _norm(e) { const r = this.cv.getBoundingClientRect(); return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]; }
  _hitJoint(nx, ny) {
    let best = null, bd = 0.04;
    this.rigs.forEach((r, fi) => {
      for (const name of RIG.joints) {
        const s = this._project(r.joints[name]);
        const d = Math.hypot(s.x - nx, s.y - ny);
        if (d < bd) { bd = d; best = { fi, name, depth: s.z }; }
      }
    });
    return best;
  }
  _hitBone(nx, ny) {
    let best = null, bd = 0.028;
    this.rigs.forEach((r, fi) => {
      for (const [p, c, key] of RIG.bones) {
        const a = this._project(r.joints[p]), b = this._project(r.joints[c]);
        const d = segDist(nx, ny, a.x, a.y, b.x, b.y);
        if (d < bd) { bd = d; best = { fi, name: key }; }
      }
    });
    return best;
  }
  _down(e) {
    const [nx, ny] = this._norm(e);
    this.hover = null;
    const h = this._hitJoint(nx, ny);
    if (h) { this.drag = { kind: 'joint', ...h }; this.cv.setPointerCapture(e.pointerId); }
    else { this.drag = { kind: 'orbit', x: nx, y: ny }; }
  }
  _move(e) {
    const [nx, ny] = this._norm(e);
    this._mouse = [nx, ny];
    if (!this.drag) {
      const hj = this._hitJoint(nx, ny);
      const nh = hj ? { kind: 'joint', fi: hj.fi, name: hj.name } : this._hitBone(nx, ny);
      const changed = JSON.stringify(nh) !== JSON.stringify(this.hover);
      this.hover = nh; if (changed || nh) this.draw();
      return;
    }
    if (this.drag.kind === 'orbit') {
      // "grab and turn": drag right -> subject turns to face right (yaw < 0);
      // drag up -> we look down from above (high angle)
      this.orbit(-(nx - this.drag.x) * Math.PI * 1.4, -(ny - this.drag.y) * Math.PI * 1.1);
      this.drag.x = nx; this.drag.y = ny;
      return;
    }
    // joint drag
    const r = this.rigs[this.drag.fi], name = this.drag.name;
    const target = this._unproject(nx, ny, this.drag.depth);
    if (name === 'waist') {
      const delta = v.sub(target, r.joints.waist);
      for (const k of Object.keys(r.joints)) r.joints[k] = v.add(r.joints[k], delta);
    } else if (RIG.ikChains[name]) {
      const [a, b, tip] = RIG.ikChains[name];
      const isArm = name.startsWith('hand');
      const l1 = r.lengths[isArm ? 'upperArm' : 'thigh'];
      const l2 = r.lengths[isArm ? 'forearm' : 'shin'];
      const sol = fabrik([r.joints[a], r.joints[b], r.joints[tip]], l1, l2, target);
      r.joints[b] = sol[1]; r.joints[tip] = sol[2];
    } else {
      r.joints[name] = target;
      constrain(r);
    }
    this.draw();
  }
  _up() { if (this.drag) { const was = this.drag; this.drag = null; if (was.kind === 'joint') { this._snap(); this._emit(); } } }

  // ---------- draw ----------
  draw() {
    const { ctx, cv } = this, W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#0c0d10'; ctx.fillRect(0, 0, W, H);
    this._drawGrid(W, H);
    this._drawGizmo(W, H);

    this.rigs.forEach((r, fi) => {
      const tint = fi === 0 ? '#4fc3f7' : '#ffb74d';
      const P = {}; for (const k of RIG.joints) P[k] = this._project(r.joints[k]);
      const bones = RIG.bones.map(([p, c, key]) => ({ p, c, key, z: (P[p].z + P[c].z) / 2 }))
        .sort((a, b) => a.z - b.z);
      for (const bn of bones) {
        const a = P[bn.p], b = P[bn.c];
        const hovB = this.hover && this.hover.kind === 'bone' && this.hover.fi === fi && this.hover.name === bn.key;
        const side = bn.c.endsWith('.L') || bn.p.endsWith('.L') ? '#7fd4ff'
          : bn.c.endsWith('.R') || bn.p.endsWith('.R') ? '#ffc07a' : '#9aa0a8';
        ctx.strokeStyle = hovB ? '#fff' : side;
        ctx.lineWidth = (hovB ? 7 : 5) * (1 + (bn.z) * 0.25);
        ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(a.x * W, a.y * H); ctx.lineTo(b.x * W, b.y * H); ctx.stroke();
      }
      RIG.joints.map(k => ({ k, s: P[k] })).sort((a, b) => a.s.z - b.s.z).forEach(({ k, s }) => {
        const hov = this.hover && this.hover.kind === 'joint' && this.hover.fi === fi && this.hover.name === k;
        const rad = (k === 'head' ? 6 : 4.5) * (1 + s.z * 0.3) + (hov ? 2 : 0);
        ctx.beginPath(); ctx.arc(s.x * W, s.y * H, rad, 0, 7);
        ctx.fillStyle = hov ? '#fff' : '#e8e8e8'; ctx.fill();
        ctx.strokeStyle = tint; ctx.lineWidth = 2; ctx.stroke();
      });
    });
    this._drawTip(W, H);
  }
  // Blender-style reference box: a ground plane under the figure and a back
  // plane behind it, drawn in perspective through the orbit camera.
  _drawGrid(W, H) {
    if (!this.rigs.length) return;
    let mnx = 9, mxx = -9, mny = 9, mxy = -9, mnz = 9, mxz = -9;
    for (const r of this.rigs) for (const k of RIG.joints) {
      const [x, y, z] = r.joints[k];
      if (x < mnx) mnx = x; if (x > mxx) mxx = x;
      if (y < mny) mny = y; if (y > mxy) mxy = y;
      if (z < mnz) mnz = z; if (z > mxz) mxz = z;
    }
    const pad = 0.12, step = 0.1;
    const floorY = mxy + 0.02, backZ = mnz - 0.02;
    const x0 = Math.min(mnx - pad, -0.3), x1 = Math.max(mxx + pad, 0.3);
    const z0 = backZ, z1 = Math.max(mxz + pad, 0.3);
    const yTop = Math.min(mny - pad, floorY - 0.5);
    const snap = v => Math.ceil(v / step) * step;
    const { ctx } = this;
    const line = (a, b, col, w) => {
      const p = this._project(a), q = this._project(b);
      ctx.strokeStyle = col; ctx.lineWidth = w || 1;
      ctx.beginPath(); ctx.moveTo(p.x * W, p.y * H); ctx.lineTo(q.x * W, q.y * H); ctx.stroke();
    };
    const gLine = 'rgba(110,140,170,0.30)';   // ground
    const bLine = 'rgba(96,108,140,0.22)';    // back plane
    const edge = 'rgba(150,170,190,0.45)';

    // ground plane (y = floorY)
    for (let x = snap(x0); x <= x1 + 1e-6; x += step) line([x, floorY, z0], [x, floorY, z1], gLine);
    for (let z = snap(z0); z <= z1 + 1e-6; z += step) line([x0, floorY, z], [x1, floorY, z], gLine);
    // back plane (z = backZ)
    for (let x = snap(x0); x <= x1 + 1e-6; x += step) line([x, yTop, backZ], [x, floorY, backZ], bLine);
    for (let y = snap(yTop); y <= floorY + 1e-6; y += step) line([x0, y, backZ], [x1, y, backZ], bLine);
    // box edges + floor/back seam
    line([x0, floorY, backZ], [x1, floorY, backZ], edge, 1.5);
    line([x0, floorY, backZ], [x0, floorY, z1], edge, 1.2);
    line([x1, floorY, backZ], [x1, floorY, z1], edge, 1.2);
    line([x0, yTop, backZ], [x0, floorY, backZ], edge, 1.2);
    line([x1, yTop, backZ], [x1, floorY, backZ], edge, 1.2);
    line([x0, yTop, backZ], [x1, yTop, backZ], edge, 1.2);
  }
  _drawGizmo(W, H) {
    const { ctx } = this, ox = 26, oy = H - 24, len = 16;
    const ax = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
    for (const [name, col] of [['x', '#e0554f'], ['y', '#5fae4f'], ['z', '#4f7fe0']]) {
      const q = rotX(rotY(ax[name], -this.cam.az), -this.cam.el);
      ctx.strokeStyle = col; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + q[0] * len, oy + q[1] * len); ctx.stroke();
    }
  }
  _drawTip(W, H) {
    if (!this.hover) return;
    const label = this.hover.kind === 'joint'
      ? RIG.pretty[this.hover.name] : RIG.bonePretty[this.hover.name];
    if (!label) return;
    const { ctx } = this;
    const x = this._mouse[0] * W + 12, y = this._mouse[1] * H - 6;
    ctx.font = '12px system-ui, sans-serif';
    const w = ctx.measureText(label).width + 12;
    ctx.fillStyle = 'rgba(20,22,26,.92)'; ctx.strokeStyle = '#3a3f47';
    const bx = Math.min(x, W - w - 2), by = Math.max(y - 16, 2);
    ctx.beginPath(); ctx.roundRect(bx, by, w, 18, 4); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#e6e6e6'; ctx.fillText(label, bx + 6, by + 13);
  }
  _emit() { this.onChange(); }
}

function segDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const L2 = dx * dx + dy * dy || 1e-9;
  let t = ((px - ax) * dx + (py - ay) * dy) / L2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

window.PoseEditor = PoseEditor;
window.RIG = RIG;
