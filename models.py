"""
데이터베이스 모델 (SQLite)
- 크롤링한 검사항목/검사주기 데이터 저장
- Q&A 질문-답변 저장 (신규)
- 미답변 질문 로깅 (신규)
"""
import sqlite3
from datetime import datetime
from rapidfuzz import fuzz
from config import DATABASE_PATH, VISION_API_MONTHLY_LIMIT


def get_connection():
    """데이터베이스 연결 (동시 접근 지원)"""
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL 모드: 읽기/쓰기 동시 접근 허용
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_database():
    """데이터베이스 초기화 - 테이블 생성"""
    conn = get_connection()
    cursor = conn.cursor()

    # 검사항목 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inspection_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            food_type TEXT NOT NULL,
            items TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 검사주기 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inspection_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            industry TEXT NOT NULL,
            food_group TEXT NOT NULL,
            food_type TEXT NOT NULL,
            cycle TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 크롤링 로그 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS crawl_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_type TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # API 사용량 추적 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_name TEXT NOT NULL,
            year_month TEXT NOT NULL,
            call_count INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(api_name, year_month)
        )
    """)

    # 영양성분검사 정보 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nutrition_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            test_type TEXT NOT NULL,
            details TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(category, test_type)
        )
    """)

    # 게시판 매핑 테이블 (자연어 처리용)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS board_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            base_url TEXT NOT NULL,
            title TEXT,
            content TEXT,
            keywords TEXT,
            synonyms TEXT,
            intent TEXT,
            priority INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ========== 신규 테이블 ==========

    # Q&A 질문-답변 테이블 (일반 질문용)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS qa_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            keywords TEXT,
            category TEXT DEFAULT '일반',
            use_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_by TEXT DEFAULT 'admin',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 미답변 질문 로그 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS unanswered_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            question TEXT NOT NULL,
            context TEXT,
            count INTEGER DEFAULT 1,
            is_resolved INTEGER DEFAULT 0,
            resolved_qa_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 관리자 테이블 (학습 권한 관리)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL UNIQUE,
            name TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# ===== 검사항목 관련 함수 =====

def save_inspection_item(category: str, food_type: str, items: str):
    """검사항목 저장 (기존 데이터 업데이트 또는 새로 추가)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id FROM inspection_items
        WHERE category = ? AND food_type = ?
    """, (category, food_type))

    existing = cursor.fetchone()

    if existing:
        cursor.execute("""
            UPDATE inspection_items
            SET items = ?, updated_at = ?
            WHERE id = ?
        """, (items, datetime.now(), existing['id']))
    else:
        cursor.execute("""
            INSERT INTO inspection_items (category, food_type, items)
            VALUES (?, ?, ?)
        """, (category, food_type, items))

    conn.commit()
    conn.close()


def get_inspection_item(category: str, food_type: str) -> dict:
    """검사항목 조회 (우선순위: 정확일치 > 끝나는일치 > 포함일치)"""
    conn = get_connection()
    cursor = conn.cursor()

    # 띄어쓰기 제거한 검색어
    search_key = food_type.replace(" ", "")

    # 1. 정확히 일치하는 경우
    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(food_type, ' ', '') = ?
    """, (category, search_key))
    result = cursor.fetchone()
    if result:
        conn.close()
        return dict(result)

    # 2. 검색어로 끝나는 경우 (예: "햄" → "생햄", "프레스햄")
    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(food_type, ' ', '') LIKE ?
    """, (category, f"%{search_key}"))
    results = cursor.fetchall()

    # 검색어로 시작하는 항목 제외 (예: "햄버거류"는 "햄"으로 시작하므로 제외)
    filtered = [r for r in results if not dict(r)['food_type'].replace(" ", "").startswith(search_key) or dict(r)['food_type'].replace(" ", "") == search_key]

    if filtered:
        conn.close()
        return dict(filtered[0])

    conn.close()
    return None


