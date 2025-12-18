"""
Google Vision API OCR
- 이미지에서 식품유형 추출
"""
import logging
from models import can_use_vision_api, increment_api_usage

logger = logging.getLogger(__name__)


def is_vision_api_available() -> bool:
    """Vision API 사용 가능 여부 확인"""
    return can_use_vision_api()


def extract_food_type_from_image(image_url: str) -> dict:
    """
    이미지에서 식품유형 추출 (Google Vision API)

    Returns:
        dict: {
            'success': bool,
            'food_type': str or None,
            'message': str
        }
    """
    # Vision API 사용 가능 여부 확인
    if not can_use_vision_api():
        return {
            'success': False,
            'food_type': None,
            'message': '이번 달 이미지 인식 횟수를 초과했습니다.'
        }

    try:
        # Google Vision API 호출 (실제 구현 필요)
        # TODO: Google Cloud Vision API 연동

        # 현재는 미구현 상태로 반환
        return {
            'success': False,
            'food_type': None,
            'message': '이미지 인식 기능이 아직 준비 중입니다.'
        }

    except Exception as e:
        logger.error(f"Vision API 오류: {e}")
        return {
            'success': False,
            'food_type': None,
            'message': '이미지 처리 중 오류가 발생했습니다.'
        }
