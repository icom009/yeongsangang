import { LiveStage } from './live.js';
import { sound } from './sound.js';
import { createMusic } from './music.js';

const $ = (s) => document.querySelector(s);
const IDLE_MS = 120_000;      // 아무 조작이 없으면 처음 화면으로
const TAKE_IDLE_SEC = 90;     // 받기 화면에서 처음으로 돌아가기까지
// 마우스·터치패드로 쓰는 기기(노트북 부스)인지. 찍기를 누르고 뒤로 물러날 시간이 더 필요하다
const FINE_POINTER = matchMedia('(pointer: fine)').matches;
const QR_DELAY_MS = 500;      // 완성 사진이 뜬 뒤 QR이 따라 올라오는 짧은 틈

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
  timer: FINE_POINTER ? 5 : 3,  // 노트북은 누르고 1~1.5m 물러날 시간이 필요해서 5초
  shotId: null,
  shotUrl: null,      // 필터 적용 사진
  plainUrl: null,     // 필터 없는 사진
  useFilter: true,
  busy: false,
  mode: 1,            // 1컷 또는 4컷(인생네컷)
  picks: [],          // 4컷에서 고른 장소 네 곳
  cuts: [],           // 찍은 사진들 {id, shot, plain, person}
  cutIndex: 0,        // 지금 몇 번째 컷인지
  jobs: [],           // 네 컷: 찍자마자 서버로 보낸 합성 작업들 (한 장씩 찍는 동안 뒤에서 돈다)
};

let live = null;
let music = null;  // 배경 음악 (init에서 만든다)
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
  music.start();  // 화면을 누른 순간이라 브라우저가 소리를 허락한다
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
    retryOnTap();
  } finally {
    restarting = false;
  }
}

