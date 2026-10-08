"""단체 사진 묶음. 단체로 와서 한 명씩 찍은 사진들을 관리 화면에서 골라 QR 하나로 모두 받게 한다.

OUT_DIR/albums.json 한 파일에 담는다 (데이터베이스 없음, 폴더째 옮기면 그대로). 묶음 id도 서버가 만든 값만 쓰고
storage.valid_id로 검사한다. 사진은 따로 복사하지 않고 id만 적어 두므로, 보관 시간이 지나 사진이 지워지면
묶음에서도 저절로 빠지고, 다 빠진 묶음은 prune()이 정리한다. 'ZIP으로 받기'는 묶음마다 한 번만 만들어
OUT_DIR/albums/에 두고 다시 쓴다 (단체 여럿이 한꺼번에 받아도 메모리에 수십 MB씩 만들지 않게).
"""
import json
import threading
import time
import zipfile

from . import config, records, storage

PATH = config.OUT_DIR / 'albums.json'
ZIP_DIR = config.OUT_DIR / 'albums'
MAX_PHOTOS = 60
_lock = threading.Lock()
_zip_lock = threading.Lock()


def _load():
    try:
        return json.loads(PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _save(data):
    tmp = PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(PATH)


def alive(ids):
    """아직 받을 수 있는 사진만 (보관 시간이 지나 지워진 사진은 뺀다)."""
    return [i for i in ids if storage.valid_id(i) and storage.path(i, 'final').exists()]


def create(ids, title=''):
    """고른 사진들로 묶음을 만든다. 찍은 순서대로 담는다."""
    # 기록 파일은 '완성하기'를 누른 순서대로 쌓이므로 그 순서가 곧 찍은 순서다 (시각은 초 단위라 같을 수 있다)
    order = {r.get('id'): n for n, r in enumerate(reversed(records.load()))}  # load()는 최신 순
    ids = alive(list(dict.fromkeys(ids)))  # 같은 사진은 한 번만
    ids.sort(key=lambda i: order.get(i, -1))
    if not ids:
        raise ValueError('묶을 사진이 없어요. 지워지지 않은 사진을 골라 주세요.')
    if len(ids) > MAX_PHOTOS:
        raise ValueError(f'한 묶음에는 {MAX_PHOTOS}장까지 담을 수 있어요.')
    aid = storage.new_id()
    rec = {'t': int(time.time()), 'title': ' '.join((title or '').split())[:30], 'ids': ids}
    with _lock:
        data = _load()
        data[aid] = rec
        _save(data)
    return aid, rec


def get(aid):
    if not storage.valid_id(aid):
        return None
    with _lock:
        return _load().get(aid)


def listing():
    """관리 화면에 보여 줄 묶음 목록 (최근 것부터)."""
    with _lock:
        data = _load()
    out = [{'id': k, 't': v['t'], 'title': v.get('title', ''), 'count': len(alive(v['ids']))}
           for k, v in data.items()]
    return sorted(out, key=lambda r: r['t'], reverse=True)


def remove(aid):
    with _lock:
        data = _load()
        if data.pop(aid, None) is None:
            return False
        _save(data)
    _drop_zip(aid)
    return True


def _drop_zip(aid):
    for p in ZIP_DIR.glob(f'{aid}_*.zip'):
        p.unlink(missing_ok=True)


def zip_path(aid, ids):
    """묶음 사진 ZIP. 처음 받을 때 한 번 만들고, 사진이 지워져 장수가 바뀌면 다시 만든다."""
    out = ZIP_DIR / f'{aid}_{len(ids)}.zip'
    with _zip_lock:
        if not out.exists():
            ZIP_DIR.mkdir(parents=True, exist_ok=True)
            _drop_zip(aid)
            tmp = out.with_suffix('.part')
            with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED) as z:  # JPEG은 더 줄지 않는다
                for n, i in enumerate(ids, 1):
                    z.write(storage.path(i, 'final'), f'yeongsangang_{n:02d}.jpg')
            tmp.replace(out)
    return out


def prune():
    """사진이 모두 지워진 묶음과 그 ZIP을 정리한다 (보관 시간 정리 때 함께)."""
    with _lock:
        data = _load()
        keep = {k: v for k, v in data.items() if alive(v['ids'])}
        if len(keep) != len(data):
            _save(keep)
    if ZIP_DIR.exists():
        for p in ZIP_DIR.glob('*.zip'):
            if p.name.rsplit('_', 1)[0] not in keep:  # 이름은 '{묶음 id}_{장수}.zip' (id에도 _가 들어갈 수 있다)
                p.unlink(missing_ok=True)
