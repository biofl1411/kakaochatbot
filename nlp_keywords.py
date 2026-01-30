"""
NLP 키워드/의도어/동의어 생성 및 관리
- 크롤링된 Q&A에서 키워드 추출
- 카테고리별 의도어/동의어 매핑
"""
import re
import logging
from models import get_all_board_mappings, update_board_keywords

logger = logging.getLogger(__name__)

# 불용어 (제외할 단어)
STOPWORDS = {
    '의', '를', '을', '이', '가', '은', '는', '에', '에서', '으로', '로', '와', '과',
    '및', '등', '수', '것', '때', '중', '후', '전', '내', '외', '상', '하', '대', '소',
    '있다', '없다', '하다', '되다', '이다', '않다', '있는', '없는', '하는', '되는',
    '그', '저', '이런', '저런', '어떤', '무슨', '어떻게', '왜', '언제', '어디',
    '합니다', '됩니다', '입니다', '습니다', '니다', '요', '네요', '세요', '해요',
    '통해', '통한', '대한', '관한', '위한', '따른', '따라', '인한', '인해',
}

# 카테고리별 의도어/동의어 매핑
CATEGORY_INTENTS = {
    "표시기준": {
        "intents": ["표시", "표기", "라벨", "라벨링", "기재", "작성"],
        "synonyms": {
            "글루텐": ["gluten", "글루텐프리", "글루텐free", "무글루텐"],
            "알레르기": ["알러지", "알러젠", "allergen", "알레르겐"],
            "원산지": ["원산국", "생산지", "수입국"],
            "영양강조": ["영양표시", "영양성분강조"],
            "고카페인": ["카페인", "caffeine"],
        }
    },
    "잔류농약_항생물질": {
        "intents": ["농약", "항생제", "잔류", "검출", "안전성"],
        "synonyms": {
            "잔류농약": ["농약잔류", "농약검사", "농산물안전성"],
            "항생물질": ["항생제", "동물용의약품", "항균제"],
            "허용기준": ["기준치", "잔류허용기준", "MRL"],
        }
    },
    "방사능": {
        "intents": ["방사능", "방사선", "세슘", "요오드", "핵"],
        "synonyms": {
            "세슘": ["Cs-137", "Cs-134", "cesium"],
            "방사능": ["방사선", "radioactive", "핵종"],
        }
    },
    "영양성분": {
        "intents": ["영양", "성분", "표시", "분석", "검사"],
        "synonyms": {
            "영양성분": ["영양소", "영양표시", "nutrition"],
            "나트륨": ["sodium", "소금", "염분"],
            "당류": ["당", "설탕", "sugar"],
            "열량": ["칼로리", "kcal", "에너지"],
            "지방": ["fat", "지질"],
            "단백질": ["protein"],
            "탄수화물": ["carbohydrate", "탄수"],
        }
    },
    "소비기한": {
        "intents": ["기한", "유통", "보관", "저장", "신선도", "설정", "실험"],
        "synonyms": {
            "소비기한": ["유통기한", "품질유지기한", "소비기간"],
            "설정실험": ["설정시험", "기한설정", "shelf life"],
            "늘리다": ["연장", "증가", "변경", "늘리고"],
            "줄이다": ["단축", "감소"],
        }
    },
    "알레르기": {
        "intents": ["알레르기", "알러지", "민감", "과민", "검사", "분석"],
        "synonyms": {
            "알레르기": ["알러지", "allergen", "알레르겐", "알러젠"],
            "ELISA": ["엘라이자", "효소면역"],
            "PCR": ["유전자분석", "DNA검사"],
            "땅콩": ["peanut", "견과류"],
            "우유": ["milk", "유제품", "유단백"],
            "계란": ["egg", "난", "달걀"],
            "밀": ["wheat", "글루텐", "소맥"],
            "대두": ["soy", "콩", "대두단백"],
        }
    },
    "이물": {
        "intents": ["이물", "이물질", "물질", "분석", "검출", "확인"],
        "synonyms": {
            "이물질": ["이물", "foreign material", "혼입물"],
            "FT-IR": ["적외선분광", "IR분석"],
            "XRF": ["형광X선", "원소분석"],
            "현미경": ["microscope", "광학현미경"],
        }
    },
    "비건_할랄_동물DNA": {
        "intents": ["비건", "할랄", "동물", "DNA", "채식", "육류"],
        "synonyms": {
            "비건": ["vegan", "채식", "식물성"],
            "할랄": ["halal", "이슬람", "무슬림"],
            "동물DNA": ["동물유래", "육류DNA", "동물성분"],
            "돼지": ["pork", "pig", "돈육"],
            "소": ["beef", "cow", "우육"],
        }
    },
    "축산": {
        "intents": ["축산", "축산물", "육류", "고기", "자가품질", "검사"],
        "synonyms": {
            "자가품질검사": ["자가검사", "품질검사", "정기검사"],
            "축산물": ["축산", "육가공", "육류"],
            "제조가공업": ["제조업", "가공업", "식품제조"],
            "즉석판매": ["즉판", "즉석"],
        }
    },
    "식품": {
        "intents": ["식품", "제조", "가공", "자가품질", "검사", "의뢰"],
        "synonyms": {
            "자가품질검사": ["자가검사", "품질검사", "정기검사"],
            "식품제조": ["식품가공", "제조가공"],
            "제조가공업": ["제조업", "가공업"],
            "즉석판매": ["즉판", "즉석"],
            "의뢰": ["접수", "신청", "요청", "하려면", "어떻게"],
        }
    },
}

