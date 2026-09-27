# 지하차도 침수 예측용 더미 데이터 생성 v3 (팀원 v2 + 연속 이벤트·6시간 기준 합본)
import pandas as pd
import numpy as np

rng = np.random.default_rng(42)
src = '랜덤포레스트_학습용_실명더미_v2.xlsx'          # 같은 폴더에 두기
out = '랜덤포레스트_학습용_더미_v3.xlsx'

f = pd.read_excel(src, sheet_name='전체학습데이터')
up = pd.read_excel(src, sheet_name='지하차도목록')
real = f[f['데이터구분'] == '실제'].copy()

# ================= 1. 정답 규칙 =================
# 튼튼함 점수: 0(약함) ~ 1(튼튼)  ← v2와 동일
def sturdy(h, L):
    return 0.5 * (h - 3.5) / 1.5 + 0.5 * (400 - L) / 330

# 침수 기준 = 기본값 + 추가값 × 튼튼함
FLOOD = {'1h': (30, 25),     # 30~55mm  (v2와 동일) 짧고 센 비
         '3h': (40, 40),     # 40~80mm  (v2와 동일) 몇 시간 이어지는 비
         '6h': (60, 50)}     # 60~110mm (v3 추가)  길고 꾸준한 비
WARN_RATE = {'3h': 0.6, '6h': 0.6}   # 위험 = 침수 기준의 60%
WARN_1H = 15                          # 위험 1시간 기준은 실제 데이터와 같은 15mm로 고정

def thresholds(h, L):
    s = sturdy(h, L)
    fl = {w: a + b * s for w, (a, b) in FLOOD.items()}
    wr = {'1h': WARN_1H + 0 * s, '3h': fl['3h'] * WARN_RATE['3h'], '6h': fl['6h'] * WARN_RATE['6h']}
    return fl, wr

def rule(r1, r3, r6, h, L):
    fl, wr = thresholds(h, L)
    flood = (r1 >= fl['1h']) | (r3 >= fl['3h']) | (r6 >= fl['6h'])
    warn = (r1 >= wr['1h']) | (r3 >= wr['3h']) | (r6 >= wr['6h'])
    return np.where(flood, 2, np.where(warn, 1, 0))

up['튼튼함'] = sturdy(up['높이_m'], up['길이_m'])
up['그룹'] = pd.qcut(up['튼튼함'], 3, labels=['취약', '보통', '튼튼'])

