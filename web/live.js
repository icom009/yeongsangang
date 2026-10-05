// 실시간 합성 미리보기: 웹캠 → MediaPipe 인물 분리 → 고른 배경 위에 합성.
// 최종 사진은 서버가 더 정밀한 모델(RVM)로 다시 만든다.
const MP_VERSION = '1.0.1'; // scripts/fetch_models.py의 MP_VERSION과 같게
const MP_BASE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}`;
const MODELS = 'https://storage.googleapis.com/mediapipe-models/image_segmenter/';
// 서버에 내려받아 둔 사본(scripts/fetch_models.py). 인터넷이 없는 현장에서도 미리보기가 되도록 먼저 쓴다
const MP_LOCAL = '/static/vendor/mediapipe';
const LOCAL_MODEL = { desktop: `${MP_LOCAL}/models/selfie_multiclass_256x256.tflite`, mobile: `${MP_LOCAL}/models/selfie_segmenter.tflite` };
// PC: 정밀한 다중 분류 모델(16MB, 0번 마스크 = 배경)
// 휴대폰: 가벼운 셀피 모델(250KB, 0번 마스크 = 사람) — 발열·끊김 방지
const MODEL_DESKTOP = { url: `${MODELS}selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite`, person: false };
const MODEL_MOBILE = { url: `${MODELS}selfie_segmenter/float16/latest/selfie_segmenter.tflite`, person: true };
const IS_MOBILE = matchMedia('(pointer: coarse)').matches;
const ASPECT = 4 / 3;
const FADE_MS = 450;
const PERSON_FILTER = 'brightness(1.06) saturate(1.1) contrast(1.03)'; // 장소별 look이 없을 때

// iOS Safari는 OffscreenCanvas에 카메라 영상을 그리면 빈 화면이 될 수 있어 일반 캔버스를 쓴다
function makeCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

const smoothstep = (a, b, x) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

export class LiveStage {
  constructor(canvas) {
    this.canvas = canvas;
    if (IS_MOBILE) { // 휴대폰은 그리는 해상도를 낮춰 부담을 줄인다
      canvas.width = 960;
      canvas.height = 720;
    }
    this.ctx = canvas.getContext('2d');
    this.W = canvas.width;
    this.H = canvas.height;

    // iOS는 화면(DOM)에 붙지 않은 video의 프레임 갱신을 멈추므로, 보이지 않게 붙여 둔다
    this.video = document.createElement('video');
    this.video.setAttribute('playsinline', '');
    this.video.setAttribute('autoplay', '');
    this.video.muted = true;
    Object.assign(this.video.style, {
      position: 'fixed', left: '0', top: '0', width: '2px', height: '2px',
      opacity: '0', pointerEvents: 'none', zIndex: '-1',
    });
    document.body.appendChild(this.video);

    this.model = IS_MOBILE ? MODEL_MOBILE : MODEL_DESKTOP;
    this.segEvery = IS_MOBILE ? 66 : 33; // 인물 분리 주기(ms). 휴대폰은 초당 15회
    this.baseEvery = this.segEvery;
    this.segCost = 0;      // 인물 분리 한 번에 걸리는 시간(ms, 이동 평균)
    this.slowCount = 0;
    this.lite = IS_MOBILE; // 가벼운 모델을 쓰는 중인지
    this.swapping = false;
    this.lastSeg = 0;
    this.segFails = 0;
    this.stallSince = 0;
    this.lastVideoTime = -1;
    this.onCameraLost = null;
    this.personFilter = PERSON_FILTER;

    this.person = makeCanvas(this.W, this.H);
    this.pctx = this.person.getContext('2d');
    this.mask = null;
    this.mctx = null;
    this.prev = null;

    this.stream = null;
    this.deviceId = null;
    this.facing = 'user';
    this.mirror = true;
    this.segmenter = null;
    this.bg = null;
    this.bgPrev = null;
    this.fadeStart = 0;
    this.running = false;
    this.frozen = false;
    this.lastTime = -1;
    this.raf = 0;
  }

  /* ---------- 카메라 ---------- */
  // facing: 'user'(전면) | 'environment'(후면). 노트북처럼 방향 정보가 없으면 deviceId로 고른다.
  // 겹쳐 불러도(끊김 복구와 화면 누르기가 겹칠 때 등) 한 번만 연다. 두 번 열면 먼저 연 카메라가
  // 꺼지지 않은 채 남아 카메라와 메모리를 계속 잡는다
  startCamera(opts) {
    if (!this.opening) this.opening = this.openCamera(opts).finally(() => { this.opening = null; });
    return this.opening;
  }

  async openCamera({ deviceId = this.deviceId, facing = this.facing } = {}) {
    if (this.stream && this.stream.active) {
      this.resume();
      return;
    }
    const video = deviceId ? { deviceId: { exact: deviceId } } : { facingMode: facing || 'user' };
    Object.assign(video, { width: { ideal: 1920 }, height: { ideal: 1080 } });
    this.stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    const set = this.stream.getVideoTracks()[0]?.getSettings() || {};
    this.deviceId = deviceId ? set.deviceId || deviceId : null;
    this.facing = set.facingMode || facing || 'user';
    // 전면 카메라는 거울처럼, 후면 카메라는 보이는 그대로
    this.mirror = this.facing !== 'environment';
    this.stream.getVideoTracks().forEach((t) => {
      t.addEventListener('ended', () => this.onCameraLost?.('ended'));
    });
    this.video.srcObject = this.stream;
    await this.video.play();
    this.prev = null;
    this.stallSince = 0;
    this.resume();
  }

  stopCamera() {
    this.pause();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.video.srcObject = null;
  }

  async hasMultipleCameras() {
    try {
      const list = await navigator.mediaDevices.enumerateDevices();
      return list.filter((d) => d.kind === 'videoinput').length > 1;
    } catch {
      return false;
    }
  }

  async switchCamera() {
    // 휴대폰·태블릿: 전면 ↔ 후면
    const supportsFacing = navigator.mediaDevices.getSupportedConstraints?.().facingMode;
    const mobile = matchMedia('(pointer: coarse)').matches;
    if (supportsFacing && mobile) {
      const next = this.facing === 'environment' ? 'user' : 'environment';
      this.stopCamera();
      await this.startCamera({ deviceId: null, facing: next });
      return;
    }
    // 노트북·PC: 연결된 카메라를 차례로
    const cams = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
    if (cams.length < 2) return;
    const cur = this.deviceId || this.stream?.getVideoTracks()[0]?.getSettings().deviceId;
    const i = cams.findIndex((c) => c.deviceId === cur);
    const next = cams[(i + 1) % cams.length].deviceId;
    this.stopCamera();
    await this.startCamera({ deviceId: next });
  }

  /* ---------- 인물 분리 모델 ---------- */
  // 인물 분리 모델은 한 번만 올려 두고 방문객마다 다시 쓴다.
  // 예전엔 시작할 때마다 새로 만들고 이전 것을 닫지 않아, 방문객이 늘수록 브라우저 메모리(WASM·GPU)가
  // 쌓였다(실측: 7명 만에 크롬 전체 1.7GB -> 2.1GB, 이벤트 리스너도 한 명마다 2개씩)
  async loadSegmenter(kind = this.lite ? 'mobile' : 'desktop') {
    const model = kind === 'mobile' ? MODEL_MOBILE : MODEL_DESKTOP;
    if (this.segmenter && this.model === model) return true;
    if (this.loading) return this.loading;
    this.loading = this.createSegmenter(kind, model).finally(() => { this.loading = null; });
    return this.loading;
  }

  async createSegmenter(kind, model) {
    try {
      if (!this.vision) {  // MediaPipe 코드·WASM 파일 위치는 한 번만 준비한다
        const local = await fetch(`${MP_LOCAL}/vision_bundle.mjs`, { method: 'HEAD' }).then((r) => r.ok, () => false);
        const base = local ? MP_LOCAL : MP_BASE;
        const { FilesetResolver, ImageSegmenter } = await import(`${base}/vision_bundle.mjs`);
        const fileset = await FilesetResolver.forVisionTasks(`${base}/wasm`);
        this.vision = { ImageSegmenter, fileset, local };
      }
      const { ImageSegmenter, fileset, local } = this.vision;
      const modelUrl = local ? LOCAL_MODEL[kind] : model.url;
      const make = (delegate) => ImageSegmenter.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: modelUrl, delegate },
        runningMode: 'VIDEO',
        outputCategoryMask: false,
        outputConfidenceMasks: true,
      });
      let seg;
      try {
        seg = await make('GPU');
      } catch {
        seg = await make('CPU');
      }
      // 모델과 마스크 뜻(0번이 사람인지 배경인지)을 한꺼번에 바꾸고, 쓰던 모델은 닫아 메모리를 돌려준다
      const old = this.segmenter;
      this.model = model;
      this.segmenter = seg;
      if (old && old !== seg) {
        try { old.close(); } catch { /* 무시 */ }
      }
      return true;
    } catch (e) {
      console.warn('segmenter unavailable', e);
      this.loadError = String(e?.message || e);
      return false;
    }
  }

  /* ---------- 배경 ---------- */
  // 장소 빛 필터를 PC 미리보기에 비슷하게 입힌다 (최종 사진은 서버가 정밀하게 보정)
  setLook(css) {
    this.personFilter = css || PERSON_FILTER;
  }

  // fg: 배경 앞쪽 사물만 남긴 투명 이미지 (없으면 null)
  setBackground(img, fg = null) {
    this.fg = fg;
    if (this.bg === img) return;
    this.bgPrev = this.bg;
    this.bg = img;
    this.fadeStart = performance.now();
    if (!this.running) this.draw(performance.now());
  }

  /* ---------- 그리기 루프 ---------- */
  resume() {
    this.frozen = false;
    if (this.running) return;
    this.running = true;
    const loop = (t) => {
      if (!this.running) return;
      try {
        this.watchdog(t);
        this.draw(t);
      } catch (e) {
        console.warn(e); // 한 프레임 오류로 미리보기 전체가 멈추지 않게
      }
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  pause() {
    this.running = false;
    cancelAnimationFrame(this.raf);
  }

  // 카메라 영상이 멈추면 다시 재생하고, 그래도 안 되면 카메라를 다시 연다
  watchdog(now) {
    if (this.frozen || !this.stream) return;
    const t = this.video.currentTime;
    if (t !== this.lastVideoTime) {
      this.lastVideoTime = t;
      this.stallSince = 0;
      return;
    }
    if (!this.stallSince) {
      this.stallSince = now;
      return;
    }
    const stalled = now - this.stallSince;
    if (stalled > 1200 && this.video.paused) this.video.play().catch(() => {});
    const track = this.stream.getVideoTracks()[0];
    if (stalled > 3000 || !track || track.readyState === 'ended') {
      this.stallSince = now;
      this.onCameraLost?.('stalled');
    }
  }

  async restartCamera() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    await this.startCamera();
  }

  // 촬영 직후: 마지막 미리보기 장면을 멈춰 둔다
  freeze() {
    this.frozen = true;
  }

  crop() {
    const vw = this.video.videoWidth;
    const vh = this.video.videoHeight;
    let sw = vw;
    let sh = vh;
    if (vw / vh > ASPECT) sw = vh * ASPECT; else sh = vw / ASPECT;
    return { sx: (vw - sw) / 2, sy: (vh - sh) / 2, sw, sh, vw, vh };
  }

  drawCover(ctx, img, alpha = 1) {
    const iw = img.naturalWidth || img.width;
    const ih = img.naturalHeight || img.height;
    const s = Math.max(this.W / iw, this.H / ih);
    const w = iw * s;
    const h = ih * s;
    ctx.globalAlpha = alpha;
    ctx.drawImage(img, (this.W - w) / 2, (this.H - h) / 2, w, h);
    ctx.globalAlpha = 1;
  }

  updateMask(now) {
    if (!this.segmenter || this.video.currentTime === this.lastTime) return;
    if (now - this.lastSeg < this.segEvery) return;
    this.lastSeg = now;
    this.lastTime = this.video.currentTime;
    const t0 = performance.now();
    let result;
    try {
      result = this.segmenter.segmentForVideo(this.video, now);
    } catch (e) {
      console.warn(e);
      // 계속 실패하면(GPU 문제 등) 합성을 끄고 카메라 화면만 보여 준다
      if (++this.segFails > 20) {
        try { this.segmenter.close(); } catch { /* 이미 망가졌을 수 있다 */ }
        this.segmenter = null;
        this.mask = null;
        this.onSegmenterLost?.(String(e?.message || e));
      }
      return;
    }
    this.segFails = 0;
    const masks = result.confidenceMasks;
    if (!masks || !masks.length) return;
    const first = masks[0];
    const w = first.width;
    const h = first.height;
    const raw = first.getAsFloat32Array();
    masks.forEach((m) => m.close());
    const conf = this.blur(raw, w, h);
    if (!this.mask || this.mask.width !== w || this.mask.height !== h) {
      this.mask = makeCanvas(w, h);
      this.mctx = this.mask.getContext('2d');
      this.maskData = this.mctx.createImageData(w, h);
      this.maskData.data.fill(255);
      this.prev = null;
    }
    if (!this.prev || this.prev.length !== conf.length) this.prev = new Float32Array(conf.length);
    const d = this.maskData.data;
    const prev = this.prev;
    for (let i = 0; i < conf.length; i++) {
      // 경계를 또렷하게 + 이전 프레임과 섞어 깜빡임 줄이기
      const fg = this.model.person ? conf[i] : 1 - conf[i];
      const a = smoothstep(0.3, 0.72, fg);
      const m = prev[i] * 0.35 + a * 0.65;
      prev[i] = m;
      d[i * 4 + 3] = m * 255;
    }
    this.mctx.putImageData(this.maskData, 0, 0);
    this.adapt(performance.now() - t0);
  }

  // 느린 노트북: 인물 분리가 화면 처리를 다 잡아먹으면 카운트다운·화면 전환까지 끊긴다.
  // 걸린 시간을 재서 분리 간격을 늘리고(카메라 영상은 그대로 매끄럽고 오려 내기만 조금 늦게 따라온다),
  // 그래도 느리면 가벼운 셀피 모델로 바꾼다
  adapt(cost) {
    this.segCost = this.segCost ? this.segCost * 0.85 + cost * 0.15 : cost;
    this.segEvery = Math.min(400, Math.max(this.baseEvery, this.segCost * 2.5));
    this.slowCount = this.segCost > 45 ? this.slowCount + 1 : 0;
    if (!this.lite && !this.swapping && this.slowCount > 30) this.swapLite();
  }

  async swapLite() {
    this.swapping = true;
    this.lite = true;  // 다음 방문객부터도 가벼운 모델 그대로 (createSegmenter가 쓰던 모델을 닫는다)
    if (await this.loadSegmenter('mobile')) {
      this.segCost = 0;
      this.slowCount = 0;
      this.prev = null;
      console.info('미리보기: 이 컴퓨터가 느려 가벼운 인물 분리 모델로 바꿨어요');
    }
    this.swapping = false;
  }

  // 작은 마스크에 3x3 박스 블러: 캔버스 filter보다 훨씬 가볍고 모든 브라우저에서 같다
  blur(src, w, h) {
    if (!this.tmp || this.tmp.length !== src.length) {
      this.tmp = new Float32Array(src.length);
      this.out = new Float32Array(src.length);
    }
    const { tmp, out } = this;
    for (let y = 0; y < h; y++) {
      const r = y * w;
      for (let x = 0; x < w; x++) {
        const l = x > 0 ? x - 1 : x;
        const rr = x < w - 1 ? x + 1 : x;
        tmp[r + x] = (src[r + l] + src[r + x] + src[r + rr]) / 3;
      }
    }
    for (let y = 0; y < h; y++) {
      const u = (y > 0 ? y - 1 : y) * w;
      const c = y * w;
      const d = (y < h - 1 ? y + 1 : y) * w;
      for (let x = 0; x < w; x++) out[c + x] = (tmp[u + x] + tmp[c + x] + tmp[d + x]) / 3;
    }
    return out;
  }

  draw(now) {
    if (this.frozen) return;
    const { ctx, W, H } = this;
    const ready = this.video.readyState >= 2 && this.video.videoWidth > 0;

    // 1) 배경 (바꿀 때는 부드럽게 겹쳐 전환)
    ctx.fillStyle = '#081a1d';
    ctx.fillRect(0, 0, W, H);
    const k = Math.min(1, (now - this.fadeStart) / FADE_MS);
    if (this.bgPrev && k < 1) this.drawCover(ctx, this.bgPrev);
    if (this.bg) this.drawCover(ctx, this.bg, this.bgPrev ? k : 1);
    if (k >= 1) this.bgPrev = null;
    if (!ready) return;

    const { sx, sy, sw, sh, vw, vh } = this.crop();
    const p = this.pctx;
    p.save();
    p.clearRect(0, 0, W, H);
    if (this.mirror) p.setTransform(-1, 0, 0, 1, W, 0); // 거울처럼 좌우 반전
    p.drawImage(this.video, sx, sy, sw, sh, 0, 0, W, H);

    if (this.segmenter) {
      this.updateMask(now);
      if (this.mask) {
        const mx = this.mask.width / vw;
        const my = this.mask.height / vh;
        p.globalCompositeOperation = 'destination-in';
        p.imageSmoothingEnabled = true;
        p.imageSmoothingQuality = 'high';
        p.drawImage(this.mask, sx * mx, sy * my, sw * mx, sh * my, 0, 0, W, H);
      }
    }
    p.restore();

    // 2) 인물 (모델이 없으면 카메라 화면을 그대로)
    // PC에서는 서버의 인물 보정(뽀샤시)과 비슷한 느낌을 가볍게 미리 보여 준다. 휴대폰은 끊김 방지로 생략
    if (!this.segmenter || this.mask) {
      if (!IS_MOBILE && this.mask) ctx.filter = this.personFilter;
      ctx.drawImage(this.person, 0, 0);
      ctx.filter = 'none';
      // 3) 앞 가림 레이어: 갈대·꽃이 인물 앞에 오도록 (배경 전환 중에는 생략)
      if (this.fg && !this.bgPrev) this.drawCover(ctx, this.fg);
    }
  }

  /* ---------- 촬영 ---------- */
  // 서버에서 다시 합성할 원본(미리보기와 같은 방향, 4:3, 최대 해상도)을 JPEG으로
  capture() {
    const { sx, sy, sw, sh } = this.crop();
    const w = Math.round(Math.min(sw, 1920));
    const h = Math.round(w / ASPECT);
    if (!w || !h) return Promise.reject(new Error('camera not ready'));
    const c = makeCanvas(w, h);
    const x = c.getContext('2d');
    if (this.mirror) x.setTransform(-1, 0, 0, 1, w, 0);
    x.drawImage(this.video, sx, sy, sw, sh, 0, 0, w, h);
    return new Promise((resolve, reject) => {
      c.toBlob((b) => {
        c.width = 0;  // 최대 1920x1440 캔버스 메모리를 가비지 컬렉션을 기다리지 않고 바로 돌려준다
        c.height = 0;
        if (b) resolve(b); else reject(new Error('toBlob failed'));
      }, 'image/jpeg', 0.92);
    });
  }
}
