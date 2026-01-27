"""
카카오 챗봇 API 서버
- 카카오 i 오픈빌더 스킬 서버
- DB에서 검사항목/검사주기 조회
"""
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
import logging

from config import SERVER_HOST, SERVER_PORT, LOG_FILE, LOG_FORMAT, URL_MAPPING, DISPLAY_Q_NUMBER, NUTRITION_LABEL_CATEGORIES
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
    get_nutrition_info,
    # Q&A 관련
    save_qa_response,
    update_qa_response,
    delete_qa_response,
    activate_qa_response,
    get_qa_by_id,
    get_all_qa_responses,
    search_qa_response,
    search_qa_by_keyword,
    get_qa_statistics,
    # 미답변 관련
    log_unanswered_question,
    get_unanswered_questions,
    get_unanswered_by_id,
    delete_unanswered_question,
    resolve_unanswered_question,
    # 관리자 관련
    is_admin_user,
    has_any_admin,
    add_admin_user,
    get_all_admin_users,
    # 1회 섭취참고량 관련
    get_serving_food_groups,
    get_serving_food_types,
    get_serving_subtypes,
    get_serving_size,
    search_serving_size,
    # 영양표시 도우미 관련
    get_daily_value,
    get_all_daily_values,
    search_daily_value,
    calculate_percent_daily_value,
    get_nutrient_claim,
    get_all_claims_for_nutrient,
    get_all_nutrient_claims,
    check_nutrient_claim,
    get_rounding_rule,
    get_all_rounding_rules,
    apply_rounding_rule,
    get_display_value
)
try:
    from vision_ocr import extract_food_type_from_image, is_vision_api_available
    VISION_AVAILABLE = True
except ImportError as e:
    logging.warning(f"Vision OCR 모듈 로드 실패: {e}")
    VISION_AVAILABLE = False
    def extract_food_type_from_image(url):
        return {'success': False, 'food_type': None, 'message': 'Vision API 사용 불가'}
    def is_vision_api_available():
        return False

# NLP 검색 기능 import
try:
    from nlp_keywords import search_qa_by_query
    NLP_AVAILABLE = True
except ImportError as e:
    logging.warning(f"NLP 모듈 로드 실패: {e}")
    NLP_AVAILABLE = False
    def search_qa_by_query(query, top_n=3, min_score=1):
        return []

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


def format_korean_spacing(text: str) -> str:
    """한국어 텍스트에 적절한 띄어쓰기 추가"""
    if not text:
        return text

    # 조사/어미 앞에 붙어있는 단어들 사이에 띄어쓰기 추가
    patterns = [
        # ~에 한한다, ~에 한하며
        (r'([가-힣])에한한다', r'\1에 한한다'),
        (r'([가-힣])에한하며', r'\1에 한하며'),
        # ~을/를 제외한다
        (r'([가-힣])은제외한다', r'\1은 제외한다'),
        (r'([가-힣])를제외한다', r'\1를 제외한다'),
        # ~또는~
        (r'([가-힣])또는([가-힣])', r'\1 또는 \2'),
        # ~및~
        (r'([가-힣])및([가-힣])', r'\1 및 \2'),
        # ~의 합으로서
        (r'의합으로서', r'의 합으로서'),
        (r'의합으로 서', r'의 합으로서'),
        # ~를 함유한
        (r'를함유한', r'를 함유한'),
        # ~이상~
        (r'([0-9])이상', r'\1 이상'),
        # ~미만~
        (r'([0-9])미만', r'\1 미만'),
        # ~이하~
        (r'([0-9])이하', r'\1 이하'),
        # ~초과~
        (r'([0-9])초과', r'\1 초과'),
        # 단위 뒤
        (r'(mg|g|kg|ml|L|%|회)([가-힣])', r'\1 \2'),
    ]

    result = text
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result)

    return result


def format_items_list(items_text: str, category: str = "식품") -> str:
    """콤마로 구분된 항목들을 줄바꿈된 리스트 형식으로 변환

    괄호 [], () 안의 콤마는 항목 구분자가 아니므로 무시
    카테고리 헤더 (매월 1회 이상), (제품 생산 단위별) 등은 bullet 없이 표시
    부칙 (유탕·유처리식품에 한한다) 등:
      - 식품: 이전 항목에 같은 줄로 붙임
      - 축산: 별도 줄에 표시 (✏️ 포함)
    """
    if not items_text:
        return items_text

    # 괄호 깊이를 추적하며 콤마로 분리
    items = []
    current_item = ""
    bracket_depth = 0  # [] 깊이
    paren_depth = 0    # () 깊이

    for char in items_text:
        if char == '[':
            bracket_depth += 1
            current_item += char
        elif char == ']':
            bracket_depth -= 1
            current_item += char
        elif char == '(':
            paren_depth += 1
            current_item += char
        elif char == ')':
            paren_depth -= 1
            current_item += char
        elif char == ',' and bracket_depth == 0 and paren_depth == 0:
            # 괄호 밖의 콤마 -> 항목 구분자
            if current_item.strip():
                items.append(current_item.strip())
            current_item = ""
        else:
            current_item += char

    # 마지막 항목 추가
    if current_item.strip():
        items.append(current_item.strip())

    # 부칙 패턴 (조건/제한 - 이전 항목에 붙여야 함)
    # 예: (유탕·유처리식품에 한한다), (살균제품에 한함), (발효제품은 제외한다)
    def is_condition_note(text):
        """부칙/조건인지 확인 - '한한다', '제외한다', '한함', '제외', '한하며', '합', '해당된다' 등으로 끝나는 경우"""
        # 띄어쓰기 제거 후 확인 (제외 한다 -> 제외한다)
        text_no_space = text.replace(" ", "")
        endings = ('한한다)', '제외한다)', '한함)', '제외)', '한하며)', '제외하며)', '의합)', '합)', '해당된다)', '해당됨)', '생략한다)')
        return text_no_space.endswith(endings)

    # 카테고리 헤더 패턴
    # 예: (매월 1회 이상), (제품 생산 단위별), (비살균 제품) 등
    category_only_pattern = re.compile(r'^\([^)]+\)$')  # 카테고리만 있는 경우
    category_with_item_pattern = re.compile(r'^(\([^)]+\))\s*(.+)$')  # 카테고리+항목 붙은 경우
    # 항목(설명)(카테고리) 다음항목 형태: 탄화물(분말 제품에 한함)(제품 생산 단위별) 세균수
    item_note_category_next_pattern = re.compile(r'^([^(]+\([^)]+\))(\([^)]+\))\s+(.+)$')
    # 항목 끝에 연속 괄호 2개: 탄화물(분말 제품에 한함)(제품 생산 단위별)
    # 마지막 괄호가 카테고리, 그 앞은 항목+설명
    double_paren_ending_pattern = re.compile(r'^(.+\))(\([^)]+\))$')
    # 항목 중간에 카테고리가 있는 경우: 보존료(카테고리) 아질산이온
    # 괄호 뒤에 내용이 있으면 카테고리 헤더로 판단
    embedded_category_pattern = re.compile(r'^([^(]+)(\([^)]+\))\s+(.+)$')
    # 항목(괄호) 형태로 끝나는 경우: 보존료(카테고리) - 괄호가 '한함' 또는 '제외'로 끝나지 않으면 카테고리
    item_trailing_paren_pattern = re.compile(r'^([^(]+)(\([^)]+\))$')

    # 각 항목에 띄어쓰기 추가 후 포맷팅
    formatted_items = []
    for item in items:
        formatted_item = format_korean_spacing(item)

        # 카테고리 헤더만 있는 경우
        if category_only_pattern.match(formatted_item):
            # 부칙인지 확인 - 부칙이면 이전 항목에 붙임
            if is_condition_note(formatted_item):
                if category == "식품":
                    # 식품: 이전 항목에 부칙 직접 붙이기 (같은 줄)
                    if formatted_items and formatted_items[-1].startswith("• "):
                        formatted_items[-1] = formatted_items[-1] + formatted_item
                    else:
                        formatted_items.append(f"✓ {formatted_item}")
                else:
                    # 축산 등: 별도 줄에 ✏️와 함께 표시
                    if formatted_items:
                        formatted_items.append("")
                    formatted_items.append(f"✏️ {formatted_item}")
            else:
                # 카테고리 헤더로 처리
                if formatted_items:
                    formatted_items.append("")
                formatted_items.append(f"✏️ {formatted_item}")
        # 카테고리 헤더 + 항목이 붙어있는 경우 (예: "(비살균 제품)아질산이온")
        elif category_with_item_pattern.match(formatted_item):
            match = category_with_item_pattern.match(formatted_item)
            category_header = match.group(1)
            item_text = match.group(2).strip()

            # 부칙인지 확인
            if is_condition_note(category_header):
                if category == "식품":
                    # 식품: 부칙을 이전 항목에 직접 붙이고 항목은 새로 추가
                    if formatted_items and formatted_items[-1].startswith("• "):
                        formatted_items[-1] = formatted_items[-1] + category_header
                    if item_text:
                        formatted_items.append(f"• {item_text}")
                else:
                    # 축산 등: 별도 줄에 ✏️와 함께 표시
                    if formatted_items:
                        formatted_items.append("")
                    formatted_items.append(f"✏️ {category_header}")
                    if item_text:
                        formatted_items.append(f"• {item_text}")
            else:
                # 카테고리 헤더 추가
                if formatted_items:
                    formatted_items.append("")
                formatted_items.append(f"✏️ {category_header}")
                # 항목 추가
                if item_text:
                    formatted_items.append(f"• {item_text}")
        # 항목(설명)(카테고리) 다음항목 형태 (예: "탄화물(분말 제품에 한함)(제품 생산 단위별) 세균수")
        elif item_note_category_next_pattern.match(formatted_item):
            match = item_note_category_next_pattern.match(formatted_item)
            item_with_note = match.group(1).strip()
            category_header = match.group(2)
            next_item = match.group(3).strip()

            # 두 번째 괄호가 부칙인지 카테고리인지 확인
            if is_condition_note(category_header):
                if category == "식품":
                    # 식품: 부칙이면 전체를 하나의 항목으로 (같은 줄)
                    formatted_items.append(f"• {item_with_note}{category_header}")
                else:
                    # 축산 등: 별도 줄에 표시
                    formatted_items.append(f"• {item_with_note}")
                    formatted_items.append("")
                    formatted_items.append(f"✏️ {category_header}")
                if next_item:
                    formatted_items.append(f"• {next_item}")
            else:
                # 이전 카테고리의 마지막 항목
                if item_with_note:
                    formatted_items.append(f"• {item_with_note}")
                # 새 카테고리 헤더
                if formatted_items:
                    formatted_items.append("")
                formatted_items.append(f"✏️ {category_header}")
                # 새 카테고리의 첫 항목
                if next_item:
                    formatted_items.append(f"• {next_item}")
        # 항목 끝에 연속 괄호 2개 (예: "탄화물(설명)(카테고리)")
        elif double_paren_ending_pattern.match(formatted_item):
            match = double_paren_ending_pattern.match(formatted_item)
            item_with_note = match.group(1).strip()
            category_header = match.group(2)

            # 두 번째 괄호가 부칙인지 카테고리인지 확인
            if is_condition_note(category_header):
                if category == "식품":
                    # 식품: 부칙이면 전체를 하나의 항목으로 (같은 줄)
                    formatted_items.append(f"• {item_with_note}{category_header}")
                else:
                    # 축산 등: 별도 줄에 표시
                    formatted_items.append(f"• {item_with_note}")
                    formatted_items.append("")
                    formatted_items.append(f"✏️ {category_header}")
            else:
                # 이전 카테고리의 마지막 항목
                if item_with_note:
                    formatted_items.append(f"• {item_with_note}")
                # 새 카테고리 헤더
                if formatted_items:
                    formatted_items.append("")
                formatted_items.append(f"✏️ {category_header}")
        # 항목 중간에 카테고리가 있는 경우 (예: "보존료(카테고리) 아질산이온")
        elif embedded_category_pattern.match(formatted_item):
            match = embedded_category_pattern.match(formatted_item)
            before_item = match.group(1).strip()
            category_header = match.group(2)
            after_item = match.group(3).strip()

            # 괄호가 부칙인지 카테고리인지 확인
            if is_condition_note(category_header):
                # 부칙이면 하나의 항목으로 합침
                formatted_items.append(f"• {before_item} {category_header} {after_item}")
            else:
                # 이전 카테고리의 마지막 항목
                if before_item:
                    formatted_items.append(f"• {before_item}")
                # 새 카테고리 헤더
                if formatted_items:
                    formatted_items.append("")
                formatted_items.append(f"✏️ {category_header}")
                # 새 카테고리의 첫 항목
                if after_item:
                    formatted_items.append(f"• {after_item}")
        # 항목(괄호)로 끝나는 경우 - 괄호가 카테고리인지 설명인지 판단
        elif item_trailing_paren_pattern.match(formatted_item):
            match = item_trailing_paren_pattern.match(formatted_item)
            item_text = match.group(1).strip()
            paren_content = match.group(2)
            # 부칙인지 확인
            if is_condition_note(paren_content):
                # 부칙이면 전체를 하나의 항목으로
                formatted_items.append(f"• {formatted_item}")
            else:
                # 카테고리면 분리
                if item_text:
                    formatted_items.append(f"• {item_text}")
                if formatted_items:
                    formatted_items.append("")
                formatted_items.append(f"✏️ {paren_content}")
        else:
            formatted_items.append(f"• {formatted_item}")

    return '\n'.join(formatted_items)


def parse_data_with_links(data_text: str) -> list:
    """크롤링된 데이터에서 텍스트와 URL을 추출

    크롤러가 저장한 형식:
    [헤더] 값1{{URL:http://...}} | 값2{{URL:http://...}}

    Returns:
        list of dict: [{"header": str, "items": [{"text": str, "url": str or None}]}]
    """
    if not data_text:
        return []

    url_pattern = re.compile(r'\{\{URL:(.*?)\}\}')
    lines = data_text.split('\n')
    result = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # [헤더] 값1 | 값2 형식 처리
        if line.startswith('[') and ']' in line:
            bracket_end = line.index(']')
            header = line[1:bracket_end]
            values_part = line[bracket_end + 1:].strip()

            section = {"header": header, "items": []}

            if values_part:
                # | 로 구분된 값들
                values = [v.strip() for v in values_part.split('|') if v.strip()]
                for value in values:
                    # URL 추출
                    url_match = url_pattern.search(value)
                    if url_match:
                        url = url_match.group(1)
                        text = url_pattern.sub('', value).strip()
                        # "자세히 보기" 텍스트 제거
                        text = re.sub(r'자세히\s*보기', '', text).strip()
                        section["items"].append({"text": text, "url": url})
                    else:
                        text = format_korean_spacing(value)
                        section["items"].append({"text": text, "url": None})

            result.append(section)
        else:
            # 일반 텍스트
            url_match = url_pattern.search(line)
            if url_match:
                url = url_match.group(1)
                text = url_pattern.sub('', line).strip()
                text = re.sub(r'자세히\s*보기', '', text).strip()
                result.append({"header": None, "items": [{"text": text, "url": url}]})
            else:
                result.append({"header": None, "items": [{"text": format_korean_spacing(line), "url": None}]})

    return result


def has_links_in_data(data_text: str) -> bool:
    """데이터에 URL이 포함되어 있는지 확인"""
    return '{{URL:' in data_text if data_text else False


def format_crawled_data(data_text: str) -> str:
    """크롤링된 데이터를 가독성 있게 포맷팅

    크롤러가 저장한 형식:
    [헤더] 값1 | 값2 | 값3
    또는
    [헤더]
      • 항목1
      • 항목2

    변환 후:
    📌 헤더
      • 값1
      • 값2
    """
    if not data_text:
        return data_text

    # URL 패턴 제거 (텍스트만 표시할 때)
    url_pattern = re.compile(r'\{\{URL:.*?\}\}')

    lines = data_text.split('\n')
    result = []

    for line in lines:
        original_line = line
        line = line.strip()
        if not line:
            continue

        # 이미 bullet point로 시작하는 라인 (크롤러에서 이미 포맷된 경우)
        if line.startswith('•') or original_line.startswith('  •'):
            clean_line = url_pattern.sub('', line).strip()
            clean_line = re.sub(r'자세히\s*보기', '', clean_line).strip()
            if clean_line:
                # • 로 시작하면 그대로 유지
                if clean_line.startswith('•'):
                    result.append(f"  {clean_line}")
                else:
                    result.append(f"  • {clean_line}")
            continue

        # [헤더] 값1 | 값2 형식 처리
        if line.startswith('[') and ']' in line:
            bracket_end = line.index(']')
            header = line[1:bracket_end]
            values_part = line[bracket_end + 1:].strip()

            # 헤더 추가
            result.append(f"\n📌 {header}")

            if values_part:
                # | 로 구분된 값들을 bullet point로
                values = [v.strip() for v in values_part.split('|') if v.strip()]
                for value in values:
                    # URL 패턴 제거
                    clean_value = url_pattern.sub('', value).strip()
                    # "자세히 보기" 텍스트 제거
                    clean_value = re.sub(r'자세히\s*보기', '', clean_value).strip()
                    if clean_value:
                        formatted_value = format_korean_spacing(clean_value)
                        result.append(f"  • {formatted_value}")
        else:
            # 일반 텍스트는 그대로 (띄어쓰기 적용)
            clean_line = url_pattern.sub('', line).strip()
            clean_line = re.sub(r'자세히\s*보기', '', clean_line).strip()
            if clean_line:
                result.append(format_korean_spacing(clean_line))

    # 첫 줄의 불필요한 줄바꿈 제거
    formatted = '\n'.join(result)
    return formatted.strip()


def format_nutrition_component_data(data_text: str) -> str:
    """9대/14대 영양성분 데이터를 특별 형식으로 포맷팅

    - 구분 섹션 제거
    - 일수와 금액을 결합 (예: 3일 500,000원)
    - 긴급 안내 메시지 추가
    - VAT 별도 표시
    """
    if not data_text:
        return data_text

    lines = data_text.split('\n')
    days_values = []
    price_values = []
    note_values = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # [헤더] 값1 | 값2 형식 처리
        if line.startswith('[') and ']' in line:
            bracket_end = line.index(']')
            header = line[1:bracket_end]
            values_part = line[bracket_end + 1:].strip()

            if values_part:
                values = [v.strip() for v in values_part.split('|') if v.strip()]

                if header == "일수":
                    days_values = values
                elif header == "금액":
                    price_values = values
                elif header == "비고":
                    note_values = values
                # 구분 섹션은 무시

    result = []

    # 일수 및 금액 결합
    if days_values and price_values:
        result.append("📌 일수 및 금액")
        for i in range(min(len(days_values), len(price_values))):
            day = days_values[i]
            price = price_values[i]
            result.append(f"  • {day} {price}원")

        # 긴급 안내 메시지
        result.append("")
        result.append("* 긴급에 해당하는 경우 사전에 긴급 일정을 협의해주세요.")

    # VAT 별도 표시
    result.append("")
    result.append("* VAT 별도")

    return '\n'.join(result)


def find_food_type_category(food_type: str) -> dict:
    """식품유형을 검색하여 해당 카테고리 정보 반환

    Returns:
        dict with keys:
        - found: bool
        - category: str (기존시행, 2021개정, 2024개정, 제외대상)
        - info: dict (해당 카테고리의 상세 정보)
        - 제외사유: str (제외대상인 경우)
    """
    food_type = food_type.strip()

    # 기존시행 검색
    if food_type in NUTRITION_LABEL_CATEGORIES["기존시행"]["식품유형"]:
        return {
            "found": True,
            "category": "기존시행",
            "info": NUTRITION_LABEL_CATEGORIES["기존시행"]
        }

    # 2021개정 검색
    if food_type in NUTRITION_LABEL_CATEGORIES["2021개정"]["식품유형"]:
        return {
            "found": True,
            "category": "2021개정",
            "info": NUTRITION_LABEL_CATEGORIES["2021개정"]
        }

    # 2024개정 검색
    if food_type in NUTRITION_LABEL_CATEGORIES["2024개정"]["식품유형"]:
        return {
            "found": True,
            "category": "2024개정",
            "info": NUTRITION_LABEL_CATEGORIES["2024개정"]
        }

    # 제외대상 검색
    for reason, food_list in NUTRITION_LABEL_CATEGORIES["제외대상"].items():
        if food_type in food_list:
            return {
                "found": True,
                "category": "제외대상",
                "제외사유": reason
            }

    # 부분 일치 검색 (유사 결과)
    similar_results = []
    for category_name, category_data in NUTRITION_LABEL_CATEGORIES.items():
        if category_name == "제외대상":
            for reason, food_list in category_data.items():
                for item in food_list:
                    if food_type in item or item in food_type:
                        similar_results.append((item, f"제외대상({reason})"))
        else:
            food_list = category_data.get("식품유형", [])
            for item in food_list:
                if food_type in item or item in food_type:
                    similar_results.append((item, category_name))

    if similar_results:
        return {
            "found": False,
            "similar": similar_results[:5]  # 최대 5개
        }

    return {"found": False}


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


