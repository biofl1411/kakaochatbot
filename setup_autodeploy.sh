#!/bin/bash
# 자동 배포 설정 스크립트
# 이 스크립트를 실행하면 systemd 서비스와 자동 배포가 설정됩니다.

set -e

echo "=========================================="
echo "  카카오 챗봇 자동 배포 설정"
echo "=========================================="

APP_DIR="/home/biofl/kakaochatbot"
WEBHOOK_SECRET=$(openssl rand -hex 20)

# 1. 로그 디렉토리 생성
echo "[1/6] 로그 디렉토리 생성..."
mkdir -p "$APP_DIR/logs"

# 2. deploy.sh 실행 권한 부여
echo "[2/6] 스크립트 실행 권한 설정..."
chmod +x "$APP_DIR/deploy.sh"

# 3. systemd 서비스 파일 복사
echo "[3/6] systemd 서비스 설치..."
sudo cp "$APP_DIR/systemd/kakaochatbot.service" /etc/systemd/system/
sudo cp "$APP_DIR/systemd/webhook.service" /etc/systemd/system/

# 4. webhook secret 설정
echo "[4/6] Webhook secret 생성..."
sudo sed -i "s/your-webhook-secret-here/$WEBHOOK_SECRET/" /etc/systemd/system/webhook.service

# 5. systemd 데몬 리로드 및 서비스 활성화
echo "[5/6] 서비스 활성화..."
sudo systemctl daemon-reload
sudo systemctl enable kakaochatbot
sudo systemctl enable webhook

# 6. 서비스 시작
echo "[6/6] 서비스 시작..."
sudo systemctl start kakaochatbot
sudo systemctl start webhook

echo ""
echo "=========================================="
echo "  설정 완료!"
echo "=========================================="
echo ""
echo "Webhook Secret (GitHub에 등록 필요):"
echo "$WEBHOOK_SECRET"
echo ""
echo "Webhook URL:"
echo "http://YOUR_SERVER_IP:9000/webhook"
echo ""
echo "서비스 상태 확인:"
echo "  sudo systemctl status kakaochatbot"
echo "  sudo systemctl status webhook"
echo ""
echo "로그 확인:"
echo "  tail -f $APP_DIR/logs/deploy.log"
echo "  tail -f $APP_DIR/logs/webhook.log"
echo ""
echo "=========================================="
echo "  GitHub Webhook 설정 방법"
echo "=========================================="
echo "1. GitHub 저장소 → Settings → Webhooks → Add webhook"
echo "2. Payload URL: http://YOUR_SERVER_IP:9000/webhook"
echo "3. Content type: application/json"
echo "4. Secret: $WEBHOOK_SECRET"
echo "5. Events: Just the push event"
echo "6. Add webhook 클릭"
echo "=========================================="
