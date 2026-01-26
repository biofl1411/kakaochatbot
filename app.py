"""
카카오 챗봇 API 서버
- 카카오 i 오픈빌더 스킬 서버
- DB에서 검사항목/검사주기 조회
"""
import re
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
    get_vision_api_remaining,
    # Q&A 관련
    save_qa_response,
    update_qa_response,
    delete_qa_response,
    activate_qa_response,
    search_qa_response,
    search_qa_by_keyword,
    get_all_qa_responses,
    get_qa_by_id,
    get_qa_statistics,
    # 미답변 질문 관련
    log_unanswered_question,
    get_unanswered_questions,
    get_unanswered_by_id,
    resolve_unanswered_question,
    delete_unanswered_question,
    # 관리자 관련
    is_admin_user,
    add_admin_user,
    get_all_admin_users,
    has_any_admin
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

# Q&A 검색에서 제외할 Open Builder 메뉴 키워드
# (카카오 i 오픈빌더에서 캐러셀/버튼으로 처리되는 키워드)
EXCLUDED_KEYWORDS = {
    # 메인 메뉴
    "검사분야", "검사주기", "검사항목",
    # 검사분야 캐러셀 메뉴
    "자가품질검사", "영양성분검사", "소비기한설정",
    "항생물질", "잔류농약", "방사능",
    "비건", "할랄", "동물DNA",
    "알레르기", "글루텐Free", "이물질검사",
    # 하위 메뉴
    "홈페이지안내", "성적서문의", "시료접수안내",
    # 기타 시스템 키워드
    "처음으로", "종료", "확인", "취소",
}