def get_inspection_item_all_matches(category: str, food_type: str) -> list:
    """검사항목 조회 - 모든 매칭 결과 반환 (정확일치 > 끝나는일치 > 포함일치)"""
    conn = get_connection()
    cursor = conn.cursor()

    # 띄어쓰기 및 가운데점(· 또는 ･) 제거한 검색어
    search_key = food_type.replace(" ", "").replace("·", "").replace("･", "")

    # 1. 정확히 일치하는 경우 (띄어쓰기, 가운데점 무시)
    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(REPLACE(REPLACE(food_type, ' ', ''), '·', ''), '･', '') = ?
    """, (category, search_key))
    result = cursor.fetchone()
    if result:
        conn.close()
        return [dict(result)]  # 정확 일치는 1개만 반환

    # 2. 검색어로 끝나는 경우 (예: "음료" → "탄산음료", "과채음료")
    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(REPLACE(REPLACE(food_type, ' ', ''), '·', ''), '･', '') LIKE ?
    """, (category, f"%{search_key}"))
    results = cursor.fetchall()

    # 검색어로 시작하는 항목 제외 (예: "햄버거류"는 "햄"으로 시작하므로 제외)
    endswith_filtered = [dict(r) for r in results if not dict(r)['food_type'].replace(" ", "").replace("·", "").replace("･", "").startswith(search_key) or dict(r)['food_type'].replace(" ", "").replace("·", "").replace("･", "") == search_key]

    if endswith_filtered:
        conn.close()
        return endswith_filtered

    # 3. 검색어가 포함된 경우 (예: "탄산" → "탄산음료", "유산균" → "유산균음료")
    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(REPLACE(REPLACE(food_type, ' ', ''), '·', ''), '･', '') LIKE ?
    """, (category, f"%{search_key}%"))
    results = cursor.fetchall()

    contains_filtered = [dict(r) for r in results]

    conn.close()
    return contains_filtered


def search_inspection_items(category: str, keyword: str) -> list:
    """검사항목 검색 (유사 검색) - 가운데점(·) 무시"""
    conn = get_connection()
    cursor = conn.cursor()

    # 가운데점(· 또는 ･) 제거한 검색어
    search_key = keyword.replace("·", "").replace("･", "")

    cursor.execute("""
        SELECT * FROM inspection_items
        WHERE category = ? AND REPLACE(REPLACE(food_type, '·', ''), '･', '') LIKE ?
        ORDER BY food_type
    """, (category, f"%{search_key}%"))

    results = cursor.fetchall()
    conn.close()

    return [dict(row) for row in results]


# ===== 검사주기 관련 함수 =====

def save_inspection_cycle(category: str, industry: str, food_group: str, food_type: str, cycle: str):
    """검사주기 저장"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id FROM inspection_cycles
        WHERE category = ? AND industry = ? AND food_type = ?
    """, (category, industry, food_type))

    existing = cursor.fetchone()

    if existing:
        cursor.execute("""
            UPDATE inspection_cycles
            SET food_group = ?, cycle = ?, updated_at = ?
            WHERE id = ?
        """, (food_group, cycle, datetime.now(), existing['id']))
    else:
        cursor.execute("""
            INSERT INTO inspection_cycles (category, industry, food_group, food_type, cycle)
            VALUES (?, ?, ?, ?, ?)
        """, (category, industry, food_group, food_type, cycle))

    conn.commit()
    conn.close()


def get_inspection_cycle(category: str, industry: str, food_type: str) -> dict:
    """검사주기 조회 (우선순위: 정확일치 > 끝나는일치 > 포함일치)"""
    conn = get_connection()
    cursor = conn.cursor()

    # 띄어쓰기 제거한 검색어
    search_key = food_type.replace(" ", "")

    # 1. 정확히 일치하는 경우
    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(food_type, ' ', '') = ?
    """, (category, industry, search_key))
    result = cursor.fetchone()
    if result:
        conn.close()
        return dict(result)

    # 2. 검색어로 끝나는 경우 (예: "햄" → "생햄", "프레스햄")
    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(food_type, ' ', '') LIKE ?
    """, (category, industry, f"%{search_key}"))
    results = cursor.fetchall()

    # 검색어로 시작하는 항목 제외 (예: "햄버거류"는 "햄"으로 시작하므로 제외)
    filtered = [r for r in results if not dict(r)['food_type'].replace(" ", "").startswith(search_key) or dict(r)['food_type'].replace(" ", "") == search_key]

    if filtered:
        conn.close()
        return dict(filtered[0])

    conn.close()
    return None


def get_inspection_cycle_all_matches(category: str, industry: str, food_type: str) -> list:
    """검사주기 조회 - 모든 매칭 결과 반환 (정확일치 > 끝나는일치 > 포함일치)"""
    conn = get_connection()
    cursor = conn.cursor()

    # 띄어쓰기 및 가운데점(· 또는 ･) 제거한 검색어
    search_key = food_type.replace(" ", "").replace("·", "").replace("･", "")

    # 1. 정확히 일치하는 경우 (띄어쓰기, 가운데점 무시)
    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(REPLACE(REPLACE(food_type, ' ', ''), '·', ''), '･', '') = ?
    """, (category, industry, search_key))
    result = cursor.fetchone()
    if result:
        conn.close()
        return [dict(result)]  # 정확 일치는 1개만 반환

    # 2. 검색어로 끝나는 경우 (예: "음료" → "탄산음료", "과채음료")
    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(REPLACE(REPLACE(food_type, ' ', ''), '·', ''), '･', '') LIKE ?
    """, (category, industry, f"%{search_key}"))
    results = cursor.fetchall()

    # 검색어로 시작하는 항목 제외 (예: "햄버거류"는 "햄"으로 시작하므로 제외)
    endswith_filtered = [dict(r) for r in results if not dict(r)['food_type'].replace(" ", "").replace("·", "").replace("･", "").startswith(search_key) or dict(r)['food_type'].replace(" ", "").replace("·", "").replace("･", "") == search_key]

    if endswith_filtered:
        conn.close()
        return endswith_filtered

    # 3. 검색어가 포함된 경우 (예: "탄산" → "탄산음료", "유산균" → "유산균음료")
    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(REPLACE(REPLACE(food_type, ' ', ''), '·', ''), '･', '') LIKE ?
    """, (category, industry, f"%{search_key}%"))
    results = cursor.fetchall()

    contains_filtered = [dict(r) for r in results]

    conn.close()
    return contains_filtered


