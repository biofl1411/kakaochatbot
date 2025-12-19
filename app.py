"""
카카오 챗봇 API 서버
- 카카오 i 오픈빌더 스킬 서버
- DB에서 검사항목/검사주기 조회
- GitHub Webhook 자동 배포
"""
import re
import os
import hmac
import hashlib
import subprocess
from flask import Flask, request, jsonify
from flask_cors import CORS
import logging

from config import SERVER_HOST, SERVER_PORT, LOG_FILE, LOG_FORMAT
from models import (
    init_database,
    get_inspection_item,
    get_inspection_item_all_matches,
    get_inspection_cycle,
    get_inspection_cycle_all_matches,
    search_inspection_items,
    search_inspection_cycles,
    find_similar_items,
    find_similar_cycles,
    get_last_crawl_time,
    can_use_vision_api,
    get_vision_api_remaining
)
from vision_ocr import extract_food_type_from_image, is_vision_api_available

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Flask 앱 생성
app = Flask(__name__)
CORS(app)

# 사용자 상태 저장 (세션 관리)
user_state = {}


def is_image_url(text: str) -> bool:
    """텍스트가 이미지 URL인지 확인"""
    if not text:
        return False
    image_patterns = [
        r'https?://talk\.kakaocdn\.net/.*\.(jpg|jpeg|png|gif)',
        r'https?://.*kakao.*\.(jpg|jpeg|png|gif)',
        r'https?://.*\.(jpg|jpeg|png|gif)(\?.*)?$'
    ]
    for pattern in image_patterns:
        if re.match(pattern, text, re.IGNORECASE):
            return True
    return False


def make_response(text: str, buttons: list = None):
    """카카오 챗봇 응답 형식 생성"""
    response = {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}]
        }
    }

    if buttons:
        response["template"]["quickReplies"] = [
            {"label": btn, "action": "message", "messageText": btn}
            for btn in buttons
        ]

    return jsonify(response)


def reset_user_state(user_id: str):
    """사용자 상태 초기화"""
    user_state[user_id] = {}


# Webhook 설정
WEBHOOK_SECRET = os.environ.get('GITHUB_WEBHOOK_SECRET', '6582f3a101793fa86fd7090985a6f8ec1276f82f')
DEPLOY_SCRIPT = '/home/biofl/kakaochatbot/deploy.sh'


def verify_webhook_signature(payload_body, signature_header):
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


@app.route('/health', methods=['GET'])
def health_check():
    """헬스체크 엔드포인트"""
    last_crawl = get_last_crawl_time()
    return jsonify({
        "status": "ok",
        "last_crawl": str(last_crawl) if last_crawl else "never"
    })


