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

    # 1회 섭취참고량 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS serving_size_reference (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            food_group TEXT NOT NULL,
            food_type TEXT NOT NULL,
            food_subtype TEXT,
            detail TEXT,
            serving_size REAL NOT NULL,
            unit TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 테이블이 비어있으면 초기 데이터 삽입
    cursor.execute("SELECT COUNT(*) as cnt FROM serving_size_reference")
    if cursor.fetchone()['cnt'] == 0:
        _insert_serving_size_data(cursor)

    # ========== 영양성분 표시 도우미 테이블 ==========

    # 1일 영양성분 기준치 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_value (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nutrient TEXT NOT NULL UNIQUE,
            daily_value REAL NOT NULL,
            unit TEXT NOT NULL,
            display_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 영양강조표시 기준 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nutrient_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nutrient TEXT NOT NULL,
            claim_type TEXT NOT NULL,
            condition TEXT NOT NULL,
            threshold REAL,
            unit TEXT,
            per_basis TEXT DEFAULT '100g',
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(nutrient, claim_type)
        )
    """)

    # 반올림 규칙 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rounding_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nutrient TEXT NOT NULL UNIQUE,
            rule_type TEXT NOT NULL,
            decimal_places INTEGER DEFAULT 0,
            round_to INTEGER,
            zero_threshold REAL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 초기 데이터 삽입
    cursor.execute("SELECT COUNT(*) as cnt FROM daily_value")
    if cursor.fetchone()['cnt'] == 0:
        _insert_daily_value_data(cursor)

    cursor.execute("SELECT COUNT(*) as cnt FROM nutrient_claims")
    if cursor.fetchone()['cnt'] == 0:
        _insert_nutrient_claims_data(cursor)

    cursor.execute("SELECT COUNT(*) as cnt FROM rounding_rules")
    if cursor.fetchone()['cnt'] == 0:
        _insert_rounding_rules_data(cursor)

    conn.commit()
    conn.close()


def _insert_serving_size_data(cursor):
    """1회 섭취참고량 초기 데이터 삽입"""
    data = [
        # [과자류, 빵류 또는 떡류]
        ("과자류, 빵류 또는 떡류", "과자", "강냉이,팝콘", None, 20, "g"),
        ("과자류, 빵류 또는 떡류", "과자", "기타", None, 30, "g"),
        ("과자류, 빵류 또는 떡류", "캔디류", "양갱", None, 50, "g"),
        ("과자류, 빵류 또는 떡류", "캔디류", "푸딩", None, 100, "g"),
        ("과자류, 빵류 또는 떡류", "캔디류", "그밖의해당식품", None, 10, "g"),
        ("과자류, 빵류 또는 떡류", "추잉껌", None, None, 3, "g"),
        ("과자류, 빵류 또는 떡류", "빵류", "피자", None, 150, "g"),
        ("과자류, 빵류 또는 떡류", "빵류", "그밖의해당식품", None, 70, "g"),
        ("과자류, 빵류 또는 떡류", "떡류", None, None, 100, "g"),

        # [빙과류]
        ("빙과류", "아이스크림류", None, None, 100, "ml"),
        ("빙과류", "빙과", None, None, 100, "g(ml)"),

        # [코코아가공품류 또는 초콜릿류]
        ("코코아가공품류 또는 초콜릿류", "초콜릿가공품", None, None, 30, "g"),
        ("코코아가공품류 또는 초콜릿류", "초콜릿류", "초콜릿가공품 제외", None, 15, "g"),

        # [당류]
        ("당류", "설탕류", None, None, 5, "g"),
        ("당류", "당시럽류", None, None, 10, "g"),
        ("당류", "올리고당", None, None, 10, "g"),
        ("당류", "물엿", None, None, 10, "g"),
        ("당류", "덩어리엿", None, None, 10, "g"),
        ("당류", "가루엿", None, None, 5, "g"),

        # [잼류]
        ("잼류", "잼", None, None, 20, "g"),
        ("잼류", "기타잼", None, None, 20, "g"),

        # [두부류 또는 묵류]
        ("두부류 또는 묵류", "두부", None, None, 80, "g"),
        ("두부류 또는 묵류", "유바", None, None, 80, "g"),
        ("두부류 또는 묵류", "가공두부", None, None, 80, "g"),
        ("두부류 또는 묵류", "묵류", None, None, 80, "g"),

        # [식용유지류]
        ("식용유지류", "식용유", None, None, 5, "g(ml)"),
        ("식용유지류", "모조치즈", None, None, 20, "g"),
        ("식용유지류", "식물성크림", None, None, 5, "g"),

        # [면류]
        ("면류", "생면", None, None, 200, "g"),
        ("면류", "숙면", None, None, 200, "g"),
        ("면류", "건면", "당면제외", None, 100, "g"),
        ("면류", "당면", None, None, 30, "g"),
        ("면류", "유탕면", "봉지", None, 120, "g"),
        ("면류", "유탕면", "용기", None, 80, "g"),

        # [음료류]
        ("음료류", "침출차", "당류포함", None, 200, "ml"),
        ("음료류", "침출차", "당류비포함", None, 300, "ml"),
        ("음료류", "액상차", "당류포함", None, 200, "ml"),
        ("음료류", "액상차", "당류비포함", None, 300, "ml"),
        ("음료류", "고형차", None, None, 200, "ml"),
        ("음료류", "커피", None, None, 240, "ml"),
        ("음료류", "농축과채즙", None, None, 100, "ml"),
        ("음료류", "과채주스", None, None, 200, "ml"),
        ("음료류", "과채음료", None, None, 200, "ml"),
        ("음료류", "탄산음료", None, None, 200, "ml"),
        ("음료류", "탄산수", None, None, 300, "ml"),
        ("음료류", "두유류", None, None, 200, "ml"),
        ("음료류", "발효음료류", None, None, 100, "ml"),
        ("음료류", "인삼홍삼음료", None, None, 100, "ml"),
        ("음료류", "혼합음료", None, None, 200, "ml"),
        ("음료류", "음료베이스", None, None, 150, "ml"),

        # [장류]
        ("장류", "한식간장", None, None, 5, "ml"),
        ("장류", "양조간장", None, None, 5, "ml"),
        ("장류", "산분해간장", None, None, 5, "ml"),
        ("장류", "효소분해간장", None, None, 5, "ml"),
        ("장류", "혼합간장", None, None, 5, "ml"),
        ("장류", "한식된장", None, None, 10, "g"),
        ("장류", "된장", None, None, 10, "g"),
        ("장류", "고추장", None, None, 10, "g"),
        ("장류", "혼합장", None, None, 10, "g"),
        ("장류", "기타장류", None, None, 10, "g"),
        ("장류", "춘장", None, None, 25, "g"),
        ("장류", "청국장", None, None, 25, "g"),
        ("장류", "나토", None, None, 50, "g"),

        # [조미식품]
        ("조미식품", "식초", None, None, 5, "ml"),
        ("조미식품", "소스", None, None, 15, "g"),
        ("조미식품", "드레싱", None, None, 15, "g"),
        ("조미식품", "덮밥소스", None, None, 165, "g"),
        ("조미식품", "마요네즈", None, None, 10, "g"),
        ("조미식품", "토마토케첩", None, None, 10, "g"),
        ("조미식품", "카레", "레토르트", None, 200, "g"),
        ("조미식품", "카레", "기타", None, 25, "g"),

        # [절임류 또는 조림류]
        ("절임류 또는 조림류", "배추김치", None, None, 40, "g"),
        ("절임류 또는 조림류", "물김치", None, None, 80, "g"),
        ("절임류 또는 조림류", "기타김치", None, None, 40, "g"),
        ("절임류 또는 조림류", "장아찌", None, None, 15, "g"),
        ("절임류 또는 조림류", "절임", "그밖의해당식품", None, 25, "g"),
        ("절임류 또는 조림류", "당절임", None, None, 25, "g"),

        # [농산가공식품류]
        ("농산가공식품류", "땅콩버터", None, None, 5, "g"),
        ("농산가공식품류", "땅콩또는견과류가공품", None, None, 10, "g"),
        ("농산가공식품류", "시리얼류", None, None, 30, "g"),
        ("농산가공식품류", "건과류", None, None, 15, "g"),
        ("농산가공식품류", "과채가공품", "기타", None, 30, "g"),
        ("농산가공식품류", "누룽지", None, None, 60, "g"),
        ("농산가공식품류", "감자튀김", None, None, 40, "g"),

        # [식육가공품]
        ("식육가공품", "햄", None, None, 30, "g"),
        ("식육가공품", "프레스햄", None, None, 30, "g"),
        ("식육가공품", "소시지", None, None, 30, "g"),
        ("식육가공품", "발효소시지", None, None, 30, "g"),
        ("식육가공품", "혼합소시지", None, None, 30, "g"),
        ("식육가공품", "베이컨류", None, None, 30, "g"),
        ("식육가공품", "건조저장육류", None, None, 15, "g"),
        ("식육가공품", "양념육", None, None, 100, "g"),
        ("식육가공품", "분쇄가공육제품", None, None, 50, "g"),
        ("식육가공품", "갈비가공품", None, None, 100, "g"),
        ("식육가공품", "식육추출가공품", None, None, 240, "g"),
        ("식육가공품", "육포", None, None, 15, "g"),
        ("식육가공품", "식육함유가공품", "기타", None, 50, "g"),

        # [알가공품류]
        ("알가공품류", "알가공품", None, None, 50, "g"),
        ("알가공품류", "알함유가공품", None, None, 50, "g"),

        # [유가공품]
        ("유가공품", "우유", None, None, 200, "ml"),
        ("유가공품", "환원유", None, None, 200, "ml"),
        ("유가공품", "가공유류", None, None, 200, "ml"),
        ("유가공품", "산양유", None, None, 200, "ml"),
        ("유가공품", "발효유", None, None, 80, "ml"),
        ("유가공품", "발효유류", "액상", None, 150, "ml"),
        ("유가공품", "발효유류", "호상", None, 100, "ml"),
        ("유가공품", "버터", None, None, 5, "g"),
        ("유가공품", "가공버터", None, None, 5, "g"),
        ("유가공품", "치즈", None, None, 20, "g"),
        ("유가공품", "가공치즈", None, None, 20, "g"),

        # [수산가공식품류]
        ("수산가공식품류", "어육살", None, None, 30, "g"),
        ("수산가공식품류", "연육", None, None, 30, "g"),
        ("수산가공식품류", "어육반제품", None, None, 30, "g"),
        ("수산가공식품류", "어묵", None, None, 30, "g"),
        ("수산가공식품류", "어육소시지", None, None, 45, "g"),
        ("수산가공식품류", "기타어육가공품", None, None, 30, "g"),
        ("수산가공식품류", "조미건어포", None, None, 15, "g"),
        ("수산가공식품류", "건어포", None, None, 15, "g"),
        ("수산가공식품류", "기타건포류", None, None, 15, "g"),
        ("수산가공식품류", "조미김", None, None, 4, "g"),
        ("수산가공식품류", "김자반", None, None, 5, "g"),

        # [동물성가공식품류]
        ("동물성가공식품류", "기타식육또는기타알", None, None, 60, "g"),
        ("동물성가공식품류", "번데기통조림", None, None, 30, "g"),
        ("동물성가공식품류", "추출가공식품", None, None, 80, "g"),

        # [벌꿀및화분가공품류]
        ("벌꿀및화분가공품류", "벌꿀류", None, None, 20, "g"),

        # [즉석식품류]
        ("즉석식품류", "생식류", None, None, 40, "g"),
        ("즉석식품류", "도시락", None, None, 1, "식"),
        ("즉석식품류", "김밥류", None, None, 1, "식"),
        ("즉석식품류", "햄버거", None, None, 150, "g"),
        ("즉석식품류", "샌드위치류", None, None, 150, "g"),
        ("즉석식품류", "즉석섭취식품", "기타", None, 1, "식"),
        ("즉석식품류", "밥", None, None, 210, "g"),
        ("즉석식품류", "국", None, None, 250, "ml(g)"),
        ("즉석식품류", "탕", None, None, 250, "ml(g)"),
        ("즉석식품류", "찌개", None, None, 200, "ml(g)"),
        ("즉석식품류", "죽", None, None, 250, "ml(g)"),
        ("즉석식품류", "스프", None, None, 150, "ml(g)"),
        ("즉석식품류", "만두류", None, None, 150, "g"),

        # [식용란]
        ("식용란", "식용란", None, None, 50, "g"),
    ]

    for row in data:
        cursor.execute("""
            INSERT INTO serving_size_reference (food_group, food_type, food_subtype, detail, serving_size, unit)
            VALUES (?, ?, ?, ?, ?, ?)
        """, row)


def _insert_daily_value_data(cursor):
    """1일 영양성분 기준치 초기 데이터 삽입"""
    data = [
        # (영양소, 기준치, 단위, 표시순서)
        # 식품 등의 표시·광고에 관한 법률 시행규칙 [별표 5] (2022.11.28 개정)
        ("탄수화물", 324, "g", 1),
        ("당류", 100, "g", 2),
        ("식이섬유", 25, "g", 3),
        ("단백질", 55, "g", 4),
        ("지방", 54, "g", 5),
        ("리놀레산", 10, "g", 6),
        ("알파-리놀렌산", 1.3, "g", 7),
        ("EPA와 DHA의 합", 330, "mg", 8),
        ("포화지방", 15, "g", 9),
        ("콜레스테롤", 300, "mg", 10),
        ("비타민A", 700, "μg RAE", 11),
        ("비타민D", 10, "μg", 12),
        ("비타민E", 11, "mg α-TE", 13),
        ("비타민K", 70, "μg", 14),
        ("비타민C", 100, "mg", 15),
        ("비타민B1", 1.2, "mg", 16),
        ("비타민B2", 1.4, "mg", 17),
        ("나이아신", 15, "mg NE", 18),
        ("비타민B6", 1.5, "mg", 19),
        ("엽산", 400, "μg DFE", 20),
        ("비타민B12", 2.4, "μg", 21),
        ("판토텐산", 5, "mg", 22),
        ("바이오틴", 30, "μg", 23),
        ("칼슘", 700, "mg", 24),
        ("인", 700, "mg", 25),
        ("나트륨", 2000, "mg", 26),
        ("칼륨", 3500, "mg", 27),
        ("마그네슘", 315, "mg", 28),
        ("철분", 12, "mg", 29),
        ("아연", 8.5, "mg", 30),
        ("구리", 0.8, "mg", 31),
        ("망간", 3.0, "mg", 32),
        ("요오드", 150, "μg", 33),
        ("셀레늄", 55, "μg", 34),
        ("몰리브덴", 25, "μg", 35),
        ("크롬", 30, "μg", 36),
    ]

    for row in data:
        nutrient, dv, unit, order = row
        cursor.execute("""
            INSERT INTO daily_value (nutrient, daily_value, unit, display_order)
            VALUES (?, ?, ?, ?)
        """, (nutrient, dv, unit, order))


def _insert_nutrient_claims_data(cursor):
    """영양강조표시 기준 초기 데이터 삽입"""
    data = [
        # 열량
        ("열량", "무", "40kcal 미만", 40, "kcal", "100g", None),
        ("열량", "저", "40kcal 이하", 40, "kcal", "100g", "음료는 20kcal 이하/100ml"),

        # 지방
        ("지방", "무", "0.5g 미만", 0.5, "g", "100g", None),
        ("지방", "저", "3g 이하", 3, "g", "100g", None),

        # 포화지방
        ("포화지방", "무", "0.1g 미만 + 트랜스지방 0.5g 미만", 0.1, "g", "100g", "트랜스지방도 함께 체크"),
        ("포화지방", "저", "1.5g 이하 + 열량의 10% 이하", 1.5, "g", "100g", "열량 기준도 함께 체크"),

        # 콜레스테롤
        ("콜레스테롤", "무", "5mg 미만 + 포화지방 1.5g 이하", 5, "mg", "100g", "포화지방도 함께 체크"),
        ("콜레스테롤", "저", "20mg 이하 + 포화지방 1.5g 이하", 20, "mg", "100g", "포화지방도 함께 체크"),

        # 나트륨
        ("나트륨", "무", "5mg 미만", 5, "mg", "100g", None),
        ("나트륨", "저", "120mg 이하", 120, "mg", "100g", None),

        # 당류
        ("당류", "무", "0.5g 미만", 0.5, "g", "100g", None),
        ("당류", "저", "5g 이하", 5, "g", "100g", None),
        ("당류", "무가당", "당류 무첨가 + 0.5g 미만", 0.5, "g", "100g", "당류를 첨가하지 않은 경우"),

        # 식이섬유
        ("식이섬유", "함유", "3g 이상", 3, "g", "100g", "1회섭취참고량 기준 가능"),
        ("식이섬유", "고", "6g 이상", 6, "g", "100g", "1회섭취참고량 기준 가능"),

        # 단백질
        ("단백질", "함유", "1일기준치 10% 이상", 5.5, "g", "100g", "55g의 10%"),
        ("단백질", "고", "1일기준치 20% 이상", 11, "g", "100g", "55g의 20%"),

        # 비타민/무기질 (대표적인 것들)
        ("비타민C", "함유", "1일기준치 15% 이상", 15, "mg", "100g", "100mg의 15%"),
        ("비타민C", "고", "1일기준치 30% 이상", 30, "mg", "100g", "100mg의 30%"),
        ("칼슘", "함유", "1일기준치 15% 이상", 105, "mg", "100g", "700mg의 15%"),
        ("칼슘", "고", "1일기준치 30% 이상", 210, "mg", "100g", "700mg의 30%"),
        ("철", "함유", "1일기준치 15% 이상", 1.8, "mg", "100g", "12mg의 15%"),
        ("철", "고", "1일기준치 30% 이상", 3.6, "mg", "100g", "12mg의 30%"),
    ]

    for row in data:
        cursor.execute("""
            INSERT INTO nutrient_claims (nutrient, claim_type, condition, threshold, unit, per_basis, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, row)