def search_inspection_cycles(category: str, industry: str, keyword: str) -> list:
    """검사주기 검색 - 가운데점(·) 무시"""
    conn = get_connection()
    cursor = conn.cursor()

    # 가운데점(· 또는 ･) 제거한 검색어
    search_key = keyword.replace("·", "").replace("･", "")

    cursor.execute("""
        SELECT * FROM inspection_cycles
        WHERE category = ? AND industry = ? AND REPLACE(REPLACE(food_type, '·', ''), '･', '') LIKE ?
        ORDER BY food_type
    """, (category, industry, f"%{search_key}%"))

    results = cursor.fetchall()
    conn.close()

    return [dict(row) for row in results]


# ===== 유사 단어 검색 =====

def get_all_food_types_items(category: str) -> list:
    """검사항목의 모든 식품 유형 조회"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT food_type FROM inspection_items
        WHERE category = ?
    """, (category,))
    results = cursor.fetchall()
    conn.close()
    return [row['food_type'] for row in results]


def get_all_food_types_cycles(category: str, industry: str) -> list:
    """검사주기의 모든 식품 유형 조회"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT food_type FROM inspection_cycles
        WHERE category = ? AND industry = ?
    """, (category, industry))
    results = cursor.fetchall()
    conn.close()
    return [row['food_type'] for row in results]


def find_similar_items(category: str, keyword: str, min_score: int = 40) -> list:
    """검사항목에서 유사한 식품 유형 찾기 (검색어로 시작/끝나는 단어 제외)"""
    all_types = get_all_food_types_items(category)
    similar = []

    # 띄어쓰기 및 가운데점(·) 제거
    keyword_normalized = keyword.replace(" ", "").replace("·", "").replace("･", "")

    for food_type in all_types:
        food_type_normalized = food_type.replace(" ", "").replace("·", "").replace("･", "")

        # 검색어로 시작하는 단어 제외 (예: "햄" 검색 시 "햄버거류" 제외)
        if food_type_normalized.startswith(keyword_normalized) and food_type_normalized != keyword_normalized:
            continue

        # 검색어로 끝나는 단어 제외 (endswith 매칭에서 처리됨)
        if food_type_normalized.endswith(keyword_normalized) and food_type_normalized != keyword_normalized:
            continue

        # 공통 글자 수 체크
        common_chars = set(keyword_normalized) & set(food_type_normalized)
        if len(common_chars) >= 2:
            # 짧은 검색어는 ratio만 사용
            if len(keyword_normalized) <= 2:
                score = fuzz.ratio(keyword_normalized, food_type_normalized)
            else:
                score = fuzz.partial_ratio(keyword_normalized, food_type_normalized)
            if score >= min_score:
                similar.append((food_type, score))

    # 점수순 정렬
    similar.sort(key=lambda x: x[1], reverse=True)
    return [item[0] for item in similar[:5]]


