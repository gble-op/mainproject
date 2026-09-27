# ------------------------------------------------------------
# 화면 시연용 DB 더미 데이터 만들기 (팀 DB 설계서 기준)
#
# 채우는 표 (전체)
#   region              지역
#   employee / manager  직원 명부와 관리자 계정
#   customer / car      회원과 등록 차량
#   underpass           지하차도 31곳 (학습 데이터의 실제 정보 사용)
#   underpass_camera    지하차도마다 카메라 3대 (CCTV / 접근 / 진입직전)
#   threshold           지하차도별 위험도 임계치
#   risk_score          최근 7일간 한 시간마다의 위험도 점수 (침수예측 모델로 실제 계산)
#   underpass_event     위험도 변화·차단·해제 기록
#   plate_log           번호판 인식 기록 (영상 테스트에서 실제로 인식한 번호 사용)
#   underpass_transit   지하차도 통행 기록 (진입-진출 짝)
#   alert_log           개인화 알림 발송 이력
#   display_log         전광판 조작 기록
#   notice (+첨부)      공지사항
#   faq                 자주 묻는 질문
#   inquiry             1:1 문의와 답변
#   email_verification  이메일 인증코드 발송 내역
#
# 설계서에 맞춘 점
#   - underpass_event 의 CHECK 조건에 맞춰
#     RISK_LEVEL_UP/DOWN 은 risk_level + risk_score_at_event 를 채우고
#     BLOCK/RELEASE 는 operator_id 를 채움
#   - car.car_num 은 UNIQUE 라 번호판 하나당 회원 한 명
#   - manager 는 employee 에 등록된 직원만 계정을 만들 수 있음
#   - 지역 '미배정'(id=1)은 시드 데이터라 그대로 두고 지역을 추가로 넣음
#
# ============================================================
# 2026-09-24 에 바꾼 것 (팀에 공유 필요)
# ============================================================
#
# [1] 위험도 점수를 0~100점으로 (회의 결정)
#     설계서 원본은 DECIMAL(4,3) 이라 0.000~9.999 까지만 들어가서,
#     72.4 같은 0~100점 값을 넣을 수 없었음. 그래서 아래 4개 칸의 자료형을
#     DECIMAL(5,1) 로 바꾼 뒤 더미를 넣었음.
#
#         ALTER TABLE risk_score        ALTER COLUMN risk_score          TYPE numeric(5,1);
#         ALTER TABLE threshold         ALTER COLUMN risk_score_min      TYPE numeric(5,1);
#         ALTER TABLE threshold_history ALTER COLUMN old_risk_score_min  TYPE numeric(5,1);
#         ALTER TABLE threshold_history ALTER COLUMN new_risk_score_min  TYPE numeric(5,1);
#         ALTER TABLE underpass_event   ALTER COLUMN risk_score_at_event TYPE numeric(5,1);
#
#     주의: 팀 공용 SQL 설계 파일(CREATE TABLE 스크립트)에는 아직 DECIMAL(4,3) 으로
#           되어 있음. 다른 팀원이 그 파일로 DB를 새로 만들면 0~100점이 들어가지 않으니
#           설계 파일도 DECIMAL(5,1) 로 고쳐야 함.
#
#     점수 계산식 (main.py 의 위험점수 함수와 같음)
#         안전 확률 x 0 + 위험 확률 x 50 + 침수 확률 x 100  ->  0~100점
#
# [2] 임계치를 40점 / 70점으로
#     위 변경에 맞춰 threshold 의 하한값을 안전 0 / 경고 40 / 위험 70 으로 넣었음.
#     (0~1 기준이었다면 0 / 0.4 / 0.7 에 해당)
#
# [3] 비밀번호를 BCrypt 해시로 저장
#     설계서의 password VARCHAR(255) 는 해시 저장을 전제한 길이로 보여 평문 대신 해시로 넣었음.
#     모든 계정의 비밀번호는 test1234! 이고, DB에는 $2b$12$... 형태로 들어감.
#     웹에서 로그인 검증도 BCrypt 로 해야 맞음 (스프링 시큐리티의 BCryptPasswordEncoder).
#     만약 웹 쪽이 평문이나 다른 방식으로 비교한다면 이 파일의 해시() 함수를 고쳐서 다시 넣으면 됨.
#
# [4] elevation 칸에는 해발고도를 넣었음
#     설계서 주석이 ' 이고도' 라서 해발고도(5~80m)로 넣었음.
#     침수예측 모델이 쓰는 '지하차도 높이'(3.6~4.8m)와는 다른 값이므로,
#     이 칸의 의미를 팀에서 확정해야 함. 모델 입력용 높이는 현재 DB에 저장되지 않음.
# ============================================================
#
# 사용법
#   python DB더미생성.py            비어 있는 표에만 넣음
#   python DB더미생성.py --다시      해당 표를 비우고 새로 넣음
# ------------------------------------------------------------

import csv
import random
import sys
from datetime import datetime, timedelta

import bcrypt
import joblib
import pandas as pd
import psycopg2

접속 = dict(host='localhost', dbname='postgres', user='postgres', password='1234')
다시하기 = '--다시' in sys.argv
rng = random.Random(42)

# 위험도 임계치 (0~100점 기준. 위 [1][2] 참고)
# 설계서 원본은 0~1 기준이었으나 회의 결정에 따라 0~100점으로 씀
경고선, 위험선 = 40.0, 70.0

모델 = joblib.load('침수예측_모델.pkl')
칸순서 = joblib.load('침수예측_모델_입력컬럼순서.pkl')


