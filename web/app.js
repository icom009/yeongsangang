import { LiveStage } from './live.js';
import { sound } from './sound.js';

const $ = (s) => document.querySelector(s);
const IDLE_MS = 120_000;      // 아무 조작이 없으면 처음 화면으로
const TAKE_IDLE_SEC = 90;     // 받기 화면에서 처음으로 돌아가기까지
const QR_DELAY_MS = 4000;     // 완성 사진을 먼저 감상한 뒤 QR 표시

// 휴대폰에서 난 오류를 서버 로그로 보낸다 (현장에서 원인 확인용)
function report(kind, msg) {
  try {
    navigator.sendBeacon?.('/api/log', JSON.stringify({ kind, msg: String(msg).slice(0, 800), ua: navigator.userAgent }));
  } catch { /* 무시 */ }
}
addEventListener('error', (e) => report('error', `${e.message} @ ${e.filename}:${e.lineno}`));
addEventListener('unhandledrejection', (e) => report('rejection', e.reason?.stack || e.reason));

const state = {
  cfg: null,
  bg: 1,
  timer: 3,
  shotId: null,
  shotUrl: null,      // 필터 적용 사진
  plainUrl: null,     // 필터 없는 사진
  useFilter: true,
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
  const order = ['place', 'shoot', 'write', 'take'];
  const cur = { places: 'place', studio: 'shoot', write: 'write', take: 'take' }[name];
  document.querySelectorAll('.progress li').forEach((li) => {
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

// 두 장의 그림을 번갈아 겹쳐 부드럽게 바꾼다 (장소의 빛 배경, 장소 고르기 배경)
function crossfade(box, src) {
  const imgs = box.querySelectorAll('img');
  const cur = box.querySelector('img.on');
  if (cur && cur.dataset.src === src) return;
  const next = cur === imgs[0] ? imgs[1] : imgs[0];
  next.onload = () => {
    next.classList.add('on');
    cur?.classList.remove('on');
  };
  next.dataset.src = src;
  next.src = src;
}

// 고른 장소의 풍경을 흐리게 번져 화면 전체의 빛으로 깐다
function setAmbient(id) {
  crossfade($('#ambient'), `/bg/${id}.jpg?w=480`);
}

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
  const caption = $('#introCaption');
  const list = state.cfg.backgrounds;
  imgs[0].classList.add('on');
  caption.textContent = `${list[0].place} · ${list[0].name}`;
  setInterval(() => {
    if ($('[data-screen="intro"]').hidden) return;
    imgs[i].classList.remove('on');
    i = (i + 1) % imgs.length;
    imgs[i].classList.add('on');
    caption.textContent = `${list[i].place} · ${list[i].name}`;
  }, 5200);
}

async function start() {
  const btn = $('#startBtn');
  const err = $('#introError');
  err.hidden = true;
  btn.disabled = true;
  btn.textContent = '카메라를 켜는 중…';
  sound.unlock();
  unlockBgm();
  try {
    if (!live) {
      live = new LiveStage($('#stageCanvas'));
      live.onCameraLost = onCameraLost;
      live.onSegmenterLost = (msg) => {
        report('segmenter-lost', msg);
        const note = $('#stageNote');
        note.textContent = '실시간 합성 미리보기가 멈춰 카메라 화면만 보여 드려요. 찍으면 배경이 합성돼요.';
        note.hidden = false;
      };
    }
    await live.startCamera();
    live.pause();
    showPlaces();
    live.loadSegmenter().then((ok) => {
      if (!ok) {
        report('segmenter', 'live preview unavailable');
        const note = $('#stageNote');
        note.textContent = '이 기기에서는 실시간 합성 미리보기를 쓸 수 없어요. 찍으면 배경이 합성돼요.';
        note.hidden = false;
      }
    });
    $('#switchCamBtn').hidden = !(await live.hasMultipleCameras());
  } catch (e) {
    report('camera', `${e?.name}: ${e?.message}`);
    err.textContent = cameraErrorText(e);
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = '체험 시작하기';
  }
}

// iOS는 클릭 직후가 아니면 음악 재생을 막으므로, 시작 버튼을 누를 때 미리 한 번 재생해 둔다
function unlockBgm() {
  const bgm = $('#bgm');
  bgm.muted = true;
  bgm.play().then(() => { bgm.pause(); bgm.currentTime = 0; bgm.muted = false; })
    .catch(() => { bgm.muted = false; });
}

function cameraErrorText(e) {
  if (!window.isSecureContext) {
    return '이 주소에서는 카메라를 켤 수 없어요. 휴대폰에서는 https로 시작하는 주소로 접속해 주세요.';
  }
  if (e && (e.name === 'NotAllowedError' || e.name === 'SecurityError')) {
    return '카메라 권한이 막혀 있어요. 주소창 옆 카메라 아이콘에서 허용한 뒤 다시 눌러 주세요.';
  }
  if (e && e.name === 'NotFoundError') {
    return '연결된 카메라를 찾지 못했어요. 카메라를 연결한 뒤 다시 눌러 주세요.';
  }
  return '카메라를 켜지 못했어요. 다른 프로그램이 카메라를 쓰고 있지 않은지 확인해 주세요.';
}

// 카메라가 끊기면(전화·알림·화면 꺼짐 등) 자동으로 다시 연다
let restarting = false;
async function onCameraLost(reason) {
  if (restarting || $('[data-screen="studio"]').hidden || state.busy) return;
  restarting = true;
  report('camera-lost', reason);
  try {
    await live.restartCamera();
  } catch (e) {
    report('camera-restart', `${e?.name}: ${e?.message}`);
    toast('카메라가 끊겼어요. 화면을 한 번 눌러 주세요.');
    addEventListener('pointerdown', () => live.restartCamera().catch(() => {}), { once: true });
  } finally {
    restarting = false;
  }
}

document.addEventListener('visibilitychange', () => {
  if (!live) return;
  const onStudio = !$('[data-screen="studio"]').hidden;
  if (document.hidden) {
    live.pause();
  } else if (onStudio && $('#reviewControls').hidden) {
    if (live.stream?.active) live.resume(); else onCameraLost('visible');
  }
});

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

/* ---------- 장소 고르기 ---------- */
let focused = 1; // 장소 고르기 화면에서 지금 펼쳐진 장소

function buildPlaces() {
  const deck = $('#placeGrid');
  deck.innerHTML = '';
  const wide = Math.max(innerWidth, innerHeight) > 900;
  state.cfg.backgrounds.forEach((b) => {
    const item = document.createElement('div');
    item.className = 'deck-item';
    item.setAttribute('role', 'option');
    item.tabIndex = 0;
    item.dataset.id = b.id;
    item.innerHTML = `
      <img alt="" decoding="async">
      <span class="deck-label"></span>
      <div class="deck-info">
        <p class="deck-where"></p>
        <h2 class="deck-name"></h2>
        <p class="deck-story"></p>
        <span class="btn btn-sail btn-lg deck-go">이곳에서 찍기</span>
      </div>`;
    item.querySelector('img').src = `/bg/${b.id}.jpg?w=${wide ? 1600 : 800}`;
    item.querySelector('.deck-label').textContent = b.name;
    item.querySelector('.deck-where').textContent = b.place;
    item.querySelector('.deck-name').textContent = b.name;
    item.querySelector('.deck-story').textContent = b.story;
    // 접힌 장소를 누르면 펼치고, 펼쳐진 장소를 누르면 바로 촬영으로
    item.addEventListener('click', () => (focused === b.id ? pickPlace(b.id) : focusPlace(b.id)));
    item.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        item.click();
      }
    });
    deck.appendChild(item);
  });
}

