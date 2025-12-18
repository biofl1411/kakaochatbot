import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import chromedriver_autoinstaller
import threading
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
from waitress import serve
import os
import base64
import json
from datetime import datetime


# 로그 설정 - 콘솔 출력과 파일 기록 동시 설정
log_file_path = os.path.join(os.getcwd(), "crawler_log.txt")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, mode='w', encoding='utf-8'),  # 파일 기록
        logging.StreamHandler()  # 콘솔 출력
    ]
)

logging.info("✅ 로그 파일 설정 완료: crawler_log.txt")


# URL 매핑 정보
url_mapping = {
    "검사항목": {
        "식품": "https://www.biofl.co.kr/sub.jsp?code=7r9P7y94&question_229",
        "축산": "https://www.biofl.co.kr/sub.jsp?code=XN0Cd4r7&question_230"
    },
    "검사주기": {
        "식품": "https://www.biofl.co.kr/sub.jsp?code=7r9P7y94",
        "축산": "https://www.biofl.co.kr/sub.jsp?code=XN0Cd4r7"
    }
}

industry_mapping = {
    "식품제조가공업": "question_236",
    "즉석판매제조가공업": "question_239",
    "축산물제조가공업": "question_200",
    "식육즙판매가공업": "question_210"
}

_driver = None
_driver_lock = threading.Lock()
user_state = {}

# Google Vision API 설정
GOOGLE_VISION_API_KEY = "AIzaSyAzaBbscximXQ02UhSWZZbnUmPkxSqigEA"
GOOGLE_VISION_API_URL = "https://vision.googleapis.com/v1/images:annotate"

# 사용량 제한 설정
MAX_TEXT_SEARCH_BEFORE_IMAGE = 3  # 텍스트 검색 3회 후 이미지 업로드 요청
MAX_IMAGE_SEARCH_PER_USER = 2  # 사용자당 이미지 검색 최대 2회
MONTHLY_USAGE_LIMIT = 950  # 월 사용량 제한

# 월별 사용량 추적
usage_data_file = os.path.join(os.getcwd(), "vision_api_usage.json")


def load_usage_data():
    """사용량 데이터 로드"""
    if os.path.exists(usage_data_file):
        try:
            with open(usage_data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"month": datetime.now().strftime("%Y-%m"), "count": 0, "users": {}}