def _insert_rounding_rules_data(cursor):
    """반올림 규칙 초기 데이터 삽입"""
    data = [
        # (영양소, 규칙유형, 소수점자리, 반올림단위, 0표시기준, 비고)
        ("열량", "round_to_nearest", 0, 5, 5, "5kcal 단위로 반올림, 5kcal 미만은 0"),
        ("탄수화물", "round_to_nearest", 0, 1, 0.5, "1g 단위로 반올림, 0.5g 미만은 0"),
        ("당류", "round_to_nearest", 0, 1, 0.5, "1g 단위로 반올림, 0.5g 미만은 0"),
        ("식이섬유", "round_to_nearest", 0, 1, 0.5, "1g 단위로 반올림, 0.5g 미만은 0"),
        ("단백질", "round_to_nearest", 0, 1, 0.5, "1g 단위로 반올림, 0.5g 미만은 0"),
        ("지방", "round_to_nearest", 1, None, 0.5, "0.5g 미만은 0, 0.5g 이상 5g 미만은 0.5g 단위로, 5g 이상은 1g 단위로"),
        ("포화지방", "round_to_nearest", 1, None, 0.5, "0.5g 미만은 0, 0.5g 이상 5g 미만은 0.5g 단위로, 5g 이상은 1g 단위로"),
        ("트랜스지방", "round_to_nearest", 1, None, 0.5, "0.5g 미만은 0, 0.5g 이상 5g 미만은 0.5g 단위로, 5g 이상은 1g 단위로"),
        ("콜레스테롤", "round_to_nearest", 0, 5, 2, "5mg 단위로 반올림, 2mg 미만은 0"),
        ("나트륨", "round_to_nearest", 0, 5, 5, "5mg 단위로 반올림, 5mg 미만은 0"),
    ]

    for row in data:
        cursor.execute("""
            INSERT INTO rounding_rules (nutrient, rule_type, decimal_places, round_to, zero_threshold, note)
            VALUES (?, ?, ?, ?, ?, ?)
        """, row)


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