def 해시(비번):
    """비밀번호를 BCrypt 해시로 바꿈 (위 [3] 참고)
       평문을 그대로 저장하지 않고 $2b$12$... 형태로 저장함.
       웹 로그인도 BCrypt 로 비교해야 맞음."""
    return bcrypt.hashpw(비번.encode(), bcrypt.gensalt()).decode()


def 단계(점수):
    return '위험' if 점수 >= 위험선 else ('경고' if 점수 >= 경고선 else '안전')


conn = psycopg2.connect(**접속)
conn.autocommit = True
cur = conn.cursor()


def 줄수(표):
    cur.execute(f'select count(*) from {표}')
    return cur.fetchone()[0]


대상표 = ['email_verification', 'inquiry_attachment', 'inquiry', 'faq_attachment', 'faq',
          'notice_attachment', 'notice', 'display_log',
          'alert_log', 'underpass_transit', 'plate_log', 'underpass_event', 'risk_score',
          'threshold_history', 'threshold', 'underpass_camera', 'underpass',
          'car', 'customer', 'manager', 'employee']

if 다시하기:
    # 지하차도가 카메라·이벤트를 참조하고 있어서 먼저 끊어줌
    cur.execute('update underpass set entry_camera_id=null, exit_camera_id=null, current_block_event_id=null')
    cur.execute('truncate ' + ', '.join(대상표) + ' restart identity cascade')
    cur.execute("delete from region where name <> '미배정'")
    print('기존 더미를 비웠어요. (지역 미배정 행은 시드 데이터라 남겨둠)')
elif any(줄수(t) for t in 대상표):
    print('이미 데이터가 들어 있어요:')
    for t in 대상표:
        n = 줄수(t)
        if n:
            print(f'  {t}: {n}줄')
    print('\n새로 넣으려면 --다시 옵션을 붙여 실행해 주세요. 지금은 아무것도 바꾸지 않았어요.')
    sys.exit()

# ================= 1. 지역 =================
지역이름 = ['서울', '경기', '인천', '부산', '충남', '충북', '세종', '강원', '전남', '경북']
지역번호 = {}
for 이름 in 지역이름:
    cur.execute('select id from region where name=%s and is_deleted=false', (이름,))
    찾음 = cur.fetchone()
    if 찾음:
        지역번호[이름] = 찾음[0]
    else:
        cur.execute('insert into region (name) values (%s) returning id', (이름,))
        지역번호[이름] = cur.fetchone()[0]
print(f'지역 {len(지역번호)}곳')

# ================= 2. 직원 명부와 관리자 계정 =================
# 설계서: 회사가 employee 를 미리 등록해두고, 그 정보와 대조해 관리자 가입을 승인함
직원명부 = [('김민수', '1982-03-15'), ('이영희', '1985-07-22'), ('박철수', '1988-11-05'),
            ('정수진', '1990-01-30'), ('최동현', '1992-06-18'),
            ('한지훈', '1987-09-09'), ('오세라', '1991-04-12')]
직원번호 = []
for i, (이름, 생일) in enumerate(직원명부):
    cur.execute('''insert into employee (name, birth_date, phone, email)
                   values (%s, %s, %s, %s) returning id''',
                (이름, 생일, f'010-{1000 + i * 111:04d}-{5000 + i * 123:04d}',
                 f'staff{i + 1:02d}@flood.go.kr'))
    직원번호.append(cur.fetchone()[0])

비밀번호해시 = 해시('test1234!')
관리자번호 = []
# 첫 번째는 최고관리자 (담당 지역 없음), 나머지는 지역담당자
cur.execute('''insert into manager (employee_id, login_id, password, role, status, region_id, approved_at)
               values (%s, 'admin', %s, 'SUPER_ADMIN', 'ACTIVE', NULL, now()) returning id''',
            (직원번호[0], 비밀번호해시))
최고관리자 = cur.fetchone()[0]
관리자번호.append(최고관리자)
cur.execute('update manager set approved_by=%s where id=%s', (최고관리자, 최고관리자))

for i in range(1, 5):
    cur.execute('''insert into manager (employee_id, login_id, password, role, status, region_id,
                                        approved_by, approved_at)
                   values (%s, %s, %s, 'REGIONAL_ADMIN', 'ACTIVE', %s, %s, now()) returning id''',
                (직원번호[i], f'manager{i}', 비밀번호해시, 지역번호[지역이름[i - 1]], 최고관리자))
    관리자번호.append(cur.fetchone()[0])

# 승인 대기 중인 계정도 하나 (승인 화면 시연용)
cur.execute('''insert into manager (employee_id, login_id, password, role, status, region_id)
               values (%s, 'manager5', %s, 'REGIONAL_ADMIN', 'PENDING', %s)''',
            (직원번호[5], 비밀번호해시, 지역번호['충남']))
print(f'직원 {len(직원번호)}명 / 관리자 {len(관리자번호)}명 활성 + 승인대기 1명')
print('  로그인: admin, manager1~4  비밀번호: test1234!  (BCrypt 해시로 저장)')

# ================= 3. 지하차도와 카메라, 임계치 =================
목록 = (pd.read_excel('랜덤포레스트_학습용_더미_v3.xlsx', sheet_name='지하차도목록')
        .drop_duplicates('지하차도명'))

지역매칭 = {'서울': '서울', '경기_안산': '경기', '고양': '경기', '계양': '인천',
            '군부산_동래구': '부산', '부산_동구': '부산', '부산_사상구': '부산',
            '청주': '충북', '충북_청주': '충북', '세종시': '세종', '충남_아산': '충남',
            '충남_태안': '충남', '무안': '전남', '양양': '강원', '칠곡': '경북'}