@app.route('/webhook', methods=['POST'])
def github_webhook():
    """GitHub Webhook 엔드포인트 - 자동 배포"""
    # Signature 검증
    signature = request.headers.get('X-Hub-Signature-256')
    if not verify_webhook_signature(request.data, signature):
        logger.warning("Invalid webhook signature")
        return jsonify({'error': 'Invalid signature'}), 401

    # 이벤트 타입 확인
    event = request.headers.get('X-GitHub-Event')
    if event != 'push':
        return jsonify({'message': f'Event {event} ignored'}), 200

    # Payload 파싱
    try:
        payload = request.get_json()
        ref = payload.get('ref', '')
        branch = ref.replace('refs/heads/', '')
        logger.info(f"Webhook received: push to {branch}")

        # 배포 스크립트 실행 (백그라운드)
        if os.path.exists(DEPLOY_SCRIPT):
            subprocess.Popen(['bash', DEPLOY_SCRIPT],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            return jsonify({'message': 'Deployment started', 'branch': branch}), 200
        else:
            return jsonify({'error': 'Deploy script not found'}), 500

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/chatbot', methods=['POST'])
def chatbot():
    """카카오 챗봇 메인 엔드포인트"""
    try:
        data = request.get_json()
        user_input = data.get("userRequest", {}).get("utterance", "").strip()
        user_id = data.get("userRequest", {}).get("user", {}).get("id", "default")

        # 이미지 업로드 확인 (params에서)
        params = data.get("action", {}).get("params", {})
        image_url = None
        if "secureimage" in params:
            image_url = params["secureimage"]
        elif "image" in params:
            image_url = params["image"]

        # 텍스트 입력이 이미지 URL인 경우도 처리
        if not image_url and user_input and is_image_url(user_input):
            image_url = user_input
            logger.info(f"[{user_id}] 텍스트로 전달된 이미지 URL 감지")

        logger.info(f"[{user_id}] 입력: {user_input[:100] if user_input else 'None'}" + (f" (이미지: {image_url[:50]}...)" if image_url else ""))

        # 사용자 상태 초기화
        if user_id not in user_state:
            user_state[user_id] = {}
        user_data = user_state[user_id]

        # 기본 버튼
        default_buttons = ["검사주기", "검사항목", "처음으로"]

        # "처음으로" 또는 "종료" 입력 시 상태 초기화
        if user_input in ["처음으로", "종료"]:
            reset_user_state(user_id)
            return make_response(
                "안녕하세요! 바이오푸드랩 챗봇[바푸]입니다.\n\n원하시는 서비스를 선택해주세요.",
                ["검사주기", "검사항목"]
            )

        # ===== 이미지 업로드 처리 =====
        if image_url and user_data.get("기능") and user_data.get("분야"):
            # 이미지에서 식품유형 추출 시도
            ocr_result = extract_food_type_from_image(image_url)

            if ocr_result['success'] and ocr_result['food_type']:
                food_type = ocr_result['food_type']
                logger.info(f"[{user_id}] OCR 식품유형: {food_type}")

                # 추출된 식품유형으로 검색
                if user_data["기능"] == "검사항목":
                    result = get_inspection_item(user_data["분야"], food_type)
                    if result:
                        user_data["실패횟수"] = 0
                        response_text = f"📷 이미지에서 '{food_type}'을(를) 찾았습니다.\n\n"
                        response_text += f"✅ [{result['food_type']}]의 검사 항목:\n\n{result['items']}"
                        response_text += f"\n\n📌 다른 식품 유형을 입력하거나, [종료]를 눌러주세요."
                        return make_response(response_text, ["종료"])
                    else:
                        # 이미지에서 추출했지만 DB에 없는 경우
                        similar = find_similar_items(user_data["분야"], food_type)
                        response_text = f"📷 이미지에서 '{food_type}'을(를) 찾았습니다.\n\n"
                        response_text += f"❌ 하지만 '{food_type}'에 대한 검사 항목을 찾을 수 없습니다."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"
                        return make_response(response_text, ["종료"])

                elif user_data["기능"] == "검사주기" and user_data.get("업종"):
                    result = get_inspection_cycle(user_data["분야"], user_data["업종"], food_type)
                    if result:
                        user_data["실패횟수"] = 0
                        response_text = f"📷 이미지에서 '{food_type}'을(를) 찾았습니다.\n\n"
                        response_text += f"✅ [{result['food_group']}] {result['food_type']}의 검사주기:\n\n{result['cycle']}"
                        response_text += f"\n\n📌 다른 식품 유형을 입력하거나, [종료]를 눌러주세요."
                        return make_response(response_text, ["종료"])
                    else:
                        similar = find_similar_cycles(user_data["분야"], user_data["업종"], food_type)
                        response_text = f"📷 이미지에서 '{food_type}'을(를) 찾았습니다.\n\n"
                        response_text += f"❌ 하지만 '{food_type}'에 대한 검사주기를 찾을 수 없습니다."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"
                        return make_response(response_text, ["종료"])
            else:
                # OCR 실패
                response_text = f"📷 {ocr_result['message']}\n\n"
                response_text += "식품유형을 직접 입력해주세요."
                return make_response(response_text, ["종료"])

        # ===== 결제수단 기능 =====
        if user_input in ["결제수단", "결제정보"]:
            user_data["기능"] = "결제수단"
            return make_response(
                "💳 결제수단을 선택해주세요.",
                ["계좌번호", "카드결제", "통장사본", "처음으로"]
            )

        # 계좌번호 선택
        if user_input == "계좌번호":
            user_data["기능"] = "결제수단"
            user_data["결제"] = "계좌번호"
            return make_response(
                "🏦 은행을 선택해주세요.",
                ["기업은행", "우리은행", "농협은행", "처음으로"]
            )

        # 은행 선택 → 계좌번호 표시
        if user_input in ["기업은행", "우리은행", "농협은행"]:
            bank_info = {
                "기업은행": "024-088021-01-017",
                "우리은행": "1005-702-799176",
                "농협은행": "301-0178-1722-11"
            }
            account = bank_info.get(user_input, "")
            response_text = f"🏦 {user_input} 계좌번호\n\n"
            response_text += f"📋 {account}\n\n"
            response_text += "━━━━━━━━━━━━━━━\n"
            response_text += "★ 입금시 '대표자명' 또는 '업체명'으로 입금 부탁드립니다.\n\n"
            response_text += "★ 업체명으로 입금 진행시, [농업회사법인 주식회사]에서 잘리는 경우가 있습니다. "
            response_text += "이와 같은 경우, 입금 확인이 늦어질 수 있으니 업체명을 식별할 수 있도록 표시 부탁드립니다."

            return make_response(response_text, ["다른은행", "결제수단", "처음으로"])

        # 다른은행 선택
        if user_input == "다른은행":
            return make_response(
                "🏦 은행을 선택해주세요.",
                ["기업은행", "우리은행", "농협은행", "처음으로"]
            )

        # 카드결제 선택
        if user_input == "카드결제":
            response_text = "💳 카드 결제 안내\n\n"
            response_text += "1. 방문 결제\n"
            response_text += "2. 토스 링크페이 결제\n"
            response_text += "3. 홈페이지 통하여 검사 진행 후, 마이페이지 카드 결제\n\n"
            response_text += "━━━━━━━━━━━━━━━\n"
            response_text += "* 영수증이 필요하신 분은 결제 창에서 이메일을 작성하셔야 합니다."

            return make_response(response_text, ["결제수단", "처음으로"])

        # 통장사본 선택
        if user_input == "통장사본":
            response_text = "📄 통장 사본 안내\n\n"
            response_text += "통장 사본은 [자료실-문서자료실] 18번 게시글을 통하여 다운로드 가능합니다.\n\n"
            response_text += "🔗 홈페이지: www.biofl.co.kr"

            return make_response(response_text, ["결제수단", "처음으로"])

        # ===== 상담원 연결 =====
        if user_input == "상담원 연결":
            response_text = "👩‍💼 상담원 연결 안내\n\n"
            response_text += "⏰ 상담 가능 시간\n"
            response_text += "평일 09:00 ~ 17:00\n\n"
            response_text += "━━━━━━━━━━━━━━━\n"
            response_text += "아래 링크를 클릭하여 상담원과 연결하세요.\n\n"
            response_text += "🔗 http://pf.kakao.com/_uCxnvxl/chat"

            return make_response(response_text, ["처음으로"])

        # Step 1: 기능 선택
        if user_input in ["검사주기", "검사항목"]:
            user_data["기능"] = user_input
            user_data.pop("분야", None)
            user_data.pop("업종", None)
            return make_response(
                f"[{user_input}] 검사할 분야를 선택해주세요.",
                ["식품", "축산", "처음으로"]
            )

        # Step 2: 분야 선택
        if user_input in ["식품", "축산"]:
            if "기능" not in user_data:
                return make_response(
                    "먼저 원하시는 서비스를 선택해주세요.",
                    ["검사주기", "검사항목"]
                )

            user_data["분야"] = user_input

            if user_data["기능"] == "검사주기":
                # 검사주기: 업종 선택 필요
                if user_input == "식품":
                    buttons = ["식품제조가공업", "즉석판매제조가공업", "처음으로"]
                else:
                    buttons = ["축산물제조가공업", "축산물즉석판매제조가공업", "처음으로"]
                return make_response(
                    f"[{user_input}] 검사할 업종을 선택해주세요.",
                    buttons
                )
            else:
                # 검사항목: 바로 식품 유형 입력
                return make_response(
                    f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n예: 과자, 음료, 소시지 등",
                    ["처음으로"]
                )

        # Step 3: 업종 선택 (검사주기만 해당)
        if user_input in ["식품제조가공업", "즉석판매제조가공업", "축산물제조가공업", "축산물즉석판매제조가공업"]:
            if user_data.get("기능") != "검사주기":
                return make_response(
                    "먼저 원하시는 서비스를 선택해주세요.",
                    ["검사주기", "검사항목"]
                )

            user_data["업종"] = user_input
            return make_response(
                f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n예: 과자, 음료, 소시지 등",
                ["처음으로"]
            )

        # Step 4: 식품 유형 입력 → 결과 조회
        if user_data.get("기능") and user_data.get("분야"):
            food_type = user_input

            if user_data["기능"] == "검사항목":
                # DB에서 검사항목 조회 - 모든 매칭 결과 확인
                all_matches = get_inspection_item_all_matches(user_data["분야"], food_type)

                if len(all_matches) > 1:
                    # 여러 개 매칭 시 선택지 제공
                    user_data["실패횟수"] = 0
                    response_text = f"'{food_type}'(와)과 관련된 식품유형이 {len(all_matches)}개 있습니다.\n\n"
                    response_text += "원하시는 항목을 선택해주세요."

                    # 버튼으로 선택지 제공 (최대 10개)
                    buttons = [match['food_type'] for match in all_matches[:10]]
                    buttons.append("종료")
                    return make_response(response_text, buttons)

                elif len(all_matches) == 1:
                    # 1개 매칭 시 바로 결과 표시
                    result = all_matches[0]
                    user_data["실패횟수"] = 0
                    response_text = f"✅ [{result['food_type']}]의 검사 항목:\n\n{result['items']}"
                    response_text += f"\n\n📌 다른 식품 유형을 입력하거나, [종료]를 눌러주세요."
                    return make_response(response_text, ["종료"])
                else:
                    # 매칭 없음 - 실패 횟수 증가
                    user_data["실패횟수"] = user_data.get("실패횟수", 0) + 1

                    # 유사 검색 (2글자 이상 공통)
                    similar = find_similar_items(user_data["분야"], food_type)

                    if user_data["실패횟수"] >= 3:
                        # 3회 이상 실패 시 서류 확인 안내
                        response_text = f"❌ '{food_type}'에 대한 검사 항목을 찾을 수 없습니다.\n\n"
                        response_text += "📋 품목제조보고서 또는 영업등록증/신고증/허가증의 '식품유형'을 확인하여 다시 입력해주세요."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"
                    elif user_data["실패횟수"] >= 1 and is_vision_api_available():
                        # 1회 이상 실패 시 이미지 업로드 안내 (Vision API 사용 가능 시)
                        response_text = f"❌ '{food_type}'에 대한 검사 항목을 찾을 수 없습니다.\n\n"
                        response_text += "📷 품목제조보고서 또는 영업등록증/신고증/허가증 이미지를 업로드하시면 자동으로 식품유형을 찾아드립니다."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"
                    else:
                        response_text = f"❌ '{food_type}'에 대한 검사 항목을 찾을 수 없습니다.\n\n☆ 다른 식품 유형을 입력하거나, [종료]를 눌러주세요."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"

                    return make_response(response_text, ["종료"])

            elif user_data["기능"] == "검사주기" and user_data.get("업종"):
                # DB에서 검사주기 조회 - 모든 매칭 결과 확인
                all_matches = get_inspection_cycle_all_matches(user_data["분야"], user_data["업종"], food_type)

                if len(all_matches) > 1:
                    # 여러 개 매칭 시 선택지 제공
                    user_data["실패횟수"] = 0
                    response_text = f"'{food_type}'(와)과 관련된 식품유형이 {len(all_matches)}개 있습니다.\n\n"
                    response_text += "원하시는 항목을 선택해주세요."

                    # 버튼으로 선택지 제공 (최대 10개)
                    buttons = [match['food_type'] for match in all_matches[:10]]
                    buttons.append("종료")
                    return make_response(response_text, buttons)

                elif len(all_matches) == 1:
                    # 1개 매칭 시 바로 결과 표시
                    result = all_matches[0]
                    user_data["실패횟수"] = 0
                    response_text = f"✅ [{result['food_group']}] {result['food_type']}의 검사주기:\n\n{result['cycle']}"
                    response_text += f"\n\n📌 다른 식품 유형을 입력하거나, [종료]를 눌러주세요."
                    return make_response(response_text, ["종료"])
                else:
                    # 매칭 없음 - 실패 횟수 증가
                    user_data["실패횟수"] = user_data.get("실패횟수", 0) + 1

                    # 유사 검색 (2글자 이상 공통)
                    similar = find_similar_cycles(user_data["분야"], user_data["업종"], food_type)

                    if user_data["실패횟수"] >= 3:
                        # 3회 이상 실패 시 업종에 따른 서류 확인 안내
                        response_text = f"❌ '{food_type}'에 대한 검사주기를 찾을 수 없습니다.\n\n"
                        if user_data["업종"] in ["식품제조가공업", "축산물제조가공업"]:
                            response_text += "📋 품목제조보고서의 '식품유형'을 확인하여 다시 입력해주세요."
                        else:
                            response_text += "📋 영업등록증 또는 신고증/허가증의 '식품유형'을 확인하여 다시 입력해주세요."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"
                    elif user_data["실패횟수"] >= 1 and is_vision_api_available():
                        # 1회 이상 실패 시 이미지 업로드 안내 (Vision API 사용 가능 시)
                        response_text = f"❌ '{food_type}'에 대한 검사주기를 찾을 수 없습니다.\n\n"
                        if user_data["업종"] in ["식품제조가공업", "축산물제조가공업"]:
                            response_text += "📷 품목제조보고서 이미지를 업로드하시면 자동으로 식품유형을 찾아드립니다."
                        else:
                            response_text += "📷 영업등록증 또는 신고증/허가증 이미지를 업로드하시면 자동으로 식품유형을 찾아드립니다."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"
                    else:
                        response_text = f"❌ '{food_type}'에 대한 검사주기를 찾을 수 없습니다.\n\n☆ 다른 식품 유형을 입력하거나, [종료]를 눌러주세요."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"

                    return make_response(response_text, ["종료"])

        # 기본 응답
        return make_response(
            "안녕하세요! 바이오푸드랩 챗봇 [바푸]입니다.\n\n원하시는 서비스를 선택해주세요.",
            ["검사주기", "검사항목"]
        )

    except Exception as e:
        logger.error(f"챗봇 오류: {e}")
        return make_response(
            "❌ 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            ["처음으로"]
        )


if __name__ == '__main__':
    # 데이터베이스 초기화
    init_database()
    logger.info(f"서버 시작: http://{SERVER_HOST}:{SERVER_PORT}")

    # 개발 서버 실행
    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=True)
