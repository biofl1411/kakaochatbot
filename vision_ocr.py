"""
Google Vision API OCR
- 이미지에서 식품유형 추출
"""
import logging
import re
import requests
from urllib.parse import unquote, urlparse

# Google Vision API import (optional)
VISION_IMPORT_SUCCESS = False
vision = None
try:
    from google.cloud import vision
    VISION_IMPORT_SUCCESS = True
except BaseException as e:
    logging.warning(f"Google Vision API 모듈 로드 실패: {e}")
    vision = None
    VISION_IMPORT_SUCCESS = False

from models import can_use_vision_api, increment_api_usage

logger = logging.getLogger(__name__)


def is_vision_api_available() -> bool:
    """Vision API 사용 가능 여부 확인"""
    if not VISION_IMPORT_SUCCESS:
        return False
    return can_use_vision_api()


def download_image(image_url: str) -> bytes:
    """이미지 다운로드 (여러 방법 시도)"""
    decoded_url = unquote(image_url)

    # 다양한 헤더 조합 시도
    header_options = [
        {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://talk.kakao.com/',
        },
        {
            'User-Agent': 'KakaoTalk',
        },
        {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
        },
    ]

    for headers in header_options:
        try:
            response = requests.get(
                decoded_url,
                headers=headers,
                timeout=15,
                verify=True,
                allow_redirects=True
            )
            if response.status_code == 200 and len(response.content) > 1000:
                logger.info(f"이미지 다운로드 성공: {len(response.content)} bytes")
                return response.content
            else:
                logger.warning(f"이미지 다운로드 실패: HTTP {response.status_code}, size={len(response.content)}")
        except Exception as e:
            logger.warning(f"이미지 다운로드 시도 실패: {e}")
            continue

    return None


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
    # Vision API 모듈 로드 확인
    if not VISION_IMPORT_SUCCESS:
        return {
            'success': False,
            'food_type': None,
            'message': 'Vision API 모듈이 설치되지 않았습니다.'
        }

    # Vision API 사용 가능 여부 확인
    if not can_use_vision_api():
        return {
            'success': False,
            'food_type': None,
            'message': '이번 달 이미지 인식 횟수를 초과했습니다.'
        }

    try:
        logger.info(f"이미지 분석 시작: {image_url[:100]}...")

        # Google Cloud Vision 클라이언트 초기화
        client = vision.ImageAnnotatorClient()

        # 방법 1: 이미지 다운로드 후 분석
        image_content = download_image(image_url)

        if image_content:
            image = vision.Image(content=image_content)
        else:
            # 방법 2: URL 직접 사용 (공개 URL인 경우)
            logger.info("URL 직접 사용 시도...")
            decoded_url = unquote(image_url)
            image = vision.Image()
            image.source.image_uri = decoded_url

        # OCR 수행
        response = client.text_detection(image=image)

        if response.error.message:
            logger.error(f"Vision API 오류: {response.error.message}")
            return {
                'success': False,
                'food_type': None,
                'message': '이미지 분석 중 오류가 발생했습니다.'
            }

        texts = response.text_annotations
        if not texts:
            logger.warning("이미지에서 텍스트를 찾을 수 없습니다.")
            return {
                'success': False,
                'food_type': None,
                'message': '이미지에서 텍스트를 찾을 수 없습니다.'
            }

        # 전체 텍스트 추출
        full_text = texts[0].description
        logger.info(f"OCR 결과: {full_text[:200]}...")

        # API 사용량 증가
        increment_api_usage("google_vision")

        # 식품유형 추출
        food_type = extract_food_type_from_text(full_text)

        if food_type:
            logger.info(f"추출된 식품유형: {food_type}")
            return {
                'success': True,
                'food_type': food_type,
                'message': f"식품유형 '{food_type}'을(를) 찾았습니다."
            }
        else:
            logger.warning("식품유형을 찾을 수 없습니다.")
            return {
                'success': False,
                'food_type': None,
                'message': '이미지에서 식품유형을 찾을 수 없습니다.'
            }

    except Exception as e:
        logger.error(f"Vision API 오류: {e}")
        return {
            'success': False,
            'food_type': None,
            'message': '이미지 처리 중 오류가 발생했습니다.'
        }


def extract_food_type_from_text(ocr_text: str) -> str:
    """OCR 텍스트에서 식품유형 추출"""
    if not ocr_text:
        return None

    try:
        # 식품유형 패턴 매칭
        patterns = [
            r'식품유형\s*[:\s]*([^\n\r,]+)',
            r'식품의\s*유형\s*[:\s]*([^\n\r,]+)',
            r'식품의\s*종류\s*[:\s]*([^\n\r,]+)',
            r'식품종류\s*[:\s]*([^\n\r,]+)',
            r'품목유형\s*[:\s]*([^\n\r,]+)',
            r'제품유형\s*[:\s]*([^\n\r,]+)',
            r'유\s*형\s*[:\s]*([^\n\r,]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, ocr_text)
            if match:
                food_type = match.group(1).strip()
                # 불필요한 문자 제거
                food_type = re.sub(r'[^\w가-힣\s]', '', food_type).strip()
                # 너무 긴 경우 첫 단어만
                if len(food_type) > 20:
                    food_type = food_type.split()[0] if food_type.split() else food_type[:20]
                if food_type:
                    return food_type

        return None

    except Exception as e:
        logger.error(f"식품유형 추출 오류: {e}")
        return None