지하차도들 = []      # (id, 이름, 지하차도높이, 길이, 접근카메라, 진입직전카메라)
for _, r in 목록.iterrows():
    지역 = 지역번호[지역매칭.get(r['지역'], '서울')]
    위도 = round(37.5 + rng.uniform(-1.5, 0.6), 7)
    경도 = round(127.0 + rng.uniform(-0.9, 1.9), 7)
    # elevation 은 해발고도 칸이라 지하차도 높이(4.0m)와 다름 (위 [4] 참고)
    # 모델이 쓰는 '지하차도 높이'는 DB에 저장할 칸이 없어서 여기 넣지 않음
    해발 = round(rng.uniform(5, 80), 2)
    cur.execute('''insert into underpass (name, region_id, road_address, latitude, longitude,
                                          elevation, length_m, flood_history_count)
                   values (%s, %s, %s, %s, %s, %s, %s, %s) returning id''',
                (r['지하차도명'], 지역, f"{r['지역']} {r['지하차도명']} 일원",
                 위도, 경도, 해발, float(r['길이_m']), rng.randint(0, 3)))
    지하차도id = cur.fetchone()[0]

    카메라 = {}
    for 역할, 거리 in [('CCTV', 15), ('APPROACH_LPR', 120), ('ROI_LPR', 5)]:
        cur.execute('''insert into underpass_camera (underpass_id, camera_role, stream_url,
                                                     distance_m, latitude, longitude)
                       values (%s, %s, %s, %s, %s, %s) returning id''',
                    (지하차도id, 역할, f'rtsp://cam.flood.go.kr/{지하차도id}/{역할.lower()}',
                     거리, 위도, 경도))
        카메라[역할] = cur.fetchone()[0]
    # 설계서: entry=입구 카메라, exit=출구 카메라
    cur.execute('update underpass set entry_camera_id=%s, exit_camera_id=%s where id=%s',
                (카메라['APPROACH_LPR'], 카메라['ROI_LPR'], 지하차도id))

    for 레벨, 하한 in [('안전', 0.0), ('경고', 경고선), ('위험', 위험선)]:
        cur.execute('''insert into threshold (underpass_id, level, risk_score_min, updated_by)
                       values (%s, %s, %s, %s) returning id''',
                    (지하차도id, 레벨, 하한, 최고관리자))
        임계치id = cur.fetchone()[0]
        # 설계서: 최초 생성분부터 이력에 남김 (변경 전 값은 NULL)
        cur.execute('''insert into threshold_history (threshold_id, old_risk_score_min,
                                                      new_risk_score_min, changed_by)
                       values (%s, NULL, %s, %s)''', (임계치id, 하한, 최고관리자))

    지하차도들.append((지하차도id, r['지하차도명'], float(r['높이_m']), int(r['길이_m']),
                      카메라['APPROACH_LPR'], 카메라['ROI_LPR']))
print(f'지하차도 {len(지하차도들)}곳 / 카메라 {len(지하차도들) * 3}대 / 임계치 {len(지하차도들) * 3}건')

# ================= 4. 회원과 등록 차량 =================
번호판들 = []
try:
    with open('차량별_인식결과.csv', encoding='utf-8-sig') as f:
        번호판들 = [r['확정번호'] for r in csv.DictReader(f) if r['확정번호']]
except FileNotFoundError:
    pass
if not 번호판들:
    번호판들 = [f'{rng.randint(10, 999)}{rng.choice("가나다라마")}{rng.randint(1000, 9999)}'
                for _ in range(45)]
번호판들 = list(dict.fromkeys(번호판들))      # car_num 이 UNIQUE 라 중복 제거

성 = ['김', '이', '박', '최', '정', '강', '조', '윤', '장', '임']
이름끝 = ['서준', '지우', '하윤', '도윤', '서연', '지호', '수아', '예준', '하은', '시윤']
차량들 = []      # (차id, 고객id, 번호)
for i, 번호 in enumerate(번호판들):
    cur.execute('''insert into customer (login_id, password, name, birth_date, email, zipcode,
                                         road_address, detail_address, phone, emergency)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id''',
                (f'user{i + 1:03d}', 비밀번호해시, rng.choice(성) + rng.choice(이름끝),
                 f'19{rng.randint(70, 99)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}',
                 f'user{i + 1:03d}@example.com', f'{rng.randint(10000, 99999)}',
                 '서울특별시 강남구 테헤란로 123', f'{rng.randint(101, 2505)}호',
                 f'010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}',
                 f'010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}'))
    고객id = cur.fetchone()[0]
    cur.execute('insert into car (customer_id, car_num) values (%s, %s) returning id',
                (고객id, 번호))
    차량들.append((cur.fetchone()[0], 고객id, 번호))
print(f'회원 {len(차량들)}명 / 등록 차량 {len(차량들)}대  (로그인 user001~, 비밀번호 test1234!)')

# ================= 5. 최근 7일간 위험도 점수와 이벤트 =================
기준 = datetime.now().replace(minute=0, second=0, microsecond=0)
시작 = 기준 - timedelta(days=7)
시간수 = 7 * 24