def find_similar_cycles(category: str, industry: str, keyword: str, min_score: int = 40) -> list:
    """검사주기에서 유사한 식품 유형 찾기 (검색어로 시작/끝나는 단어 제외)"""
    all_types = get_all_food_types_cycles(category, industry)
    similar = []

    # 띄어쓰기 및 가운데점(·) 제거
    keyword_normalized = keyword.replace(" ", "").replace("·", "").replace("･", "")

    for food_type in all_types:
        food_type_normalized = food_type.replace(" ", "").replace("·", "").replace("･", "")

        # 검색어로 시작하는 단어 제외 (예: "햄" 검색 시 "햄버거류" 제외)
        if food_type_normalized.startswith(keyword_normalized) and food_type_normalized != keyword_normalized:
            continue

        # 검색어로 끝나는 단어 제외 (endswith 매칭에서 처리됨)
        if food_type_normalized.endswith(keyword_normalized) and food_type_normalized != keyword_normalized:
            continue

        # 공통 글자 수 체크
        common_chars = set(keyword_normalized) & set(food_type_normalized)
        if len(common_chars) >= 2:
            # 짧은 검색어는 ratio만 사용
            if len(keyword_normalized) <= 2:
                score = fuzz.ratio(keyword_normalized, food_type_normalized)
            else:
                score = fuzz.partial_ratio(keyword_normalized, food_type_normalized)
            if score >= min_score:
                similar.append((food_type, score))

    # 점수순 정렬
    similar.sort(key=lambda x: x[1], reverse=True)
    return [item[0] for item in similar[:5]]


# ===== 영양성분검사 관련 함수 =====

def save_nutrition_info(category: str, test_type: str, details: str):
    """영양성분검사 정보 저장 (기존 데이터 업데이트 또는 새로 추가)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO nutrition_info (category, test_type, details, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(category, test_type)
        DO UPDATE SET details = ?, updated_at = ?
    """, (category, test_type, details, datetime.now(), details, datetime.now()))

    conn.commit()
    conn.close()


def get_nutrition_info(category: str, test_type: str) -> dict:
    """영양성분검사 정보 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM nutrition_info
        WHERE category = ? AND test_type = ?
    """, (category, test_type))

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


def get_all_nutrition_info(category: str) -> list:
    """특정 카테고리의 모든 영양성분검사 정보 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM nutrition_info
        WHERE category = ?
        ORDER BY test_type
    """, (category,))

    results = cursor.fetchall()
    conn.close()

    return [dict(row) for row in results]


# ===== 크롤링 로그 =====

def save_crawl_log(crawl_type: str, status: str, message: str = None):
    """크롤링 로그 저장"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO crawl_logs (crawl_type, status, message)
        VALUES (?, ?, ?)
    """, (crawl_type, status, message))

    conn.commit()
    conn.close()


def get_last_crawl_time() -> datetime:
    """마지막 크롤링 시간 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT created_at FROM crawl_logs
        WHERE status = 'success'
        ORDER BY created_at DESC LIMIT 1
    """)

    result = cursor.fetchone()
    conn.close()

    if result:
        return datetime.fromisoformat(result['created_at'])
    return None


# ===== API 사용량 추적 =====

def get_current_year_month() -> str:
    """현재 년월 문자열 반환 (YYYY-MM)"""
    return datetime.now().strftime("%Y-%m")


