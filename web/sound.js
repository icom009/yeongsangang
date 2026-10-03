// 효과음은 파일 없이 Web Audio로 만든다
let ac = null;

function ctx() {
  if (!ac) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ac = new AC();
  }
  if (ac.state === 'suspended') ac.resume();
  return ac;
}

export const sound = {
  // 첫 클릭 때 호출해야 이후 소리가 난다 (브라우저 자동재생 정책)
  unlock() { ctx(); },

  beep(freq = 820) {
    const a = ctx();
    if (!a) return;
    const o = a.createOscillator();
    const g = a.createGain();
    o.type = 'sine';
    o.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, a.currentTime);
    g.gain.exponentialRampToValueAtTime(0.25, a.currentTime + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + 0.18);
    o.connect(g).connect(a.destination);
    o.start();
    o.stop(a.currentTime + 0.2);
  },

  // 셔터: 짧은 잡음 두 번
  shutter() {
    const a = ctx();
    if (!a) return;
    const len = Math.floor(a.sampleRate * 0.06);
    const buf = a.createBuffer(1, len, a.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len) ** 3;
    [0, 0.085].forEach((t) => {
      const s = a.createBufferSource();
      const f = a.createBiquadFilter();
      const g = a.createGain();
      s.buffer = buf;
      f.type = 'bandpass';
      f.frequency.value = t ? 2600 : 1800;
      g.gain.value = 0.9;
      s.connect(f).connect(g).connect(a.destination);
      s.start(a.currentTime + t);
    });
  },
};
