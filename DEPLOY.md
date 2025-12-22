# 카카오 챗봇 배포 가이드

---

## ⚠️ 중요: 코드 수정 전 필수 확인사항

### 작업 전 반드시 수행할 것

1. **app.py 전체 구조를 먼저 읽고 파악하세요**
2. **특히 다음 부분은 절대 삭제하지 마세요:**
   - `make_carousel_response()` 함수
   - `make_response_with_link()` 함수
   - `INSPECTION_MENU` 딕셔너리 (cards, submenus, responses)
   - 검사분야 카드 캐러셀 관련 모든 로직
   - `format_crawled_data()` 함수
   - `format_nutrition_component_data()` 함수
3. **기존 코드를 수정하기 전에 해당 부분의 역할을 설명해주세요**
4. **코드 삭제는 명시적으로 요청하지 않는 한 하지 마세요**

### 하드코딩 금지 원칙

- 챗봇 응답 내용은 **크롤링 데이터를 사용**해야 합니다
- 이미지나 스크린샷 내용을 보고 DB에 직접 넣지 마세요
- 크롤링이 안 되면 **크롤러를 수정**하세요
- `INSPECTION_MENU["responses"]`에 하드코딩된 응답이 있으면 제거하고 DB 조회로 대체하세요

### 포맷팅 원칙

- 크롤링된 데이터는 **가독성 있게 포맷팅**해야 합니다
- 이모지 헤더 사용: 📋, 🧪, ⚠️, 💡
- 섹션 간 줄바꿈 유지
- 불릿 포인트(•) 일관성 유지

---

## 1. 프로젝트 개요

바이오푸드랩(biofl.co.kr) 카카오톡 챗봇 - 식품 검사 안내 서비스

### 주요 기능
- 자가품질검사 (검사주기, 검사항목 조회)
- 영양성분검사 안내
- 소비기한설정 (가속실험/실측실험)
- 항생물질, 잔류농약, 방사능 검사 안내
- 비건, 할랄, 동물DNA, 알레르기, 글루텐Free 검사
- 이물질검사, 성적서, 온라인서비스 안내

---

## 2. 서버 환경

| 항목 | 값 |
|------|-----|
| 서버 경로 | `/home/biofl/kakaochatbot` |
| Python 버전 | 3.11+ |
| 가상환경 | `venv` |
| 포트 | 5000 (Flask) |
| Webhook 포트 | 9000 |
| 데이터베이스 | SQLite (`data/chatbot.db`) |

### 서비스 파일
```
/home/biofl/kakaochatbot/systemd/
├── kakaochatbot.service  # 메인 챗봇 서비스
└── webhook.service       # GitHub 자동 배포 웹훅
```

---

## 3. 개발 환경

### 파일 구조
```
kakaochatbot/
├── app.py              # 메인 Flask 앱 (챗봇 로직)
├── config.py           # 설정 파일 (URL, DB 경로 등)
├── crawler.py          # 웹 크롤러 (biofl.co.kr 데이터 수집)
├── models.py           # 데이터베이스 모델
├── vision_ocr.py       # Google Vision API (이미지 분석)
├── webhook.py          # GitHub 웹훅 서버
├── deploy.sh           # 배포 스크립트
├── venv/               # Python 가상환경
├── data/               # SQLite DB (gitignore)
├── logs/               # 로그 파일 (gitignore)
└── backups/            # 백업 파일 (gitignore)
```

### 주요 의존성
```
flask
flask-cors
selenium
beautifulsoup4
rapidfuzz
google-cloud-vision (선택사항)
requests
lxml
```

---

## 4. 챗봇 메뉴 구성

### 메인 메뉴 (카드 캐러셀)
```
┌─────────────────────────────────────────────────────────────┐
│ 카드1: 자가품질검사                                           │
│   └─ 식품 / 축산 / 검사주기알림                               │
│                                                             │
│ 카드2: 영양성분검사                                           │
│   └─ 검사종류 / 표시대상확인 / 1회제공량산표                   │
│                                                             │
│ 카드3: 소비기한설정 / 항생물질 / 잔류농약 / 방사능              │
│                                                             │
│ 카드4: 비건 / 할랄 / 동물DNA                                  │
│                                                             │
│ 카드5: 알레르기 / 글루텐Free / 이물질검사                      │
│                                                             │
│ 카드6: 성적서 / 온라인서비스 / 고객지원                        │
└─────────────────────────────────────────────────────────────┘
```

### 세부 메뉴 구조
```
자가품질검사
├── 식품 → 검사주기 / 검사항목 / 검사수수료
├── 축산 → 검사주기 / 검사항목 / 검사수수료
└── 검사주기알림 (링크 버튼)

영양성분검사
├── 검사종류 → 영양표시 종류 / 9대 영양성분 / 14대 영양성분
├── 표시대상확인
└── 1회제공량산표

소비기한설정
├── 가속실험 (3개월 이상 제품)
└── 실측실험 (3개월 이내 제품)

알레르기
├── RT-PCR
└── Elisa

글루텐Free
└── Free기준
```

---

## 5. 크롤링 대상 URL

| 카테고리 | 메뉴 | Popup ID |
|----------|------|----------|
| 검사항목 | 식품 | question_241 |
| 검사항목 | 축산 | question_243 |
| 영양성분검사 | 검사종류 | question_207 |
| 영양성분검사 | 9대영양성분 | question_193 |
| 영양성분검사 | 14대영양성분 | question_192 |
| 소비기한설정 | 가속/실측실험 | question_97 |
| 항생물질 | 검사종류 | question_90 |
| 잔류농약 | 검사종류 | question_85 |
| 방사능 | 검사안내 | question_39 |
| 알레르기 | RT-PCR/Elisa | question_151 |
| 글루텐Free | Free기준 | question_161 |
| 자가품질검사 | 검사주기알림 | question_198 |