def 비만들기(지금비=None):
    """7일치 시간당 강수량. 호우 구간 두 번 + 약한 비 몇 번
       지금비 를 주면 마지막 몇 시간에 비가 이어지게 해서, 현재 상태가 경고/위험이 되도록 함"""
    비 = [0.0] * 시간수
    for _ in range(2):
        s = rng.randint(12, 시간수 - 20)
        for i in range(rng.randint(6, 14)):
            비[s + i] = round(max(0, rng.gauss(18, 12)), 1)
    for _ in range(rng.randint(3, 6)):
        s = rng.randint(0, 시간수 - 6)
        for i in range(rng.randint(2, 5)):
            비[s + i] = round(max(0, rng.gauss(2.5, 2)), 1)
    if 지금비:
        # 마지막 8시간 동안 비가 내리는 중 (화면에서 경고/위험을 보여주기 위함)
        for i in range(8):
            비[시간수 - 8 + i] = round(max(0, rng.gauss(지금비, 지금비 * 0.3)), 1)
    return 비


점수기록, 이벤트기록 = [], []
# 31곳 중 4곳은 지금 '위험', 6곳은 '경고' 상태가 되도록 마지막 시간대에 비를 내림
현재비 = {}
뒤섞기 = 지하차도들[:]
rng.shuffle(뒤섞기)
for 곳 in 뒤섞기[:4]:
    현재비[곳[0]] = 16.0          # 8시간 동안 시간당 16mm -> 6시간 누적 약 96mm
for 곳 in 뒤섞기[4:10]:
    현재비[곳[0]] = 7.5           # 8시간 동안 시간당 7.5mm -> 6시간 누적 약 45mm

for 지하차도id, 이름, 높이, 길이, _, _ in 지하차도들:
    비 = 비만들기(현재비.get(지하차도id))
    묶음 = []
    for t in range(시간수):
        창 = lambda n: round(sum(비[max(0, t - n + 1):t + 1]), 1)
        묶음.append({'강수량_mm': 비[t], '직전1시간_mm': 비[t - 1] if t else 0.0,
                     '최근3시간_누적_mm': 창(3), '최근6시간_누적_mm': 창(6),
                     '최근12시간_누적_mm': 창(12), '최근24시간_누적_mm': 창(24),
                     '높이_m': 높이, '길이_m': 길이})

    확률 = 모델.predict_proba(pd.DataFrame(묶음)[칸순서])
    앞단계, 마지막점수 = '안전', 0.0
    for t, p in enumerate(확률):
        # 안전 0점 / 위험 50점 / 침수 100점으로 두고 확률만큼 섞음 -> 0~100 (위 [1] 참고)
        # main.py 의 위험점수() 함수와 같은 계산식
        점수 = round(float(p[1] * 50 + p[2] * 100), 1)
        시각 = 시작 + timedelta(hours=t)
        점수기록.append((지하차도id, 점수, 묶음[t]['최근6시간_누적_mm'],
                         round(묶음[t]['최근6시간_누적_mm'] * rng.uniform(0.3, 0.9), 1), 시각))

        이번 = 단계(점수)
        if 이번 != 앞단계:
            순서 = ['안전', '경고', '위험']
            올라감 = 순서.index(이번) > 순서.index(앞단계)
            # CHECK 조건: UP/DOWN 은 risk_level 과 점수가 필수
            이벤트기록.append((지하차도id, 'RISK_LEVEL_UP' if 올라감 else 'RISK_LEVEL_DOWN',
                               이번, 점수, None, 시각))
            # CHECK 조건: BLOCK/RELEASE 는 조작한 관리자가 필수
            if 이번 == '위험':
                이벤트기록.append((지하차도id, 'BLOCK', None, None, 최고관리자,
                                   시각 + timedelta(minutes=2)))
            elif 앞단계 == '위험':
                이벤트기록.append((지하차도id, 'RELEASE', None, None, 최고관리자,
                                   시각 + timedelta(minutes=2)))
            앞단계 = 이번
        마지막점수 = 점수

    cur.execute('update underpass set current_risk_level=%s, current_status=%s where id=%s',
                (단계(마지막점수), 'BLOCKED' if 단계(마지막점수) == '위험' else 'NORMAL', 지하차도id))

cur.executemany('''insert into risk_score (underpass_id, risk_score, rainfall_recent_mm,
                                           rainfall_forecast_mm, predicted_at)
                   values (%s, %s, %s, %s, %s)''', 점수기록)
print(f'위험도 점수 {len(점수기록)}건 (지하차도 {len(지하차도들)}곳 x 7일 x 24시간)')

차단이벤트 = {}
for 지하차도id, 종류, 레벨, 점수, 조작자, 시각 in 이벤트기록:
    cur.execute('''insert into underpass_event (underpass_id, event_type, risk_level,
                                                risk_score_at_event, operator_id, created_at)
                   values (%s, %s, %s, %s, %s, %s) returning id''',
                (지하차도id, 종류, 레벨, 점수, 조작자, 시각))
    이벤트id = cur.fetchone()[0]
    if 종류 == 'BLOCK':
        차단이벤트[지하차도id] = 이벤트id
    elif 종류 == 'RELEASE':
        차단이벤트.pop(지하차도id, None)
print(f'위험도 변화·차단·해제 이벤트 {len(이벤트기록)}건')

# 지금 차단 중인 지하차도는 그 차단 이벤트를 현재 상태로 연결
for 지하차도id, 이벤트id in 차단이벤트.items():
    cur.execute("""update underpass set current_block_event_id=%s
                   where id=%s and current_status='BLOCKED'""", (이벤트id, 지하차도id))

