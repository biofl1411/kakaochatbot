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


def is_similar(word1, word2, threshold=100):
    return fuzz.ratio(word1, word2) >= threshold or fuzz.partial_ratio(word1, word2) >= threshold


def get_inspection_cycle(category, industry, food_type):
    url = url_mapping.get("검사주기", {}).get(category)
    if not url:
        return "❌ 검사주기 정보를 찾을 수 없습니다."

    driver = get_driver()
    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        soup = BeautifulSoup(driver.page_source, "html.parser")

        target_id = industry_mapping.get(industry)
        target_element = soup.find("div", class_="needpopup answerPop", id=target_id)
        if not target_element:
            return "❌ 검사주기 정보를 찾을 수 없습니다."

        table = target_element.find("table")
        if not table:
            return "❌ 검사주기 테이블을 찾을 수 없습니다."

        rows = table.find_all("tr")[1:]

        for row in rows:
            columns = row.find_all("td", recursive=False)
            if len(columns) < 4:
                continue

            current_food_group = columns[1].get_text(strip=True)
            food_type_text = columns[2].get_text(strip=True)
            cycle = columns[3].get_text(strip=True)

            food_type_list = [ft.strip() for ft in food_type_text.split(',')]

            if any(is_similar(food_type, ft) for ft in food_type_list):
                return f"✅ [{current_food_group}] {food_type}의 검사주기: {cycle}"

        return "❌ 해당 식품 유형의 검사주기를 찾을 수 없습니다."

    except Exception as e:
        logging.error(f"오류 발생: {e}")
        return "❌ 검사주기 정보를 가져오는 중 오류가 발생했습니다."


def get_inspection_items(category, food_type):
    url = url_mapping.get("검사항목", {}).get(category)
    if not url:
        return f"❌ {category} 검사항목 정보를 찾을 수 없습니다."

    try:
        response = requests.get(url)
        if response.status_code != 200:
            return f"❌ 요청 실패: 상태 코드 {response.status_code}"

        soup = BeautifulSoup(response.content, "html.parser")
        tables = soup.find_all("table")
        if not tables:
            return "❌ 검사 항목 테이블을 찾을 수 없습니다."

        for table in tables:
            rows = table.find_all("tr")[1:]
            for row in rows:
                columns = row.find_all("td", recursive=False)
                if len(columns) < 3:
                    continue
                current_food_type = columns[1].get_text(strip=True)
                test_items = columns[2].get_text(strip=True)
                if is_similar(food_type, current_food_type):
                    return f"✅ [{current_food_type}]의 검사 항목: {test_items}"
        return f"❌ '{food_type}'에 대한 검사 항목을 찾을 수 없습니다."

    except Exception as e:
        logging.error(f"오류 발생: {e}")
        return "❌ 검사 항목 정보를 가져오는 중 오류가 발생했습니다."


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

    if user_input in ["검사주기", "검사항목"]:
        user_data["기능"] = user_input
        response_text = "검사할 분야를 선택해주세요."
        response_buttons += ["식품", "축산"]

    elif user_input in ["식품", "축산"]:
        user_data["분야"] = user_input
        if user_data["기능"] == "검사주기":
            response_text = "검사할 업종을 선택해주세요."
            response_buttons += ["식품제조가공업", "즉석판매제조가공업"] if user_input == "식품" else ["축산물제조가공업", "식육즙판매가공업"]
        elif user_data["기능"] == "검사항목":
            response_text = "검사할 식품 유형을 입력해주세요."

    elif user_data.get("기능") == "검사주기" and "업종" not in user_data:
        user_data["업종"] = user_input
        response_text = "검사할 식품 유형을 입력해주세요."

    elif user_data.get("기능") == "검사주기" and "업종" in user_data:
        result = get_inspection_cycle(user_data.get("분야"), user_data.get("업종"), user_input)
        response_text = result

    elif user_data.get("기능") == "검사항목":
        result = get_inspection_items(user_data.get("분야"), user_input)
        response_text = result

    return jsonify({
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": response_text}}],
            "quickReplies": [{"label": btn, "action": "message", "messageText": btn} for btn in response_buttons]
        }
    })


if __name__ == '__main__':
    logging.info("🚀 Flask 서버가 시작되었습니다! http://0.0.0.0:7411")
    try:
        serve(app, host="0.0.0.0", port=7411)
    finally:
        shutdown_driver()
