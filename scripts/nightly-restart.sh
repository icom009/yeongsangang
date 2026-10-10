#!/bin/sh
# 밤마다 부스·IC-Light 컨테이너를 다시 띄워, 며칠씩 켜 둔 프로세스에 쌓인 메모리를 비운다 (집 GPU 서버).
# docker-compose.gpu.yml의 restarter 컨테이너가 이 스크립트를 돌린다. 시각은 YS_RESTART_AT (한국 시각, 기본 05:00).
# 최근 3분 사이 촬영·AI 효과 요청이 있으면 1분씩 미뤄 조용할 때 한다 (1시간 넘게 바쁘면 그날은 건너뛴다).
# 지금 바로 한 번:  docker compose exec restarter sh /restart.sh now
# 기록 보기:        docker compose logs restarter
set -u
export TZ=UTC-9   # 컨테이너는 UTC다. tzdata 없이 한국 시각을 쓰는 POSIX 표기
AT=${YS_RESTART_AT:-05:00}
BOOTH=${YS_BOOTH_CONTAINER:-yeongsangang-booth-1}
LIGHT=${YS_LIGHT_CONTAINER:-yeongsangang-ic-light-1}

log() { echo "[재시작] $(date '+%m-%d %H:%M:%S') $*"; }
mem() { docker stats --no-stream --format '{{.MemUsage}}' "$1" 2>/dev/null | cut -d/ -f1; }
health() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$BOOTH" 2>/dev/null; }

# 배포 때와 같은 기준: 촬영·완성, AI 효과 만들기, 휴대폰의 AI 상태 확인이 3분 동안 없어야 한다
busy() {
  docker logs --since 3m "$BOOTH" 2>&1 | grep -cE '"(POST /api/shots|POST /p/[^ ]+/fx/|GET /p/[^ ]+/ai[ ?])'
}

restart() {
  log "시작: 부스 $(mem "$BOOTH")· IC-Light $(mem "$LIGHT")"
  # IC-Light 먼저. 모델을 다시 올리는 몇 분 동안은 빛 보정만 쉬고, 부스가 30초마다 다시 찾아 붙는다
  docker inspect "$LIGHT" >/dev/null 2>&1 && docker restart -t 30 "$LIGHT" >/dev/null
  docker restart -t 30 "$BOOTH" >/dev/null
  for _ in $(seq 60); do
    sleep 5
    if [ "$(health)" = healthy ]; then
      log "완료: 부스 정상, $(mem "$BOOTH")"
      return 0
    fi
  done
  log "부스가 5분 안에 정상으로 돌아오지 않음 ($(health)). 한 번 더 다시 띄움"
  docker restart -t 30 "$BOOTH" >/dev/null
}

run() {
  for _ in $(seq 60); do
    if [ "$(busy)" = 0 ]; then
      restart
      return
    fi
    sleep 60
  done
  log "1시간 내내 손님이 있어 오늘은 건너뜀"
}

if [ "${1:-}" = now ]; then
  run
  exit
fi
log "대기 중: 매일 $AT (한국 시각)"
while :; do
  if [ "$(date +%H:%M)" = "$AT" ]; then
    run
    sleep 61   # 같은 분에 두 번 돌지 않게
  fi
  sleep 20
done