def activate_qa_response(qa_id: int) -> bool:
    """Q&A 활성화 (삭제 복구)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE qa_responses SET is_active = 1, updated_at = ? WHERE id = ?
    """, (datetime.now(), qa_id))

    affected = cursor.rowcount
    conn.commit()
    conn.close()

    return affected > 0


def search_qa_by_keyword(keyword: str) -> list:
    """Q&A 키워드 검색"""
    conn = get_connection()
    cursor = conn.cursor()

    search_key = f"%{keyword}%"
    cursor.execute("""
        SELECT * FROM qa_responses
        WHERE is_active = 1 AND (question LIKE ? OR answer LIKE ? OR keywords LIKE ?)
        ORDER BY use_count DESC
    """, (search_key, search_key, search_key))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def get_qa_statistics() -> dict:
    """Q&A 통계 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    # 전체 Q&A 수
    cursor.execute("SELECT COUNT(*) as cnt FROM qa_responses WHERE is_active = 1")
    total_qa = cursor.fetchone()['cnt']

    # 삭제된 Q&A 수
    cursor.execute("SELECT COUNT(*) as cnt FROM qa_responses WHERE is_active = 0")
    deleted_qa = cursor.fetchone()['cnt']

    # 총 사용 횟수
    cursor.execute("SELECT SUM(use_count) as total FROM qa_responses WHERE is_active = 1")
    total_usage = cursor.fetchone()['total'] or 0

    # 미답변 질문 수
    cursor.execute("SELECT COUNT(*) as cnt FROM unanswered_questions WHERE is_resolved = 0")
    unanswered_count = cursor.fetchone()['cnt']

    # 해결된 미답변 수
    cursor.execute("SELECT COUNT(*) as cnt FROM unanswered_questions WHERE is_resolved = 1")
    resolved_count = cursor.fetchone()['cnt']

    # 가장 많이 사용된 Q&A (상위 3개)
    cursor.execute("""
        SELECT id, question, use_count FROM qa_responses
        WHERE is_active = 1 ORDER BY use_count DESC LIMIT 3
    """)
    top_qa = [dict(row) for row in cursor.fetchall()]

    conn.close()

    return {
        'total_qa': total_qa,
        'deleted_qa': deleted_qa,
        'total_usage': total_usage,
        'unanswered_count': unanswered_count,
        'resolved_count': resolved_count,
        'top_qa': top_qa
    }


def search_qa_response(search_text: str, min_score: int = 60) -> dict:
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


# ========== 1회 섭취참고량 관련 함수 ==========

def get_serving_food_groups() -> list:
    """모든 식품군 목록 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT food_group FROM serving_size_reference
        ORDER BY id
    """)

    results = [row['food_group'] for row in cursor.fetchall()]
    conn.close()

    return results


def get_serving_food_types(food_group: str) -> list:
    """식품군별 식품유형 목록 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT food_type FROM serving_size_reference
        WHERE food_group = ?
        ORDER BY id
    """, (food_group,))

    results = [row['food_type'] for row in cursor.fetchall()]
    conn.close()

    return results


def get_serving_subtypes(food_group: str, food_type: str) -> list:
    """식품유형별 세부유형 목록 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT food_subtype FROM serving_size_reference
        WHERE food_group = ? AND food_type = ? AND food_subtype IS NOT NULL
        ORDER BY id
    """, (food_group, food_type))

    results = [row['food_subtype'] for row in cursor.fetchall()]
    conn.close()

    return results


def get_serving_size(food_group: str, food_type: str, food_subtype: str = None) -> dict:
    """1회 섭취참고량 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    if food_subtype:
        cursor.execute("""
            SELECT * FROM serving_size_reference
            WHERE food_group = ? AND food_type = ? AND food_subtype = ?
        """, (food_group, food_type, food_subtype))
    else:
        cursor.execute("""
            SELECT * FROM serving_size_reference
            WHERE food_group = ? AND food_type = ? AND (food_subtype IS NULL OR food_subtype = '')
        """, (food_group, food_type))

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


