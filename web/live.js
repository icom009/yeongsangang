// 실시간 합성 미리보기: 웹캠 → MediaPipe 인물 분리 → 고른 배경 위에 합성.
// 최종 사진은 서버가 더 정밀한 모델(RVM)로 다시 만든다.
const MP_VERSION = '1.0.1';
const MP_BASE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}`;
const MODEL = 'https://storage.googleapis.com/mediapipe-models/image_segmenter/'
  + 'selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite';
const ASPECT = 4 / 3;
const FADE_MS = 450;

const smoothstep = (a, b, x) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

export class LiveStage {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.W = canvas.width;
    this.H = canvas.height;

    this.video = document.createElement('video');
    this.video.playsInline = true;
    this.video.muted = true;

    this.person = new OffscreenCanvas(this.W, this.H);
    this.pctx = this.person.getContext('2d');
    this.mask = null;
    this.mctx = null;
    this.prev = null;

    this.stream = null;
    this.deviceId = null;
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
  async startCamera(deviceId = this.deviceId) {
    if (this.stream && this.stream.active) {
      this.resume();
      return;
    }
    const video = deviceId
      ? { deviceId: { exact: deviceId } }
      : { facingMode: 'user' };
    Object.assign(video, { width: { ideal: 1920 }, height: { ideal: 1080 } });
    this.stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    this.deviceId = this.stream.getVideoTracks()[0]?.getSettings().deviceId || deviceId;
    this.video.srcObject = this.stream;
    await this.video.play();
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
    const cams = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
    if (cams.length < 2) return;
    const i = cams.findIndex((c) => c.deviceId === this.deviceId);
    const next = cams[(i + 1) % cams.length].deviceId;
    this.stopCamera();
    this.prev = null;
    await this.startCamera(next);
  }

  /* ---------- 인물 분리 모델 ---------- */
  async loadSegmenter() {
    try {
      const { FilesetResolver, ImageSegmenter } = await import(`${MP_BASE}/vision_bundle.mjs`);
      const fileset = await FilesetResolver.forVisionTasks(`${MP_BASE}/wasm`);
      const make = (delegate) => ImageSegmenter.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MODEL, delegate },
        runningMode: 'VIDEO',
        outputCategoryMask: false,
        outputConfidenceMasks: true,
      });
      try {
        this.segmenter = await make('GPU');
      } catch {
        this.segmenter = await make('CPU');
      }
      return true;
    } catch (e) {
      console.warn('segmenter unavailable', e);
      return false;
    }
  }

  /* ---------- 배경 ---------- */
  setBackground(img) {
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
      this.draw(t);
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  pause() {
    this.running = false;
    cancelAnimationFrame(this.raf);
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
    this.lastTime = this.video.currentTime;
    let result;
    try {
      result = this.segmenter.segmentForVideo(this.video, now);
    } catch (e) {
      console.warn(e);
      return;
    }
    const masks = result.confidenceMasks;
    if (!masks || !masks.length) return;
    const bg = masks[0];
    const w = bg.width;
    const h = bg.height;
    const conf = bg.getAsFloat32Array();
    if (!this.mask || this.mask.width !== w || this.mask.height !== h) {
      this.mask = new OffscreenCanvas(w, h);
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
      const a = smoothstep(0.3, 0.72, 1 - conf[i]);
      const m = prev[i] * 0.35 + a * 0.65;
      prev[i] = m;
      d[i * 4 + 3] = m * 255;
    }
    this.mctx.putImageData(this.maskData, 0, 0);
    masks.forEach((m) => m.close());
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
    p.setTransform(-1, 0, 0, 1, W, 0); // 거울처럼 좌우 반전
    p.drawImage(this.video, sx, sy, sw, sh, 0, 0, W, H);

    if (this.segmenter) {
      this.updateMask(now);
      if (this.mask) {
        const mx = this.mask.width / vw;
        const my = this.mask.height / vh;
        p.globalCompositeOperation = 'destination-in';
        p.imageSmoothingEnabled = true;
        p.imageSmoothingQuality = 'high';
        p.filter = 'blur(2px)';
        p.drawImage(this.mask, sx * mx, sy * my, sw * mx, sh * my, 0, 0, W, H);
        p.filter = 'none';
      }
    }
    p.restore();

    // 2) 인물 (모델이 없으면 카메라 화면을 그대로)
    if (!this.segmenter || this.mask) ctx.drawImage(this.person, 0, 0);
  }

  /* ---------- 촬영 ---------- */
  // 서버에서 다시 합성할 원본(좌우 반전, 4:3, 최대 해상도)을 JPEG으로
  capture() {
    const { sx, sy, sw, sh } = this.crop();
    const w = Math.round(Math.min(sw, 1920));
    const h = Math.round(w / ASPECT);
    const c = new OffscreenCanvas(w, h);
    const x = c.getContext('2d');
    x.setTransform(-1, 0, 0, 1, w, 0);
    x.drawImage(this.video, sx, sy, sw, sh, 0, 0, w, h);
    return c.convertToBlob({ type: 'image/jpeg', quality: 0.92 });
  }
}