# ================= 6. 번호판 인식 기록과 통행 기록 =================
인식건수 = 통행건수 = 0
통행기록 = []
for 지하차도id, 이름, _, _, 입구카메라, 출구카메라 in 지하차도들:
    for _ in range(rng.randint(10, 25)):
        차id, 고객id, 번호 = rng.choice(차량들)
        진입시각 = 시작 + timedelta(hours=rng.randint(0, 시간수 - 1), minutes=rng.randint(0, 59))

        제비 = rng.random()
        if 제비 < 0.08:          # 신뢰도 낮음 -> 관리자 검토 대기
            상태 = 'LOW_CONFIDENCE'
            읽은번호 = 번호[:-1] + rng.choice('0123456789')
            확신도 = round(rng.uniform(0.500, 0.799), 3)
            매칭차 = None
            검토 = 'PENDING'
        elif 제비 < 0.12:        # 인식 실패
            상태, 읽은번호, 확신도, 매칭차, 검토 = 'FAIL', '인식실패', None, None, 'PENDING'
        elif 제비 < 0.18:        # 인식은 됐지만 등록 차량이 아님
            상태 = 'UNMATCHED'
            읽은번호 = f'{rng.randint(10, 99)}{rng.choice("가나다라마")}{rng.randint(1000, 9999)}'
            확신도 = round(rng.uniform(0.900, 0.999), 3)
            매칭차 = None
            검토 = 'NONE'
        else:
            상태 = 'SUCCESS'
            읽은번호 = 번호
            확신도 = round(rng.uniform(0.880, 0.999), 3)
            매칭차 = 차id
            검토 = 'NONE'

        cur.execute('''insert into plate_log (camera_id, detected_num, confidence, recognition_status,
                                              car_id, review_status, review_deadline, detected_at)
                       values (%s, %s, %s, %s, %s, %s, %s, %s) returning id''',
                    (입구카메라, 읽은번호, 확신도, 상태, 매칭차, 검토,
                     진입시각 + timedelta(days=7) if 검토 == 'PENDING' else None, 진입시각))
        진입로그 = cur.fetchone()[0]
        인식건수 += 1

        제비2 = rng.random()
        if 제비2 < 0.85:         # 정상 통과
            진출시각 = 진입시각 + timedelta(seconds=rng.randint(20, 120))
            cur.execute('''insert into plate_log (camera_id, detected_num, confidence,
                                                  recognition_status, car_id, detected_at)
                           values (%s, %s, %s, %s, %s, %s) returning id''',
                        (출구카메라, 읽은번호, 확신도, 상태, 매칭차, 진출시각))
            진출로그 = cur.fetchone()[0]
            인식건수 += 1
            통행기록.append((지하차도id, 읽은번호, 진입로그, 진출로그, 'EXITED', 진입시각, 진출시각))
        elif 제비2 < 0.95:       # 아직 통과 중
            통행기록.append((지하차도id, 읽은번호, 진입로그, None, 'IN_TRANSIT', 진입시각, None))
        else:                    # 들어갔는데 안 나옴 (잔류 차량 - 경보 대상)
            통행기록.append((지하차도id, 읽은번호, 진입로그, None, 'REMAINING', 진입시각, None))

cur.executemany('''insert into underpass_transit (underpass_id, car_num, entry_plate_log_id,
                                                  exit_plate_log_id, status, entry_at, exit_at)
                   values (%s, %s, %s, %s, %s, %s, %s)''', 통행기록)
print(f'번호판 인식 {인식건수}건 / 통행 기록 {len(통행기록)}건')

# ================= 7. 알림 발송 이력 =================
# 위험도가 올라간 이벤트마다, 그 무렵 그 지하차도를 지난 회원에게 알림을 보낸 것으로 둠
cur.execute("""select id, underpass_id, created_at from underpass_event
               where event_type in ('RISK_LEVEL_UP','BLOCK') order by created_at""")
알림대상 = cur.fetchall()
알림기록 = []
for 이벤트id, 지하차도id, 시각 in rng.sample(알림대상, min(120, len(알림대상))):
    for _ in range(rng.randint(1, 4)):
        차id, 고객id, 번호 = rng.choice(차량들)
        알림기록.append((이벤트id, 고객id, 차id, 번호,
                         rng.choice(['PREDICTIVE', 'ROI_FINAL']),
                         시각 + timedelta(seconds=rng.randint(5, 90))))
cur.executemany('''insert into alert_log (event_id, customer_id, car_id, car_num, alert_type, sent_at)
                   values (%s, %s, %s, %s, %s, %s)''', 알림기록)
print(f'알림 발송 이력 {len(알림기록)}건')

# ================= 8. 전광판 조작 기록 =================
# 차단·해제 이벤트가 있었던 시각에 전광판도 같이 조작한 것으로 둠
cur.execute("""select underpass_id, event_type, operator_id, created_at from underpass_event
               where event_type in ('BLOCK','RELEASE') order by created_at""")
전광판기록 = []
for 지하차도id, 종류, 조작자, 시각 in cur.fetchall():
    메시지 = 'BLOCK_NOTICE' if 종류 == 'BLOCK' else 'RELEASE_NOTICE'
    전광판기록.append((지하차도id, 메시지, 조작자 or 최고관리자, 시각 + timedelta(seconds=30)))

# 위험도가 올라갔을 때 경고 문구를 띄운 기록도 일부 넣음
cur.execute("""select underpass_id, created_at from underpass_event
               where event_type='RISK_LEVEL_UP' and risk_level='경고' order by created_at""")
경고이벤트 = cur.fetchall()
for 지하차도id, 시각 in rng.sample(경고이벤트, min(60, len(경고이벤트))):
    전광판기록.append((지하차도id, 'RISK_WARNING', rng.choice(관리자번호),
                       시각 + timedelta(minutes=rng.randint(1, 5))))

cur.executemany('''insert into display_log (underpass_id, message_type, operator_id, created_at)
                   values (%s, %s, %s, %s)''', 전광판기록)