def save_usage_data(data):
    """사용량 데이터 저장"""
    with open(usage_data_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_monthly_usage():
    """현재 월 사용량 조회 및 월 변경 시 초기화"""
    data = load_usage_data()
    current_month = datetime.now().strftime("%Y-%m")

    # 월이 바뀌면 초기화
    if data.get("month") != current_month:
        data = {"month": current_month, "count": 0, "users": {}}
        save_usage_data(data)

    return data


def increment_usage(user_id, usage_type="text"):
    """사용량 증가"""
    data = get_monthly_usage()

    if user_id not in data["users"]:
        data["users"][user_id] = {"text_search": 0, "image_search": 0}

    if usage_type == "text":
        data["users"][user_id]["text_search"] += 1
    elif usage_type == "image":
        data["users"][user_id]["image_search"] += 1
        data["count"] += 1  # Vision API 호출 횟수 증가

    save_usage_data(data)
    return data


def get_user_usage(user_id):
    """사용자별 사용량 조회"""
    data = get_monthly_usage()
    if user_id not in data["users"]:
        return {"text_search": 0, "image_search": 0}
    return data["users"][user_id]


def analyze_image_with_vision_api(image_base64):
    """Google Vision API로 이미지 분석 (OCR)"""
    headers = {"Content-Type": "application/json"}

    body = {
        "requests": [{
            "image": {"content": image_base64},
            "features": [
                {"type": "TEXT_DETECTION", "maxResults": 10},
                {"type": "DOCUMENT_TEXT_DETECTION", "maxResults": 10}
            ]
        }]
    }

    try:
        response = requests.post(
            f"{GOOGLE_VISION_API_URL}?key={GOOGLE_VISION_API_KEY}",
            headers=headers,
            json=body,
            timeout=30
        )

        if response.status_code == 200:
            result = response.json()
            if "responses" in result and len(result["responses"]) > 0:
                response_data = result["responses"][0]

                # 전체 텍스트 추출
                if "fullTextAnnotation" in response_data:
                    return response_data["fullTextAnnotation"]["text"]
                elif "textAnnotations" in response_data and len(response_data["textAnnotations"]) > 0:
                    return response_data["textAnnotations"][0]["description"]

            return None
        else:
            logging.error(f"Vision API 오류: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logging.error(f"Vision API 호출 중 오류: {e}")
        return None


app = Flask(__name__)
CORS(app)


def get_driver():
    global _driver
    with _driver_lock:
        if _driver is None:
            try:
                chromedriver_autoinstaller.install()
                options = Options()
                options.add_argument("--headless=new")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--window-size=1920,1080")
                _driver = webdriver.Chrome(options=options)
            except Exception as e:
                logging.error(f"WebDriver 생성 오류: {e}")
                logging.error(traceback.format_exc())
    return _driver


def normalize_text(text):
    """텍스트 정규화 - 특수문자 제거"""
    # 가운뎃점(·) 제거
    text = text.replace("·", "")
    return text


def is_similar(word1, word2, threshold=100):
    # 비교 전 텍스트 정규화 (가운뎃점 등 제거)
    word1 = normalize_text(word1)
    word2 = normalize_text(word2)
    return fuzz.ratio(word1, word2) >= threshold or fuzz.partial_ratio(word1, word2) >= threshold


def get_similarity_score(word1, word2):
    """두 단어의 유사도 점수 반환 (0-100)"""
    word1 = normalize_text(word1)
    word2 = normalize_text(word2)
    return max(fuzz.ratio(word1, word2), fuzz.partial_ratio(word1, word2))


def find_similar_words(search_word, all_food_types, threshold=50, limit=5):
    """유사도가 높은 단어들을 찾아 반환"""
    similarities = []
    normalized_search = normalize_text(search_word)

    for food_type_info in all_food_types:
        ft = food_type_info["food_type"]
        # 괄호 앞 부분만 추출
        ft_base = ft.split('(')[0].strip()
        normalized_ft = normalize_text(ft_base)

        score = get_similarity_score(normalized_search, normalized_ft)
        if score >= threshold:
            similarities.append({
                **food_type_info,
                "score": score
            })

    # 점수 높은 순으로 정렬
    similarities.sort(key=lambda x: x["score"], reverse=True)
    return similarities[:limit] if limit else similarities


def fix_spacing(text):
    """크롤링된 텍스트의 띄어쓰기 오류 수정"""
    # 띄어쓰기 수정 패턴 (붙어있는 것 → 띄어쓰기 추가)
    spacing_fixes = [
        ("이상크림을", "이상 크림을"),
        ("이상(크림을", "이상 (크림을"),
        ("바르거나안에", "바르거나 안에"),
        ("위에바르거나", "위에 바르거나"),
        ("것만해당", "것만 해당"),
        ("넣은것만", "넣은 것만"),
        ("채워넣은", "채워 넣은"),
        ("1회이상", "1회 이상"),
        ("9월1회", "9월 1회"),
        ("1월1회", "1월 1회"),
    ]

    for old, new in spacing_fixes:
        text = text.replace(old, new)

    return text


def split_with_parentheses(text):
    """괄호를 고려하여 쉼표로 분리 - 괄호 안의 쉼표는 무시"""
    result = []
    current = ""
    depth = 0  # 괄호 깊이

    for char in text:
        if char == '(':
            depth += 1
            current += char
        elif char == ')':
            depth -= 1
            current += char
        elif char == ',' and depth == 0:
            # 괄호 밖의 쉼표 - 분리
            if current.strip():
                result.append(current.strip())
            current = ""
        else:
            current += char

    # 마지막 항목 추가
    if current.strip():
        result.append(current.strip())

    return result


def get_inspection_cycle(category, industry, food_type):
    url = url_mapping.get("검사주기", {}).get(category)
    if not url:
        return {"type": "error", "message": "❌ 검사주기 정보를 찾을 수 없습니다."}

    driver = get_driver()
    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        soup = BeautifulSoup(driver.page_source, "html.parser")

        target_id = industry_mapping.get(industry)
        target_element = soup.find("div", class_="needpopup answerPop", id=target_id)
        if not target_element:
            return {"type": "error", "message": "❌ 검사주기 정보를 찾을 수 없습니다."}

        table = target_element.find("table")
        if not table:
            return {"type": "error", "message": "❌ 검사주기 테이블을 찾을 수 없습니다."}

        rows = table.find_all("tr")[1:]

        # 검색어 정규화
        normalized_input = normalize_text(food_type)

        # 모든 행을 순회하며 매칭되는 항목 수집
        exact_matches = []  # 정확히 일치
        endswith_matches = []  # 입력어로 끝나는 항목

        for row in rows:
            columns = row.find_all("td", recursive=False)
            if len(columns) < 4:
                continue

            current_food_group = columns[1].get_text(strip=True)
            food_type_text = columns[2].get_text(strip=True)
            cycle = columns[3].get_text(strip=True)

            # 띄어쓰기 오류 수정
            cycle = fix_spacing(cycle)

            # 괄호를 고려하여 식품 유형 분리
            food_type_list = split_with_parentheses(food_type_text)

            # 식품군과 정확히 일치하고, 식품유형이 여러 개인 경우 선택 요청
            if is_similar(food_type, current_food_group) and len(food_type_list) > 1:
                return {
                    "type": "selection",
                    "message": f"📋 [{current_food_group}]에는 여러 식품 유형이 있습니다. 아래에서 선택해주세요:",
                    "options": food_type_list,
                    "food_group": current_food_group,
                    "cycle": cycle
                }

            for ft in food_type_list:
                normalized_ft = normalize_text(ft)
                # 괄호 앞 부분만 추출 (예: "즉석섭취식품(도시락, 김밥류)" → "즉석섭취식품")
                ft_base = ft.split('(')[0].strip()
                normalized_ft_base = normalize_text(ft_base)

                # 1. 정확히 일치하는 경우
                if normalized_input == normalized_ft_base or normalized_input == normalized_ft:
                    exact_matches.append({
                        "food_group": current_food_group,
                        "food_type": ft,
                        "cycle": cycle
                    })
                # 2. 입력어로 끝나는 경우 (예: "햄" → "생햄", "프레스햄")
                elif normalized_ft_base.endswith(normalized_input) and len(normalized_input) >= 1:
                    endswith_matches.append({
                        "food_group": current_food_group,
                        "food_type": ft,
                        "cycle": cycle
                    })

        # 정확히 일치하는 항목이 1개면 결과 반환
        if len(exact_matches) == 1:
            match = exact_matches[0]
            return {
                "type": "result",
                "message": f"✅ [{match['food_group']}] {match['food_type']}의 검사주기: {match['cycle']}"
            }

        # 정확히 일치하는 항목이 여러 개면 선택 요청
        if len(exact_matches) > 1:
            options = [m["food_type"] for m in exact_matches]
            return {
                "type": "selection",
                "message": f"📋 '{food_type}'에 해당하는 항목이 여러 개 있습니다. 아래에서 선택해주세요:",
                "options": options,
                "food_group": exact_matches[0]["food_group"],
                "cycle": exact_matches[0]["cycle"]
            }

        # 입력어로 끝나는 항목들이 있으면 선택 요청
        if len(endswith_matches) > 0:
            # 정확 일치 + 끝나는 항목 합치기
            all_matches = exact_matches + endswith_matches
            if len(all_matches) == 1:
                match = all_matches[0]
                return {
                    "type": "result",
                    "message": f"✅ [{match['food_group']}] {match['food_type']}의 검사주기: {match['cycle']}"
                }
            else:
                options = [m["food_type"] for m in all_matches]
                return {
                    "type": "selection",
                    "message": f"📋 '{food_type}'(으)로 끝나는 항목이 여러 개 있습니다. 아래에서 선택해주세요:",
                    "options": options,
                    "food_group": all_matches[0]["food_group"],
                    "cycle": all_matches[0]["cycle"]
                }

        # 정확/끝나는 매칭이 없는 경우, 모든 식품 유형 수집하여 유사도 검색
        all_food_types = []
        for row in rows:
            columns = row.find_all("td", recursive=False)
            if len(columns) < 4:
                continue
            current_food_group = columns[1].get_text(strip=True)
            food_type_text = columns[2].get_text(strip=True)
            cycle = columns[3].get_text(strip=True)
            cycle = fix_spacing(cycle)

            food_type_list = split_with_parentheses(food_type_text)
            for ft in food_type_list:
                all_food_types.append({
                    "food_group": current_food_group,
                    "food_type": ft,
                    "cycle": cycle
                })

        # 유사도가 높은 단어 찾기
        similar_words = find_similar_words(food_type, all_food_types, threshold=40, limit=5)

        if similar_words:
            options = [s["food_type"] for s in similar_words]
            return {
                "type": "similar",
                "message": f"🔍 '{food_type}'과(와) 유사한 항목을 찾았습니다. 아래에서 선택해주세요:",
                "options": options,
                "all_similar": similar_words,
                "all_food_types": all_food_types,
                "search_word": food_type
            }

        return {"type": "error", "message": "❌ 해당 식품 유형의 검사주기를 찾을 수 없습니다."}

    except Exception as e:
        logging.error(f"오류 발생: {e}")
        return {"type": "error", "message": "❌ 검사주기 정보를 가져오는 중 오류가 발생했습니다."}


def get_inspection_items(category, food_type):
    url = url_mapping.get("검사항목", {}).get(category)
    if not url:
        return {"type": "error", "message": f"❌ {category} 검사항목 정보를 찾을 수 없습니다."}

    try:
        response = requests.get(url)
        if response.status_code != 200:
            return {"type": "error", "message": f"❌ 요청 실패: 상태 코드 {response.status_code}"}

        soup = BeautifulSoup(response.content, "html.parser")
        tables = soup.find_all("table")
        if not tables:
            return {"type": "error", "message": "❌ 검사 항목 테이블을 찾을 수 없습니다."}

        # 검색어 정규화
        normalized_input = normalize_text(food_type)

        # 모든 테이블을 순회하며 매칭되는 항목 수집
        exact_matches = []  # 정확히 일치
        endswith_matches = []  # 입력어로 끝나는 항목

        for table in tables:
            rows = table.find_all("tr")[1:]
            for row in rows:
                columns = row.find_all("td", recursive=False)
                if len(columns) < 3:
                    continue
                current_food_type = columns[1].get_text(strip=True)
                test_items = columns[2].get_text(strip=True)

                # 괄호를 고려하여 식품 유형 분리
                food_type_list = split_with_parentheses(current_food_type)

                for ft in food_type_list:
                    normalized_ft = normalize_text(ft)
                    # 괄호 앞 부분만 추출
                    ft_base = ft.split('(')[0].strip()
                    normalized_ft_base = normalize_text(ft_base)

                    # 1. 정확히 일치하는 경우
                    if normalized_input == normalized_ft_base or normalized_input == normalized_ft:
                        exact_matches.append({
                            "food_type": ft,
                            "test_items": test_items
                        })
                    # 2. 입력어로 끝나는 경우 (예: "햄" → "생햄", "프레스햄")
                    elif normalized_ft_base.endswith(normalized_input) and len(normalized_input) >= 1:
                        endswith_matches.append({
                            "food_type": ft,
                            "test_items": test_items
                        })

        # 정확히 일치하는 항목이 1개면 결과 반환
        if len(exact_matches) == 1:
            match = exact_matches[0]
            return {
                "type": "result",
                "message": f"✅ [{match['food_type']}]의 검사 항목: {match['test_items']}"
            }

        # 정확히 일치하는 항목이 여러 개면 선택 요청
        if len(exact_matches) > 1:
            options = [m["food_type"] for m in exact_matches]
            return {
                "type": "selection",
                "message": f"📋 '{food_type}'에 해당하는 항목이 여러 개 있습니다. 아래에서 선택해주세요:",
                "options": options,
                "matches": exact_matches
            }

        # 입력어로 끝나는 항목들이 있으면 선택 요청
        if len(endswith_matches) > 0:
            all_matches = exact_matches + endswith_matches
            if len(all_matches) == 1:
                match = all_matches[0]
                return {
                    "type": "result",
                    "message": f"✅ [{match['food_type']}]의 검사 항목: {match['test_items']}"
                }
            else:
                options = [m["food_type"] for m in all_matches]
                return {
                    "type": "selection",
                    "message": f"📋 '{food_type}'(으)로 끝나는 항목이 여러 개 있습니다. 아래에서 선택해주세요:",
                    "options": options,
                    "matches": all_matches
                }

        # 정확/끝나는 매칭이 없는 경우, 모든 식품 유형 수집하여 유사도 검색
        all_food_types = []
        for table in tables:
            rows = table.find_all("tr")[1:]
            for row in rows:
                columns = row.find_all("td", recursive=False)
                if len(columns) < 3:
                    continue
                current_food_type = columns[1].get_text(strip=True)
                test_items = columns[2].get_text(strip=True)

                food_type_list = split_with_parentheses(current_food_type)
                for ft in food_type_list:
                    all_food_types.append({
                        "food_type": ft,
                        "test_items": test_items
                    })

        # 유사도가 높은 단어 찾기
        similar_words = find_similar_words(food_type, all_food_types, threshold=40, limit=5)

        if similar_words:
            options = [s["food_type"] for s in similar_words]
            return {
                "type": "similar",
                "message": f"🔍 '{food_type}'과(와) 유사한 항목을 찾았습니다. 아래에서 선택해주세요:",
                "options": options,
                "all_similar": similar_words,
                "all_food_types": all_food_types,
                "search_word": food_type
            }

        return {"type": "error", "message": f"❌ '{food_type}'에 대한 검사 항목을 찾을 수 없습니다."}

    except Exception as e:
        logging.error(f"오류 발생: {e}")
        return {"type": "error", "message": "❌ 검사 항목 정보를 가져오는 중 오류가 발생했습니다."}


def shutdown_driver():
    global _driver
    if _driver is not None:
        _driver.quit()
        _driver = None

@app.route('/chatbot', methods=['POST'])
def chatbot():
    data = request.get_json()
    user_input = data.get("userRequest", {}).get("utterance", "")
    user_id = data.get("userRequest", {}).get("user", {}).get("id", "default")

    user_state.setdefault(user_id, {})
    user_data = user_state[user_id]
    response_text = "❓ 질문을 이해하지 못했습니다. 다시 입력해주세요."
    response_buttons = ["검사주기", "검사항목"]

    # 사용자 사용량 조회
    user_usage = get_user_usage(user_id)

    if user_input in ["검사주기", "검사항목"]:
        user_data["기능"] = user_input
        response_text = "검사할 분야를 선택해주세요."
        response_buttons += ["식품", "축산"]

    elif user_input in ["식품", "축산"]:
        user_data["분야"] = user_input
        if user_data.get("기능") == "검사주기":
            response_text = "검사할 업종을 선택해주세요."
            response_buttons += ["식품제조가공업", "즉석판매제조가공업"] if user_input == "식품" else ["축산물제조가공업", "식육즙판매가공업"]
        elif user_data.get("기능") == "검사항목":
            response_text = "검사할 식품 유형을 입력해주세요."

    elif user_data.get("기능") == "검사주기" and "업종" not in user_data:
        user_data["업종"] = user_input
        response_text = "검사할 식품 유형을 입력해주세요."

    elif user_data.get("기능") == "검사주기" and "업종" in user_data:
        # 텍스트 검색 횟수 증가
        increment_usage(user_id, "text")
        user_usage = get_user_usage(user_id)

        # "다른 유사한 단어 추천" 버튼 처리
        if user_input == "다른 유사한 단어 추천" and "pending_similar_cycle" in user_data:
            pending = user_data["pending_similar_cycle"]
            # 현재 표시된 인덱스 이후의 유사 단어 가져오기
            current_index = pending.get("current_index", 5)
            all_food_types = pending["all_food_types"]
            search_word = pending["search_word"]

            # 다음 유사 단어 찾기
            next_similar = find_similar_words(search_word, all_food_types, threshold=40, limit=10)
            # 이미 표시된 항목 제외
            shown_food_types = pending.get("shown", [])
            next_similar = [s for s in next_similar if s["food_type"] not in shown_food_types][:5]

            if next_similar:
                options = [s["food_type"] for s in next_similar]
                response_text = f"🔍 '{search_word}'과(와) 유사한 다른 항목들입니다. 아래에서 선택해주세요:"
                # 표시한 항목 기록
                pending["shown"].extend(options)
                pending["current_index"] = current_index + 5
                response_buttons = ["검사주기", "검사항목"] + options + ["다른 유사한 단어 추천"]
            else:
                response_text = f"❌ '{search_word}'과(와) 유사한 다른 항목이 더 이상 없습니다."
                del user_data["pending_similar_cycle"]

        # 유사 단어 선택 대기 중인 경우
        elif "pending_similar_cycle" in user_data:
            pending = user_data["pending_similar_cycle"]
            # 선택한 항목 찾기
            for match in pending["all_similar"]:
                if match["food_type"] == user_input or normalize_text(match["food_type"]) == normalize_text(user_input):
                    response_text = f"✅ [{match['food_group']}] {match['food_type']}의 검사주기: {match['cycle']}"
                    del user_data["pending_similar_cycle"]
                    break
            else:
                # 전체 목록에서 다시 검색
                for item in pending["all_food_types"]:
                    if item["food_type"] == user_input or normalize_text(item["food_type"]) == normalize_text(user_input):
                        response_text = f"✅ [{item['food_group']}] {item['food_type']}의 검사주기: {item['cycle']}"
                        del user_data["pending_similar_cycle"]
                        break
                else:
                    # 새로운 검색어로 재검색
                    result = get_inspection_cycle(user_data.get("분야"), user_data.get("업종"), user_input)
                    if result["type"] == "result":
                        response_text = result["message"]
                        if "pending_similar_cycle" in user_data:
                            del user_data["pending_similar_cycle"]
                    elif result["type"] == "selection":
                        response_text = result["message"]
                        user_data["pending_selection"] = {
                            "food_group": result["food_group"],
                            "cycle": result["cycle"]
                        }
                        if "pending_similar_cycle" in user_data:
                            del user_data["pending_similar_cycle"]
                        response_buttons = ["검사주기", "검사항목"] + result["options"]
                    elif result["type"] == "similar":
                        response_text = result["message"]
                        user_data["pending_similar_cycle"] = {
                            "all_similar": result["all_similar"],
                            "all_food_types": result["all_food_types"],
                            "search_word": result["search_word"],
                            "shown": result["options"],
                            "current_index": 5
                        }
                        response_buttons = ["검사주기", "검사항목"] + result["options"] + ["다른 유사한 단어 추천"]
                    else:
                        response_text = result["message"]
                        if "pending_similar_cycle" in user_data:
                            del user_data["pending_similar_cycle"]

        # 식품군 선택 대기 중인 경우, 저장된 정보로 결과 반환
        elif "pending_selection" in user_data:
            pending = user_data["pending_selection"]
            response_text = f"✅ [{pending['food_group']}] {user_input}의 검사주기: {pending['cycle']}"
            del user_data["pending_selection"]
        else:
            result = get_inspection_cycle(user_data.get("분야"), user_data.get("업종"), user_input)

            if result["type"] == "selection":
                # 식품군 선택 필요 - 옵션 제공
                response_text = result["message"]
                user_data["pending_selection"] = {
                    "food_group": result["food_group"],
                    "cycle": result["cycle"]
                }
                # 식품 유형들을 quickReplies로 추가
                response_buttons = ["검사주기", "검사항목"] + result["options"]
            elif result["type"] == "similar":
                # 유사 단어 추천 - 옵션 제공
                response_text = result["message"]
                user_data["pending_similar_cycle"] = {
                    "all_similar": result["all_similar"],
                    "all_food_types": result["all_food_types"],
                    "search_word": result["search_word"],
                    "shown": result["options"],
                    "current_index": 5
                }
                response_buttons = ["검사주기", "검사항목"] + result["options"] + ["다른 유사한 단어 추천"]
            else:
                # 결과 또는 에러
                response_text = result["message"]

        # 3회 이상 검색 시 이미지 업로드 안내 (선택 대기 중이 아닐 때만)
        if "pending_selection" not in user_data and "pending_similar_cycle" not in user_data and user_usage["text_search"] >= MAX_TEXT_SEARCH_BEFORE_IMAGE:
            response_text += "\n\n📷 검색 횟수가 3회 이상입니다. 식품 유형이 적힌 아래 서류 중 하나의 이미지를 올려주세요.\n1. 품목제조보고서\n2. 영업신고증\n3. 영업등록증\n4. 허가증"
            response_buttons.append("이미지 업로드")

    elif user_data.get("기능") == "검사항목":
        # 텍스트 검색 횟수 증가
        increment_usage(user_id, "text")
        user_usage = get_user_usage(user_id)

        # "다른 유사한 단어 추천" 버튼 처리
        if user_input == "다른 유사한 단어 추천" and "pending_similar_items" in user_data:
            pending = user_data["pending_similar_items"]
            # 현재 표시된 인덱스 이후의 유사 단어 가져오기
            all_food_types = pending["all_food_types"]
            search_word = pending["search_word"]

            # 다음 유사 단어 찾기
            next_similar = find_similar_words(search_word, all_food_types, threshold=40, limit=10)
            # 이미 표시된 항목 제외
            shown_food_types = pending.get("shown", [])
            next_similar = [s for s in next_similar if s["food_type"] not in shown_food_types][:5]

            if next_similar:
                options = [s["food_type"] for s in next_similar]
                response_text = f"🔍 '{search_word}'과(와) 유사한 다른 항목들입니다. 아래에서 선택해주세요:"
                # 표시한 항목 기록
                pending["shown"].extend(options)
                response_buttons = ["검사주기", "검사항목"] + options + ["다른 유사한 단어 추천"]
            else:
                response_text = f"❌ '{search_word}'과(와) 유사한 다른 항목이 더 이상 없습니다."
                del user_data["pending_similar_items"]

        # 유사 단어 선택 대기 중인 경우
        elif "pending_similar_items" in user_data:
            pending = user_data["pending_similar_items"]
            # 선택한 항목 찾기
            for match in pending["all_similar"]:
                if match["food_type"] == user_input or normalize_text(match["food_type"]) == normalize_text(user_input):
                    response_text = f"✅ [{match['food_type']}]의 검사 항목: {match['test_items']}"
                    del user_data["pending_similar_items"]
                    break
            else:
                # 전체 목록에서 다시 검색
                for item in pending["all_food_types"]:
                    if item["food_type"] == user_input or normalize_text(item["food_type"]) == normalize_text(user_input):
                        response_text = f"✅ [{item['food_type']}]의 검사 항목: {item['test_items']}"
                        del user_data["pending_similar_items"]
                        break
                else:
                    # 새로운 검색어로 재검색
                    result = get_inspection_items(user_data.get("분야"), user_input)
                    if result["type"] == "result":
                        response_text = result["message"]
                        if "pending_similar_items" in user_data:
                            del user_data["pending_similar_items"]
                    elif result["type"] == "selection":
                        response_text = result["message"]
                        user_data["pending_items_selection"] = {
                            "matches": result["matches"]
                        }
                        if "pending_similar_items" in user_data:
                            del user_data["pending_similar_items"]
                        response_buttons = ["검사주기", "검사항목"] + result["options"]
                    elif result["type"] == "similar":
                        response_text = result["message"]
                        user_data["pending_similar_items"] = {
                            "all_similar": result["all_similar"],
                            "all_food_types": result["all_food_types"],
                            "search_word": result["search_word"],
                            "shown": result["options"]
                        }
                        response_buttons = ["검사주기", "검사항목"] + result["options"] + ["다른 유사한 단어 추천"]
                    else:
                        response_text = result["message"]
                        if "pending_similar_items" in user_data:
                            del user_data["pending_similar_items"]

        # 검사항목 선택 대기 중인 경우, 저장된 정보로 결과 반환
        elif "pending_items_selection" in user_data:
            pending = user_data["pending_items_selection"]
            # 선택한 항목 찾기
            for match in pending["matches"]:
                if match["food_type"] == user_input or normalize_text(match["food_type"]) == normalize_text(user_input):
                    response_text = f"✅ [{match['food_type']}]의 검사 항목: {match['test_items']}"
                    break
            else:
                response_text = f"❌ '{user_input}'을(를) 찾을 수 없습니다."
            del user_data["pending_items_selection"]
        else:
            result = get_inspection_items(user_data.get("분야"), user_input)

            if result["type"] == "selection":
                # 식품유형 선택 필요 - 옵션 제공
                response_text = result["message"]
                user_data["pending_items_selection"] = {
                    "matches": result["matches"]
                }
                # 식품 유형들을 quickReplies로 추가
                response_buttons = ["검사주기", "검사항목"] + result["options"]
            elif result["type"] == "similar":
                # 유사 단어 추천 - 옵션 제공
                response_text = result["message"]
                user_data["pending_similar_items"] = {
                    "all_similar": result["all_similar"],
                    "all_food_types": result["all_food_types"],
                    "search_word": result["search_word"],
                    "shown": result["options"]
                }
                response_buttons = ["검사주기", "검사항목"] + result["options"] + ["다른 유사한 단어 추천"]
            else:
                # 결과 또는 에러
                response_text = result["message"]

        # 3회 이상 검색 시 이미지 업로드 안내 (선택 대기 중이 아닐 때만)
        if "pending_items_selection" not in user_data and "pending_similar_items" not in user_data and user_usage["text_search"] >= MAX_TEXT_SEARCH_BEFORE_IMAGE:
            response_text += "\n\n📷 검색 횟수가 3회 이상입니다. 식품 유형이 적힌 아래 서류 중 하나의 이미지를 올려주세요.\n1. 품목제조보고서\n2. 영업신고증\n3. 영업등록증\n4. 허가증"
            response_buttons.append("이미지 업로드")

    return jsonify({
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": response_text}}],
            "quickReplies": [{"label": btn, "action": "message", "messageText": btn} for btn in response_buttons]
        }
    })


@app.route('/image', methods=['POST'])
def handle_image():
    """이미지 업로드 처리 (카카오톡 이미지 메시지)"""
    data = request.get_json()
    user_id = data.get("userRequest", {}).get("user", {}).get("id", "default")

    # 월간 사용량 확인
    monthly_data = get_monthly_usage()
    if monthly_data["count"] >= MONTHLY_USAGE_LIMIT:
        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": "⚠️ 월 사용량(950회)이 초과되었습니다. 다음 달에 다시 이용해주세요."}}],
                "quickReplies": [
                    {"label": "검사주기", "action": "message", "messageText": "검사주기"},
                    {"label": "검사항목", "action": "message", "messageText": "검사항목"}
                ]
            }
        })

    # 사용자별 이미지 검색 횟수 확인
    user_usage = get_user_usage(user_id)
    if user_usage["image_search"] >= MAX_IMAGE_SEARCH_PER_USER:
        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": "⚠️ 이미지 검색은 2회 이상 사용할 수 없습니다."}}],
                "quickReplies": [
                    {"label": "검사주기", "action": "message", "messageText": "검사주기"},
                    {"label": "검사항목", "action": "message", "messageText": "검사항목"}
                ]
            }
        })

    # 카카오톡 이미지 URL 추출
    params = data.get("action", {}).get("params", {})
    image_url = None

    # secureimage 파라미터에서 이미지 URL 추출
    if "secureimage" in params:
        try:
            secure_image_data = json.loads(params["secureimage"])
            image_url = secure_image_data.get("secureUrl") or secure_image_data.get("url")
        except:
            pass

    # image 파라미터에서 이미지 URL 추출
    if not image_url and "image" in params:
        try:
            image_data = json.loads(params["image"])
            image_url = image_data.get("url")
        except:
            image_url = params.get("image")

    if not image_url:
        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": "❌ 이미지를 인식하지 못했습니다. 다시 업로드해주세요."}}],
                "quickReplies": [
                    {"label": "검사주기", "action": "message", "messageText": "검사주기"},
                    {"label": "검사항목", "action": "message", "messageText": "검사항목"}
                ]
            }
        })

    try:
        # 이미지 다운로드
        image_response = requests.get(image_url, timeout=10)
        if image_response.status_code != 200:
            raise Exception("이미지 다운로드 실패")

        # Base64 인코딩
        image_base64 = base64.b64encode(image_response.content).decode('utf-8')

        # Vision API로 이미지 분석
        extracted_text = analyze_image_with_vision_api(image_base64)

        if extracted_text:
            # 사용량 증가
            increment_usage(user_id, "image")

            response_text = f"📄 이미지에서 추출된 텍스트:\n\n{extracted_text[:1000]}"
            if len(extracted_text) > 1000:
                response_text += "\n\n... (텍스트가 너무 길어 일부만 표시됩니다)"
        else:
            response_text = "❌ 이미지에서 텍스트를 추출하지 못했습니다. 더 선명한 이미지를 업로드해주세요."

    except Exception as e:
        logging.error(f"이미지 처리 중 오류: {e}")
        response_text = "❌ 이미지 처리 중 오류가 발생했습니다. 다시 시도해주세요."

    return jsonify({
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": response_text}}],
            "quickReplies": [
                {"label": "검사주기", "action": "message", "messageText": "검사주기"},
                {"label": "검사항목", "action": "message", "messageText": "검사항목"}
            ]
        }
    })


@app.route('/usage', methods=['GET'])
def get_usage_status():
    """사용량 현황 조회 (관리자용)"""
    data = get_monthly_usage()
    return jsonify({
        "month": data["month"],
        "total_vision_api_calls": data["count"],
        "monthly_limit": MONTHLY_USAGE_LIMIT,
        "remaining": MONTHLY_USAGE_LIMIT - data["count"],
        "user_count": len(data["users"])
    })


if __name__ == '__main__':
    logging.info("🚀 Flask 서버가 시작되었습니다! http://0.0.0.0:7411")
    try:
        serve(app, host="0.0.0.0", port=7411)
    finally:
        shutdown_driver()