def get_api_usage(api_name: str) -> int:
    """현재 월의 API 호출 횟수 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    year_month = get_current_year_month()

    cursor.execute("""
        SELECT call_count FROM api_usage
        WHERE api_name = ? AND year_month = ?
    """, (api_name, year_month))

    result = cursor.fetchone()
    conn.close()

    return result['call_count'] if result else 0


def increment_api_usage(api_name: str = "google_vision") -> int:
    """API 호출 횟수 증가 및 현재 횟수 반환"""
    conn = get_connection()
    cursor = conn.cursor()

    year_month = get_current_year_month()

    # UPSERT: 있으면 증가, 없으면 새로 생성
    cursor.execute("""
        INSERT INTO api_usage (api_name, year_month, call_count, updated_at)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(api_name, year_month)
        DO UPDATE SET call_count = call_count + 1, updated_at = ?
    """, (api_name, year_month, datetime.now(), datetime.now()))

    conn.commit()

    # 현재 횟수 조회
    cursor.execute("""
        SELECT call_count FROM api_usage
        WHERE api_name = ? AND year_month = ?
    """, (api_name, year_month))

    result = cursor.fetchone()
    conn.close()

    return result['call_count'] if result else 0


def can_use_vision_api() -> bool:
    """Vision API 사용 가능 여부 (월별 제한 체크)"""
    current_usage = get_api_usage("google_vision")
    return current_usage < VISION_API_MONTHLY_LIMIT


def get_vision_api_remaining() -> int:
    """Vision API 남은 호출 횟수"""
    current_usage = get_api_usage("google_vision")
    return max(0, VISION_API_MONTHLY_LIMIT - current_usage)


# ===== 게시판 매핑 관련 함수 (자연어 처리용) =====

def save_board_mapping(question_id: str, category: str, base_url: str,
                       title: str = None, content: str = None,
                       keywords: str = None, synonyms: str = None,
                       intent: str = None, priority: int = 0):
    """게시판 매핑 저장 (기존 데이터 업데이트 또는 새로 추가)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO board_mappings (question_id, category, base_url, title, content,
                                    keywords, synonyms, intent, priority, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(question_id)
        DO UPDATE SET category = ?, base_url = ?, title = ?, content = ?,
                      keywords = ?, synonyms = ?, intent = ?, priority = ?,
                      updated_at = ?
    """, (question_id, category, base_url, title, content, keywords, synonyms, intent, priority, datetime.now(),
          category, base_url, title, content, keywords, synonyms, intent, priority, datetime.now()))

    conn.commit()
    conn.close()


def get_board_mapping(question_id: str) -> dict:
    """특정 게시판 매핑 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM board_mappings WHERE question_id = ? AND is_active = 1
    """, (question_id,))

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


def search_board_by_keywords(search_text: str) -> list:
    """키워드로 게시판 검색 (자연어 매칭)"""
    conn = get_connection()
    cursor = conn.cursor()

    # 검색어를 단어로 분리
    search_words = search_text.replace(",", " ").split()

    results = []
    for word in search_words:
        word = word.strip()
        if len(word) < 2:
            continue

        cursor.execute("""
            SELECT *,
                   CASE
                       WHEN title LIKE ? THEN 10
                       WHEN keywords LIKE ? THEN 8
                       WHEN synonyms LIKE ? THEN 6
                       WHEN content LIKE ? THEN 4
                       ELSE 0
                   END as match_score
            FROM board_mappings
            WHERE is_active = 1
              AND (title LIKE ? OR keywords LIKE ? OR synonyms LIKE ? OR content LIKE ? OR category LIKE ?)
            ORDER BY match_score DESC, priority DESC
        """, (f"%{word}%", f"%{word}%", f"%{word}%", f"%{word}%",
              f"%{word}%", f"%{word}%", f"%{word}%", f"%{word}%", f"%{word}%"))

        for row in cursor.fetchall():
            result_dict = dict(row)
            # 중복 제거
            if not any(r['question_id'] == result_dict['question_id'] for r in results):
                results.append(result_dict)

    conn.close()
    return results[:10]  # 최대 10개


def get_all_board_mappings(category: str = None) -> list:
    """모든 게시판 매핑 조회 (카테고리 필터 가능)"""
    conn = get_connection()
    cursor = conn.cursor()

    if category:
        cursor.execute("""
            SELECT * FROM board_mappings WHERE category = ? AND is_active = 1
            ORDER BY category, question_id
        """, (category,))
    else:
        cursor.execute("""
            SELECT * FROM board_mappings WHERE is_active = 1
            ORDER BY category, question_id
        """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def update_board_keywords(question_id: str, keywords: str, synonyms: str = None, intent: str = None):
    """게시판 키워드 업데이트"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE board_mappings
        SET keywords = ?, synonyms = ?, intent = ?, updated_at = ?
        WHERE question_id = ?
    """, (keywords, synonyms, intent, datetime.now(), question_id))

    conn.commit()
    conn.close()


# ========== Q&A 질문-답변 관련 함수 (신규) ==========

def save_qa_response(question: str, answer: str, keywords: str = None,
                     category: str = "일반", created_by: str = "admin") -> int:
    """Q&A 저장 - 새로운 질문-답변 쌍 추가"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO qa_responses (question, answer, keywords, category, created_by, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (question, answer, keywords, category, created_by, datetime.now()))

    qa_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return qa_id


def update_qa_response(qa_id: int, question: str = None, answer: str = None,
                       keywords: str = None, category: str = None) -> bool:
    """Q&A 수정"""
    conn = get_connection()
    cursor = conn.cursor()

    # 현재 데이터 조회
    cursor.execute("SELECT * FROM qa_responses WHERE id = ?", (qa_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        return False

    existing = dict(existing)

    # 변경된 필드만 업데이트
    cursor.execute("""
        UPDATE qa_responses
        SET question = ?, answer = ?, keywords = ?, category = ?, updated_at = ?
        WHERE id = ?
    """, (
        question or existing['question'],
        answer or existing['answer'],
        keywords if keywords is not None else existing['keywords'],
        category or existing['category'],
        datetime.now(),
        qa_id
    ))

    conn.commit()
    conn.close()
    return True


def delete_qa_response(qa_id: int) -> bool:
    """Q&A 삭제 (비활성화)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE qa_responses SET is_active = 0, updated_at = ? WHERE id = ?
    """, (datetime.now(), qa_id))

    affected = cursor.rowcount
    conn.commit()
    conn.close()

    return affected > 0


def search_qa_response(search_text: str, min_score: int = 50) -> dict:
    """Q&A 검색 - 사용자 질문과 가장 유사한 답변 찾기"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM qa_responses WHERE is_active = 1
    """)

    all_qa = cursor.fetchall()
    conn.close()

    if not all_qa:
        return None

    # 검색어 정규화
    search_normalized = search_text.replace(" ", "").lower()
    best_match = None
    best_score = 0

    for qa in all_qa:
        qa_dict = dict(qa)
        question = qa_dict['question']
        keywords = qa_dict['keywords'] or ""

        # 질문 정규화
        question_normalized = question.replace(" ", "").lower()

        # 1. 정확히 일치
        if search_normalized == question_normalized:
            # 사용 횟수 증가
            increment_qa_usage(qa_dict['id'])
            return qa_dict

        # 2. 키워드 포함 체크
        keyword_list = [k.strip().lower() for k in keywords.split(",") if k.strip()]
        for kw in keyword_list:
            if kw in search_normalized or search_normalized in kw:
                score = 80  # 키워드 매칭 높은 점수
                if score > best_score:
                    best_score = score
                    best_match = qa_dict

        # 3. 유사도 계산
        score = fuzz.partial_ratio(search_normalized, question_normalized)
        if score > best_score:
            best_score = score
            best_match = qa_dict

    # 최소 점수 이상인 경우만 반환
    if best_match and best_score >= min_score:
        increment_qa_usage(best_match['id'])
        return best_match

    return None


def increment_qa_usage(qa_id: int):
    """Q&A 사용 횟수 증가"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE qa_responses SET use_count = use_count + 1, updated_at = ? WHERE id = ?
    """, (datetime.now(), qa_id))

    conn.commit()
    conn.close()