// 화면을 누르면 카메라를 다시 연다. 여러 번 끊겨도 기다리는 것은 하나만 둔다
// (쌓이면 리스너가 늘고, 한 번 누를 때 카메라를 여러 번 열게 된다)
let tapRetry = false;
function retryOnTap() {
  if (tapRetry) return;
  tapRetry = true;
  addEventListener('pointerdown', () => {
    tapRetry = false;
    live.restartCamera().catch(() => {});
  }, { once: true });
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
  if ($('#cutGuide').open) $('#cutGuide').close();
  music.finale(false);  // 완성 음악에서 다시 잔잔한 곡으로
  live?.stopCamera();
  // 지난 방문객 사진을 숨은 화면에서도 내려 메모리를 돌려준다 (다음 사람에게 남아 있지 않게)
  ['#resultImg', '#cardPhoto', '#finalImg', '#qrImg'].forEach((s) => $(s).removeAttribute('src'));
  dropGrids();
  state.shotId = null;
  state.shotUrl = state.plainUrl = null;
  state.busy = false;
  state.cuts = [];
  state.cutIndex = 0;
  state.jobs = [];
  setMode(1);
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
      <span class="deck-pick" aria-hidden="true"></span>
      <span class="deck-label"></span>
      <div class="deck-info">
        <p class="deck-where"></p>
        <h2 class="deck-name"></h2>
        <p class="deck-story"></p>
        <span class="btn btn-sail btn-lg deck-go">이곳에서 찍기</span>
      </div>`;
    // 넓은 화면은 처음 화면·촬영 화면과 같은 주소(1600px)를 써서 같은 사진을 두 번 받아 두 벌 들고 있지 않게
    item.querySelector('img').src = wide ? `/bg/${b.id}.jpg` : `/bg/${b.id}.jpg?w=800`;
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

/* 한 컷 / 네 컷(인생네컷). 네 컷이면 장소를 네 곳 담은 뒤 차례로 찍는다 */
function setMode(m) {
  state.mode = m;
  state.picks = [];
  document.querySelectorAll('#modeSwitch button').forEach((b) => {
    b.setAttribute('aria-pressed', String(Number(b.dataset.mode) === m));
  });
  syncPicks();
}

function syncPicks() {
  const four = state.mode === 4;
  $('#placeGrid').classList.toggle('picking', four);
  $('#randomBtn').hidden = !four;
  $('#placesHint').textContent = four
    ? `서로 다른 네 곳을 골라 주세요 (${state.picks.length} / 4)`
    : '사진을 누르면 그 장소가 펼쳐져요';
  document.querySelectorAll('.deck-item').forEach((el) => {
    const i = state.picks.indexOf(Number(el.dataset.id));
    const badge = el.querySelector('.deck-pick');
    badge.classList.toggle('on', i >= 0);
    badge.textContent = i >= 0 ? String(i + 1) : '';
    el.querySelector('.deck-go').textContent = four
      ? `여기 담기 ${Math.min(state.picks.length + 1, 4)} / 4`
      : '이곳에서 찍기';
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
  if (state.mode === 4) {
    if (state.picks.length >= 4) return;
    state.picks.push(id);
    syncPicks();
    if (state.picks.length < 4) {
      stepFocus(1);  // 다음 장소를 펼쳐 준다
      return;
    }
    askFourCuts();  // 찍히는 방식을 먼저 알려 주고 확인받는다
    return;
  }
  state.picks = [id];
  startShooting();
}

function askFourCuts() {
  // 고른 곳을 장소마다 따로 (이름 중간에서 줄이 바뀌지 않게)
  const box = $('#guidePlaces');
  box.replaceChildren(...state.picks.map((id, i) => {
    const chip = document.createElement('span');
    const num = document.createElement('b');
    num.textContent = String(i + 1);
    chip.append(num, bgInfo(id)?.name || '');
    return chip;
  }));
  $('#cutGuide').showModal();
  resetIdle();
}

async function startShooting() {
  state.bg = state.picks[0];
  state.cutIndex = 0;
  state.cuts = [];
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

async function selectBg(id, force = false) {
  if (state.busy && !force) return;
  state.bg = id;
  if (state.mode === 4 && state.picks.length === 4) state.picks[state.cutIndex] = id;
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
    // 앞 가림 레이어(갈대·꽃 등)가 있는 장소는 인물 위에 함께 얹는다 (서버 합성과 같게)
    const [bg, fg] = await Promise.all([
      loadImage(`/bg/${id}.jpg`),
      b.fg ? loadImage(`/bg/${id}_fg.webp`).catch(() => null) : null,
    ]);
    live.setBackground(bg, fg);
  } catch {
    toast('배경 사진을 불러오지 못했어요. 다른 장소를 골라 보세요.');
  }
}

function enterStudio() {
  if (state.mode === 4) preloadPicks();  // 장소가 바로 바뀌도록
  setReview(false);
  $('#stageHint').hidden = true;
  showCutBadge();
  live.resume();
  selectBg(state.bg);
  resetIdle();
}

function showCutBadge(waiting = false) {
  const el = $('#cutBadge');
  el.hidden = state.mode !== 4;
  if (state.mode !== 4) return;
  el.textContent = waiting
    ? `${state.cutIndex + 1} / 4번째 · 포즈를 바꾸고 준비되면 찍기를 눌러 주세요`
    : `${state.cutIndex + 1} / 4번째 사진`;
  el.classList.toggle('waiting', waiting);
}

function setReview(on) {
  $('#shootControls').hidden = on;
  $('#reviewControls').hidden = !on;
  $('#resultImg').hidden = !on;
  $('.studio').classList.toggle('locked', on);
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// 합성을 기다리는 동안 돌아가며 보여 줄 문구 (화면이 멈춘 것처럼 보이지 않게)
const BUSY_TIPS = [
  '사람과 풍경 사이 경계를 다듬고 있어요',
  '영산강의 빛을 얼굴에 입히는 중이에요',
  '머리카락 한 올까지 살피는 중이에요',
  '갈대와 꽃을 앞쪽에 다시 심는 중이에요',
  '그곳의 노을빛·햇살 색을 맞추는 중이에요',
  '사진 속 주인공이 돋보이게 다듬는 중이에요',
  '거의 다 됐어요',
];

function rotateTips(el, tips, ms = 2400) {
  let i = 0;
  const showTip = () => {
    el.classList.remove('tip-in');
    void el.offsetWidth;
    el.textContent = tips[i % tips.length];
    el.classList.add('tip-in');
    i += 1;
  };
  showTip();
  const timer = setInterval(showTip, ms);
  return () => { clearInterval(timer); el.textContent = ''; };
}

async function countdown(sec) {
  const cd = $('#countdown');
  music.duck(true);  // 삐 소리가 잘 들리게 음악을 잠깐 낮춘다 (셔터 소리 뒤 되돌린다)
  setTimeout(() => music.duck(false), sec * 1000 + 900);
  for (let n = sec; n > 0; n--) {
    cd.textContent = n;
    cd.classList.remove('tick');
    void cd.offsetWidth;
    cd.classList.add('tick');
    $('#lookUp').hidden = n > 1;  // 마지막 1초: 화면 위 카메라를 보게 (눈이 아래로 깔리지 않게)
    sound.beep(n === 1 ? 1100 : 820);
    await wait(1000);
  }
  cd.textContent = '';
  cd.classList.remove('tick');
  $('#lookUp').hidden = true;
}

function snapEffect() {
  sound.shutter();
  const flash = $('#flash');
  flash.classList.remove('go');
  void flash.offsetWidth;
  flash.classList.add('go');
}

function sendShot(blob, bg) {
  const fd = new FormData();
  fd.append('photo', blob, 'photo.jpg');
  fd.append('bg', bg);
  return api('/api/shots', { method: 'POST', body: fd });
}

// 네 컷에서 장소가 바로 바뀌도록 미리 받아 둔다
function preloadPicks() {
  state.picks.forEach((id) => {
    const b = bgInfo(id);
    loadImage(`/bg/${id}.jpg`).catch(() => {});
    if (b?.fg) loadImage(`/bg/${id}_fg.webp`).catch(() => {});
  });
}

// 한 컷이면 찍고 바로 합성, 네 컷이면 한 장 찍을 때마다 '찍기'를 다시 눌러야 다음으로 넘어간다
// (자동으로 넘어가면 포즈 잡을 틈이 없다는 현장 의견). 합성은 찍자마자 뒤에서 돌려 마지막에 덜 기다린다
async function shoot() {
  if (state.busy) return;
  state.busy = true;
  clearTimeout(idleTimer);
  $('#shutterBtn').disabled = true;
  $('.studio').classList.add('locked');

  const four = state.mode === 4;
  if (!four || state.cutIndex === 0) state.jobs = [];
  if (four) showCutBadge();
  await countdown(state.timer);
  let blob;
  try {
    blob = await live.capture();
  } catch (e) {
    report('capture', e?.message || e);
    toast('사진을 찍지 못했어요. 다시 찍어 주세요.');
    return finishShoot(false);
  }
  snapEffect();
  state.jobs.push(sendShot(blob, four ? state.picks[state.cutIndex] : state.bg));

  if (four && state.jobs.length < 4) {
    // 다음 장소로 바꾸고, 포즈를 잡은 방문객이 다시 '찍기'를 누를 때까지 기다린다
    state.cutIndex = state.jobs.length;
    state.busy = false;
    await selectBg(state.picks[state.cutIndex]);
    showCutBadge(true);
    $('#shutterBtn').disabled = false;
    $('.studio').classList.remove('locked');
    resetIdle();
    return;
  }
  live.freeze();

  const jobs = state.jobs;
  $('#busy').hidden = false;
  $('#busyText').textContent = four
    ? '네 컷을 풍경 속에 담는 중이에요. 잠시만 기다려 주세요'
    : '풍경 속에 자연스럽게 담는 중이에요';
  if (four) {  // 몇 장까지 됐는지 보여 준다 (먼저 찍은 컷은 이미 끝나 있다)
    let done = 0;
    jobs.forEach((j) => j.then(() => {
      done += 1;
      if (!$('#busy').hidden) $('#busyText').textContent = `${done} / 4장 담았어요. 잠시만 기다려 주세요`;
    }).catch(() => {}));
  }
  const stopTips = rotateTips($('#busyTip'), BUSY_TIPS);
  // 여러 부스에서 한꺼번에 찍으면 서버가 차례로 만든다. 오래 걸리면 기다리는 이유를 알려 준다
  const slow = setTimeout(() => { $('#busyText').textContent = '찍는 분들이 많아 조금 더 걸려요. 곧 완성돼요'; }, 5000);
  try {
    const results = await Promise.all(jobs);
    state.cuts = results.map((r) => ({ id: r.id, shot: r.shot, plain: r.plain, person: r.person }));
    $('#lookName').textContent = results[0].look;
    results.forEach((r, i) => {
      if (!r.person) report('no-person', `bg ${four ? state.picks[i] : state.bg}`);
    });
    await showResult();
    finishShoot(true);
  } catch (e) {
    report('compose', e?.message || e);
    toast(e.message || '합성하지 못했어요. 다시 찍어 주세요.');
    if (four) {  // 네 컷 중 하나라도 못 만들면 첫 컷부터 다시
      state.cutIndex = 0;
      state.jobs = [];
      await selectBg(state.picks[0], true);
      showCutBadge();
    }
    finishShoot(false);
  } finally {
    clearTimeout(slow);
    stopTips();
  }
}

// 네 컷 미리보기 그림(blob: 주소). 다시 찍거나 방문객이 바뀔 때 돌려준다 (안 그러면 한 명마다 몇 MB씩 남는다)
let gridUrls = [];
function dropGrids() {
  gridUrls.forEach((u) => URL.revokeObjectURL(u));
  gridUrls = [];
}

// 서버(frame.grid)와 같은 배치로 네 컷 미리보기를 만든다
async function makeGrid(srcs) {
  const imgs = await Promise.all(srcs.map(loadImage));
  const W = 1600;
  const H = 1200;
  const g = 10;
  const cv = document.createElement('canvas');
  cv.width = W;
  cv.height = H;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = '#f4fcff';
  ctx.fillRect(0, 0, W, H);
  const cw = (W - g) >> 1;
  const ch = (H - g) >> 1;
  imgs.forEach((im, i) => {
    const s = Math.max(cw / im.width, ch / im.height);
    const w = im.width * s;
    const h = im.height * s;
    ctx.save();
    ctx.beginPath();
    ctx.rect((i % 2) * (cw + g), ((i / 2) | 0) * (ch + g), cw, ch);
    ctx.clip();
    ctx.drawImage(im, (i % 2) * (cw + g) + (cw - w) / 2, ((i / 2) | 0) * (ch + g) + (ch - h) / 2, w, h);
    ctx.restore();
  });
  // toDataURL은 화면을 잠깐 멈추고 긴 글자열을 남긴다. JPEG은 뒤에서 만들고, 큰 캔버스 메모리는 바로 돌려준다
  const blob = await new Promise((resolve, reject) => {
    cv.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/jpeg', 0.92);
  });
  cv.width = 0;
  cv.height = 0;
  const url = URL.createObjectURL(blob);
  gridUrls.push(url);
  return url;
}

async function showResult() {
  dropGrids();
  state.shotId = state.cuts[0].id;
  if (state.mode === 4) {
    $('#busyText').textContent = '네 컷을 한 장으로 모으는 중이에요';
    [state.shotUrl, state.plainUrl] = await Promise.all([
      makeGrid(state.cuts.map((c) => c.shot)),
      makeGrid(state.cuts.map((c) => c.plain)),
    ]);
    $('#cutBadge').hidden = true;
  } else {
    state.shotUrl = state.cuts[0].shot;
    state.plainUrl = state.cuts[0].plain;
    loadImage(state.plainUrl).catch(() => {});  // 원본도 미리 받아 두어 바로 바꿔 보이게
  }
  setFilter(true);
  // 사람을 못 찾으면 찍은 그대로 담고 계속 진행한다 (필터·원본이 같으므로 고르기는 숨김)
  const person = state.cuts.some((c) => c.person);
  $('.look-toggle').hidden = !person;
  if (!person) {
    $('#lookHint').textContent = '사람을 찾지 못해 배경 합성 없이 찍은 그대로 담았어요. 다시 찍어도 좋아요.';
  }
  $('#resultImg').src = (await loadImage(state.shotUrl)).src;
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
  state.shotUrl = state.plainUrl = null;
  dropGrids();
  state.cuts = [];
  state.cutIndex = 0;
  state.jobs = [];
  setReview(false);
  showCutBadge();
  if (state.mode === 4) selectBg(state.picks[0], true);
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
  // 노트북·PC는 바로 칠 수 있게 입력칸에 커서를 둔다 (휴대폰은 자판이 갑자기 올라오지 않게 그대로)
  if (FINE_POINTER) setTimeout(() => $('#msgInput').focus({ preventScroll: true }), 300);
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
      body: JSON.stringify({
        message: $('#msgInput').value,
        filter: state.useFilter,
        cuts: state.cuts.slice(1).map((c) => c.id),  // 네 컷이면 나머지 세 장
      }),
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

  music.finale(true);  // 잔잔한 곡을 줄이고 완성 음악으로

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
  music.setMuted(!music.muted);
  syncMuteButtons();
}

// 받기 화면의 '음악 끄기'와 왼쪽 아래 단추가 같은 상태를 보이게
function syncMuteButtons() {
  const m = music.muted;
  $('#muteBtn').setAttribute('aria-pressed', String(m));
  $('#muteBtn').textContent = m ? '음악 켜기' : '음악 끄기';
  $('#musicBtn').setAttribute('aria-pressed', String(m));
  $('#musicBtn').setAttribute('aria-label', m ? '배경 음악 켜기' : '배경 음악 끄기');
}

/* ---------- 연결 ---------- */
function bind() {
  $('#startBtn').addEventListener('click', () => { keepAwake(); start(); });
  $('#homeBtn').addEventListener('click', goHome);
  $('#shutterBtn').addEventListener('click', shoot);
  $('#retakeBtn').addEventListener('click', retake);
  document.querySelectorAll('#modeSwitch button').forEach((b) => {
    b.addEventListener('click', () => setMode(Number(b.dataset.mode)));
  });
  $('#guideGo').addEventListener('click', () => { $('#cutGuide').close(); startShooting(); });
  $('#guideCancel').addEventListener('click', () => {
    $('#cutGuide').close();
    state.picks = [];
    syncPicks();
  });
  // Esc로 닫으면 고른 네 곳이 그대로 남아 장소를 더 담을 수 없게 되므로 '장소 다시 고르기'와 같게 처리한다
  $('#cutGuide').addEventListener('cancel', (e) => {
    e.preventDefault();
    $('#guideCancel').click();
  });
  $('#randomBtn').addEventListener('click', () => {
    const pool = state.cfg.backgrounds.map((b) => b.id);
    state.picks = [];
    while (state.picks.length < 4 && pool.length) {
      state.picks.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
    }
    syncPicks();
    askFourCuts();
  });
  $('#useShotBtn').addEventListener('click', enterWrite);
  $('#backToShotBtn').addEventListener('click', async () => {
    show('studio');
    await live.startCamera();
    retake();
  });
  $('#finishBtn').addEventListener('click', finish);
  $('#doneBtn').addEventListener('click', goHome);
  $('#muteBtn').addEventListener('click', toggleMute);
  $('#musicBtn').addEventListener('click', toggleMute);
  $('#msgInput').addEventListener('input', onMsgInput);
  $('#prevPlace').addEventListener('click', () => stepBg(-1));
  $('#nextPlace').addEventListener('click', () => stepBg(1));
  $('#morePlacesBtn').addEventListener('click', showPlaces);
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
  addEventListener('keydown', onKey);
  // 키오스크: 마우스를 3초 동안 안 움직이면 포인터를 숨긴다 (부스 화면이 깔끔하게)
  let cursorTimer = 0;
  addEventListener('pointermove', (e) => {
    if (e.pointerType !== 'mouse') return;
    document.body.classList.remove('no-cursor');
    clearTimeout(cursorTimer);
    cursorTimer = setTimeout(() => document.body.classList.add('no-cursor'), 3000);
  }, { passive: true });
  // 화면이 꺼지지 않게 (노트북 절전 설정을 미처 못 바꿨을 때도)
  document.addEventListener('visibilitychange', () => { if (!document.hidden) keepAwake(); });
}

let wakeLock = null;
async function keepAwake() {
  try {
    if (!('wakeLock' in navigator) || wakeLock) return;
    wakeLock = await navigator.wakeLock.request('screen');
    wakeLock.addEventListener('release', () => { wakeLock = null; });
  } catch { /* 지원하지 않거나 허용되지 않음 */ }
}

// 키보드·발표용 무선 리모컨으로 처음부터 끝까지 (노트북 부스)
//   다음·확인: Enter, Space, PageDown   뒤로: PageUp, Backspace   장소 고르기: ← →
function onKey(e) {
  if (e.repeat) return;
  const next = ['Enter', 'NumpadEnter', 'Space', 'PageDown'].includes(e.code);
  const back = e.code === 'PageUp' || e.code === 'Backspace';
  const left = e.code === 'ArrowLeft' || e.code === 'ArrowUp';
  const right = e.code === 'ArrowRight' || e.code === 'ArrowDown';
  const act = (fn) => { e.preventDefault(); fn(); };
  if ($('#cutGuide').open) {
    // 리모컨 '다음'(PageDown)은 초점이 어디 있든 시작. Enter·Space는 초점이 있는 단추가 처리한다(처음 초점은 '시작')
    if (e.code === 'PageDown' || (next && !e.target.closest('button'))) act(() => $('#guideGo').click());
    else if (back) act(() => $('#guideCancel').click());
    return;
  }
  const scr = document.querySelector('[data-screen]:not([hidden])')?.dataset.screen;
  if (e.target.closest('textarea, input')) {
    // 한마디를 쓰는 중: Ctrl+Enter나 리모컨 PageDown이면 완성
    if (scr === 'write' && ((e.code === 'Enter' && (e.ctrlKey || e.metaKey)) || e.code === 'PageDown')) {
      act(() => $('#finishBtn').click());
    }
    return;
  }
  if (e.code === 'KeyM') return act(toggleMute);  // 운영자용: 배경 음악 끄기·켜기
  // 단추나 장소 카드에 초점이 있으면 Enter·Space는 그쪽이 처리한다 (두 번 눌리지 않게)
  if ((e.code === 'Enter' || e.code === 'Space') && e.target.closest('button, a, .deck-item')) return;
  if (scr === 'intro') {
    if (next) act(() => $('#startBtn').click());
  } else if (scr === 'places') {
    if (left || right) {
      act(() => stepFocus(left ? -1 : 1));
      document.querySelector(`.deck-item[data-id="${focused}"]`)?.focus({ preventScroll: true });
    } else if (next) act(() => pickPlace(focused));
    else if (back) act(goHome);
  } else if (scr === 'studio') {
    if (!$('#reviewControls').hidden) {  // 찍은 사진 확인
      if (next) act(() => $('#useShotBtn').click());
      else if (back) act(() => $('#retakeBtn').click());
    } else if (!state.busy) {
      if (next) act(shoot);
      else if (left || right) act(() => stepBg(left ? -1 : 1));
      else if (back) act(showPlaces);
    }
  } else if (scr === 'write') {
    if (next) act(() => $('#finishBtn').click());
    else if (back) act(() => $('#backToShotBtn').click());
  } else if (scr === 'take') {
    if (next) act(() => $('#doneBtn').click());
  }
}

async function init() {
  music = createMusic($('#music'), $('#bgm'));
  bind();
  syncMuteButtons();
  // 처음 화면부터 음악이 흐르게. 키오스크 크롬은 바로 되고, 보통 브라우저는 첫 터치·키 입력 때 시작한다
  music.start();
  const kick = () => {
    music.start();
    if (music.started) ['pointerdown', 'keydown'].forEach((ev) => removeEventListener(ev, kick));
  };
  ['pointerdown', 'keydown'].forEach((ev) => addEventListener(ev, kick, { passive: true }));
  try {
    state.cfg = await api('/api/config');
  } catch (e) {
    $('#introError').textContent = '서버에 연결하지 못했어요. 새로고침해 주세요.';
    $('#introError').hidden = false;
    return;
  }
  buildPlaces();
  // AI 빛 보정은 환경에 따라 켜진다. 외부 서비스를 쓰는 경우에는 처음 화면에 안내를 띄운다
  const ai = state.cfg.ai || {};
  const fx = state.cfg.effects || [];
  $('#aiHint').hidden = !ai.on;
  $('#aiNotice').hidden = !ai.external;
  if (fx.length) {
    $('#aiNotice').textContent = '휴대폰에서 AI 효과를 누르면 그 사진이 외부 AI 서비스(OpenAI)로 전송돼요.';
    // 받기 화면 사진 위에 '휴대폰에서 AI로 바꿔 보기' 안내를 얹는다 (만드는 건 휴대폰에서)
    const box = $('#fxOverlay');
    const head = document.createElement('b');
    head.textContent = '✨ QR로 받은 뒤 휴대폰에서 AI로 바꿔 보세요';
    const list = document.createElement('span');
    list.textContent = fx.map((e) => e.name).join(' · ');
    box.replaceChildren(head, list);
    box.hidden = false;
  }
  setMode(1);
  document.querySelectorAll('.timer button').forEach((b) => {  // 기본 타이머 표시를 맞춘다
    b.setAttribute('aria-checked', String(Number(b.dataset.sec) === state.timer));
  });
  startSlides();
}

init();