def get_serving_size_by_type(food_type: str) -> dict:
    """식품유형명으로 1회 섭취참고량 조회 (단순 검색)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM serving_size_reference
        WHERE food_type = ?
        LIMIT 1
    """, (food_type,))

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


def search_serving_size(keyword: str) -> list:
    """키워드로 1회 섭취참고량 검색"""
    conn = get_connection()
    cursor = conn.cursor()

    search_term = f"%{keyword}%"
    cursor.execute("""
        SELECT * FROM serving_size_reference
        WHERE food_group LIKE ? OR food_type LIKE ? OR food_subtype LIKE ?
        ORDER BY food_group, food_type
    """, (search_term, search_term, search_term))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


# ========== 1일 영양성분 기준치 관련 함수 ==========

def get_daily_value(nutrient: str) -> dict:
    """특정 영양소의 1일 기준치 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM daily_value WHERE nutrient = ?
    """, (nutrient,))

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


def get_all_daily_values() -> list:
    """모든 1일 영양성분 기준치 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM daily_value ORDER BY display_order
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def search_daily_value(keyword: str) -> list:
    """키워드로 1일 기준치 검색"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM daily_value WHERE nutrient LIKE ?
        ORDER BY display_order
    """, (f"%{keyword}%",))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def calculate_percent_daily_value(nutrient: str, amount: float) -> float:
    """영양소 함량의 %기준치 계산"""
    dv = get_daily_value(nutrient)
    if not dv or dv['daily_value'] == 0:
        return None  # 기준치가 없는 경우

    percent = (amount / dv['daily_value']) * 100
    return round(percent, 1)


# ========== 영양강조표시 관련 함수 ==========

def get_nutrient_claim(nutrient: str, claim_type: str) -> dict:
    """특정 영양소의 강조표시 기준 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM nutrient_claims WHERE nutrient = ? AND claim_type = ?
    """, (nutrient, claim_type))

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


def get_all_claims_for_nutrient(nutrient: str) -> list:
    """특정 영양소의 모든 강조표시 기준 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM nutrient_claims WHERE nutrient = ?
    """, (nutrient,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def get_all_nutrient_claims() -> list:
    """모든 영양강조표시 기준 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM nutrient_claims ORDER BY nutrient, claim_type
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def check_nutrient_claim(nutrient: str, amount: float, per_100g: bool = True) -> list:
    """
    영양소 함량이 어떤 강조표시에 해당하는지 확인

    Args:
        nutrient: 영양소명
        amount: 함량 (100g당 또는 1회 섭취량당)
        per_100g: True면 100g 기준, False면 1회 섭취참고량 기준

    Returns:
        해당하는 강조표시 목록
    """
    claims = get_all_claims_for_nutrient(nutrient)
    if not claims:
        return []

    applicable = []
    for claim in claims:
        threshold = claim['threshold']
        claim_type = claim['claim_type']

        # 무/저 타입은 threshold 미만/이하
        if claim_type in ['무', '저', '무가당']:
            if amount < threshold:
                applicable.append(claim)
        # 함유/고 타입은 threshold 이상
        elif claim_type in ['함유', '고']:
            if amount >= threshold:
                applicable.append(claim)

    return applicable