# ================= 2. 비 만들기 =================
def gen_rain(kind, hours):
    r = np.zeros(hours)
    if kind == '맑음':
        for _ in range(rng.integers(0, 3)):
            s = rng.integers(0, hours - 3)
            r[s:s + rng.integers(1, 4)] = rng.uniform(0.1, 1.0)
    elif kind == '보통비':
        s = rng.integers(0, hours // 2); d = rng.integers(10, 30)
        r[s:s + d] = rng.uniform(0.5, 6.0, len(r[s:s + d]))
    elif kind == '강한비_안전':                      # 오래 많이 오지만 시간당 세기는 약함 (아래에서 안전하게 조정)
        s = rng.integers(0, 12); d = rng.integers(18, 36)
        r[s:s + d] = rng.uniform(3, 12, len(r[s:s + d]))
    elif kind == '장마':
        s = rng.integers(0, 24); d = rng.integers(24, 48)
        seg = r[s:s + d]
        seg[:] = np.clip(rng.uniform(4, 12) + rng.normal(0, 4, len(seg)), 0, None)
        for _ in range(rng.integers(1, 4)):
            p = rng.integers(0, len(seg) - 3); seg[p:p + 3] += rng.uniform(5, 20, 3)
        seg[rng.random(len(seg)) < 0.15] = 0
    elif kind == '짧은폭우':
        s = rng.integers(6, hours - 6)
        r[s - 4:s] = rng.uniform(0, 5, 4)
        d = rng.integers(1, 3); r[s:s + d] = rng.uniform(25, 95, d)
        r[s + d:s + d + 3] = rng.uniform(0, 8, 3)
    elif kind == '태풍':
        s = rng.integers(6, 24); d = rng.integers(10, 20)
        shape = np.sin(np.linspace(0.2, np.pi - 0.2, d))
        r[s:s + d] = np.clip(rng.uniform(20, 60) * shape + rng.normal(0, 5, d), 0, None)
        if rng.random() < 0.5:
            r[s + d // 2] = rng.uniform(60, 100)
    elif kind == '경계':
        s = rng.integers(6, hours - 12); d = rng.integers(3, 8)
        r[s:s + d] = rng.uniform(4, 22, d)
    return np.round(r, 1)

def cumulate(rain):
    s = pd.Series(np.concatenate([np.zeros(24), rain]))   # 이벤트 전 24시간은 비가 없었다고 가정
    c = {w: s.rolling(w, min_periods=1).sum().values[24:] for w in [3, 6, 12, 24]}
    c[1] = s.shift(1).fillna(0).values[24:]
    return c

# ================= 3. 이벤트 → 지하차도 6곳에 적용 =================
kinds = {'맑음': (4, range(1, 13)), '보통비': (5, range(3, 12)), '강한비_안전': (5, range(5, 11)),
         '장마': (6, [6, 7, 8]), '짧은폭우': (6, [6, 7, 8, 9]), '태풍': (4, [8, 9]), '경계': (6, range(5, 10))}

used = {n: [] for n in up['지하차도명']}
for n, d in real.groupby('지하차도명'):
    used[n] += [(t.normalize(), t.normalize() + pd.Timedelta(days=1)) for t in d['일시']]

rows = []
eid = 0
for kind, (cnt, months) in kinds.items():
    for _ in range(cnt):
        eid += 1
        days = int(rng.integers(2, 4)); hours = days * 24
        picks = [r for g in ['취약', '보통', '튼튼']
                 for _, r in up[up['그룹'] == g].sample(2, random_state=int(rng.integers(1e9))).iterrows()]
        rain = gen_rain(kind, hours)
        c = cumulate(rain)
        if kind == '강한비_안전':      # 고른 6곳 모두에서 위험 기준의 90% 아래가 되도록 비를 줄임
            ratio = min(min(0.9 * WARN_1H / max(rain.max(), 0.1),
                            0.9 * thresholds(p['높이_m'], p['길이_m'])[1]['3h'] / max(c[3].max(), 0.1),
                            0.9 * thresholds(p['높이_m'], p['길이_m'])[1]['6h'] / max(c[6].max(), 0.1)) for p in picks)
            rain = np.round(rain * min(ratio, 1), 1); c = cumulate(rain)
        for p in picks:                                   # 같은 비를 서로 다른 지하차도 6곳에 적용
            while True:
                start = pd.Timestamp(int(rng.integers(2015, 2026)), int(rng.choice(list(months))), int(rng.integers(1, 26)))
                end = start + pd.Timedelta(days=days)
                if all(end <= a or start >= b for a, b in used[p['지하차도명']]):
                    used[p['지하차도명']].append((start, end)); break
            t = pd.date_range(start, periods=hours, freq='h')
            lab = rule(rain, c[3], c[6], p['높이_m'], p['길이_m'])
            rows.append(pd.DataFrame({
                '지역': p['지역'], '지역코드': p['지역코드'], '지하차도명': p['지하차도명'], '지하차도코드': p['지하차도코드'],
                '데이터구분': '합성', '시나리오': kind, '하루그룹': f'합성_E{eid:02d}_{kind}_{p["지하차도명"]}',
                '일시': t, '연도': t.year, '월': t.month, '일': t.day, '시간': t.hour, '요일': (t.weekday + 1) % 7,
                '강수량_mm': rain, '직전1시간_mm': np.round(c[1], 1),
                '최근3시간_누적_mm': np.round(c[3], 1), '최근6시간_누적_mm': np.round(c[6], 1),
                '최근12시간_누적_mm': np.round(c[12], 1), '최근24시간_누적_mm': np.round(c[24], 1),
                '높이_m': p['높이_m'], '길이_m': p['길이_m'],
                '정답_침수여부': (lab == 2).astype(int), '정답_위험단계': lab}))
syn = pd.concat(rows, ignore_index=True).sort_values(['일시', '지하차도명']).reset_index(drop=True)

# ================= 4. 실제 데이터 정답 보완 =================
# 안전(0)인데 새 규칙으로 위험 이상이면 위험(1)으로만 올림. 침수(2)는 실제 사고 기록이라 그대로.
kr = rule(real['강수량_mm'].values, real['최근3시간_누적_mm'].values, real['최근6시간_누적_mm'].values,
          real['높이_m'].values, real['길이_m'].values)
upg = (real['정답_위험단계'].values == 0) & (kr >= 1)
real_new = real.copy()
real_new.loc[upg, '정답_위험단계'] = 1
real_new['정답_침수여부'] = (real_new['정답_위험단계'] == 2).astype(int)

full = pd.concat([real_new, syn], ignore_index=True)

# ================= 5. 저장 =================
rf_cols = ['강수량_mm', '직전1시간_mm', '최근3시간_누적_mm', '최근6시간_누적_mm', '최근12시간_누적_mm',
           '최근24시간_누적_mm', '높이_m', '길이_m', '정답_위험단계']
if __name__ == '__main__':
    with pd.ExcelWriter(out) as w:
        full.to_excel(w, sheet_name='전체학습데이터', index=False)
        full[rf_cols].to_excel(w, sheet_name='RF_최종학습데이터', index=False)
    print('완료:', out)
    print('실제', len(real_new), '행 / 합성', len(syn), '행 / 실제 안전→위험', int(upg.sum()), '행')
    print(pd.crosstab(syn['시나리오'], syn['정답_위험단계']))