def get_question_label(category: str, menu_item: str) -> str:
    """카테고리와 메뉴 항목에서 Q번호를 조회하여 버튼 라벨 생성

    Args:
        category: 카테고리 (예: "영양성분검사", "소비기한설정")
        menu_item: 메뉴 항목 (예: "검사종류", "가속실험")

    Returns:
        버튼 라벨 (예: "🔗 Q.1번 참고")
    """
    if not category or not menu_item:
        return "🔗 자세히 보기"

    # DISPLAY_Q_NUMBER에서 Q번호 조회
    q_number = DISPLAY_Q_NUMBER.get(category, {}).get(menu_item)
    if q_number:
        return f"🔗 Q.{q_number}번 참고"

    return "🔗 자세히 보기"


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


def make_response_with_link(text: str, link_label: str, link_url: str, buttons: list = None):
    """카카오 챗봇 응답 형식 생성 (링크 버튼 포함)

    Args:
        text: 응답 텍스트
        link_label: 링크 버튼 라벨 (예: "자세히 보기")
        link_url: 링크 URL
        buttons: 하단 퀵리플라이 버튼 리스트
    """
    response = {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "textCard": {
                        "text": text,
                        "buttons": [
                            {
                                "label": link_label,
                                "action": "webLink",
                                "webLinkUrl": link_url
                            }
                        ]
                    }
                }
            ]
        }
    }

    if buttons:
        response["template"]["quickReplies"] = [
            {"label": btn, "action": "message", "messageText": btn}
            for btn in buttons
        ]

    return jsonify(response)


def make_carousel_response(cards: list, quick_replies: list = None):
    """카카오 챗봇 카드 캐러셀 응답 형식 생성

    Args:
        cards: 카드 리스트. 각 카드는 dict로 {"title": str, "description": str, "buttons": list, "thumbnail": str(optional)}
        quick_replies: 하단 퀵리플라이 버튼 리스트
    """
    items = []
    for card in cards:
        item = {
            "title": card.get("title", ""),
            "description": card.get("description", ""),
            "buttons": [
                {
                    "label": btn["label"],
                    "action": "message",
                    "messageText": btn.get("messageText", btn["label"])
                }
                for btn in card.get("buttons", [])
            ]
        }
        # 썸네일 이미지가 있으면 추가
        if card.get("thumbnail"):
            item["thumbnail"] = {"imageUrl": card["thumbnail"]}
        items.append(item)

    response = {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "carousel": {
                        "type": "basicCard",
                        "items": items
                    }
                }
            ]
        }
    }

    if quick_replies:
        response["template"]["quickReplies"] = [
            {"label": btn, "action": "message", "messageText": btn}
            for btn in quick_replies
        ]

    return jsonify(response)


def make_list_card_response(header: str, items: list, quick_replies: list = None):
    """카카오 챗봇 ListCard 응답 형식 생성 (링크 버튼 포함)

    Args:
        header: 리스트 카드 헤더 텍스트
        items: 아이템 리스트. [{"text": str, "url": str or None}, ...]
        quick_replies: 하단 퀵리플라이 버튼 리스트
    """
    list_items = []
    for item in items[:5]:  # 최대 5개까지만 표시
        list_item = {
            "title": item["text"]
        }
        if item.get("url"):
            list_item["link"] = {"web": item["url"]}
        list_items.append(list_item)

    response = {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "listCard": {
                        "header": {"title": header},
                        "items": list_items
                    }
                }
            ]
        }
    }

    if quick_replies:
        response["template"]["quickReplies"] = [
            {"label": btn, "action": "message", "messageText": btn}
            for btn in quick_replies
        ]

    return jsonify(response)


def make_carousel_with_links_response(title: str, data_sections: list, quick_replies: list = None):
    """URL이 포함된 데이터를 카드 캐러셀로 표시

    Args:
        title: 전체 제목
        data_sections: parse_data_with_links()의 결과
        quick_replies: 하단 퀵리플라이 버튼 리스트
    """
    cards = []

    for section in data_sections:
        if not section.get("items"):
            continue

        header = section.get("header", "")

        # 각 아이템을 개별 카드로 (링크가 있는 경우)
        for item in section["items"]:
            if item.get("url"):
                card = {
                    "title": item["text"][:40] if len(item["text"]) > 40 else item["text"],
                    "description": header if header else "",
                    "buttons": [
                        {
                            "label": "🔗 자세히 보기",
                            "action": "webLink",
                            "webLinkUrl": item["url"]
                        }
                    ]
                }
                cards.append(card)

    if not cards:
        # 링크 없는 경우 일반 텍스트 반환
        return None

    # 최대 10개 카드
    cards = cards[:10]

    response = {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "carousel": {
                        "type": "basicCard",
                        "items": cards
                    }
                }
            ]
        }
    }

    if quick_replies:
        response["template"]["quickReplies"] = [
            {"label": btn, "action": "message", "messageText": btn}
            for btn in quick_replies
        ]

    return jsonify(response)


# 카드 썸네일 이미지 URL (버전 파라미터로 캐시 무효화)
CARD_IMAGE_BASE_URL = "http://14.7.14.31:5000/static/images/"
CARD_IMAGE_VERSION = "?v=2"