# 공통 의도어 (모든 카테고리에 적용)
COMMON_INTENTS = {
    "의뢰": ["접수", "신청", "요청", "하려면", "어떻게", "방법", "절차"],
    "비용": ["가격", "요금", "금액", "얼마", "fee", "cost"],
    "기간": ["시간", "며칠", "얼마나", "소요", "걸리"],
    "서류": ["문서", "필요서류", "제출", "준비물"],
    "검체": ["시료", "샘플", "sample", "검사물"],
}


def extract_keywords_from_title(title: str) -> list:
    """title에서 키워드 추출"""
    if not title:
        return []

    # Q숫자. 제거
    title = re.sub(r'^Q\d+\.\s*', '', title)

    # 특수문자 제거 (한글, 영문, 숫자만 유지)
    title = re.sub(r'[^\w가-힣\s]', ' ', title)

    # 단어 분리
    words = title.split()

    # 불용어 제거 및 2글자 이상만
    keywords = [w for w in words if w not in STOPWORDS and len(w) >= 2]

    return keywords


def extract_keywords_from_content(content: str, max_keywords: int = 10) -> list:
    """content에서 주요 키워드 추출"""
    if not content:
        return []

    # 특수문자 제거
    content = re.sub(r'[^\w가-힣\s]', ' ', content)

    # 단어 분리
    words = content.split()

    # 단어 빈도 계산
    word_count = {}
    for w in words:
        if w not in STOPWORDS and len(w) >= 2:
            word_count[w] = word_count.get(w, 0) + 1

    # 빈도순 정렬
    sorted_words = sorted(word_count.items(), key=lambda x: x[1], reverse=True)

    # 상위 N개 반환
    return [w[0] for w in sorted_words[:max_keywords]]


def get_category_synonyms(category: str, keywords: list) -> list:
    """카테고리에 맞는 동의어 추출"""
    synonyms = []

    if category in CATEGORY_INTENTS:
        cat_synonyms = CATEGORY_INTENTS[category].get("synonyms", {})
        for keyword in keywords:
            for key, syn_list in cat_synonyms.items():
                if keyword == key or keyword in syn_list:
                    synonyms.extend(syn_list)
                    synonyms.append(key)

    # 공통 의도어에서도 동의어 추출
    for key, syn_list in COMMON_INTENTS.items():
        for keyword in keywords:
            if keyword == key or keyword in syn_list:
                synonyms.extend(syn_list)
                synonyms.append(key)

    return list(set(synonyms))


def get_category_intents(category: str) -> list:
    """카테고리별 의도어 반환"""
    intents = []

    if category in CATEGORY_INTENTS:
        intents = CATEGORY_INTENTS[category].get("intents", [])

    return intents


def generate_keywords_for_qa(qa: dict) -> dict:
    """단일 Q&A에 대한 키워드/의도어/동의어 생성"""
    title = qa.get("title", "")
    content = qa.get("content", "")
    category = qa.get("category", "")

    # 키워드 추출
    title_keywords = extract_keywords_from_title(title)
    content_keywords = extract_keywords_from_content(content, max_keywords=15)

    # 중복 제거하며 병합 (title 키워드 우선)
    all_keywords = title_keywords.copy()
    for kw in content_keywords:
        if kw not in all_keywords:
            all_keywords.append(kw)

    # 동의어 추출
    synonyms = get_category_synonyms(category, all_keywords)

    # 의도어 추출
    intents = get_category_intents(category)

    return {
        "keywords": ",".join(all_keywords[:20]),  # 최대 20개
        "synonyms": ",".join(synonyms[:15]),      # 최대 15개
        "intent": ",".join(intents[:10])          # 최대 10개
    }