print(f'전광판 조작 기록 {len(전광판기록)}건')

# ================= 9. 공지사항 =================
공지목록 = [
    ('여름철 집중호우 대비 지하차도 통행 안전수칙 안내', True,
     '장마철을 맞아 지하차도 통행 시 주의사항을 안내드립니다.\n\n'
     '1. 지하차도 진입 전 반드시 전광판의 위험단계를 확인해 주세요.\n'
     '2. 통제 중인 지하차도는 우회도로를 이용해 주시기 바랍니다.\n'
     '3. 차량이 침수되기 시작하면 즉시 차량을 두고 높은 곳으로 대피하세요.\n'
     '4. 물이 차오른 지하차도는 겉보기보다 깊습니다. 절대 진입하지 마세요.'),
    ('침수 위험 알림 서비스 정식 오픈 안내', True,
     '차량을 등록하시면 지하차도 침수 위험 시 문자로 알림을 받으실 수 있습니다.\n\n'
     '마이페이지 > 차량 관리에서 번호판을 등록해 주세요.\n'
     '등록된 차량이 위험 구간에 접근하면 사전 알림이 발송됩니다.'),
    ('AI 침수 예측 시스템 도입 안내', False,
     '강수량 데이터를 분석해 지하차도별 침수 위험도를 0~100점으로 예측하는\n'
     '시스템을 도입했습니다. 예측 결과는 실시간으로 화면에 반영됩니다.'),
    ('시스템 정기 점검 안내 (매주 화요일 02:00~04:00)', False,
     '매주 화요일 새벽 2시부터 4시까지 정기 점검이 진행됩니다.\n'
     '점검 시간 중에는 알림 서비스가 일시 중단될 수 있습니다.'),
    ('번호판 인식 카메라 신규 설치 완료 (12개소)', False,
     '지하차도 12개소에 번호판 인식 카메라 설치를 완료했습니다.\n'
     '진입 차량을 자동으로 인식해 잔류 차량 여부를 확인할 수 있게 되었습니다.'),
    ('태풍 내습 대비 비상 대응 체계 가동', True,
     '태풍 북상에 따라 전 지하차도 비상 대응 체계를 가동합니다.\n'
     '위험단계가 상향될 경우 즉시 통제 조치가 이루어질 수 있습니다.'),
    ('지하차도 통제 기준 변경 안내', False,
     '위험도 점수 70점 이상 시 통제하던 기준을 지하차도 특성에 따라\n'
     '개별 조정할 수 있도록 변경했습니다.'),
    ('개인정보 처리방침 개정 안내', False,
     '알림 서비스 제공을 위한 차량번호 수집·이용 항목이 추가되었습니다.\n'
     '자세한 내용은 개인정보 처리방침 페이지를 확인해 주세요.'),
    ('모바일 웹 화면 개선 안내', False,
     '휴대폰에서도 지하차도 위험 현황을 편하게 볼 수 있도록 화면을 개선했습니다.'),
    ('겨울철 결빙 구간 주의 안내', False,
     '기온이 낮아지면서 지하차도 진출입 구간에 결빙이 발생할 수 있습니다.\n'
     '감속 운행해 주시기 바랍니다.'),
]
공지번호 = []
for i, (제목, 중요, 내용) in enumerate(공지목록):
    작성일 = 기준 - timedelta(days=rng.randint(1, 120), hours=rng.randint(0, 23))
    cur.execute('''insert into notice (title, content, is_important, author_id, created_at, updated_at)
                   values (%s, %s, %s, %s, %s, %s) returning id''',
                (제목, 내용, 중요, rng.choice(관리자번호), 작성일, 작성일))
    공지번호.append(cur.fetchone()[0])

# 일부 공지에는 첨부파일
첨부 = []
for 번호 in rng.sample(공지번호, 4):
    이름 = rng.choice(['지하차도_안전수칙.pdf', '통제기준_변경안내.pdf',
                       '점검일정표.xlsx', '카메라_설치현황.pdf'])
    첨부.append((번호, f'/files/notice/{번호}/{이름}', 이름))
cur.executemany('''insert into notice_attachment (notice_id, file_url, file_name)
                   values (%s, %s, %s)''', 첨부)
print(f'공지사항 {len(공지번호)}건 (첨부 {len(첨부)}건)')

