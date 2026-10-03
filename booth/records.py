"""완성한 사진 이력. 관리 화면(/manage)에서 보고 다시 보낸다.

`OUT_DIR/records.jsonl`에 한 줄에 한 장씩 덧붙인다. 사진과 같은 폴더에 두어 함께 옮겨지고
함께 지워진다(데이터베이스를 따로 두지 않는 이유: 현장 노트북에서도 폴더만 복사하면 끝난다).
방문객 이름·연락처는 받지 않는다. 남는 것은 시간·장소·한마디뿐이다.
"""
import json
import threading
import time
from collections import OrderedDict

from . import config

PATH = config.OUT_DIR / 'records.jsonl'
_lock = threading.RLock()
_bg = OrderedDict()  # 촬영 때 고른 배경을 기억했다가 '완성하기' 때 함께 적는다
_MAX_PENDING = 512


def note_shot(sid, bg_id):
    with _lock:
        _bg[sid] = bg_id
        _bg.move_to_end(sid)
        while len(_bg) > _MAX_PENDING:
            _bg.popitem(last=False)


def add(sid, message, filtered):
    """'완성하기'를 누른 사진 한 장을 적는다."""
    with _lock:
        bg = _bg.pop(sid, None)
        b = config.BG_BY_ID.get(bg, {})
        rec = {'id': sid, 't': int(time.time()), 'bg': bg, 'place': b.get('place', ''),
               'name': b.get('name', ''), 'msg': message or '', 'filter': bool(filtered)}
        try:
            with open(PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        except OSError as e:  # 기록을 못 남겨도 방문객 흐름은 멈추지 않는다
            print(f'[기록] 저장 실패 {sid}: {e}', flush=True)
    return rec


def _read():
    if not PATH.exists():
        return []
    out = []
    for line in PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:  # 쓰다가 끊긴 줄은 버린다
            pass
    return out


def _write(rows):
    tmp = PATH.with_suffix('.tmp')
    tmp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    tmp.replace(PATH)


def load():
    """최신 순 목록."""
    with _lock:
        rows = _read()
    rows.reverse()
    return rows


def get(sid):
    for r in load():
        if r.get('id') == sid:
            return r
    return None


def remove(sid):
    with _lock:
        rows = _read()
        keep = [r for r in rows if r.get('id') != sid]
        if len(keep) != len(rows):
            _write(keep)
        return len(rows) - len(keep)


def prune(before):
    """보관 시간이 지난 기록을 지운다 (사진 자동 정리와 함께 돈다)."""
    with _lock:
        rows = _read()
        keep = [r for r in rows if r.get('t', 0) >= before]
        if len(keep) != len(rows):
            _write(keep)
        return len(rows) - len(keep)


def stats():
    """관리 화면 위쪽 요약: 전체·오늘·장소별."""
    rows = load()
    day = time.time() - 86400
    places = {}
    for r in rows:
        places[r.get('place') or '알 수 없음'] = places.get(r.get('place') or '알 수 없음', 0) + 1
    return {
        'total': len(rows),
        'today': sum(1 for r in rows if r.get('t', 0) >= day),
        'places': sorted(places.items(), key=lambda x: -x[1]),
    }
