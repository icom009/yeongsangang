import { LiveStage } from './live.js';
import { sound } from './sound.js';

const $ = (s) => document.querySelector(s);
const IDLE_MS = 120_000;      // 아무 조작이 없으면 처음 화면으로
const TAKE_IDLE_SEC = 90;     // 받기 화면에서 처음으로 돌아가기까지
const QR_DELAY_MS = 4000;     // 완성 사진을 먼저 감상한 뒤 QR 표시

const state = {
  cfg: null,
  bg: 1,
  timer: 3,
  shotId: null,
  shotUrl: null,
  busy: false,
};

let live = null;
let idleTimer = 0;
let takeTimer = 0;

/* ---------- 공통 ---------- */
function show(name) {
  document.querySelectorAll('[data-screen]').forEach((el) => {
    el.hidden = el.dataset.screen !== name;
  });
  $('#topbar').hidden = name === 'intro';
  const order = ['shoot', 'write', 'take'];
  const cur = { studio: 'shoot', write: 'write', take: 'take' }[name];
  document.querySelectorAll('.steps li').forEach((li) => {
    const i = order.indexOf(li.dataset.step);
    li.classList.toggle('on', li.dataset.step === cur);
    li.classList.toggle('done', i < order.indexOf(cur));
  });
  window.scrollTo(0, 0);
}

let toastTimer = 0;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 4200);
}

async function api(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = '서버와 연결이 고르지 않아요. 잠시 뒤 다시 시도해 주세요.';
    try { msg = (await res.json()).detail || msg; } catch { /* 본문 없음 */ }
    throw new Error(msg);
  }
  return res.json();
}

function bgInfo(id) {
  return state.cfg.backgrounds.find((b) => b.id === id);
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

function resetIdle() {
  clearTimeout(idleTimer);
  const onIntro = !$('[data-screen="intro"]').hidden;
  const onTake = !$('[data-screen="take"]').hidden;
  if (onIntro || onTake || state.busy) return;
  idleTimer = setTimeout(goHome, IDLE_MS);
}
['pointerdown', 'keydown'].forEach((ev) => addEventListener(ev, resetIdle, { passive: true }));

/* ---------- 시작 화면 ---------- */
function startSlides() {
  const box = $('.intro-slides');
  const imgs = state.cfg.backgrounds.map((b) => {
    const img = document.createElement('img');
    img.src = `/bg/${b.id}.jpg`;
    img.alt = '';
    box.appendChild(img);
    return img;
  });
  let i = 0;
  imgs[0].classList.add('on');
  setInterval(() => {
    if ($('[data-screen="intro"]').hidden) return;
    imgs[i].classList.remove('on');
    i = (i + 1) % imgs.length;
    imgs[i].classList.add('on');
  }, 5200);
}

async function start() {
  const btn = $('#startBtn');
  const err = $('#introError');
  err.hidden = true;
  btn.disabled = true;
  btn.textContent = '카메라를 켜는 중…';
  sound.unlock();
  try {
    if (!live) live = new LiveStage($('#stageCanvas'));
    await live.startCamera();
    show('studio');
    enterStudio();
    live.loadSegmenter().then((ok) => {
      if (!ok) {
        const note = $('#stageNote');
        note.textContent = '이 기기에서는 실시간 합성 미리보기를 쓸 수 없어요. 찍으면 배경이 합성돼요.';
        note.hidden = false;
      }
    });
    $('#switchCamBtn').hidden = !(await live.hasMultipleCameras());
  } catch (e) {
    err.textContent = cameraErrorText(e);
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = '카메라 켜고 시작하기';
  }
}

function cameraErrorText(e) {
  if (!window.isSecureContext) {
    return '카메라는 https 주소나 이 컴퓨터(localhost)에서만 켤 수 있어요. 주소를 확인해 주세요.';
  }
  if (e && (e.name === 'NotAllowedError' || e.name === 'SecurityError')) {
    return '카메라 권한이 막혀 있어요. 주소창 옆 카메라 아이콘에서 허용한 뒤 다시 눌러 주세요.';
  }
  if (e && e.name === 'NotFoundError') {
    return '연결된 카메라를 찾지 못했어요. 카메라를 연결한 뒤 다시 눌러 주세요.';
  }
  return '카메라를 켜지 못했어요. 다른 프로그램이 카메라를 쓰고 있지 않은지 확인해 주세요.';
}

function goHome() {
  clearTimeout(idleTimer);
  clearInterval(takeTimer);
  const bgm = $('#bgm');
  bgm.pause();
  live?.stopCamera();
  state.shotId = null;
  state.busy = false;
  $('#msgInput').value = '';
  show('intro');
}

/* ---------- 촬영 화면 ---------- */
function buildFilmstrip() {
  const strip = $('#filmstrip');
  strip.innerHTML = '';
  state.cfg.backgrounds.forEach((b) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'film';
    btn.setAttribute('role', 'option');
    btn.dataset.id = b.id;
    btn.innerHTML = `<img src="/bg/${b.id}.jpg?w=480" alt="" loading="lazy"><span></span>`;
    btn.querySelector('span').textContent = b.name;
    btn.addEventListener('click', () => selectBg(b.id));
    strip.appendChild(btn);
  });
}

