/* 3D homepage intro. Loaded by js/intro.js only when the intro plays (WebGL2 only).

   The story, about six seconds:
     1. Night over Center City: the grid between the two rivers, City Hall, the Parkway.
        Windows are lit and traffic draws light trails along the streets.
     2. Creative spaces light up one by one across the city, outward from Penn Square.
        Links of light join them into one network, with light flowing along every link,
        while the camera rises from street level to an overhead view.
     3. The beat: the links run white-hot, every light lifts off its roof, and all 36 converge
        and lock into the Alliance mark at once (flash, ring, light streak, a ripple through
        the frame). The camera keeps pulling back.
     4. The name rises in letter by letter and a sweep of light crosses the lockup.
     5. The page's own ground colour is laid across the screen tile by tile, the name turns to
        the page's ink, and the mark shrinks into the header's chips, exactly where the live
        mark sits, before the canvas fades onto the page.

   render(t) is a pure function of t, so the ?introtest=1 hook can seek to any frame.

   Pipeline: InstancedMesh city with a procedural window + height-fog shader, instanced traffic
   trails and GPU link particles (all motion in vertex shaders), then EffectComposer:
   RenderPass -> tilt-shift depth of field (high tier) -> UnrealBloomPass -> OutputPass (ACES +
   sRGB) -> finish pass (ripple, vignette, grain, ground tiles). After the ground starts going
   down, the mark and name are drawn once more on top so they sit above it.

   Every colour and timing lives in CONFIG below. */
import * as THREE from './vendor/three/three.module.min.js';
import { RoomEnvironment } from './vendor/three/RoomEnvironment.js';
import { RoundedBoxGeometry } from './vendor/three/RoundedBoxGeometry.js';
import { EffectComposer } from './vendor/three/EffectComposer.js';
import { RenderPass } from './vendor/three/RenderPass.js';
import { ShaderPass } from './vendor/three/ShaderPass.js';
import { UnrealBloomPass } from './vendor/three/UnrealBloomPass.js';
import { OutputPass } from './vendor/three/OutputPass.js';

export const CONFIG = {
  END: 6.0,                       // seconds; js/intro.js uses the same value
  time: {
    exposureIn: 0.35,             // snappy fade up from ambient floor
    lightsFrom: 0.85, lightStep: 0.035,         // creative spaces light up as camera crests rooftops
    linksFrom: 1.20, linkDepthStep: 0.14, linkDur: 0.42, extraLinksFrom: 1.90,
    lift: 2.62,                   // lights lift off their roofs; links run white-hot
    converge: 3.05,               // ...and accelerate towards the mark
    lock: 3.65,                   // THE BEAT: all 36 lock into the mark
    textIn: 3.72, letterStep: 0.02, letterDur: 0.65,
    sweep: 4.18, sweepDur: 0.55,  // light sweep across the lockup
    nameOut: 4.65, nameOutDur: 0.30, // wordmark dissolves before ground flood
    flood: 5.0, floodSpread: 0.38, floodTile: 0.24,   // page ground laid tile by tile
    fly: 4.85, flyDur: 0.52,      // mark flies into the header chips, docks, holds, then the fade
    fade: 5.65,                   // canvas fades onto the page
  },
  color: {
    ink: 0x0c0a10,                // night sky overhead + clear colour
    haze: 0x1a1322,               // fog / low city haze
    horizon: 0x3a1d3a,            // city glow at the horizon
    hues: [0xEC4A2C, 0x2F63E6, 0xF2B01E, 0xDB2F86],   // header mark: TL vermilion, TR cobalt, BL marigold, BR fuchsia
    cream: 0xF3EEE7,              // the name on the dark
    blocks: 0x2a2334, stone: 0x4a4152, ground: 0x110e16, river: 0x0b1222,
    windowWarm: [1.0, 0.68, 0.38], windowCool: [0.75, 0.82, 1.0],
    headlight: [1.0, 0.82, 0.55], taillight: [1.0, 0.16, 0.08],
    streets: 'rgba(255,176,104,.55)',
    flash: [1.0, 0.95, 0.88],
    // the hand-off colours are read from the page (--ground, --ink) so both themes land exactly
  },
  post: {
    high: { maxPixelRatio: 2, samples: 4, bloom: { strength: 0.6, radius: 0.5, threshold: 0.92 }, tiltShift: 1.0, trails: 180, particlesPerLink: 28 },
    low: { maxPixelRatio: 1.5, samples: 2, bloom: { strength: 0.48, radius: 0.42, threshold: 0.95 }, tiltShift: 0, trails: 110, particlesPerLink: 14 },
    lockBloom: 0.4,               // extra bloom strength at the lock, decaying
    lockFlash: 0.16,              // extra exposure at the lock, decaying
    vignette: 0.42, grain: 0.022, ripple: 0.012,
    chipEmissive: 0.7,            // tile glow on landing, tuned so the tiles match the live header chips
  },
};

const T = CONFIG.time, C = CONFIG.color;
const MONO = 'ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, "DejaVu Sans Mono", monospace';

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const seg = (t, a, b) => clamp((t - a) / (b - a));
const easeOut = (x) => 1 - Math.pow(1 - clamp(x), 3);
const expoOut = (x) => { x = clamp(x); return x >= 1 ? 1 : 1 - Math.pow(2, -10 * x); };
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
function cssColor(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    if (/^#[0-9a-f]{6}$/i.test(v)) return new THREE.Color(v);
  } catch (e) { /* fall through to the default */ }
  return new THREE.Color(fallback);
}
function radialTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 128;
  const g = c.getContext('2d'), grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, 'rgba(255,255,255,1)'); grad.addColorStop(0.25, 'rgba(255,255,255,.45)');
  grad.addColorStop(0.6, 'rgba(255,255,255,.08)'); grad.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grad; g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
// quality tier: phones, small screens and low-core devices get lighter post
function pickTier() {
  const m = /[?&]introtier=(low|high)\b/.exec(location.search);
  if (m) return m[1];
  const coarse = window.matchMedia && matchMedia('(pointer: coarse)').matches;
  const small = Math.min(innerWidth, innerHeight) < 600;
  const cores = navigator.hardwareConcurrency || 8;
  return coarse || small || cores <= 4 ? 'low' : 'high';
}

const FS_VERT = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';