# 검사 분야 메뉴 구조 정의
INSPECTION_MENU = {
    "cards": [
        {
            "title": "",
            "description": "",
            "thumbnail": f"{CARD_IMAGE_BASE_URL}card_01.jpg{CARD_IMAGE_VERSION}",
            "buttons": [
                {"label": "자가품질검사"},
                {"label": "영양성분검사"},
                {"label": "소비기한설정"}
            ]
        },
        {
            "title": "",
            "description": "",
            "thumbnail": f"{CARD_IMAGE_BASE_URL}card_02.jpg{CARD_IMAGE_VERSION}",
            "buttons": [
                {"label": "항생물질"},
                {"label": "잔류농약"},
                {"label": "방사능"}
            ]
        },
        {
            "title": "",
            "description": "",
            "thumbnail": f"{CARD_IMAGE_BASE_URL}card_03.jpg{CARD_IMAGE_VERSION}",
            "buttons": [
                {"label": "비건"},
                {"label": "할랄"},
                {"label": "동물DNA"}
            ]
        },
        {
            "title": "",
            "description": "",
            "thumbnail": f"{CARD_IMAGE_BASE_URL}card_04.jpg{CARD_IMAGE_VERSION}",
            "buttons": [
                {"label": "알레르기"},
                {"label": "글루텐Free"},
                {"label": "이물질검사"}
            ]
        },
        {
            "title": "",
            "description": "",
            "thumbnail": f"{CARD_IMAGE_BASE_URL}card_05.jpg{CARD_IMAGE_VERSION}",
            "buttons": [
                {"label": "홈페이지안내"},
                {"label": "성적서문의"},
                {"label": "시료접수안내"}
            ]
        }
    ],
    # 하위 메뉴 정의
    "submenus": {
        "자가품질검사": {
            "title": "자가품질검사",
            "buttons": ["식품", "축산", "검사주기알림", "검사수수료", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "영양성분검사": {
            "title": "영양성분검사",
            "buttons": ["검사종류", "표시대상확인", "계산도우미", "영양표시 도우미", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "계산도우미": {
            "title": "계산도우미",
            "buttons": ["배합 함량", "당알코올 계산", "표시단위 계산", "이전", "처음으로"],
            "parent": "영양성분검사"
        },
        "영양표시 도우미": {
            "title": "영양표시 도우미",
            "buttons": ["표시단위 계산", "강조표시 확인", "기준치 조회", "%기준치 계산", "표시값 변환", "이전", "처음으로"],
            "parent": "영양성분검사"
        },
        "소비기한설정": {
            "title": "소비기한설정",
            "buttons": ["가속실험", "실측실험", "검사수수료", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "항생물질": {
            "title": "항생물질",
            "buttons": ["검사종류", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "잔류농약": {
            "title": "잔류농약",
            "buttons": ["검사종류", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "방사능": {
            "title": "방사능 검사",
            "buttons": ["검사안내", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "비건": {
            "title": "비건 검사",
            "buttons": ["검사안내", "사용키트", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "할랄": {
            "title": "할랄 검사",
            "buttons": ["검사안내", "사용키트", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "동물DNA": {
            "title": "동물DNA 검사",
            "buttons": ["검사안내", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "알레르기": {
            "title": "알레르기 검사",
            "buttons": ["분석종류", "RT-PCR", "Elisa", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "글루텐Free": {
            "title": "글루텐Free 검사",
            "buttons": ["Free기준", "키트", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "이물질검사": {
            "title": "이물질검사",
            "buttons": ["이물분석장비", "금속", "비닐/고무/플라스틱", "기타", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "홈페이지안내": {
            "title": "홈페이지 안내",
            "buttons": ["견적서", "의뢰서작성", "할인쿠폰", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "성적서문의": {
            "title": "성적서 문의",
            "buttons": ["외국어", "발급문의", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "시료접수안내": {
            "title": "시료접수 안내",
            "buttons": ["시료접수", "방문수거", "오시는길", "이전", "처음으로"],
            "parent": "검사분야"
        },
        # 이물질검사 - 기타 하위 메뉴
        "기타": {
            "title": "이물질검사 - 기타",
            "buttons": ["손톱", "뼈", "원료의일부", "탄화물", "이전", "처음으로"],
            "parent": "이물질검사"
        },
        # 자가품질검사 - 식품/축산 하위 메뉴
        "자가품질검사_식품": {
            "title": "자가품질검사 - 식품",
            "buttons": ["검사주기", "검사항목", "검사수수료", "이전", "처음으로"],
            "parent": "자가품질검사"
        },
        "자가품질검사_축산": {
            "title": "자가품질검사 - 축산",
            "buttons": ["검사주기", "검사항목", "검사수수료", "이전", "처음으로"],
            "parent": "자가품질검사"
        },
        # 영양성분검사 > 검사종류 하위 메뉴
        "영양성분검사_검사종류": {
            "title": "영양성분검사 - 검사종류",
            "buttons": ["영양표시 종류", "9대 영양성분", "14대 영양성분", "이전", "처음으로"],
            "parent": "영양성분검사"
        }
    },
    # 말단 메뉴 응답 (텍스트 응답)
    "responses": {
        "검사주기알림": {
            "text": "🔔 검사주기알림 서비스\n\n자가품질검사 주기에 맞춰 알림을 받으실 수 있습니다.\n\n📞 문의: 02-XXX-XXXX\n🔗 홈페이지: www.biofl.co.kr"
        },
        "검사수수료": {
            "text": "💰 검사수수료 안내\n\n검사 항목 및 수량에 따라 수수료가 상이합니다.\n\n🔗 홈페이지에서 견적서를 확인하세요.\n📞 문의: 02-XXX-XXXX"
        },
        "검사종류": {
            "text": "🔬 검사종류 안내\n\n다양한 검사 방법을 제공합니다.\n\n자세한 내용은 홈페이지를 참고하시거나 문의해주세요.\n\n🔗 www.biofl.co.kr\n📞 문의: 02-XXX-XXXX"
        },
        "검사안내": {
            "text": "📋 검사안내\n\n검사 진행 절차 및 준비물 안내입니다.\n\n1. 시료 준비\n2. 의뢰서 작성\n3. 시료 접수\n4. 검사 진행\n5. 성적서 발급\n\n📞 문의: 02-XXX-XXXX"
        },
        "사용키트": {
            "text": "🧪 사용키트 안내\n\n검사에 사용되는 키트 정보입니다.\n\n자세한 내용은 홈페이지를 참고하세요.\n\n🔗 www.biofl.co.kr"
        },
        "키트": {
            "text": "🧪 글루텐 검사 키트\n\n글루텐 검출을 위한 전용 키트를 사용합니다.\n\n자세한 내용은 문의해주세요.\n\n📞 문의: 02-XXX-XXXX"
        },
        "금속류": {
            "text": "🔩 금속류 이물검사\n\n식품 내 금속 이물질 검출 검사입니다.\n\n• 철, 스테인리스 등\n• X-ray 또는 금속탐지기 활용\n\n📞 문의: 02-XXX-XXXX"
        },
        "고무/플라스틱": {
            "text": "🧴 고무/플라스틱 이물검사\n\n식품 내 고무 및 플라스틱 이물질 분석입니다.\n\n• FT-IR 분석\n• 재질 동정\n\n📞 문의: 02-XXX-XXXX"
        },
        "손톱": {
            "text": "💅 손톱 이물검사\n\n이물질이 손톱인지 확인하는 검사입니다.\n\n• 현미경 분석\n• DNA 분석 가능\n\n📞 문의: 02-XXX-XXXX"
        },
        "뼈": {
            "text": "🦴 뼈 이물검사\n\n이물질이 동물 뼈인지 확인하는 검사입니다.\n\n• 종 판별 가능\n• DNA 분석\n\n📞 문의: 02-XXX-XXXX"
        },
        "탄화물": {
            "text": "⚫ 탄화물 이물검사\n\n탄화된 이물질 분석입니다.\n\n• 성분 분석\n• 원인 추정\n\n📞 문의: 02-XXX-XXXX"
        },
        "원료의일부": {
            "text": "🌾 원료의일부 확인\n\n이물질이 원료의 일부인지 확인합니다.\n\n• 성분 비교 분석\n• 원료 동정\n\n📞 문의: 02-XXX-XXXX"
        },
        "모르겠음": {
            "text": "❓ 이물질 종류 모름\n\n이물질의 종류를 모르실 경우, 검체를 보내주시면 분석해드립니다.\n\n• 종합 분석\n• 재질 동정\n\n📞 문의: 02-XXX-XXXX"
        },
        "견적서": {
            "text": "📄 견적서 안내\n\n홈페이지에서 온라인 견적서를 확인하실 수 있습니다.\n\n🔗 www.biofl.co.kr > 견적서"
        },
        "의뢰서작성": {
            "text": "📝 의뢰서 작성\n\n검사 의뢰서는 홈페이지에서 작성 가능합니다.\n\n🔗 www.biofl.co.kr > 의뢰서 작성"
        },
        "할인쿠폰": {
            "text": "🎫 할인쿠폰 안내\n\n다양한 할인 혜택을 제공합니다.\n\n홈페이지에서 쿠폰을 확인하세요.\n\n🔗 www.biofl.co.kr"
        },
        "외국어": {
            "text": "🌍 외국어 성적서\n\n영문 성적서 발급이 가능합니다.\n\n• 영문 성적서\n• 기타 언어 문의\n\n📞 문의: 02-XXX-XXXX"
        },
        "발급문의": {
            "text": "📋 성적서 발급 문의\n\n성적서 발급 관련 문의사항은 아래로 연락주세요.\n\n📞 문의: 02-XXX-XXXX\n📧 이메일: info@biofl.co.kr"
        },
        "시료접수": {
            "text": "📦 시료접수 안내\n\n시료 접수 방법:\n\n1. 홈페이지에서 의뢰서 작성\n2. 시료 포장\n3. 택배 또는 방문 접수\n\n📞 문의: 02-XXX-XXXX"
        },
        "방문수거": {
            "text": "🚗 방문수거 서비스\n\n직접 방문하여 시료를 수거해드립니다.\n\n• 수도권 지역 가능\n• 사전 예약 필요\n\n📞 예약: 02-XXX-XXXX"
        },
        "오시는길": {
            "text": "📍 오시는길\n\n바이오푸드랩\n\n주소: (상세 주소)\n\n🚇 지하철: OO역 O번 출구\n🚌 버스: OO번\n🚗 주차: 건물 내 주차장 이용\n\n📞 문의: 02-XXX-XXXX"
        },
    }
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


def reset_user_state(user_id: str):
    """사용자 상태 초기화"""
    user_state[user_id] = {"히스토리": []}


def save_to_history(user_data: dict):
    """현재 상태를 히스토리에 저장"""
    if "히스토리" not in user_data:
        user_data["히스토리"] = []

    # 현재 상태 복사 (히스토리 제외)
    current_state = {k: v for k, v in user_data.items() if k != "히스토리"}

    # 빈 상태는 저장하지 않음
    if current_state:
        user_data["히스토리"].append(current_state.copy())


def go_back(user_data: dict) -> dict:
    """이전 상태로 복원하고 복원된 상태 반환"""
    if "히스토리" not in user_data or not user_data["히스토리"]:
        return None

    # 마지막 히스토리 가져오기
    previous_state = user_data["히스토리"].pop()

    # 현재 상태 초기화 후 이전 상태 복원
    history = user_data["히스토리"]
    user_data.clear()
    user_data["히스토리"] = history
    user_data.update(previous_state)

    return previous_state


@app.route('/health', methods=['GET'])
def health_check():
    """헬스체크 엔드포인트"""
    last_crawl = get_last_crawl_time()
    return jsonify({
        "status": "ok",
        "last_crawl": str(last_crawl) if last_crawl else "never"
    })


def _calculate_serving_display(user_data: dict):
    """영양성분 표시단위 계산 결과 반환"""
    food_group = user_data.get("영양표시_식품군", "")
    food_type = user_data.get("영양표시_식품유형", "")
    food_subtype = user_data.get("영양표시_세부유형", "")
    total_weight = user_data.get("영양표시_총내용량", 0)
    has_pieces = user_data.get("영양표시_낱개여부", False)
    piece_weight = user_data.get("영양표시_낱개중량", 0)
    serving_size = user_data.get("영양표시_1회섭취참고량", 100)
    unit = user_data.get("영양표시_단위", "g")

    # 입력 정보 요약
    food_name = f"{food_type}"
    if food_subtype:
        food_name += f" ({food_subtype})"

    result_text = f"""📊 영양성분 표시단위 계산 결과

━━━━━━━━━━━━━━━
📌 입력 정보
━━━━━━━━━━━━━━━
• 식품군: {food_group}
• 식품유형: {food_name}
• 총 내용량: {total_weight}g(ml)
• 1회 섭취참고량: {serving_size}{unit}"""

    if has_pieces:
        result_text += f"\n• 낱개 중량: {piece_weight}g(ml)"

    result_text += "\n\n━━━━━━━━━━━━━━━\n📋 표시 방법 안내\n━━━━━━━━━━━━━━━\n"

    # 표시 규칙 판단
    display_methods = []
    reasons = []

    # 기본: 총 내용량(1포장)당 표시
    display_methods.append("✅ 1포장당(총 내용량당) 표시 - 기본")
    reasons.append("• 모든 제품은 기본적으로 총 내용량당 표시 가능")

    # 규칙 2: 총 내용량 > 100g AND 총 내용량 > 1회섭취참고량×3 → 100g당 표시 가능
    if total_weight > 100 and total_weight > serving_size * 3:
        display_methods.append("✅ 100g(ml)당 표시 가능")
        reasons.append(f"• 총 내용량({total_weight}g) > 100g 이고")
        reasons.append(f"  총 내용량({total_weight}g) > 1회섭취참고량×3 ({serving_size}×3={serving_size*3}g)")

    # 낱개 포장이 있는 경우
    if has_pieces and piece_weight > 0:
        # 규칙 3: 낱개 ≥ 100g 또는 낱개 ≥ 1회섭취참고량 → 낱개당 표시
        if piece_weight >= 100 or piece_weight >= serving_size:
            display_methods.append("✅ 낱개당 표시 가능")
            if piece_weight >= 100:
                reasons.append(f"• 낱개 중량({piece_weight}g) ≥ 100g")
            if piece_weight >= serving_size:
                reasons.append(f"• 낱개 중량({piece_weight}g) ≥ 1회섭취참고량({serving_size}g)")

        # 규칙 4: 낱개 < 100g AND 낱개 < 1회섭취참고량 → 낱개당 표시 가능 (단, 총내용량당 병행표기 필요)
        elif piece_weight < 100 and piece_weight < serving_size:
            display_methods.append("⚠️ 낱개당 표시 가능 (조건부)")
            reasons.append(f"• 낱개 중량({piece_weight}g) < 100g 이고")
            reasons.append(f"  낱개 중량({piece_weight}g) < 1회섭취참고량({serving_size}g)")
            reasons.append("• ⚠️ 총 내용량당 영양성분도 함께 표시 필요!")

    result_text += "\n".join(display_methods)
    result_text += "\n\n━━━━━━━━━━━━━━━\n📖 판단 근거\n━━━━━━━━━━━━━━━\n"
    result_text += "\n".join(reasons)

    # 표시 예시
    result_text += "\n\n━━━━━━━━━━━━━━━\n📝 표시 예시\n━━━━━━━━━━━━━━━\n"

    if has_pieces and piece_weight > 0:
        pieces_count = int(total_weight / piece_weight)
        result_text += f"• 총 내용량: {total_weight}g ({piece_weight}g × {pieces_count}개)\n"
        if piece_weight >= 100 or piece_weight >= serving_size:
            result_text += f"• 영양성분: 낱개({piece_weight}g)당 표시"
        else:
            result_text += f"• 영양성분: 낱개({piece_weight}g)당 + 총 내용량({total_weight}g)당 병행 표시"
    else:
        if total_weight > 100 and total_weight > serving_size * 3:
            result_text += f"• 영양성분: 100g(ml)당 또는 총 내용량({total_weight}g)당 표시"
        else:
            result_text += f"• 영양성분: 총 내용량({total_weight}g)당 표시"

    # 상태 초기화
    user_data.pop("영양표시_모드", None)
    user_data.pop("영양표시_단계", None)
    user_data.pop("영양표시_식품군", None)
    user_data.pop("영양표시_식품유형", None)
    user_data.pop("영양표시_세부유형", None)
    user_data.pop("영양표시_총내용량", None)
    user_data.pop("영양표시_낱개여부", None)
    user_data.pop("영양표시_낱개중량", None)
    user_data.pop("영양표시_1회섭취참고량", None)
    user_data.pop("영양표시_단위", None)

    return make_response(result_text, ["표시단위 계산", "이전", "처음으로"])


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
            user_state[user_id] = {"히스토리": []}
        user_data = user_state[user_id]
        if "히스토리" not in user_data:
            user_data["히스토리"] = []

        # 기본 버튼
        default_buttons = ["검사주기", "검사항목", "처음으로"]

        # "처음으로" 또는 "종료" 입력 시 상태 초기화
        if user_input in ["처음으로", "종료"]:
            reset_user_state(user_id)
            return make_response(
                "안녕하세요! 바이오푸드랩 챗봇[바푸]입니다.\n\n🔬 검사분야 버튼으로 분야별 검색 가능\n⚡ 퀵 메뉴 검사주기, 검사항목으로 빠른 조회\n🧮 영양성분 메뉴에서 함량, 당알코올 함량 계산\n💡 버튼 외 자연어로 질문하셔도 됩니다!\n\n자세한 상담은 채팅방 메뉴에서 \"채널 이동\"을 누르시면 상담이 가능한 채널로 이동합니다.\n(업무 시간 09:00~17:30)\n\n개발자 : @BP_K",
                ["검사분야", "검사주기", "검사항목"]
            )

        # ===== 관리자 명령어 처리 (! 로 시작) =====
        if user_input.startswith("!"):
            admin_result = handle_admin_command(user_id, user_input)
            return make_response(admin_result, ["처음으로"])

        # "이전" 버튼 처리
        if user_input == "이전":
            # NLP 모드에서 이전 -> 검색 결과 목록으로
            if user_data.get("nlp_모드"):
                nlp_results = user_data.get("nlp_검색결과", [])
                nlp_remaining = user_data.get("nlp_남은횟수", 0)

                if nlp_remaining > 0 and nlp_results:
                    user_data["nlp_남은횟수"] = nlp_remaining - 1
                    user_data.pop("nlp_선택", None)

                    # 선택 완료된 항목 표시
                    selected_list = user_data.get("nlp_선택완료", [])

                    response_text = "🔍 검색 결과 목록:\n\n"
                    buttons = []
                    for i, r in enumerate(nlp_results, 1):
                        title_short = r['title'][:35] + "..." if len(r['title']) > 35 else r['title']
                        mark = " ✓" if i in selected_list else ""
                        response_text += f"{i}. {title_short}{mark}\n"
                        buttons.append(str(i))

                    response_text += f"\n번호를 선택해주세요. (남은 선택: {user_data['nlp_남은횟수']}회)"
                    buttons.extend(["처음으로"])

                    return make_response(response_text, buttons)
                else:
                    # 남은 횟수 소진 -> NLP 모드 종료
                    user_data.pop("nlp_모드", None)
                    user_data.pop("nlp_검색결과", None)
                    user_data.pop("nlp_전체결과", None)
                    user_data.pop("nlp_현재페이지", None)
                    user_data.pop("nlp_남은횟수", None)
                    user_data.pop("nlp_선택", None)
                    user_data.pop("nlp_선택완료", None)
                    return make_response(
                        "검색이 종료되었습니다.\n\n원하시는 서비스를 선택해주세요.",
                        ["검사분야", "검사주기", "검사항목"]
                    )

            # 계산 모드에서 이전 -> 계산도우미 메뉴로
            if user_data.get("계산_모드"):
                user_data.pop("계산_모드", None)
                user_data.pop("계산_단계", None)
                user_data.pop("총중량", None)
                user_data.pop("당알코올_배합함량", None)
                user_data.pop("당알코올_원재료함량", None)
                user_data["현재_메뉴"] = "계산도우미"
                return make_response(
                    "📊 계산도우미\n\n원하시는 계산 방법을 선택해주세요.",
                    ["배합 함량", "당알코올 계산", "이전", "처음으로"]
                )

            # 표시대상확인 모드에서 이전 -> 영양성분검사 메뉴로
            if user_data.get("표시대상_모드"):
                user_data.pop("표시대상_모드", None)
                user_data.pop("표시대상_단계", None)
                user_data.pop("표시대상_식품유형", None)
                user_data.pop("표시대상_카테고리", None)
                user_data.pop("표시대상_배추김치", None)
                user_data.pop("표시대상_개정정보", None)
                submenu = INSPECTION_MENU["submenus"]["영양성분검사"]
                user_data["현재_메뉴"] = "영양성분검사"
                return make_response(
                    f"📋 {submenu['title']}\n\n원하시는 항목을 선택해주세요.",
                    submenu["buttons"]
                )

            # 영양표시(표시단위 계산) 모드에서 이전 -> 영양성분검사 메뉴로
            if user_data.get("영양표시_모드"):
                user_data.pop("영양표시_모드", None)
                user_data.pop("영양표시_단계", None)
                user_data.pop("영양표시_식품군", None)
                user_data.pop("영양표시_식품유형", None)
                user_data.pop("영양표시_세부유형", None)
                user_data.pop("영양표시_총내용량", None)
                user_data.pop("영양표시_낱개여부", None)
                user_data.pop("영양표시_낱개중량", None)
                user_data.pop("영양표시_1회섭취참고량", None)
                user_data.pop("영양표시_단위", None)
                submenu = INSPECTION_MENU["submenus"]["영양성분검사"]
                user_data["현재_메뉴"] = "영양성분검사"
                return make_response(
                    f"📋 {submenu['title']}\n\n원하시는 항목을 선택해주세요.",
                    submenu["buttons"]
                )

            # 강조표시 확인 모드에서 이전 -> 단계별 뒤로가기
            if user_data.get("강조표시_모드"):
                step = user_data.get("강조표시_단계")

                # 함량 입력 단계 -> 영양소 선택으로
                if step == "함량_입력":
                    claim_type = user_data.get("강조표시_용어")
                    nutrients = user_data.get("강조표시_영양소_목록", [])
                    user_data["강조표시_단계"] = "영양소_선택"
                    user_data.pop("강조표시_영양소", None)
                    return make_response(
                        f"""📊 [{claim_type}] 강조표시

━━━━━━━━━━━━━━━
🔽 영양소를 선택해주세요
━━━━━━━━━━━━━━━""",
                        nutrients + ["이전", "처음으로"]
                    )

                # 비타민/무기질 선택 단계 -> 영양소 선택으로
                if step in ["비타민_선택", "무기질_선택"]:
                    claim_type = user_data.get("강조표시_용어")
                    nutrients = user_data.get("강조표시_영양소_목록", [])
                    user_data["강조표시_단계"] = "영양소_선택"
                    return make_response(
                        f"""📊 [{claim_type}] 강조표시

━━━━━━━━━━━━━━━
🔽 영양소를 선택해주세요
━━━━━━━━━━━━━━━""",
                        nutrients + ["이전", "처음으로"]
                    )

                # 영양소 선택 단계 -> 용어 선택으로
                if step == "영양소_선택":
                    user_data["강조표시_단계"] = "용어_선택"
                    user_data.pop("강조표시_용어", None)
                    user_data.pop("강조표시_영양소_목록", None)
                    return make_response(
                        """📊 영양강조표시 확인

강조표시 가능 여부를 확인해드립니다.

━━━━━━━━━━━━━━━
📌 강조표시 용어 선택
━━━━━━━━━━━━━━━
나) 영양성분 함량 강조표시

(1) 무, 저
(2) 함유(급원)
(3) 고(풍부)

마) 무가당, 무첨가

🔽 사용하고자 하는 용어를 선택해주세요""",
                        ["무", "저", "함유(급원)", "고(풍부)", "무가당", "무첨가", "이전", "처음으로"]
                    )

                # 용어 선택 단계 -> 영양표시 도우미 메뉴로
                user_data.pop("강조표시_모드", None)
                user_data.pop("강조표시_단계", None)
                user_data.pop("강조표시_영양소", None)
                user_data.pop("강조표시_용어", None)
                user_data.pop("강조표시_영양소_목록", None)
                user_data["현재_메뉴"] = "영양표시 도우미"
                return make_response(
                    "📊 영양표시 도우미\n\n원하시는 기능을 선택해주세요.",
                    ["표시단위 계산", "강조표시 확인", "기준치 조회", "%기준치 계산", "표시값 변환", "이전", "처음으로"]
                )

            # 기준치 조회 모드에서 이전 -> 영양표시 도우미 메뉴로
            if user_data.get("기준치조회_모드"):
                user_data.pop("기준치조회_모드", None)
                user_data.pop("기준치조회_단계", None)
                user_data["현재_메뉴"] = "영양표시 도우미"
                return make_response(
                    "📊 영양표시 도우미\n\n원하시는 기능을 선택해주세요.",
                    ["표시단위 계산", "강조표시 확인", "기준치 조회", "%기준치 계산", "표시값 변환", "이전", "처음으로"]
                )

            # %기준치 계산 모드에서 이전 -> 영양표시 도우미 메뉴로
            if user_data.get("퍼센트기준치_모드"):
                user_data.pop("퍼센트기준치_모드", None)
                user_data.pop("퍼센트기준치_단계", None)
                user_data.pop("퍼센트기준치_영양소", None)
                user_data["현재_메뉴"] = "영양표시 도우미"
                return make_response(
                    "📊 영양표시 도우미\n\n원하시는 기능을 선택해주세요.",
                    ["표시단위 계산", "강조표시 확인", "기준치 조회", "%기준치 계산", "표시값 변환", "이전", "처음으로"]
                )

            # 표시값 변환 모드에서 이전 -> 영양표시 도우미 메뉴로
            if user_data.get("표시값변환_모드"):
                user_data.pop("표시값변환_모드", None)
                user_data.pop("표시값변환_단계", None)
                user_data.pop("표시값변환_영양소", None)
                user_data["현재_메뉴"] = "영양표시 도우미"
                return make_response(
                    "📊 영양표시 도우미\n\n원하시는 기능을 선택해주세요.",
                    ["표시단위 계산", "강조표시 확인", "기준치 조회", "%기준치 계산", "표시값 변환", "이전", "처음으로"]
                )

            # 1. 먼저 검사분야_메뉴가 있으면 해당 메뉴로 돌아감 (응답 화면에서)
            current_inspection_menu = user_data.get("검사분야_메뉴")
            current_menu = user_data.get("현재_메뉴")

            # 응답 화면에서 이전 누르면 -> 부모 메뉴로
            # 현재_메뉴가 없거나, 현재_메뉴와 검사분야_메뉴가 같으면 부모 메뉴로 이동
            if current_inspection_menu and current_inspection_menu in INSPECTION_MENU["submenus"]:
                # 현재_메뉴가 검사분야_메뉴와 같으면 -> 캐러셀로 (하위메뉴에서 이전)
                # 현재_메뉴가 없거나 다르면 -> 검사분야_메뉴로 (응답에서 이전)
                if current_menu == current_inspection_menu:
                    # 하위메뉴에서 이전 -> 부모로
                    parent = INSPECTION_MENU["submenus"][current_menu].get("parent")
                    if parent == "검사분야":
                        user_data.pop("현재_메뉴", None)
                        user_data.pop("검사분야_메뉴", None)
                        return make_carousel_response(
                            INSPECTION_MENU["cards"],
                            quick_replies=["처음으로"]
                        )
                    elif parent in INSPECTION_MENU["submenus"]:
                        submenu = INSPECTION_MENU["submenus"][parent]
                        user_data["현재_메뉴"] = parent
                        user_data["검사분야_메뉴"] = parent
                        return make_response(
                            f"📋 {submenu['title']}\n\n원하시는 항목을 선택해주세요.",
                            submenu["buttons"]
                        )
                else:
                    # 응답 화면에서 이전 -> 검사분야_메뉴로 돌아감
                    submenu = INSPECTION_MENU["submenus"][current_inspection_menu]
                    user_data["현재_메뉴"] = current_inspection_menu
                    return make_response(
                        f"📋 {submenu['title']}\n\n원하시는 항목을 선택해주세요.",
                        submenu["buttons"]
                    )

            # 2. 현재_메뉴만 있는 경우 (하위의 하위 메뉴)
            if current_menu and current_menu in INSPECTION_MENU["submenus"]:
                parent = INSPECTION_MENU["submenus"][current_menu].get("parent")
                if parent == "검사분야":
                    user_data.pop("현재_메뉴", None)
                    user_data.pop("검사분야_메뉴", None)
                    return make_carousel_response(
                        INSPECTION_MENU["cards"],
                        quick_replies=["처음으로"]
                    )
                elif parent in INSPECTION_MENU["submenus"]:
                    submenu = INSPECTION_MENU["submenus"][parent]
                    user_data["현재_메뉴"] = parent
                    user_data["검사분야_메뉴"] = parent
                    return make_response(
                        f"📋 {submenu['title']}\n\n원하시는 항목을 선택해주세요.",
                        submenu["buttons"]
                    )

            # 3. go_back 함수로 히스토리 기반 복원 시도
            previous = go_back(user_data)
            if previous:
                # 이전 상태에 따라 적절한 화면 표시
                if previous.get("영양성분_검사종류"):
                    submenu = INSPECTION_MENU["submenus"]["영양성분검사_검사종류"]
                    user_data["현재_메뉴"] = "영양성분검사_검사종류"
                    return make_response(
                        f"📋 {submenu['title']}\n\n원하시는 항목을 선택해주세요.",
                        submenu["buttons"]
                    )
                elif previous.get("검사분야_메뉴"):
                    menu_name = previous["검사분야_메뉴"]
                    if menu_name in INSPECTION_MENU["submenus"]:
                        submenu = INSPECTION_MENU["submenus"][menu_name]
                        user_data["현재_메뉴"] = menu_name
                        return make_response(
                            f"📋 {submenu['title']}\n\n원하시는 항목을 선택해주세요.",
                            submenu["buttons"]
                        )
                elif previous.get("업종"):
                    # 업종 선택 화면으로
                    if previous.get("분야") == "식품":
                        buttons = ["식품제조가공업", "즉석판매제조가공업", "이전", "처음으로"]
                    else:
                        buttons = ["축산물제조가공업", "축산물즉석판매제조가공업", "이전", "처음으로"]
                    return make_response(
                        f"[{previous.get('분야')}] 검사할 업종을 선택해주세요.",
                        buttons
                    )
                elif previous.get("분야"):
                    # 분야 선택 화면으로
                    return make_response(
                        f"[{previous.get('기능')}] 검사할 분야를 선택해주세요.",
                        ["식품", "축산", "이전", "처음으로"]
                    )
                elif previous.get("기능"):
                    # 기능 선택 화면으로
                    return make_response(
                        "원하시는 서비스를 선택해주세요.",
                        ["검사분야", "검사주기", "검사항목"]
                    )

            # 4. 히스토리가 없으면 검사분야 캐러셀로
            return make_carousel_response(
                INSPECTION_MENU["cards"],
                quick_replies=["처음으로"]
            )

        # ===== 검사분야 카드 캐러셀 =====
        if user_input == "검사분야":
            reset_user_state(user_id)
            return make_carousel_response(
                INSPECTION_MENU["cards"],
                quick_replies=["처음으로"]
            )

        # ===== 검사분야 하위 메뉴 처리 =====
        if user_input in INSPECTION_MENU["submenus"]:
            submenu = INSPECTION_MENU["submenus"][user_input]

            # 현재 메뉴 상태 저장 (이전 버튼 처리용)
            user_data["현재_메뉴"] = user_input

            # 자가품질검사에서 식품/축산 선택 시 상태 저장
            if user_input == "자가품질검사":
                user_data["검사분야_메뉴"] = "자가품질검사"

            # 영양성분검사 메뉴 상태 저장
            if user_input == "영양성분검사":
                user_data["검사분야_메뉴"] = "영양성분검사"

            # 일반 검사 메뉴 상태 저장 (항생물질, 잔류농약, 방사능, 비건, 할랄, 동물DNA, 알레르기, 글루텐Free, 소비기한설정, 이물질검사)
            if user_input in ["항생물질", "잔류농약", "방사능", "비건", "할랄", "동물DNA", "알레르기", "글루텐Free", "소비기한설정", "이물질검사"]:
                user_data["검사분야_메뉴"] = user_input

            return make_response(
                f"📋 {submenu['title']}\n\n원하시는 항목을 선택해주세요.",
                submenu["buttons"]
            )

        # 자가품질검사 > 식품/축산 선택 시 분기 처리
        if user_data.get("검사분야_메뉴") == "자가품질검사" and user_input in ["식품", "축산"]:
            submenu_key = f"자가품질검사_{user_input}"
            if submenu_key in INSPECTION_MENU["submenus"]:
                submenu = INSPECTION_MENU["submenus"][submenu_key]
                user_data["자가품질_분야"] = user_input
                user_data["현재_메뉴"] = submenu_key  # 현재 메뉴 상태 저장
                return make_response(
                    f"📋 {submenu['title']}\n\n원하시는 항목을 선택해주세요.",
                    submenu["buttons"]
                )

        # ===== 자가품질검사 > 식품/축산 > 검사주기/검사항목 선택 시 DB 조회 로직 연결 =====
        if user_data.get("자가품질_분야") and user_input in ["검사주기", "검사항목"]:
            # 자가품질검사 메뉴에서 온 경우 DB 조회 로직으로 연결
            user_data["기능"] = user_input
            user_data["분야"] = user_data["자가품질_분야"]
            # 자가품질검사 상태 정리
            user_data.pop("자가품질_분야", None)
            user_data.pop("검사분야_메뉴", None)

            if user_input == "검사주기":
                # 검사주기: 업종 선택 필요
                if user_data["분야"] == "식품":
                    buttons = ["식품제조가공업", "즉석판매제조가공업", "처음으로"]
                else:
                    buttons = ["축산물제조가공업", "축산물즉석판매제조가공업", "처음으로"]
                return make_response(
                    f"[{user_data['분야']}] 검사할 업종을 선택해주세요.",
                    buttons
                )
            else:
                # 검사항목: 바로 식품 유형 입력
                return make_response(
                    f"[{user_data['분야']}] 검사할 식품 유형을 입력해주세요.\n\n예: 과자, 음료, 소시지 등\n\n(주의 : 품목제조보고서에 표기된 \"식품유형\"을 입력하세요. 단어에 가운데 점이 있는 경우 제외하고 입력하세요)",
                    ["처음으로"]
                )

        # ===== 영양성분검사 > 검사종류 선택 시 하위 메뉴 표시 =====
        if user_data.get("검사분야_메뉴") == "영양성분검사" and user_input == "검사종류":
            submenu = INSPECTION_MENU["submenus"]["영양성분검사_검사종류"]
            user_data["영양성분_검사종류"] = True
            user_data["현재_메뉴"] = "영양성분검사_검사종류"  # 현재 메뉴 상태 저장
            return make_response(
                f"📋 {submenu['title']}\n\n원하시는 항목을 선택해주세요.",
                submenu["buttons"]
            )

        # ===== 영양성분검사 > 계산도우미 선택 시 하위메뉴 표시 =====
        if user_data.get("검사분야_메뉴") == "영양성분검사" and user_input == "계산도우미":
            user_data["현재_메뉴"] = "계산도우미"
            return make_response(
                "📊 계산도우미\n\n원하시는 계산 방법을 선택해주세요.",
                ["배합 함량", "당알코올 계산", "이전", "처음으로"]
            )

        # ===== 영양성분검사 > 계산도우미 > 배합 함량 =====
        # 계산도우미 메뉴에서 선택하거나, 계산 결과 후 다시 선택 시 모두 처리
        if user_input == "배합 함량":
            user_data["계산_모드"] = "배합함량"
            user_data["계산_단계"] = "총중량_입력"
            user_data.pop("현재_메뉴", None)

            response_text = """📊 배합 함량(%) 계산

배합 함량은 각 원재료가 전체 중량에서 차지하는 비율입니다.

━━━━━━━━━━━━━━━
📌 계산 공식
━━━━━━━━━━━━━━━
원재료 중량 ÷ 총 중량 × 100 = 배합 함량(%)

━━━━━━━━━━━━━━━
📝 입력 예시
━━━━━━━━━━━━━━━
① 총 중량: 120
② 원재료: 케첩 60, 물엿 20, 기타 40

━━━━━━━━━━━━━━━
🔢 계산을 시작합니다!
━━━━━━━━━━━━━━━
총 중량(g)을 입력해주세요.

예: 120"""
            return make_response(response_text, ["이전", "처음으로"])

        # ===== 배합 함량 계산 진행 =====
        if user_data.get("계산_모드") == "배합함량":
            # [배합 함량] 버튼 클릭 시 처음부터 다시 시작
            if user_input == "배합 함량":
                user_data["계산_단계"] = "총중량_입력"
                user_data.pop("총중량", None)
                return make_response(
                    "🔄 처음부터 다시 계산합니다.\n\n총 중량(g)을 입력해주세요.\n\n예: 120",
                    ["이전", "처음으로"]
                )

            step = user_data.get("계산_단계")

            # 1단계: 총 중량 입력
            if step == "총중량_입력":
                try:
                    total_weight = float(user_input.replace("g", "").replace("G", "").strip())
                    if total_weight <= 0:
                        return make_response("❌ 총 중량은 0보다 커야 합니다.\n\n예: 120", ["이전", "처음으로"])
                    user_data["총중량"] = total_weight
                    user_data["계산_단계"] = "원재료_일괄입력"
                    return make_response(
                        f"✅ 총 중량: {total_weight}g\n\n원재료명과 중량(g)을 입력해주세요.\n콤마(,)로 구분하여 한 번에 입력 가능합니다.\n\n예: 케첩 60, 물엿 20, 기타 40",
                        ["이전", "처음으로"]
                    )
                except ValueError:
                    return make_response(
                        "❌ 숫자를 입력해주세요.\n\n예: 120",
                        ["이전", "처음으로"]
                    )

            # 2단계: 원재료 일괄 입력
            if step == "원재료_일괄입력":
                total_weight = user_data.get("총중량")

                # 콤마 또는 줄바꿈으로 분리
                raw_items = re.split(r'[,\n]+', user_input.strip())
                ingredients = []
                errors = []

                # "원재료명 중량" 패턴 파싱
                pattern = r'^(.+?)\s*(\d+\.?\d*)\s*g?$'

                for item in raw_items:
                    item = item.strip()
                    if not item:
                        continue
                    match = re.match(pattern, item, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()
                        weight = float(match.group(2))
                        if weight >= 0:
                            ingredients.append({"name": name, "weight": weight})
                        else:
                            errors.append(item)
                    else:
                        errors.append(item)

                if not ingredients:
                    return make_response(
                        "❌ 형식이 올바르지 않습니다.\n\n원재료명과 중량(g)을 입력해주세요.\n\n예: 케첩 60, 물엿 20, 기타 40",
                        ["이전", "처음으로"]
                    )

                # 결과 생성
                response_text = f"""✅ 배합 함량 계산 결과

━━━━━━━━━━━━━━━
📊 입력 정보
━━━━━━━━━━━━━━━
• 총 중량: {total_weight}g
• 원재료 수: {len(ingredients)}개

━━━━━━━━━━━━━━━
📌 계산 결과
━━━━━━━━━━━━━━━"""

                total_percentage = 0
                for ing in ingredients:
                    percentage = (ing["weight"] / total_weight) * 100
                    total_percentage += percentage
                    response_text += f"\n• {ing['name']}: {ing['weight']}g → {percentage:.2f}%"

                response_text += f"""

━━━━━━━━━━━━━━━
✨ 합계: {total_percentage:.2f}%

💡 소수점 둘째 자리까지 반영된 계산 값입니다.

다시 계산하시려면 [배합 함량]을 선택하세요."""

                if errors:
                    response_text += f"\n\n⚠️ 인식 실패 항목: {', '.join(errors)}"

                # 계산 완료 - 상태 초기화
                user_data.pop("계산_모드", None)
                user_data.pop("계산_단계", None)
                user_data.pop("총중량", None)

                return make_response(response_text, ["배합 함량", "이전", "처음으로"])

        # ===== 영양성분검사 > 계산도우미 > 당알코올 계산 =====
        # 계산도우미 메뉴에서 선택하거나, 계산 결과 후 다시 선택 시 모두 처리
        if user_input == "당알코올 계산":
            user_data["계산_모드"] = "당알코올"
            user_data["계산_단계"] = "배합함량_입력"
            user_data.pop("현재_메뉴", None)

            response_text = """📊 당알코올 순 함량 계산

비고란에 당알코올 값을 반영한 열량을 표시하려면,
순 당알코올 함량이 필요합니다.

━━━━━━━━━━━━━━━
📌 필요 정보
━━━━━━━━━━━━━━━
• 당알코올 배합 함량 (%)
• 당알코올 순 함량 (%)
• 수분값/Solid (%)

💡 순 함량(%)은 원재료의 한글표시사항이나 성적서에서 확인해 보시기 바랍니다.

━━━━━━━━━━━━━━━
📝 예시 (알룰로오스)
━━━━━━━━━━━━━━━
• 배합 함량: 17.43%
• 원재료 알룰로오스 함량: 96.5%
• 수분값(Solid): 70.5%

순알룰로오스 = 96.5 × 70.5 ÷ 100 = 68.03%
순 배합함량 = 17.43 × 68.03 ÷ 100 = 11.86%

━━━━━━━━━━━━━━━
🔢 계산을 도와드릴게요!
━━━━━━━━━━━━━━━
당알코올 배합 함량(%)을 입력해주세요.

예: 17.43"""
            return make_response(response_text, ["이전", "처음으로"])

        # ===== 당알코올 계산 진행 =====
        if user_data.get("계산_모드") == "당알코올":
            step = user_data.get("계산_단계")

            if step == "배합함량_입력":
                try:
                    blend_ratio = float(user_input.replace("%", "").strip())
                    user_data["당알코올_배합함량"] = blend_ratio
                    user_data["계산_단계"] = "수분값_확인"
                    return make_response(
                        f"✅ 배합 함량: {blend_ratio}%\n\n수분값(Solid) 적용이 필요하신가요?\n\n💡 성적서에서 확인한 경우 → 수분값 적용 필요\n💡 한글표시사항에서 확인한 경우 → 수분값 적용 불필요",
                        ["수분값 적용", "수분값 미적용", "이전", "처음으로"]
                    )
                except ValueError:
                    return make_response(
                        "❌ 숫자를 입력해주세요.\n\n예: 17.43",
                        ["이전", "처음으로"]
                    )

            if step == "수분값_확인":
                if user_input == "수분값 적용":
                    user_data["수분값_적용"] = True
                    user_data["계산_단계"] = "원재료함량_입력"
                    return make_response(
                        "당알코올 순 함량(%)을 입력해주세요.\n\n💡 순 함량은 원재료 성적서에서 확인하세요.\n\n예: 96.5",
                        ["이전", "처음으로"]
                    )
                elif user_input == "수분값 미적용":
                    user_data["수분값_적용"] = False
                    user_data["계산_단계"] = "원재료함량_입력"
                    return make_response(
                        "당알코올 순 함량(%)을 입력해주세요.\n\n💡 순 함량은 원재료의 한글표시사항에서 확인하세요.\n\n예: 68.03",
                        ["이전", "처음으로"]
                    )
                else:
                    return make_response(
                        "버튼을 선택해주세요.",
                        ["수분값 적용", "수분값 미적용", "이전", "처음으로"]
                    )

            if step == "원재료함량_입력":
                try:
                    ingredient_content = float(user_input.replace("%", "").strip())
                    user_data["당알코올_원재료함량"] = ingredient_content

                    # 수분값 적용 여부에 따라 분기
                    if user_data.get("수분값_적용"):
                        user_data["계산_단계"] = "수분값_입력"
                        return make_response(
                            f"✅ 당알코올 순 함량: {ingredient_content}%\n\n수분값/Solid(%)를 입력해주세요.\n\n예: 70.5",
                            ["이전", "처음으로"]
                        )
                    else:
                        # 수분값 미적용 - 바로 계산
                        blend_ratio = user_data.get("당알코올_배합함량")
                        pure_blend = blend_ratio * ingredient_content / 100

                        response_text = f"""✅ 당알코올 순 함량 계산 결과

━━━━━━━━━━━━━━━
📊 입력 정보
━━━━━━━━━━━━━━━
• 배합 함량: {blend_ratio}%
• 당알코올 순 함량: {ingredient_content}%

━━━━━━━━━━━━━━━
📌 계산 과정
━━━━━━━━━━━━━━━
{blend_ratio} × {ingredient_content} ÷ 100 = {pure_blend:.4f}%

━━━━━━━━━━━━━━━
✨ 최종 결과
━━━━━━━━━━━━━━━
순 당알코올 배합함량: {pure_blend:.2f}%

💡 소수점 둘째 자리까지 반영된 계산 값입니다.

다시 계산하시려면 [당알코올 계산]을 선택하세요."""

                        # 계산 모드 초기화
                        user_data.pop("계산_모드", None)
                        user_data.pop("계산_단계", None)
                        user_data.pop("수분값_적용", None)

                        return make_response(response_text, ["당알코올 계산", "이전", "처음으로"])
                except ValueError:
                    return make_response(
                        "❌ 숫자를 입력해주세요.\n\n예: 96.5",
                        ["이전", "처음으로"]
                    )

            if step == "수분값_입력":
                try:
                    solid_value = float(user_input.replace("%", "").strip())
                    blend_ratio = user_data.get("당알코올_배합함량")
                    ingredient_content = user_data.get("당알코올_원재료함량")

                    # 순 당알코올 계산
                    pure_content = ingredient_content * solid_value / 100
                    # 순 배합함량 계산
                    pure_blend = blend_ratio * pure_content / 100

                    response_text = f"""✅ 당알코올 순 함량 계산 결과

━━━━━━━━━━━━━━━
📊 입력 정보
━━━━━━━━━━━━━━━
• 배합 함량: {blend_ratio}%
• 당알코올 순 함량: {ingredient_content}%
• 수분값(Solid): {solid_value}%

━━━━━━━━━━━━━━━
📌 계산 과정
━━━━━━━━━━━━━━━
① 순 당알코올 함량
{ingredient_content} × {solid_value} ÷ 100 = {pure_content:.4f}%

② 순 당알코올 배합함량
{blend_ratio} × {pure_content:.4f} ÷ 100 = {pure_blend:.4f}%

━━━━━━━━━━━━━━━
✨ 최종 결과
━━━━━━━━━━━━━━━
순 당알코올 배합함량: {pure_blend:.2f}%

💡 소수점 둘째 자리까지 반영된 계산 값입니다.

다시 계산하시려면 [당알코올 계산]을 선택하세요."""

                    # 계산 모드 초기화
                    user_data.pop("계산_모드", None)
                    user_data.pop("계산_단계", None)
                    user_data.pop("수분값_적용", None)

                    return make_response(response_text, ["당알코올 계산", "이전", "처음으로"])
                except ValueError:
                    return make_response(
                        "❌ 숫자를 입력해주세요.\n\n예: 70.5",
                        ["이전", "처음으로"]
                    )

        # ===== 영양성분검사 > 계산도우미/영양표시 도우미 > 표시단위 계산 선택 시 (대화형) =====
        if user_input == "표시단위 계산" and (user_data.get("검사분야_메뉴") == "영양성분검사" or user_data.get("현재_메뉴") in ["계산도우미", "영양표시 도우미"]):
            user_data["영양표시_모드"] = True
            user_data["영양표시_단계"] = "식품군_선택"
            user_data.pop("현재_메뉴", None)

            food_groups = get_serving_food_groups()
            # 버튼 개수 제한 (최대 10개씩)
            buttons = food_groups[:9] + ["이전", "처음으로"]

            response_text = """📊 영양성분 표시단위 계산

영양성분을 어떤 단위로 표시해야 하는지 안내해드립니다.

━━━━━━━━━━━━━━━
📌 표시 단위 종류
━━━━━━━━━━━━━━━
• 1포장당 (총 내용량당)
• 100g(ml)당
• 1회 섭취참고량당
• 낱개당

━━━━━━━━━━━━━━━
🔍 식품군을 선택해주세요."""

            return make_response(response_text, buttons)

        # ===== 표시단위 계산 진행 =====
        if user_data.get("영양표시_모드"):
            step = user_data.get("영양표시_단계")

            # 식품군 선택 단계
            if step == "식품군_선택":
                food_groups = get_serving_food_groups()
                if user_input in food_groups:
                    user_data["영양표시_식품군"] = user_input
                    user_data["영양표시_단계"] = "식품유형_선택"

                    food_types = get_serving_food_types(user_input)
                    buttons = food_types[:9] + ["이전", "처음으로"]

                    return make_response(
                        f"📋 [{user_input}]\n\n식품유형을 선택해주세요.",
                        buttons
                    )
                else:
                    # 더보기 또는 다른 식품군
                    if user_input == "더보기":
                        more_groups = get_serving_food_groups()[9:]
                        buttons = more_groups[:9] + ["이전", "처음으로"]
                        return make_response(
                            "🔍 다른 식품군을 선택해주세요.",
                            buttons
                        )
                    return make_response(
                        "❌ 목록에서 식품군을 선택해주세요.",
                        food_groups[:9] + ["이전", "처음으로"]
                    )

            # 식품유형 선택 단계
            if step == "식품유형_선택":
                food_group = user_data.get("영양표시_식품군")
                food_types = get_serving_food_types(food_group)

                if user_input in food_types:
                    user_data["영양표시_식품유형"] = user_input

                    # 세부유형이 있는지 확인
                    subtypes = get_serving_subtypes(food_group, user_input)
                    if subtypes:
                        user_data["영양표시_단계"] = "세부유형_선택"
                        buttons = subtypes + ["이전", "처음으로"]
                        return make_response(
                            f"📋 [{user_input}]\n\n세부유형을 선택해주세요.",
                            buttons
                        )
                    else:
                        # 세부유형 없으면 바로 총내용량 입력
                        user_data["영양표시_단계"] = "총내용량_입력"
                        serving = get_serving_size(food_group, user_input)
                        if serving:
                            user_data["영양표시_1회섭취참고량"] = serving['serving_size']
                            user_data["영양표시_단위"] = serving['unit']

                        return make_response(
                            f"📋 [{user_input}]\n\n제품의 총 내용량(g 또는 ml)을 입력해주세요.\n\n예: 500",
                            ["이전", "처음으로"]
                        )
                else:
                    return make_response(
                        "❌ 목록에서 식품유형을 선택해주세요.",
                        food_types[:9] + ["이전", "처음으로"]
                    )

            # 세부유형 선택 단계
            if step == "세부유형_선택":
                food_group = user_data.get("영양표시_식품군")
                food_type = user_data.get("영양표시_식품유형")
                subtypes = get_serving_subtypes(food_group, food_type)

                if user_input in subtypes:
                    user_data["영양표시_세부유형"] = user_input
                    user_data["영양표시_단계"] = "총내용량_입력"

                    serving = get_serving_size(food_group, food_type, user_input)
                    if serving:
                        user_data["영양표시_1회섭취참고량"] = serving['serving_size']
                        user_data["영양표시_단위"] = serving['unit']

                    return make_response(
                        f"📋 [{food_type} - {user_input}]\n\n제품의 총 내용량(g 또는 ml)을 입력해주세요.\n\n예: 500",
                        ["이전", "처음으로"]
                    )
                else:
                    return make_response(
                        "❌ 목록에서 세부유형을 선택해주세요.",
                        subtypes + ["이전", "처음으로"]
                    )

            # 총 내용량 입력 단계
            if step == "총내용량_입력":
                try:
                    total_weight = float(user_input.replace("g", "").replace("ml", "").replace(",", "").strip())
                    user_data["영양표시_총내용량"] = total_weight
                    user_data["영양표시_단계"] = "낱개여부_선택"

                    return make_response(
                        f"📋 총 내용량: {total_weight}g(ml)\n\n제품이 낱개로 나눌 수 있나요?\n(예: 개별포장 과자, 낱개 떡 등)",
                        ["예", "아니오", "이전", "처음으로"]
                    )
                except ValueError:
                    return make_response(
                        "❌ 숫자를 입력해주세요.\n\n예: 500",
                        ["이전", "처음으로"]
                    )

            # 낱개 여부 선택 단계
            if step == "낱개여부_선택":
                if user_input == "예":
                    user_data["영양표시_낱개여부"] = True
                    user_data["영양표시_단계"] = "낱개중량_입력"

                    return make_response(
                        "📋 낱개 1개당 중량(g 또는 ml)을 입력해주세요.\n\n예: 25",
                        ["이전", "처음으로"]
                    )
                elif user_input == "아니오":
                    user_data["영양표시_낱개여부"] = False
                    # 바로 결과 계산
                    return _calculate_serving_display(user_data)
                else:
                    return make_response(
                        "❌ [예] 또는 [아니오]를 선택해주세요.",
                        ["예", "아니오", "이전", "처음으로"]
                    )

            # 낱개 중량 입력 단계
            if step == "낱개중량_입력":
                try:
                    piece_weight = float(user_input.replace("g", "").replace("ml", "").replace(",", "").strip())
                    user_data["영양표시_낱개중량"] = piece_weight
                    # 결과 계산
                    return _calculate_serving_display(user_data)
                except ValueError:
                    return make_response(
                        "❌ 숫자를 입력해주세요.\n\n예: 25",
                        ["이전", "처음으로"]
                    )

        # ===== 영양성분검사 > 영양표시 도우미 선택 시 하위메뉴 표시 =====
        if user_data.get("검사분야_메뉴") == "영양성분검사" and user_input == "영양표시 도우미":
            user_data["현재_메뉴"] = "영양표시 도우미"
            return make_response(
                "📊 영양표시 도우미\n\n원하시는 기능을 선택해주세요.",
                ["표시단위 계산", "강조표시 확인", "기준치 조회", "%기준치 계산", "표시값 변환", "이전", "처음으로"]
            )

        # ===== 영양표시 도우미 > 강조표시 확인 =====
        if user_input == "강조표시 확인" and user_data.get("현재_메뉴") == "영양표시 도우미":
            user_data["강조표시_모드"] = True
            user_data["강조표시_단계"] = "용어_선택"

            return make_response(
                """📊 영양강조표시 확인

강조표시 가능 여부를 확인해드립니다.

━━━━━━━━━━━━━━━
📌 강조표시 용어 선택
━━━━━━━━━━━━━━━
나) 영양성분 함량 강조표시

(1) 무, 저
(2) 함유(급원)
(3) 고(풍부)

마) 무가당, 무첨가

🔽 사용하고자 하는 용어를 선택해주세요""",
                ["무", "저", "함유(급원)", "고(풍부)", "무가당", "무첨가", "이전", "처음으로"]
            )

        # ===== 강조표시 확인 진행 =====
        if user_data.get("강조표시_모드"):
            step = user_data.get("강조표시_단계")

            # 용어 선택 단계
            if step == "용어_선택":
                claim_type_map = {
                    "무": "무",
                    "저": "저",
                    "함유(급원)": "함유",
                    "고(풍부)": "고",
                    "무가당": "무가당",
                    "무첨가": "무첨가"
                }

                if user_input in claim_type_map:
                    selected_claim = claim_type_map[user_input]
                    user_data["강조표시_용어"] = selected_claim
                    user_data["강조표시_단계"] = "영양소_선택"

                    # 용어별 선택 가능한 영양소
                    if selected_claim in ["무", "저"]:
                        nutrients = ["열량", "지방", "포화지방", "트랜스지방", "콜레스테롤", "나트륨", "당류"]
                        section_ref = "나)(1)"
                        desc = "해당 영양소가 없거나 적게 함유된 경우"
                    elif selected_claim == "함유":
                        nutrients = ["식이섬유", "단백질", "비타민류", "무기질류"]
                        section_ref = "나)(2)"
                        desc = "1일 영양성분 기준치의 15% 이상 함유"
                    elif selected_claim == "고":
                        nutrients = ["식이섬유", "단백질", "비타민류", "무기질류"]
                        section_ref = "나)(3)"
                        desc = "1일 영양성분 기준치의 30% 이상 함유"
                    elif selected_claim == "무가당":
                        nutrients = ["당류"]
                        section_ref = "마)"
                        desc = "당류를 첨가하지 않은 경우"
                    else:  # 무첨가
                        nutrients = ["나트륨"]
                        section_ref = "마)"
                        desc = "나트륨염을 첨가하지 않은 경우"

                    user_data["강조표시_영양소_목록"] = nutrients

                    return make_response(
                        f"""📊 [{user_input}] 강조표시

━━━━━━━━━━━━━━━
📌 적용 조항: {section_ref}
━━━━━━━━━━━━━━━
{desc}

━━━━━━━━━━━━━━━
🔽 영양소를 선택해주세요
━━━━━━━━━━━━━━━""",
                        nutrients + ["이전", "처음으로"]
                    )
                else:
                    return make_response(
                        "❌ 목록에서 용어를 선택해주세요.",
                        ["무", "저", "함유(급원)", "고(풍부)", "무가당", "무첨가", "이전", "처음으로"]
                    )

            # 영양소 선택 단계
            if step == "영양소_선택":
                valid_nutrients = user_data.get("강조표시_영양소_목록", [])
                claim_type = user_data.get("강조표시_용어")

                # 비타민류/무기질류 선택 시 세부 영양소 목록 표시
                if user_input == "비타민류":
                    user_data["강조표시_단계"] = "비타민_선택"
                    vitamins = ["비타민A", "비타민D", "비타민E", "비타민K", "비타민C",
                               "비타민B1", "비타민B2", "나이아신", "비타민B6", "엽산",
                               "비타민B12", "판토텐산", "바이오틴"]
                    return make_response(
                        f"""📊 [{claim_type}] 비타민 선택

━━━━━━━━━━━━━━━
🔽 비타민을 선택해주세요
━━━━━━━━━━━━━━━""",
                        vitamins + ["이전", "처음으로"]
                    )

                if user_input == "무기질류":
                    user_data["강조표시_단계"] = "무기질_선택"
                    minerals = ["칼슘", "인", "칼륨", "마그네슘", "철분", "아연",
                               "구리", "망간", "요오드", "셀레늄", "몰리브덴", "크롬"]
                    return make_response(
                        f"""📊 [{claim_type}] 무기질 선택

━━━━━━━━━━━━━━━
🔽 무기질을 선택해주세요
━━━━━━━━━━━━━━━""",
                        minerals + ["이전", "처음으로"]
                    )

                if user_input in valid_nutrients:
                    user_data["강조표시_영양소"] = user_input
                    user_data["강조표시_단계"] = "함량_입력"

                    # 해당 영양소+용어의 강조표시 기준 조회
                    claims = get_all_claims_for_nutrient(user_input)
                    target_claim = next((c for c in claims if c['claim_type'] == claim_type), None)

                    if target_claim:
                        condition_text = target_claim['condition']
                        note_text = f"\n※ {target_claim['note']}" if target_claim['note'] else ""
                    else:
                        condition_text = "기준 정보 없음"
                        note_text = ""

                    # 추가 조건 체크 안내 (콜레스테롤)
                    extra_check = ""
                    if user_input == "콜레스테롤" and claim_type in ["무", "저"]:
                        extra_check = "\n\n⚠️ 다) 추가조건: 포화지방 기준도 충족해야 합니다."

                    return make_response(
                        f"""📊 [{user_input}] {claim_type} 표시 기준

━━━━━━━━━━━━━━━
📌 기준
━━━━━━━━━━━━━━━
{condition_text}{note_text}{extra_check}

━━━━━━━━━━━━━━━
🔢 100g(ml)당 {user_input} 함량을 입력해주세요
━━━━━━━━━━━━━━━
숫자만 입력 (단위 제외)

예: 2.5, 15, 0.3""",
                        ["이전", "처음으로"]
                    )
                else:
                    return make_response(
                        "❌ 목록에서 영양소를 선택해주세요.",
                        valid_nutrients + ["이전", "처음으로"]
                    )

            # 비타민 선택 단계
            if step == "비타민_선택":
                vitamins = ["비타민A", "비타민D", "비타민E", "비타민K", "비타민C",
                           "비타민B1", "비타민B2", "나이아신", "비타민B6", "엽산",
                           "비타민B12", "판토텐산", "바이오틴"]
                if user_input in vitamins:
                    user_data["강조표시_영양소"] = user_input
                    user_data["강조표시_단계"] = "함량_입력"
                    claim_type = user_data.get("강조표시_용어")

                    claims = get_all_claims_for_nutrient(user_input)
                    target_claim = next((c for c in claims if c['claim_type'] == claim_type), None)

                    if target_claim:
                        condition_text = target_claim['condition']
                        note_text = f"\n※ {target_claim['note']}" if target_claim['note'] else ""
                    else:
                        condition_text = "기준 정보 없음"
                        note_text = ""

                    return make_response(
                        f"""📊 [{user_input}] {claim_type} 표시 기준

━━━━━━━━━━━━━━━
📌 기준
━━━━━━━━━━━━━━━
{condition_text}{note_text}

━━━━━━━━━━━━━━━
🔢 100g당 {user_input} 함량을 입력해주세요
━━━━━━━━━━━━━━━
숫자만 입력 (단위 제외)""",
                        ["이전", "처음으로"]
                    )
                else:
                    return make_response(
                        "❌ 목록에서 비타민을 선택해주세요.",
                        vitamins + ["이전", "처음으로"]
                    )

            # 무기질 선택 단계
            if step == "무기질_선택":
                minerals = ["칼슘", "인", "칼륨", "마그네슘", "철분", "아연",
                           "구리", "망간", "요오드", "셀레늄", "몰리브덴", "크롬"]
                if user_input in minerals:
                    user_data["강조표시_영양소"] = user_input
                    user_data["강조표시_단계"] = "함량_입력"
                    claim_type = user_data.get("강조표시_용어")

                    claims = get_all_claims_for_nutrient(user_input)
                    target_claim = next((c for c in claims if c['claim_type'] == claim_type), None)

                    if target_claim:
                        condition_text = target_claim['condition']
                        note_text = f"\n※ {target_claim['note']}" if target_claim['note'] else ""
                    else:
                        condition_text = "기준 정보 없음"
                        note_text = ""

                    return make_response(
                        f"""📊 [{user_input}] {claim_type} 표시 기준

━━━━━━━━━━━━━━━
📌 기준
━━━━━━━━━━━━━━━
{condition_text}{note_text}

━━━━━━━━━━━━━━━
🔢 100g당 {user_input} 함량을 입력해주세요
━━━━━━━━━━━━━━━
숫자만 입력 (단위 제외)""",
                        ["이전", "처음으로"]
                    )
                else:
                    return make_response(
                        "❌ 목록에서 무기질을 선택해주세요.",
                        minerals + ["이전", "처음으로"]
                    )

            # 함량 입력 단계
            if step == "함량_입력":
                try:
                    amount = float(user_input.replace(",", "").strip())
                    nutrient = user_data.get("강조표시_영양소")
                    claim_type = user_data.get("강조표시_용어")

                    # 해당 영양소+용어의 강조표시 기준 조회
                    claims = get_all_claims_for_nutrient(nutrient)
                    target_claim = next((c for c in claims if c['claim_type'] == claim_type), None)

                    # 모드 초기화
                    user_data.pop("강조표시_모드", None)
                    user_data.pop("강조표시_단계", None)
                    user_data.pop("강조표시_영양소", None)
                    user_data.pop("강조표시_용어", None)
                    user_data.pop("강조표시_영양소_목록", None)

                    if not target_claim:
                        return make_response(
                            f"""📊 강조표시 확인 결과

━━━━━━━━━━━━━━━
❌ 해당 영양소에 '{claim_type}' 표시 기준이 없습니다.

━━━━━━━━━━━━━━━
⚠️ 주의사항
━━━━━━━━━━━━━━━
본 결과는 식품등의 표시기준에 따른
참고 사항이며, 최종 검토는
사용자가 직접 확인해야 합니다.""",
                            ["강조표시 확인", "영양표시 도우미", "이전", "처음으로"]
                        )

                    threshold = target_claim['threshold']
                    is_applicable = False

                    # 무/저/무가당/무첨가 타입은 threshold 미만
                    if claim_type in ['무', '저', '무가당', '무첨가']:
                        if threshold is None:  # 무첨가 등 함량 기준 없는 경우
                            is_applicable = True
                        else:
                            is_applicable = amount < threshold
                    # 함유/고 타입은 threshold 이상
                    elif claim_type in ['함유', '고']:
                        is_applicable = amount >= threshold

                    # 조건 검토 결과 생성
                    condition = target_claim['condition']
                    note = target_claim['note']

                    # 추가 조건 체크 (콜레스테롤의 경우 포화지방 조건)
                    extra_condition_text = ""
                    if nutrient == "콜레스테롤" and claim_type in ["무", "저"]:
                        extra_condition_text = """
━━━━━━━━━━━━━━━
⚠️ 다) 추가 확인 필요
━━━━━━━━━━━━━━━
• 포화지방: 100g당 1.5g미만이고
  열량의 10%미만 충족 필요
• 위 조건 미충족 시 표시 불가"""

                    # 무가당 특별 조건
                    if claim_type == "무가당":
                        extra_condition_text = """
━━━━━━━━━━━━━━━
⚠️ 마) 추가 확인 필요
━━━━━━━━━━━━━━━
• 당류를 첨가하지 않았는지 확인
• 감미료 사용 시 별도 표시 필요
  예) "무가당, ○○○ 함유"
• 원재료에서 유래한 당류가 있을 경우
  "무가당, ○g의 당류 함유" 표시"""

                    # 무첨가(나트륨) 특별 조건
                    if claim_type == "무첨가" and nutrient == "나트륨":
                        extra_condition_text = """
━━━━━━━━━━━━━━━
⚠️ 마) 추가 확인 필요
━━━━━━━━━━━━━━━
• 나트륨염(소금 등)을 첨가하지
  않았는지 확인 필요"""

                    if is_applicable:
                        result_symbol = "✅"
                        result_text = f"'{claim_type}' 표시 기준을 충족합니다."
                    else:
                        result_symbol = "❌"
                        result_text = f"'{claim_type}' 표시 기준을 충족하지 않습니다."

                    note_text = f"\n※ {note}" if note else ""

                    response_text = f"""📊 강조표시 확인 결과

━━━━━━━━━━━━━━━
📌 입력 정보
━━━━━━━━━━━━━━━
• 표시 용어: {claim_type}
• 영양소: {nutrient}
• 함량: {amount} (100g당)

━━━━━━━━━━━━━━━
📋 나) 기준 검토
━━━━━━━━━━━━━━━
• 기준: {condition}
{note_text}

━━━━━━━━━━━━━━━
{result_symbol} 판정 결과
━━━━━━━━━━━━━━━
{result_text}{extra_condition_text}

━━━━━━━━━━━━━━━
⚠️ 주의사항
━━━━━━━━━━━━━━━
본 결과는 식품등의 표시기준에 따른
참고 사항이며, 최종 검토는
사용자가 직접 확인해야 합니다."""

                    return make_response(response_text, ["강조표시 확인", "영양표시 도우미", "이전", "처음으로"])
                except ValueError:
                    return make_response(
                        "❌ 숫자를 입력해주세요.\n\n예: 2.5, 15, 0.3",
                        ["이전", "처음으로"]
                    )

        # ===== 영양표시 도우미 > 기준치 조회 =====
        if user_input == "기준치 조회" and user_data.get("현재_메뉴") == "영양표시 도우미":
            user_data["기준치조회_모드"] = True
            user_data["기준치조회_단계"] = "영양소_선택"

            return make_response(
                """📊 1일 영양성분 기준치 조회

━━━━━━━━━━━━━━━
📌 조회 방법 선택
━━━━━━━━━━━━━━━

🔽 조회하실 항목을 선택해주세요""",
                ["전체 기준치", "열량/3대영양소", "지방류", "나트륨/당류", "비타민", "무기질", "이전", "처음으로"]
            )

        # ===== 기준치 조회 진행 =====
        if user_data.get("기준치조회_모드"):
            step = user_data.get("기준치조회_단계")

            if step == "영양소_선택":
                # 모드 초기화
                user_data.pop("기준치조회_모드", None)
                user_data.pop("기준치조회_단계", None)

                if user_input == "전체 기준치":
                    all_dvs = get_all_daily_values()
                    dv_text = "\n".join([f"• {dv['nutrient']}: {dv['daily_value']} {dv['unit']}" for dv in all_dvs if dv['daily_value'] > 0])

                    response_text = f"""📊 1일 영양성분 기준치 (전체)

━━━━━━━━━━━━━━━
{dv_text}"""

                elif user_input == "열량/3대영양소":
                    nutrients = ["탄수화물", "당류", "식이섬유", "단백질", "지방"]
                    dv_text = ""
                    for n in nutrients:
                        dv = get_daily_value(n)
                        if dv:
                            dv_text += f"• {dv['nutrient']}: {dv['daily_value']} {dv['unit']}\n"

                    response_text = f"""📊 1일 영양성분 기준치 (열량/3대영양소)

━━━━━━━━━━━━━━━
{dv_text}"""

                elif user_input == "지방류":
                    nutrients = ["지방", "리놀레산", "알파-리놀렌산", "EPA와 DHA의 합", "포화지방", "콜레스테롤"]
                    dv_text = ""
                    for n in nutrients:
                        dv = get_daily_value(n)
                        if dv:
                            dv_text += f"• {dv['nutrient']}: {dv['daily_value']} {dv['unit']}\n"

                    response_text = f"""📊 1일 영양성분 기준치 (지방류)

━━━━━━━━━━━━━━━
{dv_text}"""

                elif user_input == "나트륨/당류":
                    nutrients = ["나트륨", "당류", "칼륨"]
                    dv_text = ""
                    for n in nutrients:
                        dv = get_daily_value(n)
                        if dv:
                            dv_text += f"• {dv['nutrient']}: {dv['daily_value']} {dv['unit']}\n"

                    response_text = f"""📊 1일 영양성분 기준치 (나트륨/당류)

━━━━━━━━━━━━━━━
{dv_text}"""

                elif user_input == "비타민":
                    all_dvs = get_all_daily_values()
                    vitamins = [dv for dv in all_dvs if "비타민" in dv['nutrient'] or dv['nutrient'] in ["나이아신", "엽산", "비오틴", "판토텐산"]]
                    dv_text = "\n".join([f"• {dv['nutrient']}: {dv['daily_value']} {dv['unit']}" for dv in vitamins])

                    response_text = f"""📊 1일 영양성분 기준치 (비타민)

━━━━━━━━━━━━━━━
{dv_text}"""

                elif user_input == "무기질":
                    minerals = ["칼슘", "인", "마그네슘", "철", "아연", "구리", "망간", "요오드", "셀레늄", "몰리브덴", "크롬"]
                    dv_text = ""
                    for n in minerals:
                        dv = get_daily_value(n)
                        if dv:
                            dv_text += f"• {dv['nutrient']}: {dv['daily_value']} {dv['unit']}\n"

                    response_text = f"""📊 1일 영양성분 기준치 (무기질)

━━━━━━━━━━━━━━━
{dv_text}"""

                else:
                    return make_response(
                        "❌ 목록에서 항목을 선택해주세요.",
                        ["전체 기준치", "열량/3대영양소", "지방류", "나트륨/당류", "비타민", "무기질", "이전", "처음으로"]
                    )

                return make_response(response_text, ["기준치 조회", "영양표시 도우미", "이전", "처음으로"])

        # ===== 영양표시 도우미 > %기준치 계산 =====
        if user_input == "%기준치 계산" and user_data.get("현재_메뉴") == "영양표시 도우미":
            user_data["퍼센트기준치_모드"] = True
            user_data["퍼센트기준치_단계"] = "영양소_선택"

            return make_response(
                """📊 %기준치 계산

영양소 함량의 1일 기준치 대비 백분율을 계산합니다.

━━━━━━━━━━━━━━━
🔽 영양소를 선택해주세요""",
                ["탄수화물", "당류", "식이섬유", "단백질", "지방", "포화지방", "콜레스테롤", "나트륨", "이전", "처음으로"]
            )

        # ===== %기준치 계산 진행 =====
        if user_data.get("퍼센트기준치_모드"):
            step = user_data.get("퍼센트기준치_단계")

            # 영양소 선택 단계
            if step == "영양소_선택":
                valid_nutrients = ["탄수화물", "당류", "식이섬유", "단백질", "지방", "포화지방", "콜레스테롤", "나트륨"]
                if user_input in valid_nutrients:
                    user_data["퍼센트기준치_영양소"] = user_input
                    user_data["퍼센트기준치_단계"] = "함량_입력"

                    dv = get_daily_value(user_input)
                    dv_text = f"{dv['daily_value']} {dv['unit']}" if dv else "기준치 없음"

                    return make_response(
                        f"""📊 [{user_input}] %기준치 계산

━━━━━━━━━━━━━━━
📌 1일 기준치: {dv_text}
━━━━━━━━━━━━━━━

🔢 {user_input} 함량을 입력해주세요
(숫자만 입력)

예: 15, 2.5, 500""",
                        ["이전", "처음으로"]
                    )
                else:
                    return make_response(
                        "❌ 목록에서 영양소를 선택해주세요.",
                        ["탄수화물", "당류", "식이섬유", "단백질", "지방", "포화지방", "콜레스테롤", "나트륨", "이전", "처음으로"]
                    )

            # 함량 입력 단계
            if step == "함량_입력":
                try:
                    amount = float(user_input.replace(",", "").strip())
                    nutrient = user_data.get("퍼센트기준치_영양소")

                    # %기준치 계산
                    percent = calculate_percent_daily_value(nutrient, amount)
                    dv = get_daily_value(nutrient)

                    # 모드 초기화
                    user_data.pop("퍼센트기준치_모드", None)
                    user_data.pop("퍼센트기준치_단계", None)
                    user_data.pop("퍼센트기준치_영양소", None)

                    if percent is not None:
                        response_text = f"""📊 %기준치 계산 결과

━━━━━━━━━━━━━━━
📌 입력 정보
━━━━━━━━━━━━━━━
• 영양소: {nutrient}
• 함량: {amount} {dv['unit']}

━━━━━━━━━━━━━━━
📊 계산 결과
━━━━━━━━━━━━━━━
• 1일 기준치: {dv['daily_value']} {dv['unit']}
• %기준치: {round(percent)}%

━━━━━━━━━━━━━━━
📐 계산식
━━━━━━━━━━━━━━━
{amount} ÷ {dv['daily_value']} × 100 = {percent:.1f}%

※ 표시 시 정수로 반올림하여 표시"""
                    else:
                        response_text = f"""📊 %기준치 계산 결과

━━━━━━━━━━━━━━━
📌 입력 정보
━━━━━━━━━━━━━━━
• 영양소: {nutrient}
• 함량: {amount}

━━━━━━━━━━━━━━━
❌ 결과
━━━━━━━━━━━━━━━
{nutrient}은(는) 1일 기준치가 설정되어 있지 않아
%기준치를 계산할 수 없습니다.

※ 트랜스지방 등은 기준치가 없습니다."""

                    return make_response(response_text, ["%기준치 계산", "영양표시 도우미", "이전", "처음으로"])
                except ValueError:
                    return make_response(
                        "❌ 숫자를 입력해주세요.\n\n예: 15, 2.5, 500",
                        ["이전", "처음으로"]
                    )

        # ===== 영양표시 도우미 > 표시값 변환 =====
        if user_input == "표시값 변환" and user_data.get("현재_메뉴") == "영양표시 도우미":
            user_data["표시값변환_모드"] = True
            user_data["표시값변환_단계"] = "영양소_선택"

            return make_response(
                """📊 영양성분 표시값 변환

실측값을 표시 기준에 맞게 반올림하여 변환합니다.

━━━━━━━━━━━━━━━
🔽 영양소를 선택해주세요""",
                ["열량", "탄수화물", "당류", "식이섬유", "단백질", "지방", "포화지방", "트랜스지방", "콜레스테롤", "나트륨", "이전", "처음으로"]
            )

        # ===== 표시값 변환 진행 =====
        if user_data.get("표시값변환_모드"):
            step = user_data.get("표시값변환_단계")

            # 영양소 선택 단계
            if step == "영양소_선택":
                valid_nutrients = ["열량", "탄수화물", "당류", "식이섬유", "단백질", "지방", "포화지방", "트랜스지방", "콜레스테롤", "나트륨"]
                if user_input in valid_nutrients:
                    user_data["표시값변환_영양소"] = user_input
                    user_data["표시값변환_단계"] = "함량_입력"

                    rule = get_rounding_rule(user_input)
                    rule_text = rule['note'] if rule else "일반 반올림 적용"

                    return make_response(
                        f"""📊 [{user_input}] 표시값 변환

━━━━━━━━━━━━━━━
📌 반올림 규칙
━━━━━━━━━━━━━━━
{rule_text}

━━━━━━━━━━━━━━━
🔢 실측값을 입력해주세요
━━━━━━━━━━━━━━━
숫자만 입력

예: 127.3, 2.8, 0.3""",
                        ["이전", "처음으로"]
                    )
                else:
                    return make_response(
                        "❌ 목록에서 영양소를 선택해주세요.",
                        ["열량", "탄수화물", "당류", "식이섬유", "단백질", "지방", "포화지방", "트랜스지방", "콜레스테롤", "나트륨", "이전", "처음으로"]
                    )

            # 함량 입력 단계
            if step == "함량_입력":
                try:
                    amount = float(user_input.replace(",", "").strip())
                    nutrient = user_data.get("표시값변환_영양소")

                    # 표시값 계산
                    result = get_display_value(nutrient, amount)
                    rule = get_rounding_rule(nutrient)
                    dv = get_daily_value(nutrient)

                    # 모드 초기화
                    user_data.pop("표시값변환_모드", None)
                    user_data.pop("표시값변환_단계", None)
                    user_data.pop("표시값변환_영양소", None)

                    unit = dv['unit'] if dv else ""
                    # 단위 정리 (kcal, g, mg 등 기본 단위만 표시)
                    if "kcal" in unit:
                        display_unit = "kcal"
                    elif "mg" in unit:
                        display_unit = "mg"
                    elif "μg" in unit:
                        display_unit = "μg"
                    else:
                        display_unit = "g"

                    response_text = f"""📊 표시값 변환 결과

━━━━━━━━━━━━━━━
📌 입력 정보
━━━━━━━━━━━━━━━
• 영양소: {nutrient}
• 실측값: {amount} {display_unit}

━━━━━━━━━━━━━━━
✨ 변환 결과
━━━━━━━━━━━━━━━
• 표시값: {result['display']} {display_unit}"""

                    if result['percent_dv']:
                        response_text += f"\n• %기준치: {result['percent_dv']}%"

                    if result['rule_note']:
                        response_text += f"""

━━━━━━━━━━━━━━━
📝 적용된 규칙
━━━━━━━━━━━━━━━
{result['rule_note']}"""

                    return make_response(response_text, ["표시값 변환", "영양표시 도우미", "이전", "처음으로"])
                except ValueError:
                    return make_response(
                        "❌ 숫자를 입력해주세요.\n\n예: 127.3, 2.8, 0.3",
                        ["이전", "처음으로"]
                    )

        # ===== 영양성분검사 > 표시대상확인 선택 시 (대화형) =====
        if user_data.get("검사분야_메뉴") == "영양성분검사" and user_input == "표시대상확인":
            user_data["표시대상_모드"] = True
            user_data["표시대상_단계"] = "식품유형_입력"
            user_data.pop("현재_메뉴", None)

            response_text = """📋 영양성분 표시대상 확인

영양성분 표시 시행일을 확인해드립니다.

━━━━━━━━━━━━━━━
📌 안내
━━━━━━━━━━━━━━━
식품유형에 따라 영양성분 표시 적용 시기가 다릅니다.
- 기존 시행 품목: 즉시 적용
- 2021 개정: 2019년 매출액 기준
- 2024 개정: 2022년 매출액 기준
- 일부 품목: 영양표시 제외 대상

━━━━━━━━━━━━━━━
🔍 식품유형을 입력해주세요
━━━━━━━━━━━━━━━
예: 빵류, 과자, 두부, 배추김치"""

            return make_response(response_text, ["이전", "처음으로"])

        # ===== 표시대상확인 진행 =====
        if user_data.get("표시대상_모드"):
            step = user_data.get("표시대상_단계")

            # 식품유형 입력 단계
            if step == "식품유형_입력":
                search_result = find_food_type_category(user_input)

                if search_result.get("found"):
                    category = search_result.get("category")
                    user_data["표시대상_식품유형"] = user_input
                    user_data["표시대상_카테고리"] = category

                    # 기존시행 - 즉시 적용
                    if category == "기존시행":
                        # 모드 초기화
                        user_data.pop("표시대상_모드", None)
                        user_data.pop("표시대상_단계", None)

                        response_text = f"""✅ 영양성분 표시대상 확인 결과

━━━━━━━━━━━━━━━
📊 조회 정보
━━━━━━━━━━━━━━━
• 식품유형: {user_input}
• 분류: 기존 시행 품목

━━━━━━━━━━━━━━━
📌 결과
━━━━━━━━━━━━━━━
✨ 영양성분 표시 의무: 즉시 적용

이미 영양성분 표시 의무가 적용된 품목입니다."""
                        return make_response(response_text, ["표시대상확인", "이전", "처음으로"])

                    # 제외대상
                    elif category == "제외대상":
                        reason = search_result.get("제외사유", "")
                        reason_text = {
                            "영양성분섭취목적아님": "영양성분 섭취 목적의 식품이 아님",
                            "영양성분함량적음": "영양성분 함량이 미미함",
                            "표준화어려움": "표준화 또는 균질화가 어려움"
                        }.get(reason, reason)

                        # 모드 초기화
                        user_data.pop("표시대상_모드", None)
                        user_data.pop("표시대상_단계", None)

                        response_text = f"""✅ 영양성분 표시대상 확인 결과

━━━━━━━━━━━━━━━
📊 조회 정보
━━━━━━━━━━━━━━━
• 식품유형: {user_input}
• 분류: 영양표시 제외 대상

━━━━━━━━━━━━━━━
📌 결과
━━━━━━━━━━━━━━━
✨ 영양성분 표시 의무: 없음

━━━━━━━━━━━━━━━
📝 제외 사유
━━━━━━━━━━━━━━━
{reason_text}"""
                        return make_response(response_text, ["표시대상확인", "이전", "처음으로"])

                    # 2021개정 또는 2024개정 - 매출액 확인 필요
                    else:
                        info = search_result.get("info", {})
                        user_data["표시대상_단계"] = "매출액_입력"
                        user_data["표시대상_개정정보"] = info

                        기준년도 = info.get("매출기준년도")
                        개정일 = info.get("개정일")

                        # 배추김치 여부 확인 (2021개정에서만 다른 기준 적용)
                        is_kimchi = user_input == "배추김치"
                        user_data["표시대상_배추김치"] = is_kimchi

                        if category == "2021개정":
                            if is_kimchi:
                                threshold_info = "300억 이상 / 50억~300억 미만 / 50억 미만"
                            else:
                                threshold_info = "120억 이상 / 50억~120억 미만 / 50억 미만"
                        else:  # 2024개정
                            threshold_info = "120억 초과 / 120억 이하"

                        response_text = f"""📋 영양성분 표시대상 확인

━━━━━━━━━━━━━━━
📊 조회 정보
━━━━━━━━━━━━━━━
• 식품유형: {user_input}
• 분류: {개정일} 개정 품목

━━━━━━━━━━━━━━━
📌 매출액 기준
━━━━━━━━━━━━━━━
• 기준 년도: {기준년도}년
• 구간: {threshold_info}

━━━━━━━━━━━━━━━
🔢 {기준년도}년 매출액을 입력해주세요
━━━━━━━━━━━━━━━
숫자만 입력 (단위: 억원)

예: 50, 120, 300"""
                        return make_response(response_text, ["이전", "처음으로"])

                else:
                    # 유사 결과가 있는 경우
                    similar = search_result.get("similar", [])
                    if similar:
                        similar_text = "\n".join([f"• {item[0]} ({item[1]})" for item in similar])
                        response_text = f"""❌ '{user_input}'을(를) 찾을 수 없습니다.

━━━━━━━━━━━━━━━
🔍 유사한 식품유형
━━━━━━━━━━━━━━━
{similar_text}

정확한 식품유형을 다시 입력해주세요."""
                    else:
                        response_text = f"""❌ '{user_input}'을(를) 찾을 수 없습니다.

등록된 식품유형이 아닙니다.
정확한 식품유형을 다시 입력해주세요.

예: 빵류, 과자, 두부, 배추김치"""

                    return make_response(response_text, ["이전", "처음으로"])

            # 매출액 입력 단계
            if step == "매출액_입력":
                try:
                    # 숫자 추출 (억, 원 등 단위 제거)
                    revenue_str = re.sub(r'[억원,\s]', '', user_input)
                    revenue = float(revenue_str)

                    category = user_data.get("표시대상_카테고리")
                    food_type = user_data.get("표시대상_식품유형")
                    is_kimchi = user_data.get("표시대상_배추김치", False)
                    info = user_data.get("표시대상_개정정보", {})

                    # 시행일 계산
                    if category == "2021개정":
                        if is_kimchi:
                            # 배추김치 기준
                            if revenue >= 300:
                                시행일 = "2022.01.01"
                                구간 = "300억 이상"
                            elif revenue >= 50:
                                시행일 = "2024.01.01"
                                구간 = "50억~300억 미만"
                            else:
                                시행일 = "2026.01.01"
                                구간 = "50억 미만"
                        else:
                            # 일반 기준
                            if revenue >= 120:
                                시행일 = "2022.01.01"
                                구간 = "120억 이상"
                            elif revenue >= 50:
                                시행일 = "2024.01.01"
                                구간 = "50억~120억 미만"
                            else:
                                시행일 = "2026.01.01"
                                구간 = "50억 미만"
                    else:  # 2024개정
                        if revenue > 120:
                            시행일 = "2026.01.01"
                            구간 = "120억 초과"
                        else:
                            시행일 = "2028.01.01"
                            구간 = "120억 이하"

                    기준년도 = info.get("매출기준년도")
                    개정일 = info.get("개정일")

                    # 모드 초기화
                    user_data.pop("표시대상_모드", None)
                    user_data.pop("표시대상_단계", None)
                    user_data.pop("표시대상_식품유형", None)
                    user_data.pop("표시대상_카테고리", None)
                    user_data.pop("표시대상_배추김치", None)
                    user_data.pop("표시대상_개정정보", None)

                    response_text = f"""✅ 영양성분 표시대상 확인 결과

━━━━━━━━━━━━━━━
📊 조회 정보
━━━━━━━━━━━━━━━
• 식품유형: {food_type}
• 분류: {개정일} 개정 품목
• {기준년도}년 매출액: {revenue}억원
• 매출 구간: {구간}

━━━━━━━━━━━━━━━
📌 결과
━━━━━━━━━━━━━━━
✨ 영양성분 표시 시행일: {시행일}

{시행일}부터 영양성분 표시가 의무화됩니다."""

                    return make_response(response_text, ["표시대상확인", "이전", "처음으로"])

                except ValueError:
                    return make_response(
                        "❌ 숫자를 입력해주세요.\n\n예: 50, 120, 300",
                        ["이전", "처음으로"]
                    )

        # ===== 영양성분검사 > 검사종류 > 영양표시 종류 선택 시 DB 조회 =====
        if user_data.get("영양성분_검사종류") and user_input == "영양표시 종류":
            detail_url = URL_MAPPING.get("영양성분검사", {}).get("검사종류")

            # DB에서 크롤링된 데이터 조회
            db_data = get_nutrition_info("영양성분검사", "검사종류")

            # 응답 화면으로 이동 시 현재_메뉴 초기화
            user_data.pop("현재_메뉴", None)

            if db_data and db_data.get("details"):
                # 데이터에 링크가 포함되어 있는지 확인
                if has_links_in_data(db_data['details']):
                    data_sections = parse_data_with_links(db_data['details'])
                    carousel_response = make_carousel_with_links_response(
                        "영양표시 종류",
                        data_sections,
                        ["이전", "처음으로"]
                    )
                    if carousel_response:
                        return carousel_response

                formatted_data = format_crawled_data(db_data['details'])
                response_text = f"📊 영양표시 종류\n\n{formatted_data}"
            else:
                response_text = "📊 영양표시 종류\n\n크롤링된 데이터가 없습니다.\n서버에서 'python crawler.py'를 실행해주세요."

            if detail_url:
                return make_response_with_link(
                    response_text,
                    get_question_label("영양성분검사", "검사종류"),
                    detail_url,
                    ["이전", "처음으로"]
                )
            else:
                return make_response(response_text, ["이전", "처음으로"])

        # ===== 영양성분검사 > 검사종류 > 9대/14대 영양성분 선택 시 =====
        if user_data.get("영양성분_검사종류") and user_input in ["9대 영양성분", "14대 영양성분"]:
            url_key = user_input.replace(" ", "")  # "9대영양성분" 또는 "14대영양성분"
            detail_url = URL_MAPPING.get("영양성분검사", {}).get(url_key)

            # DB에서 크롤링된 데이터 조회
            db_data = get_nutrition_info("영양성분검사", url_key)

            # 응답 화면으로 이동 시 현재_메뉴 초기화 (이전 버튼이 부모 메뉴로 돌아가도록)
            user_data.pop("현재_메뉴", None)

            if db_data and db_data.get("details"):
                # 데이터에 링크가 포함되어 있는지 확인
                if has_links_in_data(db_data['details']):
                    data_sections = parse_data_with_links(db_data['details'])
                    carousel_response = make_carousel_with_links_response(
                        user_input,
                        data_sections,
                        ["이전", "처음으로"]
                    )
                    if carousel_response:
                        return carousel_response

                # 9대/14대 영양성분 전용 포맷 적용
                formatted_data = format_nutrition_component_data(db_data['details'])
                response_text = f"📊 {user_input}\n\n{formatted_data}"
            else:
                response_text = f"📊 {user_input}\n\n자세한 내용은 아래 링크를 확인해주세요."

            if detail_url:
                return make_response_with_link(
                    response_text,
                    get_question_label("영양성분검사", url_key),
                    detail_url,
                    ["이전", "처음으로"]
                )
            else:
                return make_response(response_text, ["이전", "처음으로"])

        # ===== 일반 검사 메뉴 > 검사종류/검사안내 선택 시 DB 조회 =====
        general_menus = ["항생물질", "잔류농약", "방사능", "비건", "할랄", "동물DNA", "알레르기", "글루텐Free", "소비기한설정", "자가품질검사"]
        current_menu = user_data.get("검사분야_메뉴")

        # 메뉴별 처리 가능한 하위 항목 (DB 조회용)
        menu_items_map = {
            "항생물질": ["검사종류"],
            "잔류농약": ["검사종류"],
            "방사능": ["검사안내"],
            "비건": ["검사안내"],
            "할랄": ["검사안내"],
            "동물DNA": ["검사안내"],
            # 알레르기는 전용 핸들러로 처리 (분석종류, RT-PCR, Elisa)
            # 글루텐Free는 전용 핸들러로 처리
            "소비기한설정": ["가속실험", "실측실험"]
            # 검사주기알림은 전용 핸들러로 처리 (별도 포맷팅)
        }

        allowed_items = menu_items_map.get(current_menu, [])
        if current_menu in general_menus and user_input in allowed_items:
            # DB에서 크롤링된 데이터 조회
            db_data = get_nutrition_info(current_menu, user_input)

            # URL 가져오기
            detail_url = URL_MAPPING.get(current_menu, {}).get(user_input)

            # 응답 화면으로 이동 시 현재_메뉴 초기화 (이전 버튼이 부모 메뉴로 돌아가도록)
            user_data.pop("현재_메뉴", None)

            if db_data and db_data.get("details"):
                # 데이터에 링크가 포함되어 있는지 확인
                if has_links_in_data(db_data['details']):
                    # 링크가 있으면 캐러셀로 표시
                    data_sections = parse_data_with_links(db_data['details'])
                    carousel_response = make_carousel_with_links_response(
                        f"{current_menu} - {user_input}",
                        data_sections,
                        ["이전", "처음으로"]
                    )
                    if carousel_response:
                        return carousel_response

                # 링크가 없거나 캐러셀 생성 실패 시 일반 텍스트로 표시
                formatted_data = format_crawled_data(db_data['details'])
                response_text = f"📋 {current_menu} - {user_input}\n\n{formatted_data}"
            else:
                response_text = f"📋 {current_menu} - {user_input}\n\n크롤링된 데이터가 없습니다.\n서버에서 'python crawler.py'를 실행해주세요."

            if detail_url:
                return make_response_with_link(
                    response_text,
                    get_question_label(current_menu, user_input),
                    detail_url,
                    ["이전", "처음으로"]
                )
            else:
                return make_response(response_text, ["이전", "처음으로"])

        # ===== 자가품질검사 > 검사주기알림 =====
        if user_input == "검사주기알림" and user_data.get("검사분야_메뉴") == "자가품질검사":
            # 응답 화면으로 이동 시 현재_메뉴 초기화 (이전 버튼이 부모 메뉴로 돌아가도록)
            user_data.pop("현재_메뉴", None)

            response_text = """🔔 자가품질검사 주기알림

홈페이지에서 자가품질검사를 하신 경우, 작성하신 제조일자를 기준으로 자동 계산하여 카카오톡 알림을 발송해드립니다.

📌 알림 발송 시점
• 검사일 7일 전 (1차 알림)
• 검사일 1일 전 (2차 알림)

📋 검사주기 산정 기준
검사대상 식품을 처음으로 제조한 날(최초 생산일자)을 기준으로 주기를 산정합니다.

💡 예시
1개월 주기의 식품유형을 1월 20일 제조하여 자가품질검사 진행
→ 다음 검사는 2월 20일 제조한 제품으로 진행

❓ 검사기간에 제조한 제품이 없는 경우
검사기간이 도래하는 시기에 해당 제품의 생산이 없다면, 그 이후 최초로 제조·가공한 제품에 대해 자가품질검사를 하셔야 합니다."""
            detail_url = "https://www.biofl.co.kr/sub.jsp?code=7r9P7y94&question_198"
            return make_response_with_link(
                response_text,
                get_question_label("자가품질검사", "검사주기알림"),
                detail_url,
                ["이전", "처음으로"]
            )

        # ===== 자가품질검사 > 검사수수료 =====
        if user_input == "검사수수료" and user_data.get("검사분야_메뉴") == "자가품질검사":
            user_data.pop("현재_메뉴", None)

            response_text = """💰 검사 수수료 안내

검사 수수료는 많은 유형과 항목들로 인하여 홈페이지를 통해 견적서를 받아보실 수 있습니다.

📝 견적 요청 방법
홈페이지 → 고객지원 → 온라인견적&검사의뢰

✨ 홈페이지 이용 혜택
• 📋 견적서 1개월 저장
• 💳 홈페이지 카드결제 가능
• 📄 검사의뢰서 1년 저장
• 🔔 자가품질검사 알림 발송
• 🎁 이벤트 쿠폰 발급"""
            return make_response_with_link(
                response_text,
                "🔗 온라인견적&검사의뢰",
                "https://www.biofl.co.kr/sub.jsp?code=e7KU3a87",
                ["이전", "처음으로"]
            )

        # ===== 알레르기 > 분석종류 =====
        if user_input == "분석종류" and user_data.get("검사분야_메뉴") == "알레르기":
            # 응답 화면으로 이동 시 현재_메뉴 초기화 (이전 버튼이 부모 메뉴로 돌아가도록)
            user_data.pop("현재_메뉴", None)

            response_text = """📋 알레르기 - 분석종류

국내에 알려진 알레르기 물질 분석 방법에는 Strip 검사, RT-PCR을 활용한 DNA 검사, Elisa 장비를 활용한 알레르기 물질의 단백질 유무 검사가 있습니다.

📋 검사항목
• RT-PCR (DNA): 단백질처럼 특정 제조 가공 공정에 따라 분해될 확률이 적고 극미량으로도 검출이 가능. 단, 알레르기를 일으키는 항원의 활성 여부는 확인할 수 없음.
• ELISA protein: 알레르기를 일으키는 항원은 단백질로 구성되어 있으며, FDA에서는 알레르기 분석을 RT-PCR이 아닌 Elisa 장비를 활용하여 분석하고 있음.

⚠️ 참고사항
• 분석 시료: 알레르기 분석 시료는 완제품, Swab, 세척수, 분말 제품을 생산하는 경우 공기중의 알레르기 물질을 검증할 수 있습니다. 자세한 내용은 "홈페이지▶️사업분야▶️알레르기검사"를 참고바랍니다."""
            detail_url = "https://www.biofl.co.kr/sub.jsp?code=G7K3Y2F9&question_26"
            return make_response_with_link(
                response_text,
                get_question_label("알레르기", "분석종류"),
                detail_url,
                ["이전", "처음으로"]
            )

        # ===== 알레르기 > RT-PCR =====
        if user_input == "RT-PCR" and user_data.get("검사분야_메뉴") == "알레르기":
            user_data.pop("현재_메뉴", None)

            response_text = """🧬 알레르기 RT-PCR Kit

📋 보유 Kit
새우, 게, 대두, 소(우유), 돼지, 닭(달걀), 토마토, 땅콩, 복숭아, 참깨, 메밀, 밀, 고등어, 오징어, 전복, 홍합, 굴, 호두

📦 입고 예정
잣

⚠️ 검출 가능 종 안내
• 오징어: 살오징어, 아르헨티나오징어, 퍼플백오징어, 아메리카대왕오징어, 대왕오징어, 물오징어, 흰오징어, 창오징어, 참오징어
• 게: 꽃게, 점박이꽃게, 톱날꽃게, 민꽃게, 블루크랩, 홍게 등"""
            detail_url = "https://www.biofl.co.kr/sub.jsp?code=G7K3Y2F9&question_251"
            return make_response_with_link(
                response_text,
                get_question_label("알레르기", "RT-PCR"),
                detail_url,
                ["이전", "처음으로"]
            )

        # ===== 알레르기 > Elisa =====
        if user_input == "Elisa" and user_data.get("검사분야_메뉴") == "알레르기":
            user_data.pop("현재_메뉴", None)

            response_text = """🔬 알레르기 ELISA Kit

📋 보유 Kit
갑각류, 토탈대두, 대두, 땅콩, 참깨, 연체류/패류, 메밀, 우유, Gluten(밀,보리,호밀), 호두, 코코넛, 캐슈, 계란흰자, 베타-락토글로불린, 라이소자임, 오브알부민, 어류

📦 별도 문의 Kit
카제인, 루핀, 브라질넛, 마카다미아, 겨자, 피칸, 아몬드, 피스타치오, 헤이즐넛

⚠️ 참고사항
• 별도 문의 Kit는 입고까지 약 3~4주 소요됩니다.
• 고객지원 → 온라인견적&검사의뢰를 통해 견적 확인 후 의뢰 가능합니다."""
            detail_url = "https://www.biofl.co.kr/sub.jsp?code=G7K3Y2F9&question_151"
            return make_response_with_link(
                response_text,
                get_question_label("알레르기", "Elisa"),
                detail_url,
                ["이전", "처음으로"]
            )

        # ===== 글루텐Free > Free기준 =====
        if user_input == "Free기준" and user_data.get("검사분야_메뉴") == "글루텐Free":
            user_data.pop("현재_메뉴", None)

            response_text = """🌾 글루텐Free 검사와 표기

📌 글루텐(Gluten)이란?
밀, 보리, 호밀 등에서 글리아딘(Gliadin)과 글루테닌(Glutenin)으로 존재하다가 물과 결합하여 생기는 물질입니다.
반죽의 쫄깃한 식감을 주거나 빵을 부풀어 오르게 하는 역할을 하지만, 체질에 따라 복통이나 소화 불안정, 피부염 등을 유발할 수 있습니다.

📋 국내 '무 글루텐' 표시 기준
• 밀, 호밀, 보리, 귀리 또는 교배종을 원재료로 사용하지 않고 총 글루텐 함량이 20mg/kg 이하인 식품
• 글루텐을 제거한 원재료를 사용하여 총 글루텐 함량이 20mg/kg 이하인 식품

🌍 국외 기준
• 미국 FDA: 20 ppm 이하
• 유럽연합 EFSA: 무 글루텐(20ppm) 또는 저 글루텐(100ppm)

🔬 바이오푸드랩 검사
AOAC International 등재 Kit 사용으로 검사의 신뢰성과 정확성을 보장합니다."""
            detail_url = "https://www.biofl.co.kr/sub.jsp?code=EJ2GKW3&question_161"
            return make_response_with_link(
                response_text,
                get_question_label("글루텐Free", "Free기준"),
                detail_url,
                ["이전", "처음으로"]
            )

        # ===== 이물질검사 > 이물분석장비 =====
        if user_input == "이물분석장비" and user_data.get("검사분야_메뉴") == "이물질검사":
            user_data.pop("현재_메뉴", None)

            response_text = """🔬 이물 분석장비 안내

━━━━━━━━━━━━━━━
📋 보유 장비
━━━━━━━━━━━━━━━
• XRF (X선 형광분석기)
• FT-IR (적외선 분광기)
• RT-PCR (유전자 증폭기)

━━━━━━━━━━━━━━━
📌 분석 방법
━━━━━━━━━━━━━━━
상황에 맞게 장비를 선택하여 이물질 단일 분석 또는 비교군과 함께 비교 분석할 수 있습니다.

✨ 가장 정확한 방법
이물질과 비교군을 함께 분석하는 것이 가장 정확합니다.

━━━━━━━━━━━━━━━
⚠️ 참고사항
━━━━━━━━━━━━━━━
이물질 분석은 분쟁 해결에 활용될 수 있지만, 주 목적은 이물 저감화 개선을 위함입니다."""
            return make_response_with_link(
                response_text,
                "🔗 자세히보기",
                "https://blog.naver.com/biofl/223908869848",
                ["이전", "처음으로"]
            )

        # ===== 이물질검사 > 금속 =====
        if user_input == "금속" and user_data.get("검사분야_메뉴") == "이물질검사":
            user_data.pop("현재_메뉴", None)

            response_text = """🔬 금속 이물 분석 (XRF)

━━━━━━━━━━━━━━━
📌 분석 원리
━━━━━━━━━━━━━━━
XRF는 시료에 X선을 쏘면 각 원소가 고유한 형광 X선을 방출하는 원리를 이용합니다.

이를 통해 시료에 포함된 원소의 종류와 함량을 분석할 수 있습니다.

━━━━━━━━━━━━━━━
📋 분석 방법
━━━━━━━━━━━━━━━
이탈된 금속을 추정 물질과 비교했을 때, 같은 원소의 함량을 나타내는 경우 동일 물질로 추정할 수 있습니다.

━━━━━━━━━━━━━━━
⚠️ 참고사항
━━━━━━━━━━━━━━━
이물질 분석은 분쟁 해결에 활용될 수 있지만, 주 목적은 이물 저감화 개선을 위함입니다."""
            return make_response_with_link(
                response_text,
                "🔗 자세히보기",
                "https://blog.naver.com/biofl/223908869848",
                ["이전", "처음으로"]
            )

        # ===== 이물질검사 > 비닐/고무/플라스틱 =====
        if user_input == "비닐/고무/플라스틱" and user_data.get("검사분야_메뉴") == "이물질검사":
            user_data.pop("현재_메뉴", None)

            response_text = """🔬 비닐/고무/플라스틱 분석 (FT-IR)

━━━━━━━━━━━━━━━
📌 분석 원리
━━━━━━━━━━━━━━━
FT-IR은 고무, 비닐, 플라스틱 같은 고분자(폴리머) 분석에 가장 특성화된 장비입니다.

대상 물질의 적외선 흡수 패턴이 고유하여, 그 패턴에 따라 물질을 특정합니다.

━━━━━━━━━━━━━━━
📋 분석 가능 항목
━━━━━━━━━━━━━━━
• 식품 내 플라스틱/비닐 이물 재질 확인
• 포장재 성분 분석
• 고무패킹, 컨베이어 벨트 조각 등 오염원 추적

━━━━━━━━━━━━━━━
⚠️ 참고사항
━━━━━━━━━━━━━━━
이물질 분석은 분쟁 해결에 활용될 수 있지만, 주 목적은 이물 저감화 개선을 위함입니다."""
            return make_response_with_link(
                response_text,
                "🔗 자세히보기",
                "https://blog.naver.com/biofl/223475869519",
                ["이전", "처음으로"]
            )

        # ===== 이물질검사 > 기타 > 손톱 =====
        if user_input == "손톱" and user_data.get("검사분야_메뉴") == "이물질검사":
            user_data.pop("현재_메뉴", None)

            response_text = """🔬 손톱 이물 분석

━━━━━━━━━━━━━━━
📌 분석 방법
━━━━━━━━━━━━━━━
손톱은 비교군에 따라 장비를 달리해야 합니다.

• 뼈, 식물(예: 유자씨)과 비교 → XRF 분석
• 비닐, 플라스틱과 비교 → FT-IR 분석

━━━━━━━━━━━━━━━
📋 분석 결과
━━━━━━━━━━━━━━━
둘의 유사성을 그래프로 비교한 데이터로 유추할 수 있고, 라이브러리에 저장된 데이터라면 유사성이 높은 물질을 확인할 수 있습니다.

✨ 비교군이 없는 경우
단일로 XRF 분석 시 원소를 통해 확인 가능합니다.

━━━━━━━━━━━━━━━
⚠️ 참고사항
━━━━━━━━━━━━━━━
이물질 분석은 분쟁 해결에 활용될 수 있지만, 주 목적은 이물 저감화 개선을 위함입니다."""
            return make_response_with_link(
                response_text,
                "🔗 자세히보기",
                "https://blog.naver.com/biofl/223774874477",
                ["이전", "처음으로"]
            )

        # ===== 이물질검사 > 기타 > 뼈 =====
        if user_input == "뼈" and user_data.get("검사분야_메뉴") == "이물질검사":
            user_data.pop("현재_메뉴", None)

            response_text = """🔬 뼈 이물 분석

━━━━━━━━━━━━━━━
📌 분석 장비
━━━━━━━━━━━━━━━
뼈는 XRF, RT-PCR 등으로 분석이 가능합니다.

━━━━━━━━━━━━━━━
📋 분석 방법
━━━━━━━━━━━━━━━
• XRF: 구성 원소 확인
  → 비교할 원료의 뼈가 있다면 원소 함량을 비교하여 유사성 추적 가능

• RT-PCR: 동물 품종 특정 및 DNA 확인
  → 과도한 식품 첨가물 사용, 고열, 압착 처리 시 DNA 검출이 어려울 수 있음

━━━━━━━━━━━━━━━
⚠️ 참고사항
━━━━━━━━━━━━━━━
이물질 분석은 분쟁 해결에 활용될 수 있지만, 주 목적은 이물 저감화 개선을 위함입니다."""
            return make_response_with_link(
                response_text,
                "🔗 자세히보기",
                "https://blog.naver.com/biofl/224120912270",
                ["이전", "처음으로"]
            )

        # ===== 이물질검사 > 기타 > 원료의일부 =====
        if user_input == "원료의일부" and user_data.get("검사분야_메뉴") == "이물질검사":
            user_data.pop("현재_메뉴", None)

            response_text = """🔬 원료의 일부 이물 분석

━━━━━━━━━━━━━━━
📌 분석 방법
━━━━━━━━━━━━━━━
원료의 일부로 추정되는 물질은 이물질과 비교군을 동시에 하나의 장비로 확인할 때 가장 정확한 결과를 얻을 수 있습니다.

━━━━━━━━━━━━━━━
📋 장비 선택
━━━━━━━━━━━━━━━
다른 이물질 사례를 참고하여 적합한 장비를 선택할 수 있습니다.

• 금속류 → XRF
• 고분자(비닐, 플라스틱) → FT-IR
• 동물성 → RT-PCR

━━━━━━━━━━━━━━━
⚠️ 참고사항
━━━━━━━━━━━━━━━
이물질 분석은 분쟁 해결에 활용될 수 있지만, 주 목적은 이물 저감화 개선을 위함입니다."""
            return make_response_with_link(
                response_text,
                "🔗 자세히보기",
                "https://blog.naver.com/biofl/223655921728",
                ["이전", "처음으로"]
            )

        # ===== 이물질검사 > 기타 > 탄화물 =====
        if user_input == "탄화물" and user_data.get("검사분야_메뉴") == "이물질검사":
            user_data.pop("현재_메뉴", None)

            response_text = """🔬 탄화물 이물 분석 (FT-IR)

━━━━━━━━━━━━━━━
📌 탄화물이란?
━━━━━━━━━━━━━━━
식품을 조리하는 과정에서 탄화물은 어쩔 수 없이 발생하는 물질입니다.

특히 고온에서 요리할 때 단백질과 당분이 반응하는 '마이야르 반응'과 함께 탄화물이 형성됩니다.

━━━━━━━━━━━━━━━
📋 분석 방법
━━━━━━━━━━━━━━━
FT-IR로 분석하여 Glycerol, Cellulose(섬유질) 등을 확인하여 식품의 탄화물인지 확인할 수 있습니다.

━━━━━━━━━━━━━━━
⚠️ 참고사항
━━━━━━━━━━━━━━━
이물질 분석은 분쟁 해결에 활용될 수 있지만, 주 목적은 이물 저감화 개선을 위함입니다."""
            return make_response_with_link(
                response_text,
                "🔗 자세히보기",
                "https://blog.naver.com/biofl/223621551214",
                ["이전", "처음으로"]
            )

        # ===== 소비기한설정 > 검사수수료 =====
        if user_input == "검사수수료" and user_data.get("검사분야_메뉴") == "소비기한설정":
            # 응답 화면으로 이동 시 현재_메뉴 초기화 (이전 버튼이 부모 메뉴로 돌아가도록)
            user_data.pop("현재_메뉴", None)

            response_text = """💰 소비기한설정 검사수수료 안내

📋 필요 자료
소비기한설정 실험은 식품유형, 제조방법, 원재료, 포장재질 등의 자료가 필요합니다.

📝 견적 요청 방법
홈페이지 "고객지원 → 온라인견적&검사의뢰"에서 소비기한설정검사 의뢰서를 작성해 주시면 내용을 토대로 견적서를 작성하여 보내드립니다.

💵 대략적인 비용
• 실측실험: 100만원 ~ 200만원
• 가속실험: 200만원 ~ 350만원

※ 구체적인 예상 비용은 견적서를 통해 전달드리겠습니다.

💡 예산 맞춤 안내
실험 비용에 사용 가능한 예산을 알려주시면, 해당 금액에 맞는 스케쥴을 짜드립니다."""
            return make_response_with_link(
                response_text,
                "🔗 온라인견적&검사의뢰",
                "https://www.biofl.co.kr/sub.jsp?code=e7KU3a87",
                ["이전", "처음으로"]
            )

        # ===== 홈페이지안내 > 견적서 =====
        if user_input == "견적서" and user_data.get("현재_메뉴") == "홈페이지안내":
            user_data.pop("현재_메뉴", None)

            response_text = """📋 견적서 안내

회원 가입 후 24시간 견적서를 제공합니다.

✨ 견적서 서비스
• 📁 견적서 1개월간 보관
• 🔗 의뢰서와 연동 (검사항목 자동 입력)
• 📄 검사 이후 거래명세서 별도 제공

📝 이용 방법
홈페이지 → 고객지원 → 온라인견적&검사의뢰"""
            return make_response_with_link(
                response_text,
                "🔗 온라인견적&검사의뢰",
                "https://www.biofl.co.kr/sub.jsp?code=e7KU3a87",
                ["이전", "처음으로"]
            )

        # ===== 홈페이지안내 > 의뢰서작성 =====
        if user_input == "의뢰서작성" and user_data.get("현재_메뉴") == "홈페이지안내":
            user_data.pop("현재_메뉴", None)

            response_text = """📝 의뢰서 작성 안내

의뢰서 작성 방법은 로그인 이후 고객 Zone에서 각 분야별 검사 의뢰서 작성 방법을 안내해 드리고 있습니다.

📌 작성 순서
1. 홈페이지 회원가입 및 로그인
2. 고객 Zone 접속
3. 분야별 검사 의뢰서 선택
4. 의뢰서 작성 완료"""
            return make_response_with_link(
                response_text,
                "🔗 온라인견적&검사의뢰",
                "https://www.biofl.co.kr/sub.jsp?code=e7KU3a87",
                ["이전", "처음으로"]
            )

        # ===== 홈페이지안내 > 할인쿠폰 =====
        if user_input == "할인쿠폰" and user_data.get("현재_메뉴") == "홈페이지안내":
            user_data.pop("현재_메뉴", None)

            response_text = """🎁 할인쿠폰 안내

회원을 대상으로 이벤트 쿠폰을 발송해 드리고 있습니다.

🎟️ 쿠폰 종류
• 💵 금액권 (예: 10,000원)
• 📊 할인권 (예: 10% 할인)

⚠️ 유의사항
• 자가품질검사는 쿠폰 적용 제외
• 쿠폰 유효기간 경과 시 자동 소멸

💡 쿠폰 받는 방법
회원가입 후 이벤트 참여"""
            return make_response(response_text, ["이전", "처음으로"])

        # ===== 성적서문의 > 외국어 =====
        if user_input == "외국어" and user_data.get("현재_메뉴") == "성적서문의":
            user_data.pop("현재_메뉴", None)

            response_text = """🌍 외국어 성적서 안내

현재 영문 성적서만 발행 가능합니다.

📝 신청 방법
1. 홈페이지 자료실에서 영문성적서 신청서 다운로드
2. 신청서 작성 후 이메일 발송
3. 검사 의뢰한 성적서에 한하여 발행

📧 이메일: qa@biofl.co.kr

📁 양식 다운로드
홈페이지 → 고객지원 → 자료실"""
            return make_response_with_link(
                response_text,
                "🔗 자료실 바로가기",
                "https://www.biofl.co.kr/sub.jsp?code=zW8P5EZl",
                ["이전", "처음으로"]
            )

        # ===== 성적서문의 > 발급문의 =====
        if user_input == "발급문의" and user_data.get("현재_메뉴") == "성적서문의":
            user_data.pop("현재_메뉴", None)

            response_text = """📄 성적서 발급 문의

⏰ 처리기한
영문/국문 성적서 발급은 워킹데이 기준 1~2일

📬 발송 방법
별도 처리기한 지정 시, 해당일 오후 6시까지 요청하신 방법(팩스, 이메일 등)으로 발송됩니다.

📞 문의 전화
070-7410-1404"""
            return make_response(response_text, ["이전", "처음으로"])

        # ===== 시료접수안내 > 시료접수 =====
        if user_input == "시료접수" and user_data.get("현재_메뉴") == "시료접수안내":
            user_data.pop("현재_메뉴", None)

            response_text = """📦 시료접수 안내

시료접수 방법을 영상으로 확인하실 수 있습니다.

🎬 시료접수 안내 영상
아래 버튼을 클릭하시면 유튜브 영상으로 이동합니다."""
            return make_response_with_link(
                response_text,
                "▶️ 영상 보기",
                "https://youtu.be/jSfKfBvDw28?si=JmXcNdori4kffbnN",
                ["이전", "처음으로"]
            )

        # ===== 시료접수안내 > 방문수거 =====
        if user_input == "방문수거" and user_data.get("현재_메뉴") == "시료접수안내":
            user_data.pop("현재_메뉴", None)

            response_text = """🚗 방문수거 안내

시료 방문수거 서비스를 제공하고 있습니다.

📋 자세한 안내
아래 버튼을 클릭하시면 블로그에서 상세 내용을 확인하실 수 있습니다."""
            return make_response_with_link(
                response_text,
                "📝 블로그 보기",
                "https://blog.naver.com/biofl/223526211851",
                ["이전", "처음으로"]
            )

        # ===== 시료접수안내 > 오시는길 =====
        if user_input == "오시는길" and user_data.get("현재_메뉴") == "시료접수안내":
            user_data.pop("현재_메뉴", None)

            response_text = """📍 오시는길

바이오푸드랩 위치 안내입니다.

🏢 주소
아래 버튼을 클릭하시면 홈페이지에서 상세 위치를 확인하실 수 있습니다."""
            return make_response_with_link(
                response_text,
                "🗺️ 위치 보기",
                "https://www.biofl.co.kr/sub.jsp?code=05WAdu5F",
                ["이전", "처음으로"]
            )

        # ===== 검사분야 말단 메뉴 응답 =====
        if user_input in INSPECTION_MENU["responses"]:
            # 응답 화면으로 이동 시 현재_메뉴 초기화 (이전 버튼이 부모 메뉴로 돌아가도록)
            user_data.pop("현재_메뉴", None)

            response_data = INSPECTION_MENU["responses"][user_input]
            return make_response(
                response_data["text"],
                ["이전", "처음으로"]
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
                        formatted_items = format_items_list(result['items'], user_data["분야"])
                        response_text = f"📷 이미지에서 '{food_type}'을(를) 찾았습니다.\n\n"
                        response_text += f"✅ [{result['food_type']}]의 검사 항목:\n\n{formatted_items}"
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
                        formatted_cycle = format_korean_spacing(result['cycle'])
                        formatted_food_type = format_korean_spacing(result['food_type'])
                        response_text = f"📷 이미지에서 '{food_type}'을(를) 찾았습니다.\n\n"
                        response_text += f"✅ [{result['food_group']}] {formatted_food_type}의 검사주기:\n\n{formatted_cycle}"
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
            save_to_history(user_data)  # 히스토리 저장
            user_data["기능"] = user_input
            user_data.pop("분야", None)
            user_data.pop("업종", None)
            return make_response(
                f"[{user_input}] 검사할 분야를 선택해주세요.",
                ["식품", "축산", "이전", "처음으로"]
            )

        # Step 2: 분야 선택
        if user_input in ["식품", "축산"]:
            if "기능" not in user_data:
                return make_response(
                    "먼저 원하시는 서비스를 선택해주세요.",
                    ["검사주기", "검사항목"]
                )

            save_to_history(user_data)  # 히스토리 저장
            user_data["분야"] = user_input

            # 식품유형 힌트 확인
            food_hint = user_data.get("식품유형_힌트")

            if user_data["기능"] == "검사주기":
                # 검사주기: 업종 선택 필요
                if user_input == "식품":
                    buttons = ["식품제조가공업", "즉석판매제조가공업", "이전", "처음으로"]
                else:
                    buttons = ["축산물제조가공업", "축산물즉석판매제조가공업", "이전", "처음으로"]
                return make_response(
                    f"[{user_input}] 검사할 업종을 선택해주세요.",
                    buttons
                )
            else:
                # 검사항목: 바로 식품 유형 입력
                msg = f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n예: 과자, 음료, 소시지 등\n\n(주의 : 품목제조보고서에 표기된 \"식품유형\"을 입력하세요. 단어에 가운데 점이 있는 경우 제외하고 입력하세요)"
                if food_hint:
                    msg += f"\n\n💡 '{food_hint}'(으)로 검색하시려면 그대로 입력해주세요."
                return make_response(msg, ["이전", "처음으로"])

        # Step 3: 업종 선택 (검사주기만 해당)
        if user_input in ["식품제조가공업", "즉석판매제조가공업", "축산물제조가공업", "축산물즉석판매제조가공업"]:
            if user_data.get("기능") != "검사주기":
                return make_response(
                    "먼저 원하시는 서비스를 선택해주세요.",
                    ["검사주기", "검사항목"]
                )

            save_to_history(user_data)  # 히스토리 저장
            user_data["업종"] = user_input

            # 식품유형 힌트 확인
            food_hint = user_data.get("식품유형_힌트")
            hint_msg = f"\n\n💡 '{food_hint}'(으)로 검색하시려면 그대로 입력해주세요." if food_hint else ""

            # 식품제조가공업, 축산물제조가공업은 품목제조보고서 주의 메시지
            if user_input in ["식품제조가공업", "축산물제조가공업"]:
                msg = f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n예: 과자, 음료, 소시지 등\n\n(주의 : 품목제조보고서에 표기된 \"식품유형\"을 입력하세요. 단어에 가운데 점이 있는 경우 제외하고 입력하세요)"
                return make_response(msg + hint_msg, ["이전", "처음으로"])
            elif user_input == "즉석판매제조가공업":
                # 즉석판매제조가공업은 영업신고증 주의 메시지 + 바로가기 버튼
                message = f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n"
                message += "예: 과자, 음료, 소시지 등\n\n"
                message += "(주의 : 영업신고증에 표기된 \"식품유형\"을 입력하세요. 단어에 가운데 점이 있는 경우 제외하고 입력하세요.)\n\n"
                message += "* 주의 즉석판매제조가공업은 영업등록증에 표기된 식품의 유형만 자가품질검사 대상이 됩니다.\n\n"
                message += "대상은 바로가기 버튼을 클릭하여 Q5. [즉석판매제조가공업] 자가품질검사 대상식품 및 검사주기를 참고해주세요."
                message += hint_msg
                return make_response_with_link(
                    message,
                    "바로가기",
                    "https://www.biofl.co.kr/sub.jsp?code=7r9P7y94",
                    ["이전", "처음으로"]
                )
            else:
                # 축산물즉석판매제조가공업은 신고필증 주의 메시지 + 바로가기 버튼
                message = f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n"
                message += "예: 과자, 음료, 소시지 등\n\n"
                message += "(주의 : 신고필증에 표기된 \"식품유형\"을 입력하세요. 단어에 가운데 점이 있는 경우 제외하고 입력하세요.)\n\n"
                message += "* 주의 축산물즉석판매제조가공업은 신고필증에 표기된 식품의 유형을 확인해주시고 바로가기 버튼을 클릭하여 \"Q5. [식육즉석판매가공업] 자가품질검사 대상식품 및 검사주기\"를 참고해 주세요."
                message += hint_msg
                return make_response_with_link(
                    message,
                    "바로가기",
                    "https://www.biofl.co.kr/sub.jsp?code=XN0Cd4r7",
                    ["이전", "처음으로"]
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
                    formatted_items = format_items_list(result['items'], user_data["분야"])
                    response_text = f"✅ [{result['food_type']}]의 검사 항목:\n\n{formatted_items}"
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
                    elif user_data["실패횟수"] >= 2 and is_vision_api_available():
                        # 2회 이상 실패 시 이미지 업로드 안내 (Vision API 사용 가능 시)
                        response_text = f"❌ '{food_type}'에 대한 검사 항목을 찾을 수 없습니다.\n\n"
                        response_text += "📷 품목제조보고서 또는 영업등록증/신고증/허가증 이미지를 업로드하시면 자동으로 식품유형을 찾아드립니다."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"
                    else:
                        response_text = f"❌ '{food_type}'에 대한 검사 항목을 찾을 수 없습니다.\n\n"
                        response_text += "☆ 식품 유형을 1회 잘못 입력하셨습니다.\n\n"
                        response_text += "품목제조보고서의 \"식품의 유형\"을 확인하여 다시 한번 입력하거나, [종료]를 눌러주세요."
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
                    formatted_cycle = format_korean_spacing(result['cycle'])
                    formatted_food_type = format_korean_spacing(result['food_type'])
                    response_text = f"✅ [{result['food_group']}] {formatted_food_type}의 검사주기:\n\n{formatted_cycle}"
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
                    elif user_data["실패횟수"] >= 2 and is_vision_api_available():
                        # 2회 이상 실패 시 이미지 업로드 안내 (Vision API 사용 가능 시)
                        response_text = f"❌ '{food_type}'에 대한 검사주기를 찾을 수 없습니다.\n\n"
                        if user_data["업종"] in ["식품제조가공업", "축산물제조가공업"]:
                            response_text += "📷 품목제조보고서 이미지를 업로드하시면 자동으로 식품유형을 찾아드립니다."
                        else:
                            response_text += "📷 영업등록증 또는 신고증/허가증 이미지를 업로드하시면 자동으로 식품유형을 찾아드립니다."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"
                    else:
                        response_text = f"❌ '{food_type}'에 대한 검사주기를 찾을 수 없습니다.\n\n"
                        response_text += "☆ 식품 유형을 1회 잘못 입력하셨습니다.\n\n"
                        response_text += "품목제조보고서의 \"식품의 유형\"을 확인하여 다시 한번 입력하거나, [종료]를 눌러주세요."
                        if similar:
                            response_text += f"\n\n🔍 유사한 항목: {', '.join(similar)}"

                    return make_response(response_text, ["종료"])

        # ===== NLP 모드: 번호 선택 처리 =====
        if user_data.get("nlp_모드"):
            nlp_results = user_data.get("nlp_검색결과", [])
            all_results = user_data.get("nlp_전체결과", [])

            # "더보기" 처리
            if user_input == "더보기":
                page_size = 5
                current_page = user_data.get("nlp_현재페이지", 0) + 1
                user_data["nlp_현재페이지"] = current_page

                start_idx = current_page * page_size
                end_idx = start_idx + page_size
                page_results = all_results[start_idx:end_idx]

                if page_results:
                    user_data["nlp_검색결과"] = page_results
                    user_data["nlp_남은횟수"] = len(page_results)

                    response_text = f"🔍 추가 검색 결과 ({start_idx + 1}~{start_idx + len(page_results)}번):\n\n"
                    buttons = []
                    for i, r in enumerate(page_results, 1):
                        title_short = r['title'][:35] + "..." if len(r['title']) > 35 else r['title']
                        response_text += f"{i}. {title_short}\n"
                        buttons.append(str(i))

                    response_text += "\n번호를 선택해주세요."

                    # 더 많은 결과가 있으면 "더보기" 버튼 추가
                    if len(all_results) > end_idx:
                        buttons.append("더보기")
                        response_text += f"\n(총 {len(all_results)}개 중 {start_idx + 1}~{start_idx + len(page_results)}번)"

                    buttons.append("처음으로")
                    return make_response(response_text, buttons)
                else:
                    response_text = "더 이상 검색 결과가 없습니다."
                    return make_response(response_text, ["처음으로"])

            # 숫자 입력 확인
            if user_input.isdigit():
                selected_idx = int(user_input)
                if 1 <= selected_idx <= len(nlp_results):
                    selected_qa = nlp_results[selected_idx - 1]
                    title = selected_qa.get("title", "")

                    # ===== 검사주기/검사항목 Q&A: 버튼 로직으로 연결 =====
                    # 식품/축산 + 검사주기/검사항목 패턴 감지
                    is_food_cycle = "식품" in title and "검사주기" in title and "축산" not in title
                    is_food_item = "식품" in title and "검사항목" in title and "축산" not in title
                    is_livestock_cycle = "축산" in title and "검사주기" in title
                    is_livestock_item = "축산" in title and "검사항목" in title

                    if is_food_item or is_livestock_item or is_food_cycle or is_livestock_cycle:
                        # NLP 모드 종료
                        user_data.pop("nlp_모드", None)
                        user_data.pop("nlp_검색결과", None)
                        user_data.pop("nlp_전체결과", None)
                        user_data.pop("nlp_현재페이지", None)
                        user_data.pop("nlp_남은횟수", None)
                        user_data.pop("nlp_선택", None)
                        user_data.pop("nlp_선택완료", None)

                        # 식품 검사항목 → 검사항목 > 식품
                        if is_food_item:
                            user_data["기능"] = "검사항목"
                            user_data["분야"] = "식품"
                            return make_response(
                                "[식품] 검사할 식품 유형을 입력해주세요.\n\n예: 과자, 음료, 소시지 등\n\n(주의 : 품목제조보고서에 표기된 \"식품유형\"을 입력하세요. 단어에 가운데 점이 있는 경우 제외하고 입력하세요)",
                                ["이전", "처음으로"]
                            )
                        # 축산 검사항목 → 검사항목 > 축산
                        elif is_livestock_item:
                            user_data["기능"] = "검사항목"
                            user_data["분야"] = "축산"
                            return make_response(
                                "[축산] 검사할 식품 유형을 입력해주세요.\n\n예: 햄, 소시지, 베이컨 등\n\n(주의 : 품목제조보고서에 표기된 \"식품유형\"을 입력하세요. 단어에 가운데 점이 있는 경우 제외하고 입력하세요)",
                                ["이전", "처음으로"]
                            )
                        # 식품 검사주기 → 검사주기 > 식품 > 업종 선택
                        elif is_food_cycle:
                            user_data["기능"] = "검사주기"
                            user_data["분야"] = "식품"
                            return make_response(
                                "📋 [식품] 검사주기 조회\n\n업종을 선택해주세요.",
                                ["식품제조가공업", "즉석판매제조가공업", "이전", "처음으로"]
                            )
                        # 축산 검사주기 → 검사주기 > 축산 > 업종 선택
                        elif is_livestock_cycle:
                            user_data["기능"] = "검사주기"
                            user_data["분야"] = "축산"
                            return make_response(
                                "📋 [축산] 검사주기 조회\n\n업종을 선택해주세요.",
                                ["축산물제조가공업", "축산물즉석판매제조가공업", "이전", "처음으로"]
                            )

                    # 선택 완료 목록에 추가
                    if "nlp_선택완료" not in user_data:
                        user_data["nlp_선택완료"] = []
                    if selected_idx not in user_data["nlp_선택완료"]:
                        user_data["nlp_선택완료"].append(selected_idx)

                    user_data["nlp_선택"] = selected_idx

                    # 답변 표시
                    content = selected_qa.get("content", "")
                    category = selected_qa.get("category", "")

                    # 답변 텍스트 구성 (최대 1000자)
                    response_text = f"📌 [{category}] {title}\n\n"
                    if content:
                        if len(content) > 900:
                            response_text += content[:900] + "...\n\n(전체 내용은 홈페이지를 참고해주세요)"
                        else:
                            response_text += content

                    remaining = user_data.get("nlp_남은횟수", 0)
                    if remaining > 0:
                        response_text += f"\n\n💡 원하시는 답변이 아니라면 [이전]을 눌러주세요. (남은 횟수: {remaining}회)"
                        buttons = ["이전", "처음으로"]
                    else:
                        response_text += "\n\n검색이 완료되었습니다."
                        buttons = ["처음으로"]
                        # NLP 모드 종료
                        user_data.pop("nlp_모드", None)
                        user_data.pop("nlp_검색결과", None)
                        user_data.pop("nlp_전체결과", None)
                        user_data.pop("nlp_현재페이지", None)
                        user_data.pop("nlp_남은횟수", None)
                        user_data.pop("nlp_선택", None)
                        user_data.pop("nlp_선택완료", None)

                    return make_response(response_text, buttons)

            # 잘못된 입력
            response_text = "번호를 다시 선택해주세요.\n\n"
            buttons = []
            for i, r in enumerate(nlp_results, 1):
                title_short = r['title'][:35] + "..." if len(r['title']) > 35 else r['title']
                response_text += f"{i}. {title_short}\n"
                buttons.append(str(i))
            buttons.append("처음으로")
            return make_response(response_text, buttons)

        # ===== 비용/수수료 관련 질문 처리 =====
        cost_keywords = ["비용", "수수료", "가격", "단가", "얼마", "요금"]
        if any(kw in user_input for kw in cost_keywords):
            # 비용 모드 시작
            user_data["비용문의_모드"] = True

            # 영양성분 관련 키워드가 있는지 확인
            if "영양" in user_input or "영양성분" in user_input:
                user_data["비용문의_분야"] = "영양성분"
                return make_response(
                    "💰 영양성분 검사 비용 안내입니다.\n\n검사 종류를 선택해주세요.",
                    ["5대 영양성분", "9대 영양성분", "14대 영양성분", "처음으로"]
                )
            else:
                return make_response(
                    "💰 검사 비용 안내입니다.\n\n어떤 검사의 비용을 확인하시겠어요?",
                    ["영양성분", "자가품질검사", "기타", "처음으로"]
                )

        # ===== 비용문의 모드: 분야 선택 처리 =====
        if user_data.get("비용문의_모드"):
            if user_input == "영양성분":
                user_data["비용문의_분야"] = "영양성분"
                return make_response(
                    "💰 영양성분 검사 비용 안내입니다.\n\n검사 종류를 선택해주세요.",
                    ["5대 영양성분", "9대 영양성분", "14대 영양성분", "이전", "처음으로"]
                )
            elif user_input in ["자가품질검사", "기타"]:
                user_data.pop("비용문의_모드", None)
                return make_response(
                    "💰 검사 수수료 안내입니다.\n\n검사 수수료는 식품유형, 살균여부, 보관방법 등에 따라 검사항목과 비용이 달라집니다.\n\n정확한 비용 확인을 위해 [검사항목] 메뉴를 이용해주세요.",
                    ["검사항목", "처음으로"]
                )
            elif user_input in ["9대 영양성분", "14대 영양성분"]:
                # 기존 영양성분검사 로직으로 연결
                user_data.pop("비용문의_모드", None)
                user_data.pop("비용문의_분야", None)
                user_data["영양성분_검사종류"] = True
                user_data["검사분야_메뉴"] = "영양성분검사"

                # 기존 9대/14대 로직 실행 (위의 코드에서 처리됨)
                url_key = user_input.replace(" ", "")
                detail_url = URL_MAPPING.get("영양성분검사", {}).get(url_key)
                db_data = get_nutrition_info("영양성분검사", url_key)

                if db_data and db_data.get("details"):
                    formatted_data = format_nutrition_component_data(db_data['details'])
                    response_text = f"💰 {user_input} 검사비용\n\n{formatted_data}"
                else:
                    response_text = f"💰 {user_input} 검사비용\n\n자세한 내용은 아래 링크를 확인해주세요."

                if detail_url:
                    return make_response_with_link(
                        response_text,
                        get_question_label("영양성분검사", url_key),
                        detail_url,
                        ["이전", "처음으로"]
                    )
                else:
                    return make_response(response_text, ["이전", "처음으로"])
            elif user_input == "5대 영양성분":
                # 5대 영양성분은 크롤링 데이터 사용
                user_data["비용문의_분야"] = "영양성분"

                response_text = "💰 5대 영양성분 검사비용\n\n"
                response_text += "• 종류: 어린이기호식품 5대\n"
                response_text += "• 항목: 열량, 당류, 단백질, 포화지방, 나트륨\n"
                response_text += "• 수수료: 150,000원 (부가세 별도)\n"
                response_text += "• 소요기간: 영업일 기준 12일"

                return make_response(response_text, ["이전", "처음으로"])
            elif user_input == "이전":
                # 비용문의_분야가 영양성분이면 영양성분 목록으로
                if user_data.get("비용문의_분야") == "영양성분":
                    return make_response(
                        "💰 영양성분 검사 비용 안내입니다.\n\n검사 종류를 선택해주세요.",
                        ["5대 영양성분", "9대 영양성분", "14대 영양성분", "이전", "처음으로"]
                    )
                else:
                    return make_response(
                        "💰 검사 비용 안내입니다.\n\n어떤 검사의 비용을 확인하시겠어요?",
                        ["영양성분", "자가품질검사", "기타", "처음으로"]
                    )

        # ===== 자연어 "식품유형 + 주기/항목" 패턴 감지 → 버튼 로직 연결 =====
        # 예: "과자 검사주기", "빵류 항목", "소시지 검사항목", "유형 주기", "유형 항목"
        cycle_pattern = re.search(r'(.+?)\s*(검사\s*주기|주기)', user_input)
        item_pattern = re.search(r'(.+?)\s*(검사\s*항목|항목)', user_input)

        if cycle_pattern or item_pattern:
            if cycle_pattern:
                food_type_candidate = cycle_pattern.group(1).strip()
                target_function = "검사주기"
            else:
                food_type_candidate = item_pattern.group(1).strip()
                target_function = "검사항목"

            # 일반적인 단어 필터링
            generic_words = ["유형", "식품", "축산", "의", "에", "를", "을", ""]

            # 기능 설정
            user_data["기능"] = target_function

            # 유효한 식품유형이 있으면 힌트로 저장
            if food_type_candidate and food_type_candidate not in generic_words and len(food_type_candidate) >= 2:
                user_data["식품유형_힌트"] = food_type_candidate

            # 항상 분야 선택부터 시작
            return make_response(
                f"📋 {target_function} 조회\n\n분야를 선택해주세요.",
                ["식품", "축산", "이전", "처음으로"]
            )

        # ===== NLP 검색 (자연어 질문 처리) =====
        if NLP_AVAILABLE and len(user_input) >= 5:
            # 메뉴 키워드가 아닌 경우에만 NLP 검색
            menu_keywords = ["검사분야", "검사주기", "검사항목", "자가품질검사", "영양성분검사",
                            "식품", "축산", "배합 함량", "당알코올 계산", "표시대상확인"]
            if user_input not in menu_keywords:
                nlp_results = search_qa_by_query(user_input, top_n=15, min_score=2)  # 최대 15개 검색

                if nlp_results:
                    logger.info(f"[{user_id}] NLP 검색 결과: {len(nlp_results)}개")

                    # NLP 모드 시작
                    user_data["nlp_모드"] = True
                    user_data["nlp_전체결과"] = nlp_results  # 전체 결과 저장
                    user_data["nlp_현재페이지"] = 0  # 현재 페이지 (0부터 시작)
                    user_data["nlp_선택완료"] = []

                    # 첫 페이지 5개 표시
                    page_size = 5
                    current_page = 0
                    start_idx = current_page * page_size
                    end_idx = start_idx + page_size
                    page_results = nlp_results[start_idx:end_idx]

                    user_data["nlp_검색결과"] = page_results
                    user_data["nlp_남은횟수"] = len(page_results)

                    response_text = "🔍 관련 Q&A를 찾았습니다:\n\n"
                    buttons = []
                    for i, r in enumerate(page_results, 1):
                        title_short = r['title'][:35] + "..." if len(r['title']) > 35 else r['title']
                        response_text += f"{i}. {title_short}\n"
                        buttons.append(str(i))

                    response_text += "\n번호를 선택해주세요."

                    # 더 많은 결과가 있으면 "더보기" 버튼 추가
                    if len(nlp_results) > end_idx:
                        buttons.append("더보기")
                        response_text += f"\n(총 {len(nlp_results)}개 중 1~{len(page_results)}번)"

                    buttons.append("처음으로")

                    return make_response(response_text, buttons)

        # 기본 응답 (의도 파악 실패 시)
        return make_response(
            "질문에 답변을 드리지 못해서 죄송합니다.\n\n채팅방 메뉴 \"채널 이동\" 메뉴를 통해 직원에게 문의하여 주시기 바랍니다.\n\n(업무 시간 09:00~17:30)",
            ["검사분야", "검사주기", "검사항목", "처음으로"]
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