async function selectBg(id) {
  if (state.busy) return;
  state.bg = id;
  const b = bgInfo(id);
  const total = state.cfg.backgrounds.length;
  $('#sceneIndex').textContent = `배경 ${id} / ${total}`;
  $('#sceneName').textContent = b.name;
  $('#sceneNote').textContent = b.note;
  document.querySelectorAll('.film').forEach((f) => {
    f.setAttribute('aria-selected', String(Number(f.dataset.id) === id));
  });
  document.querySelector(`.film[data-id="${id}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
  try {
    live.setBackground(await loadImage(`/bg/${id}.jpg`));
  } catch {
    toast('배경 사진을 불러오지 못했어요. 다른 배경을 골라 보세요.');
  }
}

function enterStudio() {
  setReview(false);
  $('#stageHint').hidden = true;
  live.resume();
  selectBg(state.bg);
  resetIdle();
}

function setReview(on) {
  $('#shootControls').hidden = on;
  $('#reviewControls').hidden = !on;
  $('#resultImg').hidden = !on;
  $('.studio').classList.toggle('locked', on);
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function shoot() {
  if (state.busy) return;
  state.busy = true;
  clearTimeout(idleTimer);
  $('#shutterBtn').disabled = true;
  $('.studio').classList.add('locked');

  const cd = $('#countdown');
  for (let n = state.timer; n > 0; n--) {
    cd.textContent = n;
    cd.classList.remove('tick');
    void cd.offsetWidth;
    cd.classList.add('tick');
    sound.beep(n === 1 ? 1100 : 820);
    await wait(1000);
  }
  cd.textContent = '';
  cd.classList.remove('tick');

  let blob;
  try {
    blob = await live.capture();
  } catch {
    toast('사진을 찍지 못했어요. 다시 찍어 주세요.');
    return finishShoot(false);
  }
  sound.shutter();
  const flash = $('#flash');
  flash.classList.remove('go');
  void flash.offsetWidth;
  flash.classList.add('go');
  live.freeze();

  $('#busy').hidden = false;
  try {
    const fd = new FormData();
    fd.append('photo', blob, 'photo.jpg');
    fd.append('bg', state.bg);
    const res = await api('/api/shots', { method: 'POST', body: fd });
    state.shotId = res.id;
    state.shotUrl = res.shot;
    const img = await loadImage(res.shot);
    $('#resultImg').src = img.src;
    finishShoot(true);
  } catch (e) {
    toast(e.message || '합성하지 못했어요. 다시 찍어 주세요.');
    finishShoot(false);
  }
}

function finishShoot(ok) {
  $('#busy').hidden = true;
  $('#shutterBtn').disabled = false;
  state.busy = false;
  if (ok) {
    setReview(true);
    live.pause();
  } else {
    $('.studio').classList.remove('locked');
    live.resume();
  }
  resetIdle();
}

function retake() {
  state.shotId = null;
  setReview(false);
  live.resume();
}

/* ---------- 한마디 화면 ---------- */
function layoutCard() {
  const f = state.cfg.frame;
  const [W, H] = f.size;
  const [x1, y1, x2, y2] = f.hole;
  const photo = $('#cardPhoto');
  Object.assign(photo.style, {
    left: `${(x1 / W) * 100}%`, top: `${(y1 / H) * 100}%`,
    width: `${((x2 - x1) / W) * 100}%`, height: `${((y2 - y1) / H) * 100}%`,
    borderRadius: `${(f.radius / W) * 100}cqw`,
  });
  const [tx1, ty1, tx2, ty2] = f.text;
  Object.assign($('#cardText').style, {
    left: `${(tx1 / W) * 100}%`, top: `${(ty1 / H) * 100}%`,
    width: `${((tx2 - tx1) / W) * 100}%`, height: `${((ty2 - ty1) / H) * 100}%`,
    color: f.color,
  });
}

const measureCtx = document.createElement('canvas').getContext('2d');

function messageLines() {
  const lines = $('#msgInput').value.split('\n').map((s) => s.trim()).filter(Boolean).slice(0, 3);
  return lines.length ? lines : [state.cfg.defaultMessage];
}

// 서버(frame.py)와 같은 규칙으로 글자 크기를 정해 미리보기와 결과물이 같게 보이도록 한다
function fitMessage() {
  const f = state.cfg.frame;
  const [tx1, ty1, tx2, ty2] = f.text;
  const bw = tx2 - tx1;
  const bh = ty2 - ty1;
  const lines = messageLines();
  let size = 72;
  while (size > 22) {
    measureCtx.font = `${size}px 'Ownglyph PDH'`;
    const widest = Math.max(...lines.map((l) => measureCtx.measureText(l).width));
    if (widest <= bw && size * 1.25 * lines.length <= bh) break;
    size -= 2;
  }
  const el = $('#cardText');
  el.textContent = lines.join('\n');
  el.style.fontSize = `${(size / f.size[0]) * 100}cqw`;
  el.style.lineHeight = '1.25';
  const raw = $('#msgInput').value;
  $('#msgCount').textContent = `${raw.length} / 60`;
}

function enterWrite() {
  $('#cardPhoto').src = state.shotUrl;
  show('write');
  layoutCard();
  document.fonts.load("40px 'Ownglyph PDH'").finally(fitMessage);
  fitMessage();
  resetIdle();
}

function onMsgInput() {
  const t = $('#msgInput');
  const lines = t.value.split('\n');
  if (lines.length > 3) {
    t.value = lines.slice(0, 3).join('\n');
  }
  fitMessage();
}

async function finish() {
  const btn = $('#finishBtn');
  btn.disabled = true;
  btn.textContent = '만드는 중…';
  try {
    const res = await api(`/api/shots/${state.shotId}/final`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: $('#msgInput').value }),
    });
    await loadImage(res.final);
    enterTake(res);
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '완성하기';
  }
}

/* ---------- 받기 화면 ---------- */
function enterTake(res) {
  clearTimeout(idleTimer);
  $('#finalImg').src = res.final;
  const qr = $('#qrBox');
  qr.classList.remove('on');
  $('#qrImg').removeAttribute('src');
  show('take');
  live?.stopCamera();

  const bgm = $('#bgm');
  bgm.currentTime = 0;
  bgm.volume = 0.85;
  bgm.muted = $('#muteBtn').getAttribute('aria-pressed') === 'true';
  bgm.play().catch(() => {});

  setTimeout(() => {
    const img = $('#qrImg');
    img.onload = () => qr.classList.add('on');
    img.src = res.qr;
  }, QR_DELAY_MS);

  let left = TAKE_IDLE_SEC;
  const idle = $('#takeIdle');
  idle.textContent = '';
  clearInterval(takeTimer);
  takeTimer = setInterval(() => {
    left -= 1;
    if (left <= 20) idle.textContent = `${left}초 뒤 처음 화면으로 돌아가요.`;
    if (left <= 0) goHome();
  }, 1000);
}

function toggleMute() {
  const btn = $('#muteBtn');
  const muted = btn.getAttribute('aria-pressed') !== 'true';
  btn.setAttribute('aria-pressed', String(muted));
  btn.textContent = muted ? '음악 켜기' : '음악 끄기';
  $('#bgm').muted = muted;
}

/* ---------- 연결 ---------- */
function bind() {
  $('#startBtn').addEventListener('click', start);
  $('#homeBtn').addEventListener('click', goHome);
  $('#shutterBtn').addEventListener('click', shoot);
  $('#retakeBtn').addEventListener('click', retake);
  $('#useShotBtn').addEventListener('click', enterWrite);
  $('#backToShotBtn').addEventListener('click', async () => {
    show('studio');
    await live.startCamera();
    retake();
  });
  $('#finishBtn').addEventListener('click', finish);
  $('#doneBtn').addEventListener('click', goHome);
  $('#muteBtn').addEventListener('click', toggleMute);
  $('#msgInput').addEventListener('input', onMsgInput);
  $('#switchCamBtn').addEventListener('click', async () => {
    try { await live.switchCamera(); } catch { toast('카메라를 바꾸지 못했어요.'); }
  });
  document.querySelectorAll('.chip').forEach((c) => c.addEventListener('click', () => {
    $('#msgInput').value = c.textContent;
    fitMessage();
  }));
  document.querySelectorAll('.timer button').forEach((b) => b.addEventListener('click', () => {
    state.timer = Number(b.dataset.sec);
    document.querySelectorAll('.timer button').forEach((x) => {
      x.setAttribute('aria-checked', String(x === b));
    });
  }));
  // 스페이스/엔터로도 찍을 수 있게 (무선 리모컨·키보드 대응)
  addEventListener('keydown', (e) => {
    const onStudio = !$('[data-screen="studio"]').hidden;
    if (!onStudio || e.target.closest('button, textarea')) return;
    if ((e.code === 'Space' || e.code === 'Enter') && !$('#shootControls').hidden) {
      e.preventDefault();
      shoot();
    }
  });
}

async function init() {
  bind();
  try {
    state.cfg = await api('/api/config');
  } catch (e) {
    $('#introError').textContent = '서버에 연결하지 못했어요. 새로고침해 주세요.';
    $('#introError').hidden = false;
    return;
  }
  buildFilmstrip();
  startSlides();
}

init();