def get_all_qa_responses(category: str = None, include_inactive: bool = False) -> list:
    """모든 Q&A 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    if category and not include_inactive:
        cursor.execute("""
            SELECT * FROM qa_responses WHERE category = ? AND is_active = 1
            ORDER BY use_count DESC, updated_at DESC
        """, (category,))
    elif category:
        cursor.execute("""
            SELECT * FROM qa_responses WHERE category = ?
            ORDER BY use_count DESC, updated_at DESC
        """, (category,))
    elif not include_inactive:
        cursor.execute("""
            SELECT * FROM qa_responses WHERE is_active = 1
            ORDER BY use_count DESC, updated_at DESC
        """)
    else:
        cursor.execute("""
            SELECT * FROM qa_responses
            ORDER BY use_count DESC, updated_at DESC
        """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def get_qa_by_id(qa_id: int) -> dict:
    """ID로 Q&A 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM qa_responses WHERE id = ?", (qa_id,))
    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


# ========== 미답변 질문 로깅 (신규) ==========

def log_unanswered_question(question: str, user_id: str = None, context: str = None) -> int:
    """미답변 질문 로깅"""
    conn = get_connection()
    cursor = conn.cursor()

    # 동일한 질문이 있는지 확인 (정규화해서 비교)
    question_normalized = question.replace(" ", "").lower()

    cursor.execute("""
        SELECT id, count FROM unanswered_questions
        WHERE REPLACE(LOWER(question), ' ', '') = ? AND is_resolved = 0
    """, (question_normalized,))

    existing = cursor.fetchone()

    if existing:
        # 이미 있으면 카운트 증가
        cursor.execute("""
            UPDATE unanswered_questions
            SET count = count + 1, updated_at = ?
            WHERE id = ?
        """, (datetime.now(), existing['id']))
        log_id = existing['id']
    else:
        # 새로 추가
        cursor.execute("""
            INSERT INTO unanswered_questions (user_id, question, context)
            VALUES (?, ?, ?)
        """, (user_id, question, context))
        log_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return log_id


