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


def add(sid, message, filtered, ids=None):
    """'완성하기'를 누른 사진 한 장을 적는다. ids는 네 컷이면 네 장의 id(첫째가 대표)."""
    ids = list(ids or [sid])
    with _lock:
        bgs = [_bg.pop(i, None) for i in ids]
        b = config.BG_BY_ID.get(bgs[0], {})
        rec = {'id': sid, 't': int(time.time()), 'bg': bgs[0], 'bgs': bgs, 'place': b.get('place', ''),
               'name': b.get('name', ''), 'msg': message or '', 'filter': bool(filtered),
               'cuts': len(ids)}
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


def with_orphans():
    """기록에 없는데 사진만 남아 있는 것(이 기능을 넣기 전에 찍은 사진)도 함께 보여 준다.
    관리 화면에서 지울 수 있어야 하므로 빠뜨리지 않는다."""
    rows = load()
    known = {r.get('id') for r in rows}
    tail = '_final.jpg'
    for p in config.OUT_DIR.glob('*' + tail):
        sid = p.name[:-len(tail)]
        if sid in known:
            continue
        rows.append({'id': sid, 't': int(p.stat().st_mtime), 'bg': None, 'place': '',
                     'name': '', 'msg': '', 'filter': None, 'orphan': True})
    rows.sort(key=lambda r: r.get('t', 0), reverse=True)
    return rows


def stats():
    """관리 화면 위쪽 요약: 전체·오늘·장소별."""
    rows = with_orphans()
    day = time.time() - 86400
    places = {}
    for r in rows:
        key = r.get('place') or '장소 미상'
        places[key] = places.get(key, 0) + 1
    return {
        'total': len(rows),
        'today': sum(1 for r in rows if r.get('t', 0) >= day),
        'places': sorted(places.items(), key=lambda x: -x[1]),
    }
