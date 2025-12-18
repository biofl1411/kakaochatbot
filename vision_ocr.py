"""
Google Vision API를 이용한 OCR 모듈
- 품목제조보고서, 영업등록증 등에서 식품유형 추출
"""
import os
import re
import logging
import requests
from io import BytesIO

from models import can_use_vision_api, increment_api_usage, get_vision_api_remaining

logger = logging.getLogger(__name__)

# Google Vision API 키 (환경변수에서 로드)
GOOGLE_API_KEY = os.environ.get("GOOGLE_VISION_API_KEY", "")


def extract_food_type_from_image(image_url: str) -> dict:
    """
    이미지에서 식품유형 추출

    Args:
        image_url: 카카오에서 제공하는 이미지 URL

    Returns:
        dict: {
            'success': bool,
            'food_type': str or None,
            'message': str,
            'remaining': int
        }
    """
    # API 사용량 체크
    if not can_use_vision_api():
        return {
            'success': False,
            'food_type': None,
            'message': '이번 달 이미지 분석 횟수를 모두 사용했습니다.',
            'remaining': 0
        }

    # API 키 체크
    if not GOOGLE_API_KEY:
        logger.error("Google Vision API 키가 설정되지 않았습니다.")
        return {
            'success': False,
            'food_type': None,
            'message': '이미지 분석 서비스가 설정되지 않았습니다.',
            'remaining': get_vision_api_remaining()
        }

    try:
        # 이미지 다운로드
        image_response = requests.get(image_url, timeout=10)
        if image_response.status_code != 200:
            return {
                'success': False,
                'food_type': None,
                'message': '이미지를 다운로드할 수 없습니다.',
                'remaining': get_vision_api_remaining()
            }

        # Base64 인코딩
        import base64
        image_content = base64.b64encode(image_response.content).decode('utf-8')

        # Google Vision API 호출
        vision_url = f"https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_API_KEY}"

        request_body = {
            "requests": [{
                "image": {"content": image_content},
                "features": [{"type": "TEXT_DETECTION"}]
            }]
        }

        response = requests.post(vision_url, json=request_body, timeout=30)

        if response.status_code != 200:
            logger.error(f"Vision API 오류: {response.status_code} - {response.text}")
            return {
                'success': False,
                'food_type': None,
                'message': '이미지 분석 중 오류가 발생했습니다.',
                'remaining': get_vision_api_remaining()
            }

        # API 호출 성공 시 사용량 증가
        increment_api_usage("google_vision")

        # 텍스트 추출
        result = response.json()
        responses = result.get('responses', [])

        if not responses or 'textAnnotations' not in responses[0]:
            return {
                'success': False,
                'food_type': None,
                'message': '이미지에서 텍스트를 찾을 수 없습니다.',
                'remaining': get_vision_api_remaining()
            }

        full_text = responses[0]['textAnnotations'][0]['description']
        logger.info(f"OCR 텍스트: {full_text[:200]}...")

        # 식품유형 추출
        food_type = extract_food_type_from_text(full_text)

        if food_type:
            return {
                'success': True,
                'food_type': food_type,
                'message': f"식품유형 '{food_type}'을(를) 찾았습니다.",
                'remaining': get_vision_api_remaining()
            }
        else:
            return {
                'success': False,
                'food_type': None,
                'message': '이미지에서 식품유형을 찾을 수 없습니다.',
                'remaining': get_vision_api_remaining()
            }

    except requests.exceptions.Timeout:
        logger.error("Vision API 타임아웃")
        return {
            'success': False,
            'food_type': None,
            'message': '이미지 분석 시간이 초과되었습니다.',
            'remaining': get_vision_api_remaining()
        }
    except Exception as e:
        logger.error(f"OCR 오류: {e}")
        return {
            'success': False,
            'food_type': None,
            'message': '이미지 분석 중 오류가 발생했습니다.',
            'remaining': get_vision_api_remaining()
        }


def extract_food_type_from_text(text: str) -> str:
    """
    OCR 텍스트에서 식품유형 추출

    Args:
        text: OCR로 추출된 전체 텍스트

    Returns:
        str: 추출된 식품유형 또는 None
    """
    # 줄 단위로 분리
    lines = text.split('\n')

    # 패턴 1: "식품유형" 또는 "식품의 유형" 다음에 오는 값
    for i, line in enumerate(lines):
        line_clean = line.strip()

        # "식품유형:" 또는 "식품의유형:" 형태
        if '식품유형' in line_clean or '식품의유형' in line_clean or '식품의 유형' in line_clean:
            # 같은 줄에서 콜론 이후 값 추출
            match = re.search(r'식품[의\s]*유형[:\s]*(.+)', line_clean)
            if match:
                food_type = match.group(1).strip()
                # 불필요한 문자 제거
                food_type = re.sub(r'[|/\\].*', '', food_type).strip()
                if food_type and len(food_type) >= 2:
                    return food_type

            # 다음 줄에 값이 있을 수 있음
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and len(next_line) >= 2 and not any(
                    kw in next_line for kw in ['품목', '제조', '업소', '영업', '신고', '허가', '등록']
                ):
                    return next_line

    # 패턴 2: 표 형식에서 찾기
    for i, line in enumerate(lines):
        if '식품유형' in line or '식품의유형' in line:
            # 같은 줄 또는 인접 줄에서 값 찾기
            # 표에서는 같은 행에 값이 있을 수 있음
            parts = re.split(r'[\s\t|]+', line)
            for j, part in enumerate(parts):
                if '식품' in part and '유형' in part:
                    # 다음 파트가 값일 수 있음
                    if j + 1 < len(parts) and parts[j + 1].strip():
                        candidate = parts[j + 1].strip()
                        if len(candidate) >= 2 and not any(
                            kw in candidate for kw in ['품목', '제조', '업소', '영업']
                        ):
                            return candidate

    return None


def is_vision_api_available() -> bool:
    """Vision API 사용 가능 여부"""
    return bool(GOOGLE_API_KEY) and can_use_vision_api()