# ========== 반올림 규칙 관련 함수 ==========

def get_rounding_rule(nutrient: str) -> dict:
    """특정 영양소의 반올림 규칙 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM rounding_rules WHERE nutrient = ?
    """, (nutrient,))

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else None


def get_all_rounding_rules() -> list:
    """모든 반올림 규칙 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM rounding_rules
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def apply_rounding_rule(nutrient: str, amount: float) -> str:
    """
    영양소 함량에 반올림 규칙 적용

    Args:
        nutrient: 영양소명
        amount: 원래 함량

    Returns:
        표시용 문자열 (예: "0", "15", "2.5")
    """
    rule = get_rounding_rule(nutrient)

    if not rule:
        # 규칙이 없으면 기본 반올림 (정수)
        return str(round(amount))

    zero_threshold = rule['zero_threshold']
    round_to = rule['round_to']
    decimal_places = rule['decimal_places']

    # 0 표시 기준 미만이면 0 반환
    if amount < zero_threshold:
        return "0"

    # 지방류 특수 규칙 (0.5g 이상 5g 미만은 0.5g 단위)
    if nutrient in ['지방', '포화지방', '트랜스지방']:
        if amount < 5:
            # 0.5g 단위로 반올림
            rounded = round(amount * 2) / 2
            return f"{rounded:.1f}"
        else:
            # 1g 단위로 반올림
            return str(round(amount))

    # 일반 반올림
    if round_to:
        # 특정 단위로 반올림 (예: 5 단위)
        rounded = round(amount / round_to) * round_to
        if decimal_places == 0:
            return str(int(rounded))
        else:
            return f"{rounded:.{decimal_places}f}"
    else:
        # 소수점 자리수에 맞춰 반올림
        if decimal_places == 0:
            return str(round(amount))
        else:
            return f"{round(amount, decimal_places):.{decimal_places}f}"


def get_display_value(nutrient: str, amount: float) -> dict:
    """
    영양소의 표시값 계산 (반올림 + %기준치)

    Returns:
        {
            'display': 표시값 문자열,
            'percent_dv': %기준치 (있는 경우),
            'rule_note': 적용된 규칙 설명
        }
    """
    display = apply_rounding_rule(nutrient, amount)
    percent_dv = calculate_percent_daily_value(nutrient, amount)
    rule = get_rounding_rule(nutrient)

    return {
        'display': display,
        'percent_dv': round(percent_dv) if percent_dv else None,
        'rule_note': rule['note'] if rule else None
    }


# 데이터베이스 초기화 실행
if __name__ == "__main__":
    init_database()
    print("데이터베이스 초기화 완료!")