def generate_all_keywords():
    """모든 Q&A에 대해 키워드 생성 및 DB 업데이트"""
    all_qa = get_all_board_mappings()

    print(f"총 {len(all_qa)}개 Q&A 키워드 생성 시작...")

    for i, qa in enumerate(all_qa):
        question_id = qa.get("question_id")
        category = qa.get("category")
        title = qa.get("title", "")[:30]

        # 키워드 생성
        result = generate_keywords_for_qa(qa)

        # DB 업데이트
        update_board_keywords(
            question_id=question_id,
            keywords=result["keywords"],
            synonyms=result["synonyms"],
            intent=result["intent"]
        )

        print(f"[{i+1}/{len(all_qa)}] {category}/{question_id}: {title}...")
        print(f"    키워드: {result['keywords'][:50]}...")

    print(f"\n완료: {len(all_qa)}개 Q&A 키워드 생성 완료")


def show_keywords_sample(category: str = None, limit: int = 5):
    """키워드 샘플 확인"""
    all_qa = get_all_board_mappings(category)

    for qa in all_qa[:limit]:
        print(f"\n{'='*60}")
        print(f"[{qa['category']}] {qa['title']}")
        print(f"  키워드: {qa.get('keywords', 'N/A')}")
        print(f"  동의어: {qa.get('synonyms', 'N/A')}")
        print(f"  의도어: {qa.get('intent', 'N/A')}")


def search_qa_by_query(query: str, top_n: int = 3, min_score: int = 1) -> list:
    """
    사용자 질문으로 관련 Q&A 검색

    Args:
        query: 사용자 질문
        top_n: 반환할 최대 결과 수
        min_score: 최소 매칭 점수

    Returns:
        [{"question_id", "category", "title", "content", "score", "matched_keywords"}, ...]
    """
    if not query or len(query.strip()) < 2:
        return []

    # 질문에서 키워드 추출
    query_keywords = extract_keywords_from_title(query)

    if not query_keywords:
        return []

    # 모든 Q&A 조회
    all_qa = get_all_board_mappings()

    results = []

    for qa in all_qa:
        score = 0
        matched = []

        # DB에 저장된 키워드, 동의어, 의도어
        qa_keywords = (qa.get("keywords") or "").lower().split(",")
        qa_synonyms = (qa.get("synonyms") or "").lower().split(",")
        qa_intents = (qa.get("intent") or "").lower().split(",")

        # title도 매칭 대상에 포함
        title_text = (qa.get("title") or "").lower()

        for qk in query_keywords:
            qk_lower = qk.lower()

            # 1. 키워드 매칭 (1점)
            for kw in qa_keywords:
                if qk_lower in kw or kw in qk_lower:
                    score += 1
                    matched.append(qk)
                    break

            # 2. 동의어 매칭 (1점)
            for syn in qa_synonyms:
                if syn and (qk_lower in syn or syn in qk_lower):
                    score += 1
                    matched.append(f"{qk}(동의어)")
                    break

            # 3. 의도어 매칭 (0.5점)
            for intent in qa_intents:
                if intent and (qk_lower in intent or intent in qk_lower):
                    score += 0.5
                    matched.append(f"{qk}(의도)")
                    break

            # 4. Title 직접 매칭 (2점 - 가중치)
            if qk_lower in title_text:
                score += 2
                matched.append(f"{qk}(제목)")

        if score >= min_score:
            results.append({
                "question_id": qa.get("question_id"),
                "category": qa.get("category"),
                "title": qa.get("title"),
                "content": qa.get("content"),
                "base_url": qa.get("base_url"),
                "score": score,
                "matched_keywords": list(set(matched))
            })

    # 점수순 정렬
    results.sort(key=lambda x: x["score"], reverse=True)

    return results[:top_n]


def test_search(query: str):
    """검색 테스트"""
    print(f"\n{'='*60}")
    print(f"질문: {query}")
    print(f"{'='*60}")

    results = search_qa_by_query(query, top_n=5)

    if not results:
        print("매칭된 결과가 없습니다.")
        return

    for i, r in enumerate(results, 1):
        print(f"\n[{i}] {r['category']} - {r['title'][:40]}...")
        print(f"    점수: {r['score']} | 매칭: {', '.join(r['matched_keywords'][:5])}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "generate":
        # 키워드 생성 실행
        generate_all_keywords()

        # 샘플 확인
        print("\n" + "="*60)
        print("키워드 생성 결과 샘플")
        show_keywords_sample(limit=3)
    else:
        # 검색 테스트
        test_queries = [
            "글루텐 검사 비용이 얼마예요?",
            "빵류의 영양성분검사와 자가품질검사를 접수하려면 어떻게 해야하나요?",
            "과자를 생산하고 있습니다. 소비기한을 6개월에서 1년으로 늘리고 싶어요",
            "알레르기 검사 방법",
            "방사능 기준이 뭐예요?",
        ]

        for q in test_queries:
            test_search(q)