# ================= 10. 자주 묻는 질문 =================
faq목록 = [
    ('침수 위험 알림은 어떻게 받나요?',
     '회원가입 후 마이페이지에서 차량 번호를 등록하시면 됩니다.\n'
     '등록된 차량이 위험 구간에 접근하거나, 해당 지역 지하차도의 위험단계가\n'
     '올라가면 등록하신 연락처로 알림이 발송됩니다.'),
    ('위험단계는 어떤 기준으로 정해지나요?',
     'AI 모델이 최근 강수량과 지하차도 구조를 분석해 0~100점의 위험도 점수를 계산합니다.\n'
     '40점 미만은 안전, 40점 이상은 경고, 70점 이상은 위험으로 표시됩니다.\n'
     '지하차도별로 기준은 조금씩 다를 수 있습니다.'),
    ('차량을 여러 대 등록할 수 있나요?',
     '네, 가능합니다. 마이페이지 > 차량 관리에서 추가로 등록하실 수 있습니다.\n'
     '단, 하나의 번호판은 한 명의 회원에게만 등록됩니다.'),
    ('지하차도가 통제되면 어떻게 알 수 있나요?',
     '현장 전광판에 통제 안내가 표시되고, 홈페이지 실시간 현황에도 반영됩니다.\n'
     '차량을 등록하신 회원에게는 별도 알림이 발송됩니다.'),
    ('예측이 틀릴 수도 있나요?',
     '예측 모델의 정확도는 약 98%이지만 100%는 아닙니다.\n'
     '예측은 참고 자료이며, 실제 통제 여부는 관리자가 현장 상황을 확인해 판단합니다.\n'
     '위험해 보이는 지하차도는 예측 결과와 관계없이 진입하지 마세요.'),
    ('번호판이 잘못 인식되면 어떻게 되나요?',
     '인식 신뢰도가 낮은 경우 관리자가 직접 확인해 수정합니다.\n'
     '잘못된 알림을 받으셨다면 1:1 문의로 알려주시기 바랍니다.'),
    ('비밀번호를 잊어버렸어요.',
     '로그인 화면의 [비밀번호 찾기]를 눌러 가입 시 등록한 이메일을 입력하시면\n'
     '인증 코드가 발송됩니다. 인증 후 새 비밀번호를 설정하실 수 있습니다.'),
    ('회원 탈퇴는 어떻게 하나요?',
     '마이페이지 > 회원정보 > 회원 탈퇴에서 진행하실 수 있습니다.\n'
     '탈퇴 시 등록된 차량 정보와 알림 설정이 모두 해제됩니다.'),
    ('알림을 받고 싶지 않습니다.',
     '마이페이지에서 알림 수신 설정을 끄실 수 있습니다.\n'
     '다만 침수 위험 알림은 안전과 직결되므로 유지하시기를 권장합니다.'),
    ('어느 지역의 지하차도를 볼 수 있나요?',
     '현재 서울, 경기, 인천, 부산, 충남, 충북, 세종, 강원, 전남, 경북 지역의\n'
     '지하차도 31곳의 정보를 제공하고 있습니다. 대상 지역은 계속 확대할 예정입니다.'),
    ('전광판 문구는 누가 바꾸나요?',
     '지역 담당 관리자가 상황에 따라 직접 조작합니다.\n'
     '모든 조작 내역은 기록으로 남습니다.'),
    ('지하차도에 갇혔을 때는 어떻게 해야 하나요?',
     '차량을 즉시 포기하고 높은 곳으로 대피하세요.\n'
     '물이 차오르면 수압 때문에 차문이 열리지 않습니다. 창문을 깨고 탈출해야 합니다.\n'
     '119에 신고하고 주변 사람들에게도 알려주세요.'),
]
faq번호 = []
for 질문, 답변 in faq목록:
    작성일 = 기준 - timedelta(days=rng.randint(10, 150))
    cur.execute('''insert into faq (question, answer, author_id, created_at, updated_at)
                   values (%s, %s, %s, %s, %s) returning id''',
                (질문, 답변, rng.choice(관리자번호), 작성일, 작성일))
    faq번호.append(cur.fetchone()[0])
# 일부 FAQ 에 첨부파일
faq첨부 = []
for 번호 in rng.sample(faq번호, 3):
    이름 = rng.choice(['차량등록_방법.pdf', '위험단계_기준표.pdf', '침수시_행동요령.pdf'])
    faq첨부.append((번호, f'/files/faq/{번호}/{이름}', 이름))
cur.executemany('''insert into faq_attachment (faq_id, file_url, file_name)
                   values (%s, %s, %s)''', faq첨부)
print(f'자주 묻는 질문 {len(faq번호)}건 (첨부 {len(faq첨부)}건)')