def handle_admin_command(user_id: str, command: str) -> str:
    """관리자 명령어 처리"""
    # 첫 번째 !명령어 사용자는 자동으로 관리자 등록 (관리자가 없는 경우)
    if not has_any_admin():
        add_admin_user(user_id, "초기관리자")
        logger.info(f"[{user_id}] 초기 관리자로 자동 등록")

    # 관리자 권한 확인
    if not is_admin_user(user_id):
        return "❌ 관리자 권한이 없습니다."

    # 명령어 파싱
    parts = command.split(" ", 1)
    cmd = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    # !도움말
    if cmd == "!도움말":
        return """📋 관리자 명령어 도움말

[Q&A 관리]
!학습 질문|답변 : Q&A 추가
!학습 질문|답변|키워드1,키워드2 : 키워드와 함께 추가
!수정 ID|새답변 : Q&A 답변 수정
!삭제 ID : Q&A 삭제
!활성화 ID : 삭제된 Q&A 복구
!QA목록 : 등록된 Q&A 목록
!검색 키워드 : Q&A 검색
!상세 ID : Q&A 상세 정보

[미답변 관리]
!미답변 : 미답변 질문 목록 (빈도순)
!미답변학습 ID|답변 : 미답변을 Q&A로 등록
!미답변삭제 ID : 미답변 삭제

[시스템]
!통계 : Q&A/미답변 통계
!API사용량 : Vision API 사용량
!관리자추가 유저ID : 관리자 추가
!관리자목록 : 관리자 목록"""

    # !학습 질문|답변 또는 !학습 질문|답변|키워드
    if cmd == "!학습":
        if not args:
            return "❌ 형식: !학습 질문|답변 또는 !학습 질문|답변|키워드1,키워드2"

        parts = args.split("|")
        if len(parts) < 2:
            return "❌ 형식: !학습 질문|답변 (|로 구분)"

        question = parts[0].strip()
        answer = parts[1].strip()
        keywords = parts[2].strip() if len(parts) > 2 else None

        if not question or not answer:
            return "❌ 질문과 답변을 모두 입력해주세요."

        qa_id = save_qa_response(question, answer, keywords, created_by=user_id)
        result = f"✅ Q&A 등록 완료! (ID: {qa_id})\n\n질문: {question}\n답변: {answer}"
        if keywords:
            result += f"\n키워드: {keywords}"
        return result

    # !수정 ID|새답변
    if cmd == "!수정":
        if not args:
            return "❌ 형식: !수정 ID|새답변"

        parts = args.split("|")
        if len(parts) < 2:
            return "❌ 형식: !수정 ID|새답변"

        try:
            qa_id = int(parts[0].strip())
        except ValueError:
            return "❌ ID는 숫자여야 합니다."

        new_answer = parts[1].strip()
        if not new_answer:
            return "❌ 새 답변을 입력해주세요."

        if update_qa_response(qa_id, answer=new_answer):
            return f"✅ Q&A #{qa_id} 수정 완료!\n새 답변: {new_answer}"
        else:
            return f"❌ Q&A #{qa_id}를 찾을 수 없습니다."

    # !삭제 ID
    if cmd == "!삭제":
        if not args:
            return "❌ 형식: !삭제 ID"

        try:
            qa_id = int(args.strip())
        except ValueError:
            return "❌ ID는 숫자여야 합니다."

        qa = get_qa_by_id(qa_id)
        if not qa:
            return f"❌ Q&A #{qa_id}를 찾을 수 없습니다."

        if delete_qa_response(qa_id):
            return f"✅ Q&A #{qa_id} 삭제 완료!\n삭제된 질문: {qa['question']}"
        else:
            return f"❌ Q&A #{qa_id} 삭제 실패"

    # !QA목록
    if cmd == "!QA목록":
        qa_list = get_all_qa_responses()
        if not qa_list:
            return "📋 등록된 Q&A가 없습니다."

        result = f"📋 Q&A 목록 ({len(qa_list)}개)\n"
        result += "━━━━━━━━━━━━━━━\n"
        for qa in qa_list[:15]:  # 최대 15개
            q_short = qa['question'][:20] + "..." if len(qa['question']) > 20 else qa['question']
            result += f"#{qa['id']} [{qa['use_count']}회] {q_short}\n"

        if len(qa_list) > 15:
            result += f"\n... 외 {len(qa_list) - 15}개"
        return result

    # !미답변
    if cmd == "!미답변":
        unanswered = get_unanswered_questions(limit=15)
        if not unanswered:
            return "📋 미답변 질문이 없습니다."

        result = f"📋 미답변 질문 목록 ({len(unanswered)}개, 빈도순)\n"
        result += "━━━━━━━━━━━━━━━\n"
        for ua in unanswered:
            q_short = ua['question'][:25] + "..." if len(ua['question']) > 25 else ua['question']
            result += f"#{ua['id']} [{ua['count']}회] {q_short}\n"
        return result

    # !미답변학습 ID|답변
    if cmd == "!미답변학습":
        if not args:
            return "❌ 형식: !미답변학습 ID|답변"

        parts = args.split("|")
        if len(parts) < 2:
            return "❌ 형식: !미답변학습 ID|답변"

        try:
            ua_id = int(parts[0].strip())
        except ValueError:
            return "❌ ID는 숫자여야 합니다."

        answer = parts[1].strip()
        if not answer:
            return "❌ 답변을 입력해주세요."

        # 미답변 질문 조회
        unanswered = get_unanswered_by_id(ua_id)
        if not unanswered:
            return f"❌ 미답변 #{ua_id}를 찾을 수 없습니다."

        # Q&A로 등록
        qa_id = save_qa_response(unanswered['question'], answer, created_by=user_id)

        # 미답변 해결 처리
        resolve_unanswered_question(ua_id, qa_id)

        return f"✅ 미답변 #{ua_id} → Q&A #{qa_id} 등록 완료!\n\n질문: {unanswered['question']}\n답변: {answer}"

    # !미답변삭제 ID
    if cmd == "!미답변삭제":
        if not args:
            return "❌ 형식: !미답변삭제 ID"

        try:
            ua_id = int(args.strip())
        except ValueError:
            return "❌ ID는 숫자여야 합니다."

        unanswered = get_unanswered_by_id(ua_id)
        if not unanswered:
            return f"❌ 미답변 #{ua_id}를 찾을 수 없습니다."

        if delete_unanswered_question(ua_id):
            return f"✅ 미답변 #{ua_id} 삭제 완료!\n삭제된 질문: {unanswered['question']}"
        else:
            return f"❌ 미답변 #{ua_id} 삭제 실패"

    # !관리자추가 유저ID
    if cmd == "!관리자추가":
        if not args:
            return "❌ 형식: !관리자추가 유저ID"

        new_admin_id = args.strip()
        if add_admin_user(new_admin_id):
            return f"✅ 관리자 추가 완료: {new_admin_id}"
        else:
            return f"❌ 이미 등록된 관리자이거나 추가 실패: {new_admin_id}"

    # !관리자목록
    if cmd == "!관리자목록":
        admins = get_all_admin_users()
        if not admins:
            return "📋 등록된 관리자가 없습니다."

        result = f"📋 관리자 목록 ({len(admins)}명)\n"
        result += "━━━━━━━━━━━━━━━\n"
        for admin in admins:
            name = admin['name'] or "이름없음"
            result += f"• {name} ({admin['user_id'][:10]}...)\n"
        return result

    # !통계
    if cmd == "!통계":
        stats = get_qa_statistics()
        result = "📊 Q&A 통계\n"
        result += "━━━━━━━━━━━━━━━\n"
        result += f"• 등록된 Q&A: {stats['total_qa']}개\n"
        result += f"• 삭제된 Q&A: {stats['deleted_qa']}개\n"
        result += f"• 총 사용 횟수: {stats['total_usage']}회\n"
        result += f"• 미답변 질문: {stats['unanswered_count']}개\n"
        result += f"• 해결된 미답변: {stats['resolved_count']}개\n"
        if stats['top_qa']:
            result += "\n🏆 인기 Q&A (TOP 3)\n"
            for i, qa in enumerate(stats['top_qa'], 1):
                q_short = qa['question'][:15] + "..." if len(qa['question']) > 15 else qa['question']
                result += f"{i}. {q_short} ({qa['use_count']}회)\n"
        return result

    # !검색 키워드
    if cmd == "!검색":
        if not args:
            return "❌ 형식: !검색 키워드"

        results = search_qa_by_keyword(args.strip())
        if not results:
            return f"❌ '{args}'에 대한 검색 결과가 없습니다."

        result = f"🔍 '{args}' 검색 결과 ({len(results)}개)\n"
        result += "━━━━━━━━━━━━━━━\n"
        for qa in results[:10]:
            q_short = qa['question'][:20] + "..." if len(qa['question']) > 20 else qa['question']
            result += f"#{qa['id']} [{qa['use_count']}회] {q_short}\n"
        return result

    # !상세 ID
    if cmd == "!상세":
        if not args:
            return "❌ 형식: !상세 ID"

        try:
            qa_id = int(args.strip())
        except ValueError:
            return "❌ ID는 숫자여야 합니다."

        qa = get_qa_by_id(qa_id)
        if not qa:
            return f"❌ Q&A #{qa_id}를 찾을 수 없습니다."

        result = f"📋 Q&A #{qa_id} 상세 정보\n"
        result += "━━━━━━━━━━━━━━━\n"
        result += f"질문: {qa['question']}\n\n"
        result += f"답변: {qa['answer']}\n\n"
        result += f"키워드: {qa['keywords'] or '없음'}\n"
        result += f"카테고리: {qa['category']}\n"
        result += f"사용횟수: {qa['use_count']}회\n"
        result += f"상태: {'활성' if qa['is_active'] else '삭제됨'}\n"
        result += f"생성자: {qa['created_by']}\n"
        result += f"생성일: {qa['created_at'][:10]}"
        return result

    # !활성화 ID
    if cmd == "!활성화":
        if not args:
            return "❌ 형식: !활성화 ID"

        try:
            qa_id = int(args.strip())
        except ValueError:
            return "❌ ID는 숫자여야 합니다."

        qa = get_qa_by_id(qa_id)
        if not qa:
            return f"❌ Q&A #{qa_id}를 찾을 수 없습니다."

        if qa['is_active']:
            return f"❌ Q&A #{qa_id}는 이미 활성 상태입니다."

        if activate_qa_response(qa_id):
            return f"✅ Q&A #{qa_id} 활성화 완료!\n복구된 질문: {qa['question']}"
        else:
            return f"❌ Q&A #{qa_id} 활성화 실패"

    # !API사용량
    if cmd == "!API사용량":
        remaining = get_vision_api_remaining()
        from config import VISION_API_MONTHLY_LIMIT
        used = VISION_API_MONTHLY_LIMIT - remaining
        result = "📊 Vision API 사용량\n"
        result += "━━━━━━━━━━━━━━━\n"
        result += f"• 월 제한: {VISION_API_MONTHLY_LIMIT}회\n"
        result += f"• 사용량: {used}회\n"
        result += f"• 잔여: {remaining}회\n"
        if remaining < 100:
            result += "\n⚠️ 잔여 횟수가 적습니다!"
        return result

    return f"❌ 알 수 없는 명령어: {cmd}\n!도움말 로 명령어 목록을 확인하세요."


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
                "안녕하세요! 바이오푸드랩 챗봇[바푸]입니다.\n\n🔬 검사분야 버튼으로 분야별 검색 가능\n⚡ 퀵 메뉴 검사주기, 검사항목으로 빠른 조회\n🧮 영양성분 메뉴에서 함량, 당알코올 함량 계산\n💡 버튼 외 자연어로 질문하셔도 됩니다!\n\n자세한 상담은 채팅방 메뉴에서 \"채널 이동\"을 누르시면 상담이 가능한 채널로 이동합니다.\n(업무 시간 09:00~17:30)\n\n개발자 : @BP_K",
                ["검사주기", "검사항목"]
            )

        # ===== 관리자 명령어 처리 (! 로 시작) =====
        if user_input.startswith("!"):
            admin_result = handle_admin_command(user_id, user_input)
            return make_response(admin_result, ["처음으로"])

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
                    f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n예: 과자, 음료, 소시지 등\n\n📷 품목제조보고서 이미지를 보내주시면 자동으로 식품유형을 추출합니다.",
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
                f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n예: 과자, 음료, 소시지 등\n\n📷 품목제조보고서 이미지를 보내주시면 자동으로 식품유형을 추출합니다.",
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

        # ===== Q&A 검색 =====
        # Open Builder 메뉴 키워드는 Q&A 검색에서 제외 (캐러셀/버튼으로 처리됨)
        if user_input not in EXCLUDED_KEYWORDS:
            qa_result = search_qa_response(user_input)
            if qa_result:
                logger.info(f"[{user_id}] Q&A 매칭: #{qa_result['id']} - {qa_result['question']}")
                return make_response(qa_result['answer'], ["검사주기", "검사항목", "처음으로"])

        # ===== 미답변 질문 로깅 =====
        # 의미 있는 질문인 경우만 로깅 (2글자 이상, 특수 명령/메뉴 키워드 제외)
        if len(user_input) >= 2 and not user_input.startswith("!") and user_input not in EXCLUDED_KEYWORDS:
            log_unanswered_question(user_input, user_id)
            logger.info(f"[{user_id}] 미답변 로깅: {user_input}")

        # ===== 검사분야 캐러셀 응답 =====
        if user_input == "검사분야":
            logger.info(f"[{user_id}] 검사분야 캐러셀 반환")
            carousel_response = {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {
                            "carousel": {
                                "type": "basicCard",
                                "items": [
                                    {
                                        "title": "자가품질검사",
                                        "description": "식품/축산물 자가품질검사",
                                        "buttons": [
                                            {"label": "자가품질검사", "action": "message", "messageText": "자가품질검사"}
                                        ]
                                    },
                                    {
                                        "title": "영양성분검사",
                                        "description": "영양성분표시, 영양강조표시 검사",
                                        "buttons": [
                                            {"label": "영양성분검사", "action": "message", "messageText": "영양성분검사"}
                                        ]
                                    },
                                    {
                                        "title": "소비기한설정",
                                        "description": "유통기한/소비기한 설정 검사",
                                        "buttons": [
                                            {"label": "소비기한설정", "action": "message", "messageText": "소비기한설정"}
                                        ]
                                    },
                                    {
                                        "title": "항생물질/잔류농약/방사능",
                                        "description": "안전성 검사",
                                        "buttons": [
                                            {"label": "항생물질", "action": "message", "messageText": "항생물질"},
                                            {"label": "잔류농약", "action": "message", "messageText": "잔류농약"},
                                            {"label": "방사능", "action": "message", "messageText": "방사능"}
                                        ]
                                    },
                                    {
                                        "title": "기타검사",
                                        "description": "비건/할랄/DNA/알레르기/글루텐/이물질",
                                        "buttons": [
                                            {"label": "비건", "action": "message", "messageText": "비건"},
                                            {"label": "할랄", "action": "message", "messageText": "할랄"},
                                            {"label": "이물질검사", "action": "message", "messageText": "이물질검사"}
                                        ]
                                    }
                                ]
                            }
                        }
                    ],
                    "quickReplies": [
                        {"label": "검사주기", "action": "message", "messageText": "검사주기"},
                        {"label": "검사항목", "action": "message", "messageText": "검사항목"},
                        {"label": "처음으로", "action": "message", "messageText": "처음으로"}
                    ]
                }
            }
            return jsonify(carousel_response)

        # ===== Open Builder 메뉴 키워드는 스킬에서 응답하지 않음 =====
        # Open Builder 블록에서 캐러셀/버튼 응답을 직접 처리하도록 빈 응답 반환
        if user_input in EXCLUDED_KEYWORDS:
            logger.info(f"[{user_id}] Open Builder 키워드 - 스킬 패스스루: {user_input}")
            return jsonify({
                "version": "2.0",
                "template": {
                    "outputs": []
                }
            })

        # 기본 응답
        return make_response(
            "안녕하세요! 바이오푸드랩 챗봇[바푸]입니다.\n\n🔬 검사분야 버튼으로 분야별 검색 가능\n⚡ 퀵 메뉴 검사주기, 검사항목으로 빠른 조회\n🧮 영양성분 메뉴에서 함량, 당알코올 함량 계산\n💡 버튼 외 자연어로 질문하셔도 됩니다!\n\n자세한 상담은 채팅방 메뉴에서 \"채널 이동\"을 누르시면 상담이 가능한 채널로 이동합니다.\n(업무 시간 09:00~17:30)\n\n개발자 : @BP_K",
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
