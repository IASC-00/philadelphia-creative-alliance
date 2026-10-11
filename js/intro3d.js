/* 3D homepage intro. Loaded by js/intro.js only when the intro plays.
   Night over Center City (the grid between the two rivers, City Hall, the Parkway). Lights
   come on across the city, one creative space at a time; threads of light connect them into
   one network as the camera rises; then every light lifts off and sets into the Alliance's
   mark (the header's four chips, each laid as tesserae), the name comes up beside it, and the
   dark is lifted tile by tile onto the page. render(t) is a pure function of t, so the test
   hook can seek to any frame. */
import * as THREE from './vendor/three/three.module.min.js';
import { RoomEnvironment } from './vendor/three/RoomEnvironment.js';
import { RoundedBoxGeometry } from './vendor/three/RoundedBoxGeometry.js';

const END = 6.3;
const REVEAL = 5.55;
const INK = 0x0c0a10;
// the header mark: top-left vermilion, top-right cobalt, bottom-left marigold, bottom-right fuchsia
const HUES = [0xEC4A2C, 0x2F63E6, 0xF2B01E, 0xDB2F86];
const MONO = 'ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, "DejaVu Sans Mono", monospace';

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const seg = (t, a, b) => clamp((t - a) / (b - a));
const easeOut = (x) => 1 - Math.pow(1 - clamp(x), 3);
const easeInOut = (x) => { x = clamp(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
const backOut = (x, k = 1.6) => { x = clamp(x); const c = k + 1; return 1 + c * Math.pow(x - 1, 3) + k * Math.pow(x - 1, 2); };

function rng(seed) {
  return () => {
    seed = seed + 0x6D2B79F5 | 0; let r = Math.imul(seed ^ seed >>> 15, 1 | seed);
    r = r + Math.imul(r ^ r >>> 7, 61 | r) ^ r; return ((r ^ r >>> 14) >>> 0) / 4294967296;
  };
}
function shuffle(a, R) { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(R() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; }
function distToSeg(px, pz, ax, az, bx, bz) {
  const vx = bx - ax, vz = bz - az, u = clamp(((px - ax) * vx + (pz - az) * vz) / (vx * vx + vz * vz));
  return Math.hypot(px - ax - u * vx, pz - az - u * vz);
}

function radialTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 128;
  const g = c.getContext('2d'), grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, 'rgba(255,255,255,1)'); grad.addColorStop(0.25, 'rgba(255,255,255,.45)');
  grad.addColorStop(0.6, 'rgba(255,255,255,.08)'); grad.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grad; g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

export async function create(container, opts = {}) {
  const coarse = window.matchMedia && matchMedia('(pointer: coarse)').matches;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, coarse ? 1.5 : 2));
  renderer.setSize(innerWidth, innerHeight, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.autoClear = false;
  renderer.setClearColor(INK, 1);
  const canvas = renderer.domElement;
  canvas.className = 'pi-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  const onLost = (e) => { e.preventDefault(); if (opts.onLost) opts.onLost(); };
  canvas.addEventListener('webglcontextlost', onLost);
  container.insertBefore(canvas, container.querySelector('.pi-skip'));

  const R = rng(11);
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(INK, 0.035);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const envRT = pmrem.fromScene(room, 0.04);
  scene.environment = envRT.texture;
  scene.environmentIntensity = 0.35;
  pmrem.dispose();
  if (room.dispose) room.dispose();

  const hemi = new THREE.HemisphereLight(0x8a7fb0, 0x0b0910, 0.55);
  const moon = new THREE.DirectionalLight(0xa9b8ff, 1.1);
  moon.position.set(-6, 10, 5);
  const glint = new THREE.PointLight(0xffffff, 0, 9, 1.4);
  scene.add(hemi, moon, glint);

  /* ---------- the city: Center City's grid between the Schuylkill and the Delaware ---------- */
  const NX = 10, NZ = 7, AVE = 0.14, BW = 0.78;
  const blocks = [];
  for (let i = -NX; i < NX; i++) for (let j = -NZ; j < NZ; j++) {
    const x = (i + 0.5) + (i >= 0 ? AVE : -AVE), z = (j + 0.5) + (j >= 0 ? AVE : -AVE);
    if (distToSeg(x, z, 0, 0, -8.5, -6.5) < 0.62) continue;          // the Parkway, City Hall to the north-west
    const near = Math.max(0, 1 - Math.hypot(x * 0.8, z * 1.2) / 6);
    let h = 0.12 + Math.pow(R(), 3) * 0.55;
    if (R() < near * 0.55) h += 0.6 + R() * 1.8 * near;
    blocks.push({ x, z, h });
  }
  const boxGeo = new THREE.BoxGeometry(1, 1, 1); boxGeo.translate(0, 0.5, 0);
  const blockMat = new THREE.MeshStandardMaterial({ color: 0x2a2334, roughness: 0.82, metalness: 0.12 });
  const inst = new THREE.InstancedMesh(boxGeo, blockMat, blocks.length);
  const m4 = new THREE.Matrix4(), col = new THREE.Color();
  blocks.forEach((b, k) => {
    m4.makeScale(BW, b.h, BW); m4.setPosition(b.x, 0, b.z); inst.setMatrixAt(k, m4);
    inst.setColorAt(k, col.set(0xffffff).multiplyScalar(0.75 + R() * 0.5));
  });
  scene.add(inst);
  // City Hall in the middle of Broad and Market
  const stone = new THREE.MeshStandardMaterial({ color: 0x4a4152, roughness: 0.7, metalness: 0.1 });
  const hall = new THREE.Mesh(boxGeo, stone); hall.scale.set(0.42, 0.55, 0.42); scene.add(hall);
  const tower = new THREE.Mesh(boxGeo, stone); tower.scale.set(0.16, 1.55, 0.16); scene.add(tower);
  const spire = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.35, 8), stone); spire.position.y = 1.72; scene.add(spire);

  const groundMat = new THREE.MeshStandardMaterial({ color: 0x110e16, roughness: 0.95 });
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(90, 90), groundMat);
  ground.rotation.x = -Math.PI / 2; scene.add(ground);
  const riverMat = new THREE.MeshStandardMaterial({ color: 0x0b1222, roughness: 0.2, metalness: 0.75, envMapIntensity: 0.7 });
  const riverGeo = new THREE.PlaneGeometry(6, 40);
  for (const x of [-13.6, 13.6]) {
    const r = new THREE.Mesh(riverGeo, riverMat); r.rotation.x = -Math.PI / 2; r.position.set(x, 0.006, 0); scene.add(r);
  }
  // street light, drawn as a texture laid over the ground
  const sc = document.createElement('canvas'); sc.width = 1024; sc.height = 768;
  {
    const g = sc.getContext('2d'), U = (x) => (x + 11) / 22 * 1024, V = (z) => (z + 8) / 16 * 768;
    g.strokeStyle = 'rgba(255,176,104,.55)'; g.lineCap = 'round';
    for (let i = -NX; i <= NX; i++) {
      const x = i + (i > 0 ? AVE : i < 0 ? -AVE : 0);
      g.lineWidth = i === 0 ? 7 : 2.5; g.beginPath(); g.moveTo(U(x), V(-NZ - 0.3)); g.lineTo(U(x), V(NZ + 0.3)); g.stroke();
    }
    for (let j = -NZ; j <= NZ; j++) {
      const z = j + (j > 0 ? AVE : j < 0 ? -AVE : 0);
      g.lineWidth = j === 0 ? 7 : 2.5; g.beginPath(); g.moveTo(U(-NX - 0.3), V(z)); g.lineTo(U(NX + 0.3), V(z)); g.stroke();
    }
    g.lineWidth = 8; g.beginPath(); g.moveTo(U(0), V(0)); g.lineTo(U(-8.5), V(-6.5)); g.stroke();
  }
  const streetTex = new THREE.CanvasTexture(sc); streetTex.colorSpace = THREE.SRGBColorSpace; streetTex.anisotropy = 4;
  const streetMat = new THREE.MeshBasicMaterial({ map: streetTex, transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending, depthWrite: false });
  const streets = new THREE.Mesh(new THREE.PlaneGeometry(22, 16), streetMat);
  streets.rotation.x = -Math.PI / 2; streets.position.y = 0.012; scene.add(streets);

  /* ---------- 36 lights: creative spaces across the city, 9 in each mark colour ---------- */
  const pool = shuffle(blocks.filter((b) => Math.abs(b.x) < 9.4 && Math.abs(b.z) < 6.4 && Math.hypot(b.x, b.z) > 0.9), R);
  let picked = [];
  for (let minD = 2.3; picked.length < 36 && minD > 0.5; minD -= 0.2) {
    picked = [];
    for (const b of pool) { if (picked.every((p) => Math.hypot(p.x - b.x, p.z - b.z) > minD)) picked.push(b); if (picked.length === 36) break; }
  }
  const hues = shuffle(Array.from({ length: 36 }, (_, i) => i % 4), R);
  const haloTex = radialTexture();
  const tileGeo = new RoundedBoxGeometry(0.36, 0.14, 0.36, 2, 0.035);
  const poolGeo = new THREE.PlaneGeometry(1, 1);
  const byDist = picked.map((b, i) => i).sort((a, b) => Math.hypot(picked[a].x, picked[a].z) - Math.hypot(picked[b].x, picked[b].z));
  const slotCount = [0, 0, 0, 0];
  const tiles = picked.map((b, i) => {
    const hue = hues[i], c = new THREE.Color(HUES[hue]);
    const mat = new THREE.MeshPhysicalMaterial({ color: c, emissive: c, emissiveIntensity: 0, roughness: 0.38, metalness: 0.05, clearcoat: 0.7, clearcoatRoughness: 0.18, envMapIntensity: 0.6, fog: false });
    const mesh = new THREE.Mesh(tileGeo, mat);
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: haloTex, color: c, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, opacity: 0, fog: false }));
    const glow = new THREE.Mesh(poolGeo, new THREE.MeshBasicMaterial({ map: haloTex, color: c, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, opacity: 0 }));
    glow.rotation.x = -Math.PI / 2; glow.position.set(b.x, 0.02, b.z); glow.scale.setScalar(2.8);
    scene.add(mesh, halo, glow);
    const axis = new THREE.Vector3(R() - 0.5, R() - 0.5, R() - 0.5).normalize();
    return {
      mesh, mat, halo, glow, hue, axis, slot: slotCount[hue]++,
      home: new THREE.Vector3(b.x, b.h + 0.07, b.z),
      lightAt: 0.35 + byDist.indexOf(i) * 0.034,
      flyAt: 3.3 + R() * 0.42, jitter: (R() - 0.5) * 0.07,
      target: new THREE.Vector3(), tq: new THREE.Quaternion(), screenX: 0,
    };
  });

  /* ---------- the network: threads of light between the lights ---------- */
  const N = tiles.length, P = (k) => tiles[k].home;
  const inTree = new Array(N).fill(false), depth = new Array(N).fill(0), edges = [];
  inTree[byDist[0]] = true;
  for (let n = 1; n < N; n++) {
    let best = null, bd = Infinity;
    for (let a = 0; a < N; a++) if (inTree[a]) for (let b = 0; b < N; b++) if (!inTree[b]) {
      const d = P(a).distanceTo(P(b)); if (d < bd) { bd = d; best = [a, b]; }
    }
    inTree[best[1]] = true; depth[best[1]] = depth[best[0]] + 1;
    edges.push({ a: best[0], b: best[1], at: 1.2 + depth[best[0]] * 0.19 + R() * 0.08, dur: 0.5 });
  }
  const has = (a, b) => edges.some((e) => (e.a === a && e.b === b) || (e.a === b && e.b === a));
  let extra = 0;
  for (const a of shuffle([...Array(N).keys()], R)) {
    if (extra >= 12) break;
    let best = -1, bd = 3.8;
    for (let b = 0; b < N; b++) if (b !== a && !has(a, b)) { const d = P(a).distanceTo(P(b)); if (d < bd) { bd = d; best = b; } }
    if (best >= 0) { edges.push({ a, b: best, at: 2.1 + R() * 0.7, dur: 0.55 }); extra++; }
  }
  const lineVS = 'varying float vU; void main(){ vU = uv.x; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';
  const lineFS = [
    'uniform float uProg; uniform float uFade; uniform float uTime; uniform vec3 uA; uniform vec3 uB; varying float vU;',
    'void main(){',
    '  if (vU > uProg) discard;',
    '  float head = exp(-pow((uProg - vU) * 14.0, 2.0)) * (1.0 - step(0.999, uProg));',
    '  float pulse = exp(-pow((fract(vU - uTime * 0.7) - 0.5) * 9.0, 2.0)) * step(0.999, uProg);',
    '  vec3 c = mix(uA, uB, vU) * (0.6 + 1.8 * head + 1.0 * pulse) * uFade;',
    '  gl_FragColor = vec4(c, 1.0);',
    '  #include <tonemapping_fragment>',
    '  #include <colorspace_fragment>',
    '}',
  ].join('\n');
  for (const e of edges) {
    const a = P(e.a), b = P(e.b), d = a.distanceTo(b);
    const mid = a.clone().add(b).multiplyScalar(0.5); mid.y += 0.3 + d * 0.28;
    const geo = new THREE.TubeGeometry(new THREE.QuadraticBezierCurve3(a, mid, b), 40, 0.016, 5, false);
    e.mat = new THREE.ShaderMaterial({
      uniforms: { uProg: { value: 0 }, uFade: { value: 1 }, uTime: { value: 0 }, uA: { value: new THREE.Color(HUES[tiles[e.a].hue]) }, uB: { value: new THREE.Color(HUES[tiles[e.b].hue]) } },
      vertexShader: lineVS, fragmentShader: lineFS,
      blending: THREE.AdditiveBlending, transparent: true, depthWrite: false,
    });
    e.mesh = new THREE.Mesh(geo, e.mat); scene.add(e.mesh);
  }

  /* ---------- the name, in the site's own monospace, as the header sets it ---------- */
  const textCanvas = document.createElement('canvas');
  const textTex = new THREE.CanvasTexture(textCanvas); textTex.colorSpace = THREE.SRGBColorSpace; textTex.anisotropy = 4;
  const textMat = new THREE.MeshBasicMaterial({ map: textTex, transparent: true, opacity: 0, depthWrite: false, toneMapped: false, fog: false });
  const textMesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), textMat); scene.add(textMesh);
  function drawText(align) {
    const px = 120, sp = px * 0.1, lh = px * 1.28, lines = ['PHILADELPHIA', 'CREATIVE', 'ALLIANCE'];
    const g = textCanvas.getContext('2d'); g.font = '600 ' + px + 'px ' + MONO;
    const widthOf = (s) => [...s].reduce((w, ch) => w + g.measureText(ch).width + sp, -sp);
    const W = Math.ceil(Math.max(...lines.map(widthOf))) + 8, H = Math.ceil(lh * 3);
    textCanvas.width = W; textCanvas.height = H;
    g.font = '600 ' + px + 'px ' + MONO; g.fillStyle = '#F3EEE7'; g.textBaseline = 'middle';
    lines.forEach((s, k) => {
      let x = align === 'center' ? (W - widthOf(s)) / 2 : 4;
      for (const ch of s) { g.fillText(ch, x, lh * (k + 0.5)); x += g.measureText(ch).width + sp; }
    });
    textTex.needsUpdate = true;
    return { w: W, h: H, px, lh };
  }

  /* ---------- reveal: the dark is lifted tile by tile, starting at the mark ---------- */
  const maskMat = new THREE.ShaderMaterial({
    uniforms: { uT: { value: 0 }, uCell: { value: 100 }, uCenter: { value: new THREE.Vector2() }, uFar: { value: 1000 } },
    vertexShader: 'void main(){ gl_Position = vec4(position.xy, 0.0, 1.0); }',
    fragmentShader: [
      'uniform float uT; uniform float uCell; uniform vec2 uCenter; uniform float uFar;',
      'float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }',
      'void main(){',
      '  vec2 px = gl_FragCoord.xy; vec2 id = floor(px / uCell); vec2 c = (id + 0.5) * uCell;',
      '  float h = hash(id); float st = ' + REVEAL.toFixed(2) + ' + length(c - uCenter) / uFar * 0.45 + h * 0.08;',
      '  float p = clamp((uT - st) / 0.3, 0.0, 1.0); p *= p;',
      '  float a = (h - 0.5) * 0.7 * p; vec2 l = (px - c) / (0.5 * uCell);',
      '  l = mat2(cos(a), -sin(a), sin(a), cos(a)) * l;',
      '  float keep = step(max(abs(l.x), abs(l.y)), 1.02 * (1.0 - p));',
      '  gl_FragColor = vec4(0.0, 0.0, 0.0, keep);',
      '}',
    ].join('\n'),
    blending: THREE.CustomBlending, blendEquation: THREE.AddEquation,
    blendSrc: THREE.ZeroFactor, blendDst: THREE.SrcAlphaFactor,
    blendSrcAlpha: THREE.ZeroFactor, blendDstAlpha: THREE.SrcAlphaFactor,
    transparent: true, depthTest: false, depthWrite: false,
  });
  const maskScene = new THREE.Scene();
  maskScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), maskMat));
  const orthoCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  /* ---------- camera path + lockup layout (recomputed on resize) ---------- */
  const camera = new THREE.PerspectiveCamera(42, 1, 0.05, 120);
  const finalCam = new THREE.PerspectiveCamera(42, 1, 0.05, 120);
  let path, look, lock = {};
  const tmpV = new THREE.Vector3(), tmpQ = new THREE.Quaternion(), faceUp = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);

  function layout() {
    const w = innerWidth, h = innerHeight, aspect = w / h, portrait = aspect < 0.9;
    renderer.setSize(w, h, false);
    const fov = portrait ? 56 : 42;
    camera.fov = finalCam.fov = fov; camera.aspect = finalCam.aspect = aspect;
    camera.updateProjectionMatrix(); finalCam.updateProjectionMatrix();
    const V = (x, y, z) => new THREE.Vector3(x, y, z);
    // street level on Market, looking west to City Hall; then up over the whole grid
    path = new THREE.CatmullRomCurve3(portrait
      ? [V(9.6, 0.42, 0.02), V(10.3, 3.0, 2.2), V(13.2, 9.6, 3.0), V(15.2, 15.6, 2.2)]
      : [V(9.6, 0.42, 0.02), V(8.6, 2.6, 4.2), V(5.4, 8.6, 10.4), V(2.4, 13.4, 13.2)]);
    look = new THREE.CatmullRomCurve3(portrait
      ? [V(0, 0.75, 0), V(0, 0.5, 0), V(0.4, 0, 0.3), V(0.6, 0, 0)]
      : [V(0, 0.75, 0), V(0, 0.5, 0), V(0, 0, 0.6), V(0, 0, 0.3)]);
    finalCam.position.copy(path.getPoint(1)); finalCam.lookAt(look.getPoint(1)); finalCam.updateMatrixWorld(true);

    // the lockup in front of the final camera: mark of 2x2 chips, 3x3 tesserae each, plus the name
    const s = 0.36, g = 0.07, G = 0.16, chip = 3 * s + 2 * g, markW = 2 * chip + G;
    const tx = drawText(portrait ? 'center' : 'left');
    const textH = markW * (portrait ? 0.74 : 0.92), textW = textH * tx.w / tx.h;
    const gap = portrait ? 0.42 : 0.55;
    const lockW = portrait ? Math.max(markW, textW) : markW + gap + textW;
    const lockH = portrait ? markW + gap + textH : markW;
    const tv = Math.tan(THREE.MathUtils.degToRad(fov / 2));
    const fw = portrait ? 0.8 : 0.6, fh = portrait ? 0.62 : 0.5;
    const D = Math.max(lockW / (2 * fw * tv * aspect), lockH / (2 * fh * tv));
    const left = portrait ? -markW / 2 : -lockW / 2, top = lockH / 2;
    for (const t of tiles) {
      const cx = t.hue % 2, cy = t.hue > 1 ? 1 : 0;
      const x = left + cx * (chip + G) + (t.slot % 3) * (s + g) + s / 2;
      const y = top - (cy * (chip + G) + Math.floor(t.slot / 3) * (s + g) + s / 2);
      t.target.set(x, y, -D).applyMatrix4(finalCam.matrixWorld);
      t.tq.copy(finalCam.quaternion).multiply(faceUp).multiply(tmpQ.setFromAxisAngle(new THREE.Vector3(0, 1, 0), t.jitter));
      t.screenX = (x - left) / markW;
    }
    const txc = portrait ? 0 : left + markW + gap + textW / 2, tyc = portrait ? top - markW - gap - textH / 2 : 0;
    lock = { D, txc, tyc, textW, textH, left, markW, top, portrait };
    textMesh.scale.set(textW, textH, 1);
    textMesh.quaternion.copy(finalCam.quaternion);
    // where the mark sits on screen, for the reveal
    const mc = new THREE.Vector3(left + markW / 2, top - markW / 2, -D).applyMatrix4(finalCam.matrixWorld).project(finalCam);
    const bw = w * renderer.getPixelRatio(), bh = h * renderer.getPixelRatio();
    maskMat.uniforms.uCenter.value.set((mc.x * 0.5 + 0.5) * bw, (mc.y * 0.5 + 0.5) * bh);
    maskMat.uniforms.uCell.value = Math.ceil(Math.max(bw, bh) / 9);
    maskMat.uniforms.uFar.value = Math.hypot(bw, bh);
  }
  layout();
  const onResize = () => layout();
  window.addEventListener('resize', onResize);

  /* ---------- one frame at time t ---------- */
  function update(t) {
    renderer.toneMappingExposure = 1.05 * easeOut(seg(t, 0, 0.8));
    const u = easeInOut(seg(t, 0, 4.6)) * 0.985 + 0.015 * seg(t, 4.6, END);
    camera.position.copy(path.getPoint(Math.min(u, 1)));
    camera.lookAt(look.getPoint(Math.min(u, 1)));
    // the city sinks into the dark while the mark comes together
    const dim = easeInOut(seg(t, 3.4, 4.6));
    scene.fog.density = 0.035 + dim * 0.03;
    hemi.intensity = 0.55 * (1 - 0.6 * dim); moon.intensity = 1.1 * (1 - 0.45 * dim);
    scene.environmentIntensity = 0.35 * (1 - 0.75 * dim);
    streetMat.opacity = 0.45 * easeOut(seg(t, 0.2, 1.2)) * (1 - 0.7 * dim);
    const sweep = -0.25 + 1.5 * seg(t, 4.85, 5.6);
    for (const tl of tiles) {
      const lu = seg(t, tl.lightAt, tl.lightAt + 0.45), on = easeOut(lu);
      const flash = Math.exp(-Math.pow((t - tl.lightAt - 0.12) / 0.1, 2));
      const fu = seg(t, tl.flyAt, tl.flyAt + 0.95), fe = easeInOut(fu);
      if (fu <= 0) {
        tl.mesh.position.copy(tl.home); tl.mesh.position.y += (1 - on) * 0.35;
        tl.mesh.scale.setScalar(Math.max(0.001, backOut(lu)));
        tl.mesh.quaternion.identity();
        tl.mat.emissiveIntensity = 1.5 * on + 2.2 * flash;
      } else {
        // lift off and set into the mark: a curve up and over, a tumble that settles
        const b = backOut(fu, 1.1), mid = tmpV.copy(tl.home).lerp(tl.target, 0.5); mid.y += 2.6;
        const k = 1 - b;
        tl.mesh.position.set(
          k * k * tl.home.x + 2 * k * b * mid.x + b * b * tl.target.x,
          k * k * tl.home.y + 2 * k * b * mid.y + b * b * tl.target.y,
          k * k * tl.home.z + 2 * k * b * mid.z + b * b * tl.target.z);
        tl.mesh.scale.setScalar(1);
        tl.mesh.quaternion.identity().slerp(tl.tq, fe).multiply(tmpQ.setFromAxisAngle(tl.axis, (1 - easeOut(fu)) * Math.PI * 1.6));
        tl.mat.emissiveIntensity = 1.5 * (1 - fe) + 0.42 * fe + 0.9 * Math.exp(-Math.pow((tl.screenX - sweep) / 0.14, 2)) * (t > 4.8 ? 1 : 0);
      }
      const away = 1 - easeOut(seg(t, tl.flyAt, tl.flyAt + 0.5));
      tl.halo.position.copy(tl.mesh.position);
      tl.halo.scale.setScalar(1.3 + 0.6 * flash);
      tl.halo.material.opacity = (0.8 * on + 0.8 * flash) * away;
      tl.glow.material.opacity = 0.6 * on * away;
    }
    for (const e of edges) {
      e.mat.uniforms.uProg.value = easeOut(seg(t, e.at, e.at + e.dur));
      e.mat.uniforms.uFade.value = 1 - easeOut(seg(t, 3.25, 3.8));
      e.mat.uniforms.uTime.value = t;
      e.mesh.visible = t > e.at && t < 3.85;
    }
    // the name comes up beside the mark
    const tu = easeOut(seg(t, 4.25, 5.0));
    textMat.opacity = tu;
    textMesh.position.set(lock.txc, lock.tyc - (1 - tu) * 0.18, -lock.D).applyMatrix4(finalCam.matrixWorld);
    textMesh.visible = tu > 0;
    // a glint of light across the tesserae
    const gu = seg(t, 4.85, 5.6);
    glint.intensity = 45 * Math.sin(Math.PI * gu);
    glint.position.set(lock.left + lock.markW * (-0.3 + 1.6 * gu), lock.top + 0.6, -lock.D + 1.4).applyMatrix4(finalCam.matrixWorld);
    maskMat.uniforms.uT.value = t;
  }

  function render(t) {
    update(t);
    renderer.clear();
    renderer.render(scene, camera);
    if (t >= REVEAL) renderer.render(maskScene, orthoCam);
  }

  function dispose() {
    window.removeEventListener('resize', onResize);
    canvas.removeEventListener('webglcontextlost', onLost);
    const seen = new Set();
    for (const s of [scene, maskScene]) s.traverse((o) => {
      if (o.geometry && !seen.has(o.geometry)) { seen.add(o.geometry); o.geometry.dispose(); }
      const ms = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
      for (const m of ms) if (!seen.has(m)) { seen.add(m); m.dispose(); }
    });
    boxGeo.dispose(); haloTex.dispose(); streetTex.dispose(); textTex.dispose(); envRT.dispose();
    renderer.dispose(); renderer.forceContextLoss();
    if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
  }

  // compile everything before the clock starts, so the first frames don't stall
  if (renderer.compileAsync) await renderer.compileAsync(scene, camera); else renderer.compile(scene, camera);
  renderer.compile(maskScene, orthoCam);
  render(0);
  return { render, dispose };
}
