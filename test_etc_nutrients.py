import json
from app import app

def test_etc_nutrient(client, category, nutrient, value, user_id, page2=False):
    p = {'userRequest': {'utterance': '영양성분검사', 'user': {'id': user_id}}}
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '영양표시 도우미'
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '일반(3세 이상)'
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '표시값 변환'
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = '기타'
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = category
    client.post('/chatbot', json=p)
    if page2:
        p['userRequest']['utterance'] = '더보기▶'
        client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = nutrient
    client.post('/chatbot', json=p)
    p['userRequest']['utterance'] = str(value)
    resp = client.post('/chatbot', json=p)
    data = json.loads(resp.data)
    return data['template']['outputs'][0]['simpleText']['text']

fat_acids = [("리놀레산", 5.2), ("알파-리놀렌산", 0.8), ("EPA와 DHA의 합", 0.5)]
vitamins_p1 = [("비타민A", 450), ("비타민D", 8.5), ("비타민E", 7.3), ("비타민K", 55), ("비타민C", 60), ("비타민B1", 0.8), ("비타민B2", 1.0)]
vitamins_p2 = [("나이아신", 10), ("비타민B6", 1.2), ("엽산", 250), ("비타민B12", 1.5), ("판토텐산", 3.5), ("바이오틴", 20)]
minerals_p1 = [("칼슘", 523.7), ("인", 450), ("칼륨", 2100), ("마그네슘", 200), ("철분", 8.5), ("아연", 6.0), ("구리", 0.5)]
minerals_p2 = [("망간", 2.0), ("요오드", 100), ("셀레늄", 40), ("몰리브덴", 25), ("크롬", 20)]

print("=" * 60)
print("일반(3세 이상) 기타 영양소 전체 검증")
print("=" * 60)

idx = 0
groups = [
    ("지방산류", fat_acids, False),
    ("비타민류", vitamins_p1, False),
    ("비타민류", vitamins_p2, True),
    ("무기질류", minerals_p1, False),
    ("무기질류", minerals_p2, True),
]

for category, nutrients, page2 in groups:
    page_label = " (더보기)" if page2 else ""
    print(f"\n### {category}{page_label} ###")
    for nutrient, value in nutrients:
        with app.test_client() as client:
            result = test_etc_nutrient(client, category, nutrient, value, f'test_gen_etc_{idx}', page2=page2)
            print(f"\n--- {nutrient} (입력: {value}) ---")
            print(result)
            if '오류' in result:
                print(f"*** ERROR: {nutrient} ***")
            idx += 1

print("\n\n=== DONE ===")