function focusPlace(id) {
  focused = id;
  setAmbient(id);
  document.querySelectorAll('.deck-item').forEach((el) => {
    const on = Number(el.dataset.id) === id;
    el.classList.toggle('open', on);
    el.setAttribute('aria-selected', String(on));
  });
}

function stepFocus(delta) {
  const list = state.cfg.backgrounds;
  const i = list.findIndex((b) => b.id === focused);
  focusPlace(list[(i + delta + list.length) % list.length].id);
}

function showPlaces() {
  if (state.busy) return;
  live?.pause();
  show('places');
  focusPlace(state.bg);
  resetIdle();
}

async function pickPlace(id) {
  state.bg = id;
  show('studio');
  // 장소를 고르는 동안 카메라가 끊겼으면(화면 꺼짐 등) 다시 연다
  if (!live.stream?.active) {
    try {
      await live.restartCamera();
    } catch (e) {
      report('camera-restart', `${e?.name}: ${e?.message}`);
      toast('카메라가 끊겼어요. 화면을 한 번 눌러 주세요.');
    }
  }
  enterStudio();
}

/* ---------- 촬영 화면 ---------- */
function stepBg(delta) {
  const list = state.cfg.backgrounds;
  const i = list.findIndex((b) => b.id === state.bg);
  selectBg(list[(i + delta + list.length) % list.length].id);
}

