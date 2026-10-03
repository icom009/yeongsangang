"""합성 모델(RVM)을 model/ 폴더에 내려받는다. Docker 빌드와 처음 설치 때 실행."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booth.compose import ensure_model  # noqa: E402

print(ensure_model())
