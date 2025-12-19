"""
GitHub Webhook 수신기
- GitHub에서 push 이벤트 발생 시 자동 배포 실행
"""
import os
import hmac
import hashlib
import subprocess
import logging
from flask import Flask, request, jsonify

# 설정
WEBHOOK_SECRET = os.environ.get('GITHUB_WEBHOOK_SECRET', 'your-webhook-secret-here')
DEPLOY_SCRIPT = '/home/biofl/kakaochatbot/deploy.sh'
ALLOWED_BRANCHES = ['main', 'master', 'claude/fix-image-analysis-error-TN7ai']

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/biofl/kakaochatbot/logs/webhook.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def verify_signature(payload_body, signature_header):
    """GitHub webhook signature 검증"""
    if not signature_header:
        return False

    hash_object = hmac.new(
        WEBHOOK_SECRET.encode('utf-8'),
        msg=payload_body,
        digestmod=hashlib.sha256
    )
    expected_signature = "sha256=" + hash_object.hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)


@app.route('/webhook', methods=['POST'])
def webhook():
    """GitHub webhook 엔드포인트"""
    # Signature 검증
    signature = request.headers.get('X-Hub-Signature-256')
    if WEBHOOK_SECRET != 'your-webhook-secret-here':
        if not verify_signature(request.data, signature):
            logger.warning("Invalid webhook signature")
            return jsonify({'error': 'Invalid signature'}), 401

    # 이벤트 타입 확인
    event = request.headers.get('X-GitHub-Event')
    if event != 'push':
        logger.info(f"Ignoring event: {event}")
        return jsonify({'message': f'Event {event} ignored'}), 200

    # Payload 파싱
    try:
        payload = request.get_json()
    except Exception as e:
        logger.error(f"Failed to parse payload: {e}")
        return jsonify({'error': 'Invalid payload'}), 400

    # 브랜치 확인
    ref = payload.get('ref', '')
    branch = ref.replace('refs/heads/', '')

    if branch not in ALLOWED_BRANCHES:
        logger.info(f"Ignoring push to branch: {branch}")
        return jsonify({'message': f'Branch {branch} ignored'}), 200

    # 배포 실행
    logger.info(f"Deploying branch: {branch}")
    try:
        # deploy.sh의 BRANCH 변수를 업데이트하고 실행
        env = os.environ.copy()
        env['DEPLOY_BRANCH'] = branch

        result = subprocess.run(
            ['bash', DEPLOY_SCRIPT],
            capture_output=True,
            text=True,
            timeout=300,  # 5분 타임아웃
            env=env
        )

        if result.returncode == 0:
            logger.info(f"Deployment successful: {result.stdout}")
            return jsonify({
                'message': 'Deployment successful',
                'branch': branch
            }), 200
        else:
            logger.error(f"Deployment failed: {result.stderr}")
            return jsonify({
                'error': 'Deployment failed',
                'details': result.stderr
            }), 500

    except subprocess.TimeoutExpired:
        logger.error("Deployment timed out")
        return jsonify({'error': 'Deployment timed out'}), 500
    except Exception as e:
        logger.error(f"Deployment error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    """헬스체크"""
    return jsonify({'status': 'ok'}), 200


if __name__ == '__main__':
    # 로그 디렉토리 생성
    os.makedirs('/home/biofl/kakaochatbot/logs', exist_ok=True)

    logger.info("Webhook server starting on port 9000")
    app.run(host='0.0.0.0', port=9000)
