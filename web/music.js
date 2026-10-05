// 배경 음악: 체험 내내 잔잔한 곡을 돌아가며 틀고, 촬영 카운트다운 동안은 낮추고,
// 완성 화면에서는 완성 음악으로 바꾼다. 곡은 Kevin MacLeod(incompetech.com), CC BY 4.0 (bgm/CREDITS.md)
const PLAYLIST = ['/bgm/carefree.mp3', '/bgm/life-of-riley.mp3', '/bgm/fretless.mp3', '/bgm/sunshine.mp3'];
const LEVEL = { ambient: 0.32, duck: 0.08, finale: 0.85 };
const KEY = 'ys-music-muted';

function fade(el, to, ms = 900) {
  // 볼륨을 부드럽게 바꾼다 (iOS Safari는 볼륨을 못 바꾸므로 그냥 넘어간다)
  clearInterval(el._fade);
  const from = el.volume;
  const t0 = performance.now();
  return new Promise((done) => {
    el._fade = setInterval(() => {
      const k = Math.min(1, (performance.now() - t0) / ms);
      try { el.volume = from + (to - from) * k; } catch { /* 무시 */ }
      if (k >= 1) { clearInterval(el._fade); done(); }
    }, 40);
  });
}

export function createMusic(ambient, finale) {
  let index = Math.floor(Math.random() * PLAYLIST.length);  // 매번 다른 곡으로 시작
  let started = false;
  let inFinale = false;
  let ducked = false;
  let muted = false;
  try { muted = localStorage.getItem(KEY) === '1'; } catch { /* 저장 안 됨 */ }
  ambient.muted = finale.muted = muted;

  const load = () => { ambient.src = PLAYLIST[index % PLAYLIST.length]; };
  ambient.addEventListener('ended', () => {  // 다음 곡으로
    index += 1;
    load();
    ambient.play().catch(() => {});
  });
  ambient.loop = false;
  finale.loop = true;
  load();

  const api = {
    // 처음 화면이 뜰 때나 첫 터치·키 입력 때 부른다 (브라우저가 소리를 막아 두므로)
    start() {
      if (started) return;
      ambient.volume = 0;
      ambient.play().then(() => {
        started = true;
        fade(ambient, inFinale ? 0 : (ducked ? LEVEL.duck : LEVEL.ambient), 1500);
      }).catch(() => {});
    },
    get started() { return started; },
    // 카운트다운·셔터 동안 잠깐 낮춘다 (삐 소리가 잘 들리게)
    duck(on) {
      ducked = on;
      if (!inFinale) fade(ambient, on ? LEVEL.duck : LEVEL.ambient, on ? 300 : 1200);
    },
    // 완성 화면: 잔잔한 곡을 줄이고 완성 음악을 처음부터. 나가면 다시 잔잔한 곡으로
    async finale(on) {
      if (on === inFinale) return;
      inFinale = on;
      if (on) {
        fade(ambient, 0, 700).then(() => { if (inFinale) ambient.pause(); });
        finale.currentTime = 0;
        finale.volume = 0;
        finale.play().then(() => fade(finale, LEVEL.finale, 700)).catch(() => {});
      } else {
        fade(finale, 0, 800).then(() => { if (!inFinale) finale.pause(); });
        if (started) {
          ambient.play().catch(() => {});
          fade(ambient, LEVEL.ambient, 1500);
        }
      }
    },
    get muted() { return muted; },
    setMuted(m) {
      muted = m;
      ambient.muted = finale.muted = m;
      try { localStorage.setItem(KEY, m ? '1' : '0'); } catch { /* 저장 안 됨 */ }
    },
  };
  return api;
}