export async function create(container, opts = {}) {
  const tierName = pickTier(), Q = CONFIG.post[tierName];
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, Q.maxPixelRatio));
  renderer.setSize(innerWidth, innerHeight, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.autoClear = false;
  renderer.setClearColor(C.ink, 1);
  const canvas = renderer.domElement;
  canvas.className = 'pi-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  const onLost = (e) => { e.preventDefault(); if (opts.onLost) opts.onLost(); };
  canvas.addEventListener('webglcontextlost', onLost);
  const skipEl = container.querySelector('.pi-skip');
  container.insertBefore(canvas, skipEl);

  // the page we hand off to: its ground and ink, whichever theme it is in
  const GROUND = cssColor('--ground', '#E9E7E0'), INKC = cssColor('--ink', '#17131F');
  const CREAM = new THREE.Color(C.cream);
  const chipsEl = document.querySelector('.topbar .brand .chips');

  const R = rng(11);
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(C.haze, 0.034);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const envRT = pmrem.fromScene(room, 0.04);
  scene.environment = envRT.texture;
  scene.environmentIntensity = 0.3;
  pmrem.dispose();
  if (room.dispose) room.dispose();

  const hemi = new THREE.HemisphereLight(0x8a7fb0, 0x0b0910, 0.5);
  const moon = new THREE.DirectionalLight(0xa9b8ff, 1.0);
  moon.position.set(-6, 10, 5);
  const glint = new THREE.PointLight(0xfff2e0, 0, 9, 1.4);
  scene.add(hemi, moon, glint);

  // sky dome: ink overhead, the haze + a violet city glow at the horizon (matches the fog, so the far city melts into it)
  const skyMat = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { uTop: { value: new THREE.Color(C.ink) }, uHaze: { value: new THREE.Color(C.haze) }, uGlow: { value: new THREE.Color(C.horizon) }, uK: { value: 1 } },
    vertexShader: 'varying vec3 vP; void main(){ vP = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: 'uniform vec3 uTop; uniform vec3 uHaze; uniform vec3 uGlow; uniform float uK; varying vec3 vP;\n' +
      'void main(){ float y = max(vP.y, 0.0); gl_FragColor = vec4(mix(uHaze + uGlow * 0.5 * uK, uTop, smoothstep(0.0, 0.35, y)), 1.0); }',
  });
  const sky = new THREE.Mesh(new THREE.SphereGeometry(70, 32, 16), skyMat);
  scene.add(sky);

  /* ---------- the city: Center City's grid between the Schuylkill and the Delaware ---------- */
  const NX = 10, NZ = 7, AVE = 0.14, BW = 0.78;
  const blocks = [];
  for (let i = -NX; i < NX; i++) for (let j = -NZ; j < NZ; j++) {
    const x = (i + 0.5) + (i >= 0 ? AVE : -AVE), z = (j + 0.5) + (j >= 0 ? AVE : -AVE);
    if (distToSeg(x, z, 0, 0, -8.5, -6.5) < 0.62) continue;          // the Parkway, City Hall to the north-west
    if (Math.abs(x) < 0.7 && Math.abs(z) < 0.7) continue;              // Penn Square
    const near = Math.max(0, 1 - Math.hypot(x * 0.8, z * 1.2) / 6);
    let h = 0.12 + Math.pow(R(), 3) * 0.55;
    if (R() < near * 0.55) h += 0.6 + R() * 1.8 * near;
    blocks.push({ x, z, h });
  }
  const boxGeo = new THREE.BoxGeometry(1, 1, 1); boxGeo.translate(0, 0.5, 0);
  const winU = { value: 0 }, hazeU = { value: new THREE.Color(C.haze) };
  const blockMat = new THREE.MeshStandardMaterial({ color: C.blocks, roughness: 0.8, metalness: 0.15 });
  // procedural windows (from world position, so tall towers get more floors, not stretched ones)
  // and a low haze that pools between the buildings
  blockMat.onBeforeCompile = (sh) => {
    sh.uniforms.uWin = winU; sh.uniforms.uHaze = hazeU;
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWP; varying vec3 vWN;')
      .replace('#include <project_vertex>', [
        '#include <project_vertex>',
        '#ifdef USE_INSTANCING',
        '  vWP = (modelMatrix * instanceMatrix * vec4(transformed, 1.0)).xyz;',
        '  vWN = normalize(mat3(modelMatrix) * mat3(instanceMatrix) * objectNormal);',
        '#else',
        '  vWP = (modelMatrix * vec4(transformed, 1.0)).xyz;',
        '  vWN = normalize(mat3(modelMatrix) * objectNormal);',
        '#endif',
      ].join('\n'));
    const ww = C.windowWarm.map((v) => v.toFixed(3)).join(','), wc = C.windowCool.map((v) => v.toFixed(3)).join(',');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWP; varying vec3 vWN; uniform float uWin; uniform vec3 uHaze;')
      .replace('#include <emissivemap_fragment>', [
        '#include <emissivemap_fragment>',
        'if (abs(vWN.y) < 0.5 && uWin > 0.0) {',
        '  vec2 f = vec2(dot(vWP.xz, vec2(abs(vWN.z), abs(vWN.x))) * 9.0, vWP.y * 13.0);',
        '  vec2 id = floor(f); vec2 g = fract(f);',
        '  float win = step(0.2, g.x) * step(g.x, 0.8) * step(0.28, g.y) * step(g.y, 0.78);',
        '  float h = fract(sin(dot(id + vWN.xz * 17.0, vec2(12.9898, 78.233))) * 43758.5453);',
        '  float lit = step(0.7, h) * step(0.07, vWP.y) * (0.5 + 0.9 * fract(h * 7.0));',
        // far away the grid is finer than a pixel: fade to its average instead of shimmering
        '  float aa = smoothstep(0.35, 0.8, max(fwidth(f.x), fwidth(f.y)));',
        '  vec3 warm = mix(vec3(' + ww + '), vec3(' + wc + '), step(0.93, h));',
        '  totalEmissiveRadiance += warm * mix(win * lit, 0.11, aa) * uWin;',
        '}',
      ].join('\n'))
      .replace('#include <fog_fragment>', [
        '#include <fog_fragment>',
        'float hf = exp(-max(vWP.y, 0.0) * 1.8);',
        'gl_FragColor.rgb = mix(gl_FragColor.rgb, uHaze, clamp((1.0 - exp(-length(vViewPosition) * 0.05)) * hf * 0.7, 0.0, 1.0));',
      ].join('\n'));
  };
  const inst = new THREE.InstancedMesh(boxGeo, blockMat, blocks.length);
  const m4 = new THREE.Matrix4(), col = new THREE.Color();
  blocks.forEach((b, k) => {
    m4.makeScale(BW, b.h, BW); m4.setPosition(b.x, 0, b.z); inst.setMatrixAt(k, m4);
    inst.setColorAt(k, col.set(0xffffff).multiplyScalar(0.75 + R() * 0.5));
  });
  scene.add(inst);
  // City Hall in the middle of Broad and Market, its clock lit
  const stone = new THREE.MeshStandardMaterial({ color: C.stone, roughness: 0.7, metalness: 0.1 });
  const hall = new THREE.Mesh(boxGeo, stone); hall.scale.set(0.5, 0.5, 0.5); scene.add(hall);
  const tower = new THREE.Mesh(boxGeo, stone); tower.scale.set(0.16, 1.55, 0.16); scene.add(tower);
  const clockMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(1.0, 0.8, 0.55) });
  const clock = new THREE.Mesh(boxGeo, clockMat); clock.scale.set(0.175, 0.1, 0.175); clock.position.y = 1.28; scene.add(clock);
  const spire = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.35, 8), stone); spire.position.y = 1.72; scene.add(spire);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(140, 140), new THREE.MeshStandardMaterial({ color: C.ground, roughness: 0.95 }));
  ground.rotation.x = -Math.PI / 2; scene.add(ground);
  const riverMat = new THREE.MeshStandardMaterial({ color: C.river, roughness: 0.15, metalness: 0.8, envMapIntensity: 0.8 });
  const riverGeo = new THREE.PlaneGeometry(6, 60);
  for (const x of [-13.6, 13.6]) {
    const r = new THREE.Mesh(riverGeo, riverMat); r.rotation.x = -Math.PI / 2; r.position.set(x, 0.006, 0); scene.add(r);
  }
  // street light, drawn once as a texture laid over the ground
  const aveX = [], stZ = [];
  for (let i = -NX; i <= NX; i++) aveX.push(i + (i > 0 ? AVE : i < 0 ? -AVE : 0));
  for (let j = -NZ; j <= NZ; j++) stZ.push(j + (j > 0 ? AVE : j < 0 ? -AVE : 0));
  const sc = document.createElement('canvas'); sc.width = 1024; sc.height = 768;
  {
    const g = sc.getContext('2d'), U = (x) => (x + 11) / 22 * 1024, V = (z) => (z + 8) / 16 * 768;
    g.strokeStyle = C.streets; g.lineCap = 'round';
    aveX.forEach((x, k) => { g.lineWidth = k === NX ? 7 : 2.5; g.beginPath(); g.moveTo(U(x), V(-NZ - 0.3)); g.lineTo(U(x), V(NZ + 0.3)); g.stroke(); });
    stZ.forEach((z, k) => { g.lineWidth = k === NZ ? 7 : 2.5; g.beginPath(); g.moveTo(U(-NX - 0.3), V(z)); g.lineTo(U(NX + 0.3), V(z)); g.stroke(); });
    g.lineWidth = 8; g.beginPath(); g.moveTo(U(0), V(0)); g.lineTo(U(-8.5), V(-6.5)); g.stroke();
  }
  const streetTex = new THREE.CanvasTexture(sc); streetTex.colorSpace = THREE.SRGBColorSpace; streetTex.anisotropy = 4;
  const streetMat = new THREE.MeshBasicMaterial({ map: streetTex, transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending, depthWrite: false });
  const streets = new THREE.Mesh(new THREE.PlaneGeometry(22, 16), streetMat);
  streets.rotation.x = -Math.PI / 2; streets.position.y = 0.012; scene.add(streets);

  /* ---------- traffic: instanced light trails, all motion in the vertex shader ---------- */
  const NT = Q.trails, quad = new THREE.PlaneGeometry(1, 1);
  const trailGeo = new THREE.InstancedBufferGeometry();
  trailGeo.index = quad.index;
  trailGeo.setAttribute('position', quad.getAttribute('position'));
  trailGeo.setAttribute('uv', quad.getAttribute('uv'));
  const aLine = new Float32Array(NT * 4), aMove = new Float32Array(NT * 3), aCol = new Float32Array(NT * 3);
  for (let k = 0; k < NT; k++) {
    const axis = R() < 0.5 ? 0 : 1, dir = R() < 0.5 ? -1 : 1;
    const c = axis === 0 ? aveX[Math.floor(R() * aveX.length)] : stZ[Math.floor(R() * stZ.length)];
    aLine.set([axis, c, dir * 0.035, dir], k * 4);                  // axis, street line, lane offset, direction
    aMove.set([0.7 + R() * 1.1, R() * 40, 0.25 + R() * 0.35], k * 3); // speed, phase, trail length
    aCol.set(R() < 0.42 ? C.taillight : C.headlight, k * 3);
  }
  trailGeo.setAttribute('aLine', new THREE.InstancedBufferAttribute(aLine, 4));
  trailGeo.setAttribute('aMove', new THREE.InstancedBufferAttribute(aMove, 3));
  trailGeo.setAttribute('aCol', new THREE.InstancedBufferAttribute(aCol, 3));
  trailGeo.instanceCount = NT;
  const trailMat = new THREE.ShaderMaterial({
    uniforms: { uT: { value: 0 }, uOn: { value: 0 } },
    vertexShader: [
      'attribute vec4 aLine; attribute vec3 aMove; attribute vec3 aCol; uniform float uT; varying vec2 vUv; varying vec3 vCol; varying float vEdge;',
      'void main(){',
      '  float span = aLine.x < 0.5 ? 15.0 : 21.0;',
      '  float s = mod(aMove.y + aLine.w * aMove.x * uT, span) - span * 0.5;',
      '  float along = s - aLine.w * (position.x + 0.5) * aMove.z;',
      '  vec3 p = aLine.x < 0.5 ? vec3(aLine.y + aLine.z + position.y * 0.028, 0.03, along) : vec3(along, 0.03, aLine.y + aLine.z + position.y * 0.028);',
      '  vEdge = 1.0 - smoothstep(span * 0.5 - 1.2, span * 0.5, abs(s));',   // fade in/out at the ends of the grid
      '  vUv = uv; vCol = aCol;',
      '  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);',
      '}',
    ].join('\n'),
    fragmentShader: [
      'uniform float uOn; varying vec2 vUv; varying vec3 vCol; varying float vEdge;',
      'void main(){',
      '  float head = pow(clamp(1.0 - vUv.x, 0.0, 1.0), 2.2);   // clamp: MSAA can sample just past the quad',
      '  float across = max(0.0, 1.0 - abs(vUv.y - 0.5) * 2.0);',
      '  gl_FragColor = vec4(vCol * head * across * 2.6 * uOn * vEdge, 1.0);',
      '}',
    ].join('\n'),
    blending: THREE.AdditiveBlending, transparent: true, depthWrite: false,
  });
  const trails = new THREE.Mesh(trailGeo, trailMat); trails.frustumCulled = false; scene.add(trails);

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
    const hue = hues[i], c = new THREE.Color(C.hues[hue]);
    const mat = new THREE.MeshPhysicalMaterial({ color: c, emissive: c, emissiveIntensity: 0, roughness: 0.36, metalness: 0.05, clearcoat: 0.8, clearcoatRoughness: 0.15, envMapIntensity: 0.6, fog: false });
    const mesh = new THREE.Mesh(tileGeo, mat);
    mesh.layers.enable(1);                                          // layer 1 = drawn again above the ground tiles
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: haloTex, color: c, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, opacity: 0, fog: false }));
    const glow = new THREE.Mesh(poolGeo, new THREE.MeshBasicMaterial({ map: haloTex, color: c, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, opacity: 0 }));
    glow.rotation.x = -Math.PI / 2; glow.position.set(b.x, 0.02, b.z); glow.scale.setScalar(2.8);
    scene.add(mesh, halo, glow);
    const axis = new THREE.Vector3(R() - 0.5, R() - 0.5, R() - 0.5).normalize();
    return {
      mesh, mat, halo, glow, hue, axis, c, slot: slotCount[hue]++,
      home: new THREE.Vector3(b.x, b.h + 0.07, b.z),
      lightAt: T.lightsFrom + byDist.indexOf(i) * T.lightStep, liftAt: T.lift + R() * 0.12,
      lockAt: T.lock, jitter: (R() - 0.5) * 0.05,
      target: new THREE.Vector3(), tq: new THREE.Quaternion(), local: new THREE.Vector2(), screenX: 0, cx: 0, cy: 0,
    };
  });

  /* ---------- the network: links of light (a minimum spanning tree grown from Penn Square, plus a few cross-links) ---------- */
  const N = tiles.length, P = (k) => tiles[k].home;
  const inTree = new Array(N).fill(false), depth = new Array(N).fill(0), edges = [];
  inTree[byDist[0]] = true;
  for (let n = 1; n < N; n++) {
    let best = null, bd = Infinity;
    for (let a = 0; a < N; a++) if (inTree[a]) for (let b = 0; b < N; b++) if (!inTree[b]) {
      const d = P(a).distanceTo(P(b)); if (d < bd) { bd = d; best = [a, b]; }
    }
    inTree[best[1]] = true; depth[best[1]] = depth[best[0]] + 1;
    edges.push({ a: best[0], b: best[1], at: T.linksFrom + depth[best[0]] * T.linkDepthStep + R() * 0.07, dur: T.linkDur });
  }
  const has = (a, b) => edges.some((e) => (e.a === a && e.b === b) || (e.a === b && e.b === a));
  let extra = 0;
  for (const a of shuffle([...Array(N).keys()], R)) {
    if (extra >= 12) break;
    let best = -1, bd = 3.8;
    for (let b = 0; b < N; b++) if (b !== a && !has(a, b)) { const d = P(a).distanceTo(P(b)); if (d < bd) { bd = d; best = b; } }
    if (best >= 0) { edges.push({ a, b: best, at: T.extraLinksFrom + R() * 0.6, dur: T.linkDur + 0.05 }); extra++; }
  }
  // the link itself: a thin tube that draws on, then glows with the colours of both ends
  const lineVS = 'varying float vU; void main(){ vU = uv.x; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';
  const lineFS = [
    'uniform float uProg; uniform float uFade; uniform float uHot; uniform vec3 uA; uniform vec3 uB; varying float vU;',
    'void main(){',
    '  if (vU > uProg) discard;',
    '  float hd = (uProg - vU) * 14.0; float head = exp(-hd * hd) * (1.0 - step(0.999, uProg));',
    '  vec3 c = mix(uA, uB, vU) * (0.55 + 2.6 * head);',
    '  c = mix(c, vec3(1.0, 0.95, 0.88) * 1.6, uHot);',
    '  gl_FragColor = vec4(c * uFade, 1.0);',
    '}',
  ].join('\n');
  const hotU = { value: 0 }, fadeU = { value: 1 };
  const linkData = [];
  for (const e of edges) {
    const a = P(e.a), b = P(e.b), d = a.distanceTo(b);
    const mid = a.clone().add(b).multiplyScalar(0.5); mid.y += 0.3 + d * 0.28;
    linkData.push({ a, mid, b, e });
    const geo = new THREE.TubeGeometry(new THREE.QuadraticBezierCurve3(a, mid, b), 40, 0.011, 5, false);
    e.mat = new THREE.ShaderMaterial({
      uniforms: { uProg: { value: 0 }, uFade: fadeU, uHot: hotU, uA: { value: new THREE.Color(C.hues[tiles[e.a].hue]) }, uB: { value: new THREE.Color(C.hues[tiles[e.b].hue]) } },
      vertexShader: lineVS, fragmentShader: lineFS,
      blending: THREE.AdditiveBlending, transparent: true, depthWrite: false,
    });
    e.mesh = new THREE.Mesh(geo, e.mat); scene.add(e.mesh);
  }

  /* ---------- GPU particles flowing along every link (one draw call, motion in the vertex shader) ---------- */
  const PPL = Q.particlesPerLink, NP = linkData.length * PPL;
  const pA = new Float32Array(NP * 3), pM = new Float32Array(NP * 3), pB = new Float32Array(NP * 3);
  const pSeed = new Float32Array(NP * 3), pTime = new Float32Array(NP * 2), pCA = new Float32Array(NP * 3), pCB = new Float32Array(NP * 3);
  let k = 0;
  for (const L of linkData) {
    const ca = new THREE.Color(C.hues[tiles[L.e.a].hue]), cb = new THREE.Color(C.hues[tiles[L.e.b].hue]);
    for (let j = 0; j < PPL; j++, k++) {
      pA.set([L.a.x, L.a.y, L.a.z], k * 3); pM.set([L.mid.x, L.mid.y, L.mid.z], k * 3); pB.set([L.b.x, L.b.y, L.b.z], k * 3);
      pSeed.set([R(), 0.35 + R() * 0.35, 0.6 + R() * 0.8], k * 3);   // phase, speed, size
      pTime.set([L.e.at, L.e.dur], k * 2);
      pCA.set([ca.r, ca.g, ca.b], k * 3); pCB.set([cb.r, cb.g, cb.b], k * 3);
    }
  }
  const partGeo = new THREE.BufferGeometry();
  partGeo.setAttribute('position', new THREE.BufferAttribute(pA, 3));
  partGeo.setAttribute('aM', new THREE.BufferAttribute(pM, 3));
  partGeo.setAttribute('aB', new THREE.BufferAttribute(pB, 3));
  partGeo.setAttribute('aSeed', new THREE.BufferAttribute(pSeed, 3));
  partGeo.setAttribute('aTime', new THREE.BufferAttribute(pTime, 2));
  partGeo.setAttribute('aCA', new THREE.BufferAttribute(pCA, 3));
  partGeo.setAttribute('aCB', new THREE.BufferAttribute(pCB, 3));
  const partMat = new THREE.ShaderMaterial({
    uniforms: { uT: { value: 0 }, uHot: hotU, uFade: fadeU, uScale: { value: 500 } },
    vertexShader: [
      'attribute vec3 aM; attribute vec3 aB; attribute vec3 aSeed; attribute vec2 aTime; attribute vec3 aCA; attribute vec3 aCB;',
      'uniform float uT; uniform float uHot; uniform float uScale; varying vec3 vCol; varying float vA;',
      'void main(){',
      '  float prog = clamp((uT - aTime.x) / aTime.y, 0.0, 1.0); prog = 1.0 - pow(1.0 - prog, 3.0);',
      // flow along the link; once the links run hot the light rushes faster
      '  float s = fract(aSeed.x + uT * aSeed.y + uHot * uHot * (uT - 2.5) * 1.6);',
      '  float k = 1.0 - s;',
      '  vec3 p = k * k * position + 2.0 * k * s * aM + s * s * aB;',
      '  vec4 mv = modelViewMatrix * vec4(p, 1.0);',
      '  float on = step(s, prog) * step(0.001, prog);',
      '  gl_PointSize = uScale * 0.02 * aSeed.z * (1.0 + uHot) / -mv.z * on;',
      '  vCol = mix(mix(aCA, aCB, s) * 2.2, vec3(1.0, 0.95, 0.88) * 3.0, uHot);',
      '  vA = on * smoothstep(0.0, 0.06, s) * (1.0 - smoothstep(0.94, 1.0, s));',
      '  gl_Position = projectionMatrix * mv;',
      '}',
    ].join('\n'),
    fragmentShader: [
      'uniform float uFade; varying vec3 vCol; varying float vA;',
      'void main(){ float d = length(gl_PointCoord - 0.5); float a = 1.0 - smoothstep(0.0, 0.5, d); a *= a;',
      '  gl_FragColor = vec4(vCol * a * vA * uFade, 1.0); }',
    ].join('\n'),
    blending: THREE.AdditiveBlending, transparent: true, depthWrite: false,
  });
  const particles = new THREE.Points(partGeo, partMat); particles.frustumCulled = false; scene.add(particles);

  /* ---------- light trails behind the tiles as they converge (camera-facing ribbons) ---------- */
  const TS = 14;
  const ribPos = new Float32Array(N * TS * 2 * 3), ribCol = new Float32Array(N * TS * 2 * 3), ribIdx = [];
  for (let n = 0; n < N; n++) for (let j = 0; j < TS - 1; j++) {
    const o = (n * TS + j) * 2; ribIdx.push(o, o + 1, o + 2, o + 1, o + 3, o + 2);
  }
  const ribGeo = new THREE.BufferGeometry();
  ribGeo.setAttribute('position', new THREE.BufferAttribute(ribPos, 3).setUsage(THREE.DynamicDrawUsage));
  ribGeo.setAttribute('color', new THREE.BufferAttribute(ribCol, 3).setUsage(THREE.DynamicDrawUsage));
  ribGeo.setIndex(ribIdx);
  const ribMat = new THREE.MeshBasicMaterial({ vertexColors: true, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, side: THREE.DoubleSide, fog: false });
  const ribbons = new THREE.Mesh(ribGeo, ribMat); ribbons.frustumCulled = false; scene.add(ribbons);

  /* ---------- the lockup group: sits in front of the final camera ---------- */
  const lockG = new THREE.Group(); scene.add(lockG);
  const flashC = new THREE.Color(...C.flash);
  const ringMat = new THREE.MeshBasicMaterial({ color: flashC.clone().multiplyScalar(1.3), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, fog: false });
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.985, 1, 160), ringMat); lockG.add(ring);
  const streakMat = new THREE.MeshBasicMaterial({ map: haloTex, color: flashC.clone().multiplyScalar(4), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, fog: false });
  const streak = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), streakMat); lockG.add(streak);

  /* ---------- the name, in the site's own monospace, one plane per letter ---------- */
  const textCanvas = document.createElement('canvas');
  const textTex = new THREE.CanvasTexture(textCanvas); textTex.colorSpace = THREE.SRGBColorSpace; textTex.anisotropy = 4;
  const letterVS = 'uniform float uX0; uniform float uX1; varying vec2 vUv; varying float vX; void main(){ vUv = uv; vX = mix(uX0, uX1, uv.x); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';
  const letterFS = [
    'uniform sampler2D map; uniform vec4 uRect; uniform vec3 uColor; uniform float uOpacity; uniform float uSweep; varying vec2 vUv; varying float vX;',
    'void main(){',
    '  float a = texture2D(map, uRect.xy + vUv * uRect.zw).a;',
    '  float sx = (vX - uSweep) / 0.14; float sw = exp(-sx * sx);',
    '  gl_FragColor = vec4(uColor + vec3(1.0, 0.93, 0.82) * sw * 1.1, a * uOpacity);',
    '  #include <tonemapping_fragment>',
    '  #include <colorspace_fragment>',
    '}',
  ].join('\n');
  const letterGeo = new THREE.PlaneGeometry(1, 1);
  let letters = [];
  const sweepU = { value: -99 }, letterColor = new THREE.Color();
  function buildText(align) {
    for (const l of letters) { lockG.remove(l.mesh); l.mesh.material.dispose(); }
    letters = [];
    const px = 120, sp = px * 0.1, lh = px * 1.28, lines = ['PHILADELPHIA', 'CREATIVE', 'ALLIANCE'];
    const g = textCanvas.getContext('2d'); g.font = '600 ' + px + 'px ' + MONO;
    const widthOf = (s) => [...s].reduce((w, ch) => w + g.measureText(ch).width + sp, -sp);
    const W = Math.ceil(Math.max(...lines.map(widthOf))) + 8, H = Math.ceil(lh * 3);
    textCanvas.width = W; textCanvas.height = H;
    g.font = '600 ' + px + 'px ' + MONO; g.fillStyle = '#ffffff'; g.textBaseline = 'middle';
    const rects = [];
    lines.forEach((s, li) => {
      let x = align === 'center' ? (W - widthOf(s)) / 2 : 4, n = 0;
      for (const ch of s) {
        const cw = g.measureText(ch).width;
        g.fillText(ch, x, lh * (li + 0.5));
        rects.push({ x: x - sp / 2, y: lh * li, w: cw + sp, h: lh, line: li, n: n++ });
        x += cw + sp;
      }
    });
    textTex.needsUpdate = true;
    for (const r of rects) {
      const mat = new THREE.ShaderMaterial({
        uniforms: { map: { value: textTex }, uRect: { value: new THREE.Vector4(r.x / W, 1 - (r.y + r.h) / H, r.w / W, r.h / H) },
          uColor: { value: new THREE.Color() }, uOpacity: { value: 0 }, uSweep: sweepU, uX0: { value: 0 }, uX1: { value: 0 } },
        vertexShader: letterVS, fragmentShader: letterFS, transparent: true, depthWrite: false, depthTest: false,
      });
      const mesh = new THREE.Mesh(letterGeo, mat); mesh.layers.enable(1); mesh.renderOrder = 5;
      lockG.add(mesh);
      letters.push({ mesh, mat, r, x: 0, y: 0, order: 0 });
    }
    return { w: W, h: H };
  }

  /* ---------- post: EffectComposer chain ---------- */
  const camera = new THREE.PerspectiveCamera(42, 1, 0.05, 160);
  const finalCam = new THREE.PerspectiveCamera(42, 1, 0.05, 160);
  const composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: Q.samples }));
  const renderPass = new RenderPass(scene, camera);
  // tilt-shift depth of field: the overhead city reads as a miniature; sharp again for the lockup
  const tiltShader = (dir) => ({
    uniforms: { tDiffuse: { value: null }, uDir: { value: new THREE.Vector2(...dir) }, uAmount: { value: 0 }, uFocus: { value: 0.5 } },
    vertexShader: FS_VERT,
    fragmentShader: [
      'uniform sampler2D tDiffuse; uniform vec2 uDir; uniform float uAmount; uniform float uFocus; varying vec2 vUv;',
      'void main(){',
      '  float r = uAmount * smoothstep(0.12, 0.55, abs(vUv.y - uFocus));',
      '  vec4 c = texture2D(tDiffuse, vUv) * 0.2270270;',
      '  vec2 o1 = uDir * r * 1.3846154, o2 = uDir * r * 3.2307692;',
      '  c += (texture2D(tDiffuse, vUv + o1) + texture2D(tDiffuse, vUv - o1)) * 0.3162162;',
      '  c += (texture2D(tDiffuse, vUv + o2) + texture2D(tDiffuse, vUv - o2)) * 0.0702703;',
      '  gl_FragColor = c;',
      '}',
    ].join('\n'),
  });
  const tiltH = new ShaderPass(tiltShader([1, 0])), tiltV = new ShaderPass(tiltShader([0, 1]));
  const bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), Q.bloom.strength, Q.bloom.radius, Q.bloom.threshold);
  const output = new OutputPass();
  // finish (display space, after tone mapping): ripple at the lock, vignette, grain, and the page's ground laid tile by tile
  const finish = new ShaderPass({
    uniforms: {
      tDiffuse: { value: null }, uT: { value: 0 }, uRes: { value: new THREE.Vector2(1, 1) },
      uGround: { value: GROUND.clone().convertLinearToSRGB() }, uCenter: { value: new THREE.Vector2() }, uCell: { value: 100 }, uFar: { value: 1000 },
      uRipple: { value: new THREE.Vector2(0, 0) }, uVig: { value: CONFIG.post.vignette }, uGrain: { value: CONFIG.post.grain },
    },
    vertexShader: FS_VERT,
    fragmentShader: [
      'uniform sampler2D tDiffuse; uniform float uT; uniform vec2 uRes; uniform vec3 uGround; uniform vec2 uCenter; uniform float uCell; uniform float uFar;',
      'uniform vec2 uRipple; uniform float uVig; uniform float uGrain; varying vec2 vUv;',
      'float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }',
      'void main(){',
      '  vec2 px = vUv * uRes;',
      // ripple: a ring of refraction running out from the mark (uRipple = radius in px, strength)
      '  vec2 dv = px - uCenter; float dist = length(dv);',
      '  float wx = (dist - uRipple.x) / (uRes.y * 0.045); float wave = exp(-wx * wx) * uRipple.y;',
      '  vec2 uv = vUv - (dv / max(dist, 1.0)) * wave;',
      '  vec3 c = texture2D(tDiffuse, uv).rgb;',
      '  vec2 q = vUv - 0.5; c *= 1.0 - uVig * smoothstep(0.25, 0.85, length(q * vec2(1.15, 1.0)));',
      '  c += (hash(px + fract(uT * 7.0) * 91.0) - 0.5) * uGrain;',
      // ground tiles: each cell scales up and rotates into place, outward from the mark
      '  vec2 id = floor(px / uCell); vec2 ctr = (id + 0.5) * uCell; float h = hash(id);',
      '  float st = ' + T.flood.toFixed(3) + ' + length(ctr - uCenter) / uFar * ' + T.floodSpread.toFixed(3) + ' + h * 0.07;',
      '  float p = clamp((uT - st) / ' + T.floodTile.toFixed(3) + ', 0.0, 1.0); p = 1.0 - pow(1.0 - p, 3.0);',
      '  float a = (h - 0.5) * 0.9 * (1.0 - p); vec2 l = (px - ctr) / (0.5 * uCell);',
      '  l = mat2(cos(a), -sin(a), sin(a), cos(a)) * l;',
      '  float d = max(abs(l.x), abs(l.y)); float aaw = 2.0 / uCell;',
      '  float cov = 1.0 - smoothstep(1.1 * p - aaw, 1.1 * p, d);',
      '  vec3 g = uGround * (1.0 - 0.06 * (1.0 - p));',
      '  gl_FragColor = vec4(mix(c, g, cov), 1.0);',
      '}',
    ].join('\n'),
  });
  composer.addPass(renderPass);
  if (Q.tiltShift > 0) { composer.addPass(tiltH); composer.addPass(tiltV); }
  composer.addPass(bloom);
  composer.addPass(output);
  composer.addPass(finish);

  /* ---------- camera path + lockup layout (recomputed on resize) ---------- */
  let path, look, lock = {}, chip = null, bufW = 1, bufH = 1;
  const tmpV = new THREE.Vector3(), tmpV2 = new THREE.Vector3(), tmpQ = new THREE.Quaternion();
  const faceUp = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
  // mark geometry: 3x3 tesserae per chip; on the flight home the gap goes to the header's own chip:gap ratio (7px chips, 2px gap)
  const S = 0.36, GAP = 0.07, CHIP = 3 * S + 2 * GAP, G0 = 0.16;

  function layout() {
    const w = innerWidth, h = innerHeight, aspect = w / h, portrait = aspect < 0.9;
    renderer.setSize(w, h, false);
    composer.setPixelRatio(renderer.getPixelRatio());
    composer.setSize(w, h);
    const pr = renderer.getPixelRatio(); bufW = Math.round(w * pr); bufH = Math.round(h * pr);
    finish.uniforms.uRes.value.set(bufW, bufH);
    const fov = portrait ? 56 : 42;
    partMat.uniforms.uScale.value = bufH / (2 * Math.tan(THREE.MathUtils.degToRad(fov / 2)));
    tiltH.uniforms.uDir.value.set(1 / bufW, 0); tiltV.uniforms.uDir.value.set(0, 1 / bufH);
    camera.fov = finalCam.fov = fov; camera.aspect = finalCam.aspect = aspect;
    camera.updateProjectionMatrix(); finalCam.updateProjectionMatrix();
    const V = (x, y, z) => new THREE.Vector3(x, y, z);
    // spline fly-over: street level on Market looking west to City Hall, then up over the whole grid
    path = new THREE.CatmullRomCurve3(portrait
      ? [V(9.6, 0.42, 0.02), V(10.3, 3.0, 2.2), V(13.2, 9.6, 3.0), V(15.2, 15.6, 2.2)]
      : [V(9.6, 0.42, 0.02), V(8.6, 2.6, 4.2), V(5.4, 8.6, 10.4), V(2.4, 13.4, 13.2)]);
    look = new THREE.CatmullRomCurve3(portrait
      ? [V(0, 0.75, 0), V(0, 0.5, 0), V(0.4, 0, 0.3), V(0.6, 0, 0)]
      : [V(0, 0.75, 0), V(0, 0.5, 0), V(0, 0, 0.6), V(0, 0, 0.3)]);
    finalCam.position.copy(path.getPoint(1)); finalCam.lookAt(look.getPoint(1)); finalCam.updateMatrixWorld(true);
    lockG.position.copy(finalCam.position); lockG.quaternion.copy(finalCam.quaternion);

    // the lockup: mark of 2x2 chips, 3x3 tesserae each, plus the name
    const markW = 2 * CHIP + G0;
    const tx = buildText(portrait ? 'center' : 'left');
    const textH = markW * (portrait ? 0.74 : 0.92), textW = textH * tx.w / tx.h;
    const gap = portrait ? 0.42 : 0.55;
    const lockW = portrait ? Math.max(markW, textW) : markW + gap + textW;
    const lockH = portrait ? markW + gap + textH : markW;
    const tv = Math.tan(THREE.MathUtils.degToRad(fov / 2));
    const fw = portrait ? 0.84 : 0.64, fh = portrait ? 0.66 : 0.54;
    const D = Math.max(lockW / (2 * fw * tv * aspect), lockH / (2 * fh * tv));
    const left = portrait ? -markW / 2 : -lockW / 2, top = lockH / 2;
    const mcx = left + markW / 2, mcy = top - markW / 2;
    for (const t of tiles) {
      t.cx = t.hue % 2; t.cy = t.hue > 1 ? 1 : 0;
      // position inside its chip, relative to the chip centre (the flight re-spaces the chips)
      t.local.set((t.slot % 3) * (S + GAP) + S / 2 - CHIP / 2, -(Math.floor(t.slot / 3) * (S + GAP) + S / 2 - CHIP / 2));
      const x = left + t.cx * (CHIP + G0) + (t.slot % 3) * (S + GAP) + S / 2;
      const y = top - (t.cy * (CHIP + G0) + Math.floor(t.slot / 3) * (S + GAP) + S / 2);
      t.target.set(x, y, -D).applyMatrix4(finalCam.matrixWorld);
      t.tq.copy(finalCam.quaternion).multiply(faceUp).multiply(tmpQ.setFromAxisAngle(new THREE.Vector3(0, 1, 0), t.jitter));
      t.screenX = (x - left) / lockW;
      t.lockAt = T.lock + Math.hypot(x - mcx, y - mcy) / markW * 0.05;   // inner tiles land a hair first: a ratchet, not a smear
    }
    const txc = portrait ? 0 : left + markW + gap + textW / 2, tyc = portrait ? top - markW - gap - textH / 2 : 0;
    for (const l of letters) {
      const r = l.r;
      l.w = r.w / tx.w * textW; l.h = r.h / tx.h * textH;
      l.x = txc - textW / 2 + (r.x + r.w / 2) / tx.w * textW; l.y = tyc + textH / 2 - (r.y + r.h / 2) / tx.h * textH;
      l.mesh.scale.set(l.w, l.h, 1);
      l.mat.uniforms.uX0.value = l.x - l.w / 2; l.mat.uniforms.uX1.value = l.x + l.w / 2;
      l.order = r.line * 3 + r.n;   // lines start close together, letters run along each line
    }
    lock = { D, left, top, markW, lockW, mcx, mcy, portrait, lh: textH / 3 };
    ring.position.set(mcx, mcy, -D - 0.05);
    streak.position.set(mcx, mcy, -D + 0.05);
    // where the mark sits on screen (for the ground tiles and the ripple)
    const mc = tmpV.set(mcx, mcy, -D).applyMatrix4(finalCam.matrixWorld).project(finalCam);
    finish.uniforms.uCenter.value.set((mc.x * 0.5 + 0.5) * bufW, (mc.y * 0.5 + 0.5) * bufH);
    finish.uniforms.uCell.value = Math.ceil(Math.max(bufW, bufH) / (portrait ? 9 : 16));
    finish.uniforms.uFar.value = Math.hypot(bufW, bufH);
    // the live header chips the mark settles into
    chip = null;
    if (chipsEl) {
      const r = chipsEl.getBoundingClientRect();
      if (r.width > 2 && r.bottom > 0 && r.top < h) chip = { x: (r.left + r.width / 2) / w * 2 - 1, y: -((r.top + r.height / 2) / h * 2 - 1), px: r.width };
    }
  }
  layout();
  const onResize = () => layout();
  window.addEventListener('resize', onResize);

  /* ---------- tile flight: a pure function of t ---------- */
  function flightPos(tl, t, out) {
    // lifted above its roof, gathering light
    const lu = easeOut(seg(t, tl.liftAt, tl.liftAt + 0.42));
    out.copy(tl.home); out.y += lu * 0.55;
    if (t <= T.converge) return out;
    // then it accelerates into its place in the mark, arriving at speed (the stop is the snap)
    const e = Math.pow(seg(t, T.converge, tl.lockAt), 2.3);
    const ax = tl.home.x, ay = tl.home.y + 0.55, az = tl.home.z;
    tmpV2.set(ax, ay, az).lerp(tl.target, 0.45); tmpV2.y += 1.6;
    const k = 1 - e;
    out.set(k * k * ax + 2 * k * e * tmpV2.x + e * e * tl.target.x,
      k * k * ay + 2 * k * e * tmpV2.y + e * e * tl.target.y,
      k * k * az + 2 * k * e * tmpV2.z + e * e * tl.target.z);
    return out;
  }

  /* ---------- one frame at time t ---------- */
  const fwd = new THREE.Vector3(), side = new THREE.Vector3(), rA = new THREE.Vector3(), rB = new THREE.Vector3();
  const flyA = new THREE.Vector3(), flyB = new THREE.Vector3();
  function update(t) {
    // camera: the fly-over settles exactly as the mark locks, kicks with the impact, then keeps pulling back
    const u = easeInOut(seg(t, 0, T.lock));
    camera.position.copy(path.getPoint(u));
    camera.lookAt(look.getPoint(u));
    camera.getWorldDirection(fwd);
    const dl = t - T.lock;
    if (dl > 0) {
      const kick = 0.035 * lock.D * Math.exp(-dl * 7) * Math.sin(dl * 24);
      const pull = 0.09 * lock.D * easeOut(seg(t, T.lock, CONFIG.END));
      camera.position.addScaledVector(fwd, kick - pull);
    }
    camera.updateMatrixWorld(true);

    // exposure: fade up, plus the flash of the lock
    const dL = t - T.lock - 0.04;
    const flash = dL > 0 ? Math.exp(-dL * 8) : 0;
    renderer.toneMappingExposure = easeOut(seg(t, 0, T.exposureIn)) * (1 + CONFIG.post.lockFlash * flash);
    bloom.strength = Q.bloom.strength + CONFIG.post.lockBloom * flash;
    // depth of field: strongest over the miniature city, gone before the lockup
    const tilt = Q.tiltShift * easeInOut(seg(t, 0.6, 1.8)) * (1 - easeInOut(seg(t, T.lift, T.converge + 0.2)));
    tiltH.uniforms.uAmount.value = tiltV.uniforms.uAmount.value = tilt * 2.2 * (bufH / 900);
    tiltH.enabled = tiltV.enabled = Q.tiltShift > 0 && tilt > 0.01;

    // the city sinks into the dark as the lights gather
    const on = easeOut(seg(t, 0.15, 1.3)), dim = easeInOut(seg(t, T.lift, T.lock - 0.1));
    scene.fog.density = 0.034 + dim * 0.03;
    hemi.intensity = 0.5 * (1 - 0.6 * dim); moon.intensity = 1.0 * (1 - 0.5 * dim);
    scene.environmentIntensity = 0.3 * (1 - 0.6 * dim);
    skyMat.uniforms.uK.value = on * (1 - 0.7 * dim);
    streetMat.opacity = 0.4 * on * (1 - 0.75 * dim);
    winU.value = 0.9 * on * (1 - 0.8 * dim);
    trailMat.uniforms.uT.value = t; trailMat.uniforms.uOn.value = on * (1 - 0.8 * dim);
    clockMat.color.setRGB(1.0, 0.8, 0.55).multiplyScalar(0.2 + 1.6 * on * (1 - 0.7 * dim));

    const sweep = -0.3 + 1.6 * seg(t, T.sweep, T.sweep + T.sweepDur);
    // flight into the header: the chip centre, at the mark's depth from the current camera
    const fly = easeInOut(seg(t, T.fly, T.fly + T.flyDur));
    // the 3x3 tesserae close up into one solid chip each, like the header's
    const fuse = 1 - (1 - 3 * S / CHIP) * easeInOut(seg(t, T.fly, T.fly + 0.35)), chipNow = CHIP * fuse;
    let flyC = null, flyK = 1;
    if (fly > 0 && chip) {
      const mcW = flyA.set(lock.mcx, lock.mcy, -lock.D).applyMatrix4(finalCam.matrixWorld);
      const dcur = tmpV.copy(mcW).sub(camera.position).dot(fwd);
      const tv = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
      const target = flyB.set(chip.x * dcur * tv * camera.aspect, chip.y * dcur * tv, -dcur).applyMatrix4(camera.matrixWorld);
      const markPx = (2 * 3 * S + 3 * S * 2 / 7) / (2 * dcur * tv) * innerHeight;   // the fused mark, header gap ratio
      flyK = 1 + (chip.px / markPx - 1) * fly;
      flyC = mcW.lerp(target, fly);
    }
    const gapNow = G0 + (chipNow * 2 / 7 - G0) * fly;
    let ri = 0;
    for (const tl of tiles) {
      const lu = seg(t, tl.lightAt, tl.lightAt + 0.4), lit = easeOut(lu);
      const pop = Math.exp(-Math.pow((t - tl.lightAt - 0.1) / 0.09, 2));
      const lift = easeOut(seg(t, tl.liftAt, tl.liftAt + 0.42));
      const dLock = t - tl.lockAt;
      if (t < tl.liftAt) {
        tl.mesh.position.copy(tl.home); tl.mesh.position.y += (1 - lit) * 0.35;
        tl.mesh.scale.setScalar(Math.max(0.001, backOut(lu)));
        tl.mesh.quaternion.identity();
        tl.mat.emissiveIntensity = 2.0 * lit + 4.0 * pop;
      } else if (dLock < 0) {
        flightPos(tl, t, tl.mesh.position);
        const x = seg(t, T.converge, tl.lockAt), e = Math.pow(x, 2.3);
        tl.mesh.scale.setScalar(1 + 0.12 * lift * (1 - e));
        tl.mesh.quaternion.identity().slerp(tl.tq, e).multiply(tmpQ.setFromAxisAngle(tl.axis, (1 - e) * (0.6 * lift + 2.4 * x)));
        tl.mat.emissiveIntensity = 1.8 + 1.0 * lift;
      } else {
        // locked: a thunk into the plane, a flash, then the tile cools to its own colour
        const sq = Math.exp(-dLock * 11) * Math.cos(dLock * 34);
        if (flyC) {
          const lx = tl.local.x * fuse + (tl.cx ? 1 : -1) * (chipNow / 2 + gapNow / 2), ly = tl.local.y * fuse + (tl.cy ? -1 : 1) * (chipNow / 2 + gapNow / 2);
          tl.mesh.position.set(lx * flyK, ly * flyK, 0).applyQuaternion(finalCam.quaternion).add(flyC);
          tl.mesh.scale.setScalar(flyK);
        } else {
          tl.mesh.position.copy(tl.target).addScaledVector(fwd, -0.18 * sq);
          tl.mesh.scale.setScalar(1 + 0.06 * sq);
        }
        tl.mesh.quaternion.copy(tl.tq);
        const cool = easeOut(seg(dLock, 0, 0.55));
        const sw = Math.exp(-Math.pow((tl.screenX - sweep) / 0.12, 2));
        // as it flies home the tile brightens to read as the flat header chip it becomes
        tl.mat.emissiveIntensity = (3.2 * (1 - cool) + 0.42 * cool + 1.1 * sw) * (1 - fly) + CONFIG.post.chipEmissive * fly;
      }
      const away = 1 - easeOut(seg(t, tl.liftAt, tl.liftAt + 0.35));
      tl.halo.position.copy(tl.mesh.position);
      tl.halo.scale.setScalar(1.1 + 0.8 * pop + 0.5 * lift * (1 - away));
      tl.halo.material.opacity = (0.45 * lit + 0.8 * pop) * away + 0.35 * lift * (1 - seg(t, T.converge, tl.lockAt));
      tl.glow.material.opacity = 0.55 * lit * away;

      // ribbon: where this tile was over the last ~quarter second
      const showR = t > T.converge && dLock < 0.35;
      const fadeR = 1 - easeOut(seg(dLock, 0, 0.3));
      for (let j = 0; j < TS; j++) {
        const tj = Math.max(T.converge, Math.min(t, tl.lockAt) - j * 0.02);
        flightPos(tl, tj, rA);
        flightPos(tl, Math.max(T.converge, tj - 0.01), rB);
        side.copy(rA).sub(rB).cross(tmpV.copy(rA).sub(camera.position)).normalize();
        if (!showR) side.set(0, 0, 0);
        const wdt = 0.05 * (1 - j / (TS - 1)), o = (ri * TS + j) * 6;
        ribPos[o] = rA.x + side.x * wdt; ribPos[o + 1] = rA.y + side.y * wdt; ribPos[o + 2] = rA.z + side.z * wdt;
        ribPos[o + 3] = rA.x - side.x * wdt; ribPos[o + 4] = rA.y - side.y * wdt; ribPos[o + 5] = rA.z - side.z * wdt;
        const b = showR ? 2.6 * Math.pow(1 - j / (TS - 1), 1.5) * fadeR : 0;
        ribCol[o] = ribCol[o + 3] = tl.c.r * b; ribCol[o + 1] = ribCol[o + 4] = tl.c.g * b; ribCol[o + 2] = ribCol[o + 5] = tl.c.b * b;
      }
      ri++;
    }
    ribbons.visible = t > T.converge && t < T.lock + 0.5;
    if (ribbons.visible) { ribGeo.attributes.position.needsUpdate = true; ribGeo.attributes.color.needsUpdate = true; }

    // links + particles: draw on, run hot, then fade as the lights leave
    hotU.value = easeOut(seg(t, T.lift - 0.1, T.lift + 0.3));
    fadeU.value = 1 - easeOut(seg(t, T.converge - 0.05, T.converge + 0.25));
    const linksOn = t < T.converge + 0.3;
    for (const e of edges) {
      e.mat.uniforms.uProg.value = easeOut(seg(t, e.at, e.at + e.dur));
      e.mesh.visible = linksOn && t > e.at;
    }
    partMat.uniforms.uT.value = t;
    particles.visible = linksOn && t > T.linksFrom;

    // the lock: ring, streak, ripple
    const ru = seg(dL, 0, 0.75);
    ring.visible = streak.visible = dL > 0 && dL < 0.9;
    ring.scale.setScalar(lock.markW * (0.62 + 3.2 * expoOut(ru)));
    ringMat.opacity = (1 - easeOut(ru)) * 0.9;
    const su = seg(dL, 0, 0.7);
    streak.scale.set(lock.markW * (0.4 + 7 * expoOut(su)), lock.markW * 0.07 * (1 - 0.6 * su), 1);
    streakMat.opacity = Math.sin(Math.PI * Math.min(1, su * 1.6)) * (1 - su);
    const rp = seg(dL, 0, 0.9);
    finish.uniforms.uRipple.value.set(expoOut(rp) * Math.hypot(bufW, bufH) * 0.75, dL > 0 ? CONFIG.post.ripple * (1 - rp) * (1 - rp) : 0);

    // the name rises in, letter by letter; it turns to the page's ink as the ground is laid
    letterColor.copy(CREAM).lerp(INKC, easeInOut(seg(t, T.flood + 0.05, T.flood + 0.4)));
    const nameOut = 1 - easeOut(seg(t, T.nameOut, T.nameOut + T.nameOutDur));   // gone before the ground is laid
    for (const l of letters) {
      const st = T.textIn + l.order * T.letterStep, e = expoOut(seg(t, st, st + T.letterDur));
      l.mat.uniforms.uOpacity.value = Math.min(1, e * 1.4) * nameOut;
      l.mat.uniforms.uColor.value.copy(letterColor);
      l.mesh.position.set(l.x, l.y - (1 - e) * lock.lh * 0.7, -lock.D);
      l.mesh.visible = e > 0 && nameOut > 0;
    }
    sweepU.value = lock.left + lock.lockW * sweep;
    const gu = seg(t, T.sweep, T.sweep + T.sweepDur);
    glint.intensity = 40 * Math.sin(Math.PI * gu);
    glint.position.set(lock.left + lock.lockW * sweep, lock.top + 0.6, -lock.D + 1.4).applyMatrix4(finalCam.matrixWorld);

    finish.uniforms.uT.value = t;
    canvas.style.opacity = String(1 - seg(t, T.fade, CONFIG.END - 0.03));
    // the night-themed Skip button leaves before the page's ground arrives
    if (skipEl) { const so = 1 - seg(t, T.flood - 0.25, T.flood); skipEl.style.opacity = String(so); skipEl.style.pointerEvents = so <= 0 ? 'none' : ''; }
  }

  function render(t) {
    update(t);
    camera.layers.set(0);
    composer.render(0);
    // once the ground is being laid, the mark and name are drawn again on top of it
    if (t >= T.flood) {
      renderer.setRenderTarget(null);
      renderer.clearDepth();
      camera.layers.set(1);
      renderer.render(scene, camera);
      camera.layers.set(0);
    }
  }

  function dispose() {
    window.removeEventListener('resize', onResize);
    canvas.removeEventListener('webglcontextlost', onLost);
    const seen = new Set();
    scene.traverse((o) => {
      if (o.geometry && !seen.has(o.geometry)) { seen.add(o.geometry); o.geometry.dispose(); }
      const ms = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
      for (const m of ms) if (!seen.has(m)) { seen.add(m); m.dispose(); }
    });
    for (const p of composer.passes) if (p.dispose) p.dispose();
    composer.dispose();
    boxGeo.dispose(); quad.dispose(); haloTex.dispose(); streetTex.dispose(); textTex.dispose(); envRT.dispose();
    renderer.dispose(); renderer.forceContextLoss();
    if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
  }

  // compile everything and warm the passes before the clock starts, so the first frames don't stall
  if (renderer.compileAsync) await renderer.compileAsync(scene, camera); else renderer.compile(scene, camera);
  for (const tt of [1.2, T.lock + 0.1, T.flood + 0.3]) render(tt);
  render(0);
  if (opts.test) window.__pcaDbg = { tier: tierName, scene, tiles, composer };
  return { render, dispose, END: CONFIG.END };
}