---

## 6. 서버 배포 방법

### 방법 1: 수동 배포 (권장)

```bash
# 서버 SSH 접속 후
cd /home/biofl/kakaochatbot

# 최신 코드 가져오기
git fetch origin <브랜치명>
git reset --hard origin/<브랜치명>

# 서버 재시작
sudo fuser -k 5000/tcp
sleep 2
source venv/bin/activate
python3 app.py &
```

### 방법 2: 크롤링 포함 배포

```bash
cd /home/biofl/kakaochatbot

# 코드 업데이트
git fetch origin <브랜치명>
git reset --hard origin/<브랜치명>

# 서버 재시작
sudo fuser -k 5000/tcp
sleep 2
source venv/bin/activate
python3 app.py &

# 크롤링 실행 (데이터 갱신)
python3 -c "
from crawler import Crawler
crawler = Crawler()
crawler.crawl_all()
"
```

### 방법 3: 특정 카테고리만 크롤링

```bash
# 일반 검사 정보만 (항생물질, 알레르기, 글루텐Free 등)
python3 -c "
from crawler import Crawler
crawler = Crawler()
crawler.crawl_general_info()
"

# 영양성분검사만
python3 -c "
from crawler import Crawler
crawler = Crawler()
crawler.crawl_nutrition_info()
"
```

---

## 7. 자동 배포 (GitHub Webhook)

### 허용된 브랜치
`webhook.py`의 `ALLOWED_BRANCHES` 설정:
```python
ALLOWED_BRANCHES = ['main', 'master']
```

### 새 브랜치 추가 시
```python
ALLOWED_BRANCHES = ['main', 'master', 'claude/새브랜치명']
```

---

## 8. 로그 확인

```bash
# 챗봇 로그
tail -f /home/biofl/kakaochatbot/logs/chatbot.log

# 서버 실시간 로그
tail -f /home/biofl/kakaochatbot/logs/server.log
```

---

## 9. 문제 해결

### 포트 충돌
```bash
sudo fuser -k 5000/tcp
```

### 브랜치 충돌
```bash
git fetch origin <브랜치명>
git reset --hard origin/<브랜치명>
```

### 크롤링 데이터 초기화
```bash
rm data/chatbot.db
python3 -c "
from models import init_database
init_database()
"
```

---

## 10. 현재 작업 브랜치

```
claude/inspection-card-carousel-Qjngy
```

### 배포 명령어 (복사용)
```bash
cd /home/biofl/kakaochatbot && git fetch origin claude/inspection-card-carousel-Qjngy && git reset --hard origin/claude/inspection-card-carousel-Qjngy && sudo fuser -k 5000/tcp && sleep 2 && source venv/bin/activate && python3 app.py &
```

---

## 11. 핵심 파일 역할

| 파일 | 역할 | 수정 시 주의사항 |
|------|------|-----------------|
| `app.py` | 메인 챗봇 로직, 라우팅, 응답 생성 | INSPECTION_MENU, make_carousel_response 절대 삭제 금지 |
| `config.py` | URL 매핑, Popup ID, 섹션 필터 | 크롤링 대상 변경 시 수정 |
| `crawler.py` | 웹 크롤링, 텍스트 추출/포맷팅 | 포맷팅 함수 수정 시 가독성 유지 |
| `models.py` | DB 모델, CRUD 함수 | 스키마 변경 시 주의 |
| `vision_ocr.py` | 이미지 분석 (선택사항) | Google Vision API 키 필요 |

---

## 12. 자주 발생하는 문제

### 챗봇이 응답 안 함
1. 서버가 실행 중인지 확인: `pgrep -f "python3 app.py"`
2. 포트 확인: `sudo lsof -i :5000`
3. 로그 확인: `tail -f logs/chatbot.log`

### 크롤링 데이터가 안 나옴
1. DB에 데이터 있는지 확인:
   ```python
   from models import get_nutrition_info
   print(get_nutrition_info('카테고리', '메뉴'))
   ```
2. 크롤링 재실행:
   ```python
   from crawler import Crawler
   Crawler().crawl_general_info()
   ```

### 메뉴 상태가 저장 안 됨
- `user_data["검사분야_메뉴"]` 설정 확인
- `app.py`의 메뉴 상태 저장 로직 확인 (약 930번째 줄)

### 섹션 필터링 안 됨
- `config.py`의 `SECTION_FILTER` 확인
- `crawler.py`의 `_extract_section_text()` 함수 확인

---

## 13. 새 기능 추가 체크리스트

- [ ] `config.py`에 URL 및 Popup ID 추가
- [ ] `config.py`의 `SECTION_FILTER` 추가 (필요 시)
- [ ] `app.py`의 `INSPECTION_MENU["submenus"]`에 메뉴 추가
- [ ] `app.py`의 `menu_items_map`에 항목 추가
- [ ] `app.py`의 메뉴 상태 저장 로직 추가
- [ ] 크롤링 테스트
- [ ] 챗봇 응답 테스트

---

## 14. 연락처

- GitHub: https://github.com/biofl1411/kakaochatbot
- 웹사이트: https://www.biofl.co.kr