def get_unanswered_questions(limit: int = 20, only_unresolved: bool = True) -> list:
    """미답변 질문 목록 조회 (빈도순)"""
    conn = get_connection()
    cursor = conn.cursor()

    if only_unresolved:
        cursor.execute("""
            SELECT * FROM unanswered_questions
            WHERE is_resolved = 0
            ORDER BY count DESC, updated_at DESC
            LIMIT ?
        """, (limit,))
    else:
        cursor.execute("""
            SELECT * FROM unanswered_questions
            ORDER BY count DESC, updated_at DESC
            LIMIT ?
        """, (limit,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def get_unanswered_by_id(unanswered_id: int) -> dict:
    """ID로 미답변 질문 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM unanswered_questions WHERE id = ?", (unanswered_id,))
    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


def resolve_unanswered_question(unanswered_id: int, qa_id: int = None) -> bool:
    """미답변 질문을 해결됨으로 표시"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE unanswered_questions
        SET is_resolved = 1, resolved_qa_id = ?, updated_at = ?
        WHERE id = ?
    """, (qa_id, datetime.now(), unanswered_id))

    affected = cursor.rowcount
    conn.commit()
    conn.close()

    return affected > 0


def delete_unanswered_question(unanswered_id: int) -> bool:
    """미답변 질문 삭제"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM unanswered_questions WHERE id = ?", (unanswered_id,))

    affected = cursor.rowcount
    conn.commit()
    conn.close()

    return affected > 0


# ========== 관리자 관련 함수 (신규) ==========

def add_admin_user(user_id: str, name: str = None) -> bool:
    """관리자 추가"""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO admin_users (user_id, name) VALUES (?, ?)
        """, (user_id, name))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def remove_admin_user(user_id: str) -> bool:
    """관리자 제거"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE admin_users SET is_active = 0 WHERE user_id = ?
    """, (user_id,))

    affected = cursor.rowcount
    conn.commit()
    conn.close()

    return affected > 0


def is_admin_user(user_id: str) -> bool:
    """관리자 여부 확인"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id FROM admin_users WHERE user_id = ? AND is_active = 1
    """, (user_id,))

    result = cursor.fetchone()
    conn.close()

    return result is not None


def get_all_admin_users() -> list:
    """모든 관리자 목록 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM admin_users WHERE is_active = 1
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def has_any_admin() -> bool:
    """관리자가 한 명이라도 있는지 확인"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as cnt FROM admin_users WHERE is_active = 1")
    result = cursor.fetchone()
    conn.close()

    return result['cnt'] > 0


# 데이터베이스 초기화 실행
if __name__ == "__main__":
    init_database()
    print("데이터베이스 초기화 완료!")