async function selectBg(id) {
  if (state.busy) return;
  state.bg = id;
  const b = bgInfo(id);
  const list = state.cfg.backgrounds;
  $('#sceneTag').textContent = b.place;
  $('#sceneName').textContent = b.name;
  $('#sceneStory').textContent = b.story;
  $('#sceneCount').textContent = `${list.indexOf(b) + 1} / ${list.length}`;
  $('#capTag').textContent = b.place;
  $('#capName').textContent = b.name;
  setAmbient(id);
  live.setLook(b.look?.preview);
  const cap = $('#stageCaption');
  cap.classList.remove('show');
  void cap.offsetWidth;
  cap.classList.add('show');
  try {
    live.setBackground(await loadImage(`/bg/${id}.jpg`));
  } catch {
    toast('배경 사진을 불러오지 못했어요. 다른 장소를 골라 보세요.');
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
  } catch (e) {
    report('capture', e?.message || e);
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
  $('#busyText').textContent = '풍경 속에 자연스럽게 담는 중이에요';
  // 여러 부스에서 한꺼번에 찍으면 서버가 차례로 만든다. 오래 걸리면 기다리는 이유를 알려 준다
  const slow = setTimeout(() => { $('#busyText').textContent = '찍는 분들이 많아 조금 더 걸려요. 곧 완성돼요'; }, 5000);
  try {
    const fd = new FormData();
    fd.append('photo', blob, 'photo.jpg');
    fd.append('bg', state.bg);
    const res = await api('/api/shots', { method: 'POST', body: fd });
    state.shotId = res.id;
    state.shotUrl = res.shot;
    state.plainUrl = res.plain;
    $('#lookName').textContent = res.look;
    const img = await loadImage(res.shot);
    loadImage(res.plain).catch(() => {}); // 원본도 미리 받아 두어 바로 바꿔 보이게
    setFilter(true);
    // 사람을 못 찾으면 찍은 그대로 담고 계속 진행한다 (필터·원본이 같으므로 고르기는 숨김)
    $('.look-toggle').hidden = !res.person;
    if (!res.person) {
      report('no-person', `bg ${state.bg}`);
      $('#lookHint').textContent = '사람을 찾지 못해 배경 합성 없이 찍은 그대로 담았어요. 다시 찍어도 좋아요.';
    }
    $('#resultImg').src = img.src;
    finishShoot(true);
  } catch (e) {
    report('compose', e?.message || e);
    toast(e.message || '합성하지 못했어요. 다시 찍어 주세요.');
    finishShoot(false);
  } finally {
    clearTimeout(slow);
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

function setFilter(on) {
  state.useFilter = on;
  document.querySelectorAll('.look-toggle button').forEach((b) => {
    b.setAttribute('aria-checked', String((b.dataset.filter === '1') === on));
  });
  $('#lookHint').textContent = on
    ? '그곳의 햇살과 빛 색에 맞춰 자동으로 보정했어요.'
    : '보정 없이 배경만 합성한 사진이에요.';
  if (state.shotId) $('#resultImg').src = on ? state.shotUrl : state.plainUrl;
}

function chosenShot() {
  return state.useFilter ? state.shotUrl : state.plainUrl;
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
  $('#cardPhoto').src = chosenShot();
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
      body: JSON.stringify({ message: $('#msgInput').value, filter: state.useFilter }),
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
  $('#prevPlace').addEventListener('click', () => stepBg(-1));
  $('#nextPlace').addEventListener('click', () => stepBg(1));
  $('#morePlacesBtn').addEventListener('click', showPlaces);
  addEventListener('keydown', (e) => {
    if ($('[data-screen="places"]').hidden) return;
    const prev = e.code === 'ArrowLeft' || e.code === 'ArrowUp';
    const next = e.code === 'ArrowRight' || e.code === 'ArrowDown';
    if (prev || next) {
      e.preventDefault();
      stepFocus(prev ? -1 : 1);
      document.querySelector(`.deck-item[data-id="${focused}"]`)?.focus({ preventScroll: true });
    }
  });
  document.querySelectorAll('.look-toggle button').forEach((b) => b.addEventListener('click', () => {
    setFilter(b.dataset.filter === '1');
  }));
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
    if ($('#shootControls').hidden || state.busy) return;
    if (e.code === 'Space' || e.code === 'Enter') {
      e.preventDefault();
      shoot();
    } else if (e.code === 'ArrowLeft' || e.code === 'ArrowRight') {
      e.preventDefault();
      stepBg(e.code === 'ArrowLeft' ? -1 : 1);
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
  buildPlaces();
  startSlides();
}

init();