# ================= 11. 1:1 문의와 답변 =================
# 설계서: parent_id 가 NULL 이면 원글(고객), 값이 있으면 답글(관리자). 답글은 원글당 1개
문의목록 = [
    ('알림이 오지 않습니다', '차량을 등록했는데 어제 비가 많이 왔을 때 알림을 못 받았습니다. 확인 부탁드립니다.',
     '확인 결과 등록하신 차량의 연락처 인증이 완료되지 않은 상태였습니다.\n'
     '마이페이지에서 연락처 인증을 완료해 주시면 정상적으로 발송됩니다.'),
    ('차량 번호가 잘못 등록되었어요', '번호판 뒷자리를 잘못 입력했습니다. 수정하려는데 안 됩니다.',
     '이미 등록된 번호는 직접 수정이 어렵습니다.\n기존 차량을 삭제하신 후 다시 등록해 주세요.'),
    ('우리 동네 지하차도도 추가해 주세요', '자주 다니는 지하차도가 목록에 없습니다. 추가 가능한가요?',
     '대상 지하차도는 관할 지자체와 협의해 순차적으로 확대하고 있습니다.\n'
     '요청하신 구간은 검토 목록에 반영하겠습니다.'),
    ('통제 중인데 화면에는 안전이라고 나옵니다', '오늘 오전에 현장은 통제 중이었는데 화면은 안전으로 표시됐습니다.',
     '현장 통제와 시스템 반영 사이에 시차가 있었던 것으로 확인됩니다.\n'
     '관리자 조작 시 즉시 반영되도록 조치했습니다. 불편을 드려 죄송합니다.'),
    ('알림 문자가 너무 자주 옵니다', '같은 지하차도에 대해 알림이 여러 번 왔습니다.',
     '위험단계가 오르내릴 때마다 발송되어 중복이 발생했습니다.\n'
     '동일 구간은 일정 시간 내 1회만 발송되도록 개선 중입니다.'),
    ('비밀번호 재설정 메일이 안 옵니다', '메일을 여러 번 요청했는데 오지 않습니다. 스팸함도 확인했습니다.',
     '가입하신 이메일 주소에 오타가 있는 것으로 확인됩니다.\n'
     '고객센터로 신분 확인 후 변경 도와드리겠습니다.'),
    ('앱은 없나요?', '휴대폰 앱으로도 쓸 수 있으면 좋겠습니다.',
     '현재는 모바일 웹으로 제공하고 있습니다. 앱 출시는 검토 중입니다.'),
    ('예측 점수의 의미가 궁금합니다', '위험도 점수 65점은 어느 정도 위험한 건가요?',
     '0~100점 중 40점 이상이 경고, 70점 이상이 위험입니다.\n'
     '65점은 경고 단계로, 진입 전 현장 상황을 확인하시는 것이 좋습니다.'),
    ('탈퇴하면 기록도 지워지나요?', '탈퇴 시 그동안의 통행 기록도 삭제되는지 궁금합니다.',
     '회원 정보와 차량 등록 정보는 삭제됩니다.\n'
     '다만 재난 대응 기록은 관련 법령에 따라 일정 기간 보관됩니다.'),
    ('전광판이 꺼져 있었습니다', '어제 저녁에 지나갔는데 전광판에 아무것도 안 나왔습니다.',
     '해당 구간 전광판 통신 장애로 확인되어 복구 조치했습니다. 알려주셔서 감사합니다.'),
    ('가족 차량도 등록할 수 있나요?', '제 명의가 아닌 배우자 차량도 등록 가능한가요?',
     '번호판 하나당 한 계정에만 등록 가능합니다.\n배우자분 명의로 가입 후 등록하시면 됩니다.'),
    ('회원가입이 안 됩니다', '인증 코드를 입력해도 계속 오류가 납니다.', None),
    ('지하차도 통제 기준을 알고 싶습니다', '어떤 기준으로 통제 결정이 내려지는지 궁금합니다.', None),
    ('알림 수신 시간을 조정하고 싶어요', '새벽에 오는 알림 때문에 잠을 설쳤습니다.', None),
    ('데이터는 얼마나 자주 갱신되나요?', '화면의 위험도 점수가 실시간인지 궁금합니다.', None),
]
문의수 = 답변수 = 첨부수 = 0
for 제목, 내용, 답변 in 문의목록:
    고객 = rng.choice(차량들)[1]
    작성일 = 기준 - timedelta(days=rng.randint(1, 60), hours=rng.randint(0, 23))
    상태 = 'ANSWERED' if 답변 else 'PENDING'
    cur.execute('''insert into inquiry (parent_id, customer_id, title, content, status, created_at)
                   values (NULL, %s, %s, %s, %s, %s) returning id''',
                (고객, 제목, 내용, 상태, 작성일))
    원글 = cur.fetchone()[0]
    문의수 += 1
    # 일부 문의에는 사진을 첨부한 것으로 둠
    if rng.random() < 0.35:
        이름 = rng.choice(['현장사진.jpg', '화면캡처.png', '오류메시지.png', '전광판_사진.jpg'])
        cur.execute('''insert into inquiry_attachment (inquiry_id, file_url, file_name)
                       values (%s, %s, %s)''', (원글, f'/files/inquiry/{원글}/{이름}', 이름))
        첨부수 += 1
    if 답변:
        cur.execute('''insert into inquiry (parent_id, author_manager_id, content, status, created_at)
                       values (%s, %s, %s, 'ANSWERED', %s)''',
                    (원글, rng.choice(관리자번호), 답변,
                     작성일 + timedelta(hours=rng.randint(2, 48))))
        답변수 += 1
print(f'1:1 문의 {문의수}건 (답변 완료 {답변수}건 / 대기 {문의수 - 답변수}건 / 첨부 {첨부수}건)')

# ================= 12. 이메일 인증 내역 =================
# 설계서 CHECK 조건
#   SIGNUP        -> email 만 있고 customer_id / manager_id 는 NULL (가입 전이라 계정이 없음)
#   PASSWORD_RESET-> customer_id 와 manager_id 중 정확히 하나만 값이 있어야 함
인증기록 = []
for i in range(30):      # 가입 인증
    시각 = 기준 - timedelta(days=rng.randint(1, 90), hours=rng.randint(0, 23))
    완료 = 시각 + timedelta(minutes=rng.randint(1, 4)) if rng.random() < 0.85 else None
    인증기록.append((None, None, f'signup{i + 1:03d}@example.com', 'SIGNUP',
                     f'{rng.randint(100000, 999999)}', 시각 + timedelta(minutes=5), 완료, 시각))
for _ in range(12):      # 회원 비밀번호 재설정
    고객 = rng.choice(차량들)[1]
    시각 = 기준 - timedelta(days=rng.randint(1, 60), hours=rng.randint(0, 23))
    완료 = 시각 + timedelta(minutes=rng.randint(1, 4)) if rng.random() < 0.8 else None
    인증기록.append((고객, None, None, 'PASSWORD_RESET',
                     f'{rng.randint(100000, 999999)}', 시각 + timedelta(minutes=5), 완료, 시각))
for _ in range(3):       # 관리자 비밀번호 재설정
    시각 = 기준 - timedelta(days=rng.randint(1, 60))
    인증기록.append((None, rng.choice(관리자번호), None, 'PASSWORD_RESET',
                     f'{rng.randint(100000, 999999)}', 시각 + timedelta(minutes=5),
                     시각 + timedelta(minutes=2), 시각))
cur.executemany('''insert into email_verification (customer_id, manager_id, email, purpose,
                                                   code, expires_at, verified_at, created_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)''', 인증기록)
print(f'이메일 인증 내역 {len(인증기록)}건')

cur.close()
conn.close()
print('\n더미 데이터 넣기 완료!')
