"""
카카오 챗봇 API 서버
- 카카오 i 오픈빌더 스킬 서버
- DB에서 검사항목/검사주기 조회
"""
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
import logging

from config import SERVER_HOST, SERVER_PORT, LOG_FILE, LOG_FORMAT, URL_MAPPING, DISPLAY_Q_NUMBER
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
    get_nutrition_info
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


def format_items_list(items_text: str) -> str:
    """콤마로 구분된 항목들을 줄바꿈된 리스트 형식으로 변환

    괄호 [], () 안의 콤마는 항목 구분자가 아니므로 무시
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

    # 각 항목에 띄어쓰기 추가 후 bullet point로 포맷팅
    formatted_items = []
    for item in items:
        formatted_item = format_korean_spacing(item)
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
                    "basicCard": {
                        "description": text,
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
            "buttons": ["식품", "축산", "검사주기알림", "이전", "처음으로"],
            "parent": "검사분야"
        },
        "영양성분검사": {
            "title": "영양성분검사",
            "buttons": ["검사종류", "표시대상확인", "1회제공량산표", "이전", "처음으로"],
            "parent": "검사분야"
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
            "buttons": ["금속류", "고무/플라스틱", "기타", "이전", "처음으로"],
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
            "buttons": ["손톱", "뼈", "더보기", "이전", "처음으로"],
            "parent": "이물질검사"
        },
        "더보기": {
            "title": "이물질검사 - 기타 더보기",
            "buttons": ["탄화물", "원료의일부", "모르겠음", "이전", "처음으로"],
            "parent": "기타"
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
                "안녕하세요! 바이오푸드랩 챗봇[바푸]입니다.\n\n원하시는 서비스를 선택해주세요.",
                ["검사분야", "검사주기", "검사항목"]
            )

        # "이전" 버튼 처리
        if user_input == "이전":
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

            # 일반 검사 메뉴 상태 저장 (항생물질, 잔류농약, 방사능, 비건, 할랄, 동물DNA, 알레르기, 글루텐Free, 소비기한설정)
            if user_input in ["항생물질", "잔류농약", "방사능", "비건", "할랄", "동물DNA", "알레르기", "글루텐Free", "소비기한설정"]:
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

        # ===== 영양성분검사 > 표시대상확인, 1회제공량산표 선택 시 DB 조회 =====
        if user_data.get("검사분야_메뉴") == "영양성분검사" and user_input in ["표시대상확인", "1회제공량산표"]:
            # DB에서 크롤링된 데이터 조회
            db_data = get_nutrition_info("영양성분검사", user_input)

            # URL 가져오기
            detail_url = URL_MAPPING.get("영양성분검사", {}).get(user_input)

            # 응답 화면으로 이동 시 현재_메뉴 초기화
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

                formatted_data = format_crawled_data(db_data['details'])
                response_text = f"📋 {user_input}\n\n{formatted_data}"
            else:
                response_text = f"📋 {user_input}\n\n크롤링된 데이터가 없습니다.\n서버에서 'python crawler.py'를 실행해주세요."

            if detail_url:
                return make_response_with_link(
                    response_text,
                    get_question_label("영양성분검사", user_input),
                    detail_url,
                    ["이전", "처음으로"]
                )
            else:
                return make_response(response_text, ["이전", "처음으로"])

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
            "글루텐Free": ["Free기준"],
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
땅콩, 아몬드, 캐슈넛, 헤이즐넛, 호두, 피칸, 마카다미아, 피스타치오, 브라질넛, 대두, 루핀, 겨자, 셀러리, 참깨, 밀(글리아딘), 메밀, 달걀, 우유

⚠️ 참고사항
• ELISA는 단백질 기반 분석으로 알레르기 항원의 활성 여부를 확인할 수 있습니다.
• FDA 권장 분석 방법입니다."""
            detail_url = "https://www.biofl.co.kr/sub.jsp?code=G7K3Y2F9&question_151"
            return make_response_with_link(
                response_text,
                get_question_label("알레르기", "Elisa"),
                detail_url,
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
                        formatted_items = format_items_list(result['items'])
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
                return make_response(
                    f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n예: 과자, 음료, 소시지 등\n\n(주의 : 품목제조보고서에 표기된 \"식품유형\"을 입력하세요. 단어에 가운데 점이 있는 경우 제외하고 입력하세요)",
                    ["이전", "처음으로"]
                )

        # Step 3: 업종 선택 (검사주기만 해당)
        if user_input in ["식품제조가공업", "즉석판매제조가공업", "축산물제조가공업", "축산물즉석판매제조가공업"]:
            if user_data.get("기능") != "검사주기":
                return make_response(
                    "먼저 원하시는 서비스를 선택해주세요.",
                    ["검사주기", "검사항목"]
                )

            save_to_history(user_data)  # 히스토리 저장
            user_data["업종"] = user_input

            # 식품제조가공업, 축산물제조가공업은 품목제조보고서 주의 메시지
            if user_input in ["식품제조가공업", "축산물제조가공업"]:
                return make_response(
                    f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n예: 과자, 음료, 소시지 등\n\n(주의 : 품목제조보고서에 표기된 \"식품유형\"을 입력하세요. 단어에 가운데 점이 있는 경우 제외하고 입력하세요)",
                    ["이전", "처음으로"]
                )
            elif user_input == "즉석판매제조가공업":
                # 즉석판매제조가공업은 영업신고증 주의 메시지 + 바로가기 버튼
                message = f"[{user_input}] 검사할 식품 유형을 입력해주세요.\n\n"
                message += "예: 과자, 음료, 소시지 등\n\n"
                message += "(주의 : 영업신고증에 표기된 \"식품유형\"을 입력하세요. 단어에 가운데 점이 있는 경우 제외하고 입력하세요.)\n\n"
                message += "* 주의 즉석판매제조가공업은 영업등록증에 표기된 식품의 유형만 자가품질검사 대상이 됩니다.\n\n"
                message += "대상은 바로가기 버튼을 클릭하여 Q5. [즉석판매제조가공업] 자가품질검사 대상식품 및 검사주기를 참고해주세요."
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
                    formatted_items = format_items_list(result['items'])
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

        # 기본 응답
        return make_response(
            "안녕하세요! 바이오푸드랩 챗봇 [바푸]입니다.\n\n원하시는 서비스를 선택해주세요.",
            ["검사분야", "검사주기", "검사항목"]
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
