"""
카카오 챗봇 API 서버
- 카카오 i 오픈빌더 스킬 서버
- DB에서 검사항목/검사주기 조회
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import logging

from config import SERVER_HOST, SERVER_PORT, LOG_FILE, LOG_FORMAT
from models import (
    init_database,
    get_inspection_item,
    get_inspection_cycle,
    search_inspection_items,
    search_inspection_cycles,
    get_last_crawl_time
)

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


@app.route('/health', methods=['GET'])
def health_check():
    """헬스체크 엔드포인트"""
    last_crawl = get_last_crawl_time()
    return jsonify({
        "status": "ok",
        "last_crawl": str(last_crawl) if last_crawl else "never"
    })


@app.route('/chatbot', methods=['POST'])
def chatbot():
    """카카오 챗봇 메인 엔드포인트"""
    try:
        data = request.get_json()
        user_input = data.get("userRequest", {}).get("utterance", "").strip()
        user_id = data.get("userRequest", {}).get("user", {}).get("id", "default")

        logger.info(f"[{user_id}] 입력: {user_input}")

        # 사용자 상태 초기화
        if user_id not in user_state:
            user_state[user_id] = {}
        user_data = user_state[user_id]

        # 기본 버튼
        default_buttons = ["검사주기", "검사항목", "처음으로"]

        # "처음으로" 입력 시 상태 초기화
        if user_input == "처음으로":
            reset_user_state(user_id)
            return make_response(
                "안녕하세요! 바이오에프엘 검사 안내 챗봇입니다.\n\n원하시는 서비스를 선택해주세요.",
                ["검사주기", "검사항목"]
            )

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
                    buttons = ["축산물제조가공업", "식육즙판매가공업", "처음으로"]
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
        if user_input in ["식품제조가공업", "즉석판매제조가공업", "축산물제조가공업", "식육즙판매가공업"]:
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
                # DB에서 검사항목 조회
                result = get_inspection_item(user_data["분야"], food_type)

                if result:
                    response_text = f"✅ [{result['food_type']}]의 검사 항목:\n\n{result['items']}"
                else:
                    # 유사 검색
                    similar = search_inspection_items(user_data["분야"], food_type)
                    if similar:
                        suggestions = ", ".join([r['food_type'] for r in similar[:5]])
                        response_text = f"❌ '{food_type}'에 대한 정확한 정보를 찾을 수 없습니다.\n\n유사한 항목: {suggestions}"
                    else:
                        response_text = f"❌ '{food_type}'에 대한 검사 항목을 찾을 수 없습니다."

                reset_user_state(user_id)
                return make_response(response_text, default_buttons)

            elif user_data["기능"] == "검사주기" and user_data.get("업종"):
                # DB에서 검사주기 조회
                result = get_inspection_cycle(user_data["분야"], user_data["업종"], food_type)

                if result:
                    response_text = f"✅ [{result['food_group']}] {result['food_type']}의 검사주기:\n\n{result['cycle']}"
                else:
                    # 유사 검색
                    similar = search_inspection_cycles(user_data["분야"], user_data["업종"], food_type)
                    if similar:
                        suggestions = ", ".join([r['food_type'] for r in similar[:5]])
                        response_text = f"❌ '{food_type}'에 대한 정확한 정보를 찾을 수 없습니다.\n\n유사한 항목: {suggestions}"
                    else:
                        response_text = f"❌ '{food_type}'에 대한 검사주기를 찾을 수 없습니다."

                reset_user_state(user_id)
                return make_response(response_text, default_buttons)

        # 기본 응답
        return make_response(
            "안녕하세요! 바이오에프엘 검사 안내 챗봇입니다.\n\n원하시는 서비스를 선택해주세요.",
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
