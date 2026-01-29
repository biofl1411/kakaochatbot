import json
from app import app

def test_etc_nutrient(client, category, nutrient, value, user_id, page2=False):
    """Test a 기타 nutrient through category selection"""
    p = {'userRequest': {'utterance': '영양성분검사', 'user': {'id': user_id}}}
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '영양표시 도우미'
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '영유아(만1~2세)'
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '표시값 변환'
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '기타'
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = category
    client.post('/chatbot', json=p)
    
    # If page 2, click 더보기▶
    if page2:
        p['userRequest']['utterance'] = '더보기▶'
        client.post('/chatbot', json=p)
    
    p['userRequest']['utterance'] = nutrient
    resp = client.post('/chatbot', json=p)
    data = json.loads(resp.data)
    select_text = data['template']['outputs'][0]['simpleText']['text']
    
    # Enter value
    p['userRequest']['utterance'] = str(value)
    resp = client.post('/chatbot', json=p)
    data = json.loads(resp.data)
    return data['template']['outputs'][0]['simpleText']['text']

# All 기타 nutrients with test values
# 지방산류
fat_acids = [
    ("리놀레산", 5.2),
    ("알파-리놀렌산", 0.8),
    ("EPA와 DHA의 합", 0.5),
]

# 비타민류 page 1
vitamins_p1 = [
    ("비타민A", 450),
    ("비타민D", 8.5),
    ("비타민E", 7.3),
    ("비타민K", 55),
    ("비타민C", 60),
    ("비타민B1", 0.8),
    ("비타민B2", 1.0),
]

# 비타민류 page 2
vitamins_p2 = [
    ("나이아신", 10),
    ("비타민B6", 1.2),
    ("엽산", 250),
    ("비타민B12", 1.5),
    ("판토텐산", 3.5),
    ("바이오틴", 20),
]

# 무기질류 page 1
minerals_p1 = [
    ("칼슘", 523.7),
    ("인", 450),
    ("칼륨", 2100),
    ("마그네슘", 200),
    ("철분", 8.5),
    ("아연", 6.0),
    ("구리", 0.5),
]

# 무기질류 page 2
minerals_p2 = [
    ("망간", 2.0),
    ("요오드", 100),
    ("셀레늄", 40),
    ("몰리브덴", 25),
    ("크롬", 20),
]

print("=" * 60)
print("영유아(만1~2세) 기타 영양소 전체 검증")
print("=" * 60)

idx = 0

print("\n### 지방산류 ###")
for nutrient, value in fat_acids:
    with app.test_client() as client:
        result = test_etc_nutrient(client, "지방산류", nutrient, value, f'test_infant_fat_{idx}')
        print(f"\n--- {nutrient} (입력: {value}) ---")
        print(result)
        if '오류' in result:
            print(f"*** ERROR: {nutrient} ***")
        idx += 1

print("\n### 비타민류 (페이지1) ###")
for nutrient, value in vitamins_p1:
    with app.test_client() as client:
        result = test_etc_nutrient(client, "비타민류", nutrient, value, f'test_infant_vit1_{idx}')
        print(f"\n--- {nutrient} (입력: {value}) ---")
        print(result)
        if '오류' in result:
            print(f"*** ERROR: {nutrient} ***")
        idx += 1

print("\n### 비타민류 (페이지2 - 더보기) ###")
for nutrient, value in vitamins_p2:
    with app.test_client() as client:
        result = test_etc_nutrient(client, "비타민류", nutrient, value, f'test_infant_vit2_{idx}', page2=True)
        print(f"\n--- {nutrient} (입력: {value}) ---")
        print(result)
        if '오류' in result:
            print(f"*** ERROR: {nutrient} ***")
        idx += 1

print("\n### 무기질류 (페이지1) ###")
for nutrient, value in minerals_p1:
    with app.test_client() as client:
        result = test_etc_nutrient(client, "무기질류", nutrient, value, f'test_infant_min1_{idx}')
        print(f"\n--- {nutrient} (입력: {value}) ---")
        print(result)
        if '오류' in result:
            print(f"*** ERROR: {nutrient} ***")
        idx += 1

print("\n### 무기질류 (페이지2 - 더보기) ###")
for nutrient, value in minerals_p2:
    with app.test_client() as client:
        result = test_etc_nutrient(client, "무기질류", nutrient, value, f'test_infant_min2_{idx}', page2=True)
        print(f"\n--- {nutrient} (입력: {value}) ---")
        print(result)
        if '오류' in result:
            print(f"*** ERROR: {nutrient} ***")
        idx += 1

print("\n\n=== DONE ===")
