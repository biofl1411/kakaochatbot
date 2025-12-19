#!/bin/bash
# 자동 배포 스크립트
# GitHub에서 최신 코드를 가져오고 서버를 재시작합니다.

set -e

# 설정
APP_DIR="/home/biofl/kakaochatbot"
BRANCH="main"  # 배포할 브랜치 (필요시 변경)
LOG_FILE="/home/biofl/kakaochatbot/logs/deploy.log"
SERVICE_NAME="kakaochatbot"

# 로그 함수
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# 로그 디렉토리 생성
mkdir -p "$(dirname "$LOG_FILE")"

log "========== 배포 시작 =========="

# 작업 디렉토리로 이동
cd "$APP_DIR"

# Git 최신 코드 가져오기
log "Git pull 실행 중..."
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

log "Git pull 완료"

# 가상환경 활성화 및 의존성 설치 (필요한 경우)
if [ -f "requirements.txt" ]; then
    log "의존성 설치 중..."
    source venv/bin/activate
    pip install -r requirements.txt --quiet
    log "의존성 설치 완료"
fi

# 서비스 재시작
log "서비스 재시작 중..."
sudo systemctl restart "$SERVICE_NAME"

# 서비스 상태 확인
sleep 2
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    log "서비스 재시작 성공!"
else
    log "ERROR: 서비스 재시작 실패!"
    sudo systemctl status "$SERVICE_NAME" | tee -a "$LOG_FILE"
    exit 1
fi

log "========== 배포 완료 =========="
