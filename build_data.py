#!/usr/bin/env python3
"""
從 price.csv / finance.csv 產生 data.js，給 index.html 使用。
用法：python3 build_data.py [--days 250]
  --days：保留最近幾個交易日的因子分布（網頁上「分組回溯期間」最多可選到這個天數）
不需要安裝任何套件。
"""
import csv, math, json, sys, os, argparse
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=250)
ap.add_argument("--sketch", type=int, default=101, help="每日每因子保留的分位點數")
args = ap.parse_args()

FACTORS = ["pe", "ret1", "ret3", "ret5", "vol5", "amount", "volume",
           "overnight", "sbl", "margin", "mktcap"]


def num(s):
    s = (s or "").strip().replace(",", "")
    if s in ("", "-", "--", "N/A"):
        return None
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except ValueError:
        return None


# ---------- 財報：單季 EPS ----------
eps = defaultdict(dict)          # code -> {yyyymm: eps}
industry = {}
with open(os.path.join(HERE, "finance.csv"), encoding="utf-8-sig", newline="") as f:
    r = csv.reader(f)
    next(r)
    for row in r:
        code, ym = row[0].strip(), row[2].strip()
        v = num(row[4])
        if v is None:
            v = num(row[3])
        if v is not None:
            eps[code][ym] = v
        industry[code] = row[9].strip()

# 法定公告期限：Q1→5/15、Q2→8/14、Q3→11/14、Q4→隔年3/31（歷史日期用，避免偷看未來）
def deadline(ym):
    y, m = int(ym[:4]), int(ym[4:])
    return {3: y * 10000 + 515, 6: y * 10000 + 814, 9: y * 10000 + 1114, 12: (y + 1) * 10000 + 331}[m]


def prev_q(ym):
    y, m = int(ym[:4]), int(ym[4:])
    m -= 3
    if m == 0:
        y, m = y - 1, 12
    return f"{y}{m:02d}"


def ttm(code, latest_ym):
    """以 latest_ym 為最新一季，往回加總四季；缺任何一季回傳 None"""
    q, s = latest_ym, 0.0
    for _ in range(4):
        v = eps.get(code, {}).get(q)
        if v is None:
            return None
        s += v
        q = prev_q(q)
    return s


all_q = sorted({q for d in eps.values() for q in d})

# ---------- 價格：先找出需要的日期範圍 ----------
price_path = os.path.join(HERE, "price.csv")
print("掃描日期中…", file=sys.stderr)
dates = set()
with open(price_path, encoding="utf-8-sig", newline="") as f:
    next(f)
    for line in f:
        p1 = line.find(",")
        p2 = line.find(",", p1 + 1)
        p3 = line.find(",", p2 + 1)
        dates.add(line[p2 + 1:p3])
dates = sorted(d for d in dates if d.isdigit())
keep_dates = dates[-args.days:]
start = dates[max(0, len(dates) - args.days - 10)]   # 多抓幾天算 3/5 日報酬與隔夜
print(f"交易日共 {len(dates)} 天，輸出 {keep_dates[0]} ~ {keep_dates[-1]}", file=sys.stderr)

series = defaultdict(list)  # code -> [(date, open, close, vol, amt, ret, mktcap, sbl, margin)]
names = {}
with open(price_path, encoding="utf-8-sig", newline="") as f:
    r = csv.reader(f)
    next(r)
    for row in r:
        d = row[2].strip()
        if d < start:
            continue
        code = row[0].strip()
        names[code] = row[1].strip()
        series[code].append((d, num(row[3]), num(row[6]), num(row[7]), num(row[8]),
                             num(row[9]), num(row[12]), num(row[17]), num(row[18])))

keep_set = set(keep_dates)
latest_date = keep_dates[-1]
daily = {d: {k: [] for k in FACTORS} for d in keep_dates}
latest_rows = []


def std(xs):
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


for code, rows in series.items():
    rows.sort()
    for i, (d, o, c, vol, amt, ret, mc, sbl, mg) in enumerate(rows):
        if d not in keep_set:
            continue
        rets = [x[5] for x in rows[max(0, i - 4):i + 1]]
        f = dict.fromkeys(FACTORS)
        f["ret1"] = ret
        if len(rets) >= 3 and None not in rets[-3:]:
            f["ret3"] = (math.prod(1 + x / 100 for x in rets[-3:]) - 1) * 100
        if len(rets) == 5 and None not in rets:
            f["ret5"] = (math.prod(1 + x / 100 for x in rets) - 1) * 100
            f["vol5"] = std(rets)
        f["amount"] = amt
        f["volume"] = vol
        if i > 0 and o and rows[i - 1][2]:
            f["overnight"] = (o / rows[i - 1][2] - 1) * 100
        f["sbl"] = sbl
        f["margin"] = mg if mg else None     # 0 = 無融資，視為缺值
        f["mktcap"] = mc
        # 本益比：價格 / 近四季 EPS 加總
        if d == latest_date:
            qs = [q for q in eps.get(code, {})]
            lq = max(qs) if qs else None
        else:
            di = int(d)
            lq = max((q for q in eps.get(code, {}) if deadline(q) <= di), default=None)
        t = ttm(code, lq) if lq else None
        if c and t and t > 0:
            f["pe"] = c / t
        if d == latest_date:
            if c is None or (vol or 0) == 0 and o is None:
                pass
            qhist = sorted(eps.get(code, {}).items())[-8:]
            latest_rows.append({
                "code": code, "name": names.get(code, ""), "ind": industry.get(code, ""),
                "close": c, "ttm": t, "lq": lq,
                "eps": [[q, v] for q, v in qhist],
                **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()},
            })
        for k in FACTORS:
            if f[k] is not None:
                daily[d][k].append(f[k])

# 每日每因子保留分位點（壓縮檔案大小，合併多日時用來近似整體分布）
S = args.sketch


def sketch(vals):
    if not vals:
        return []
    vals.sort()
    n = len(vals)
    out = []
    for j in range(S):
        pos = j * (n - 1) / (S - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        out.append(float(f"{vals[lo] + (vals[hi] - vals[lo]) * (pos - lo):.6g}"))
    return out


hist = {k: [] for k in FACTORS}
counts = {k: [] for k in FACTORS}
for d in keep_dates:
    for k in FACTORS:
        counts[k].append(len(daily[d][k]))
        hist[k].append(sketch(daily[d][k]))

data = {
    "latestDate": latest_date,
    "dates": keep_dates,
    "quarters": all_q,
    "hist": hist,
    "counts": counts,
    "stocks": latest_rows,
}
out = os.path.join(HERE, "data.js")
with open(out, "w", encoding="utf-8") as f:
    f.write("window.DATA=")
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    f.write(";")
print(f"完成：{out}（{os.path.getsize(out)/1e6:.1f} MB，{len(latest_rows)} 檔）", file=sys.stderr)
