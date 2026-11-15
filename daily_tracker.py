# -*- coding: utf-8 -*-
"""
每日候选池跟踪表生成器（仪表盘版）
用法:
  python daily_tracker.py                # 用本地 kline_all.csv 生成看板(最近20个交易日)
  python daily_tracker.py --update       # 先从腾讯行情增量更新最近10个交易日数据, 再生成
输出:
  watchlist_tracker.csv  (Excel 可直接打开, 含D1~D5逐日涨幅)
  watchlist_tracker.html (仪表盘: 汇总卡片/红绿着色/逐日走势串/筛选搜索)
逻辑(全部来自前期回测结论):
  入选池: 当日跌幅<=-4% 且 20日内有涨停
  D1确认(放宽口径): 收盘站回D0最低价 + 阳线 + 站回MA5 + 量比>=1 -> 10日内大涨 74.1% (n=1702)
  强确认: + D1收盘位置>0.75 -> 86.0% (n=1041);  顶格: + D0急跌<=-6% -> 89.5% (n=476)
  排雷:   D1确认但当日涨幅<2%(温和反抽) -> 43.0%, 回避
"""
import pandas as pd, numpy as np, sys, json, os, datetime

STK = os.environ.get('STKBT', os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(STK, 'kline_all.csv')
CONFIRMED = ['顶格(89%档)', '强确认(86%档)', '确认(74%档)', '排雷回避(43%)']

def fetch_update():
    """增量抓取最近10个交易日日线, 合并进 kline_all.csv"""
    import urllib.request
    uni = pd.read_csv(os.path.join(STK, 'universe.csv'))
    codes = uni['code'].tolist()
    old = pd.read_csv(CSV)
    have = set(map(tuple, old[['code','date']].values))
    rows = []
    for i, code in enumerate(codes):
        for host in ('https://web.ifzq.gtimg.cn', 'https://proxy.finance.qq.com'):
            try:
                url = f'{host}/appstock/app/fqkline/get?param={code},day,,,10,'
                with urllib.request.urlopen(url, timeout=8) as r:
                    data = json.loads(r.read().decode())
                d = data['data'][code]
                k = d.get('qfqday') or d.get('day') or []
                break
            except Exception:
                k = None
        if k:
            for bar in k:
                dt = bar[0].replace('-', '')
                if (code, dt) not in have:
                    rows.append([code, bar[0], float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]), float(bar[5])])
        if i % 500 == 499:
            print('  fetched %d/%d' % (i+1, len(codes)))
    if rows:
        new = pd.DataFrame(rows, columns=['code','date','open','close','high','low','volume'])
        old = pd.concat([old, new]).drop_duplicates(['code','date']).sort_values(['code','date'])
        old.to_csv(CSV, index=False)
        print('新增 %d 条K线, 数据截止 %s' % (len(rows), old['date'].max()))
    else:
        print('无新增数据')

def build(days=20):
    df = pd.read_csv(CSV)
    df = df.sort_values(['code','date']).reset_index(drop=True)
    g = df.groupby('code', sort=False)
    df['prev_close'] = g['close'].shift(1)
    df['chg'] = df['close']/df['prev_close'] - 1
    df['lu'] = (df['chg'] >= 0.095).astype(int)
    df['_i'] = np.arange(len(df))
    df['_lu_i'] = np.where(df['lu']==1, df['_i'], np.nan)
    df['_last_lu_i'] = df.groupby('code')['_lu_i'].ffill()
    df['lu_ago'] = df['_i'] - df['_last_lu_i']
    df['ma5'] = g['close'].transform(lambda s: s.rolling(5, min_periods=3).mean())
    df['vol_ma5'] = g['volume'].transform(lambda s: s.shift(1).rolling(5, min_periods=3).mean())
    df['vrat'] = df['volume']/df['vol_ma5']
    df['dn'] = (df['chg'] <= -0.02).astype(int)
    df['dn_run'] = df.groupby('code', sort=False)['dn'].transform(lambda s: s.groupby((s != s.shift()).cumsum()).cumsum())
    df['cpos'] = (df['close']-df['low'])/(df['high']-df['low']).replace(0, np.nan)
    low9 = g['low'].transform(lambda s: s.rolling(9, min_periods=1).min())
    high9 = g['high'].transform(lambda s: s.rolling(9, min_periods=1).max())
    rsv = ((df['close']-low9)/(high9-low9).replace(0, np.nan)*100).fillna(50)
    df['K'] = rsv.groupby(df['code'], sort=False).transform(lambda s: s.ewm(alpha=1/3, adjust=False).mean())
    # 60日高低区间位置(0=最低,1=最高): 验证高位票有效性用
    low60 = g['low'].transform(lambda s: s.rolling(60, min_periods=20).min())
    high60 = g['high'].transform(lambda s: s.rolling(60, min_periods=20).max())
    df['pos60'] = (df['close']-low60)/(high60-low60).replace(0, np.nan)
    # 未来10日大涨(验证列)
    K = 10
    chg_mat = np.column_stack([g['chg'].shift(-k).reindex(df.index).values for k in range(1, K+1)])
    big_mat = chg_mat > 0.06
    df['big10'] = big_mat.any(axis=1)
    df['big_day'] = np.where(big_mat.any(axis=1), big_mat.argmax(axis=1)+1, np.nan)

    last_dates = sorted(df['date'].unique())[-days:]
    pool = df[(df['chg'] <= -0.04) & (df['lu_ago'].notna()) & (df['lu_ago'] <= 20) & df['date'].isin(last_dates)].copy()
    # D1~D5 逐日: 按 code+_i 对齐(D0后第k个交易日), 双键防跨代码泄漏
    for k in range(1, 6):
        nk = df.copy()
        nk['_i'] = nk['_i'] - k
        cols_k = ['_i', 'code', 'date', 'low', 'open', 'close', 'vrat', 'chg', 'cpos', 'ma5'] if k == 1 \
            else ['_i', 'code', 'date', 'chg']
        nk = nk[cols_k]
        names_k = ['_i', 'code', 'd%d_date' % k] + (
            ['d%d_low' % k, 'd%d_open' % k, 'd%d_close' % k, 'd%d_vrat' % k, 'd%d_chg' % k, 'd%d_cpos' % k, 'd%d_ma5' % k]
            if k == 1 else ['d%d_chg' % k])
        nk.columns = names_k
        pool = pool.merge(nk, on=['code', '_i'], how='left')

    nnl = pool['d1_close'] > pool['low']   # 放宽口径: D1收盘站回D0最低价上方
    yang = pool['d1_close'] > pool['d1_open']
    above = pool['d1_close'] > pool['d1_ma5']
    vok = pool['d1_vrat'] >= 1
    combo = nnl & yang & above & vok
    strong = combo & (pool['d1_cpos'] > 0.75)
    top = strong & (pool['chg'] <= -0.06)
    weak = combo & (pool['d1_chg'] < 0.02)
    d1_exist = pool['d1_date'].notna()
    pool['信号'] = np.select(
        [d1_exist & weak, d1_exist & top, d1_exist & strong, d1_exist & combo, d1_exist],
        ['排雷回避(43%)', '顶格(89%档)', '强确认(86%档)', '确认(74%档)', '部分确认,继续观察'],
        default='待确认(D1未到)')

    pool['状态'] = np.where(d1_exist, 'D1已走完', 'D1待走(关注)')
    # D1盘中最低 vs D0预警线: 盘中跑--update时d1_low=当日实时最低, 收盘后=全日最低
    vs_jl = ((pool['d1_low'] / pool['low'] - 1) * 100).round(1)
    bd = pool['big_day'].astype('Int64')
    is1 = (bd == 1).fillna(False)
    after = 'D1后第' + (bd - 1).astype('Int64').astype(str).replace('<NA>', '?') + '天≥6%'
    pool['验证_后续大涨'] = np.where(pool['big10'].fillna(False) == True,
        np.where(is1, '确认日D1当天≥6%', after), '未出现大涨')

    names = pd.read_csv(os.path.join(STK, 'universe.csv'))[['code','name']]
    pool = pool.merge(names, on='code', how='left')
    pool['优先分'] = ((pool['chg'] <= -0.06).astype(int) + (pool['chg'] <= -0.08).astype(int)
        + (pool['lu_ago'] <= 5).astype(int) + (pool['K'] > 75).astype(int))
    out = pd.DataFrame({
        'D0日期': pool['date'], '代码': pool['code'], '名称': pool['name'],
        '优先分': pool['优先分'],
        'D0收盘': pool['close'].round(2), 'D0最低价': pool['low'].round(2), 'D0跌幅%': (pool['chg']*100).round(1),
        '涨停距今天数': pool['lu_ago'].astype(int), 'D0量比': pool['vrat'].round(2),
        '连跌天数': pool['dn_run'].astype(int), 'D0_K值': pool['K'].round(0),
        '状态': pool['状态'], '信号': pool['信号'],
        'D1日期': pool['d1_date'], 'D1最低(现)': pool['d1_low'].round(2), 'vs预警线': vs_jl,
        '收盘站回D0低': np.where(d1_exist, np.where(nnl,'√','×'), ''),
        'D1阳线': np.where(d1_exist, np.where(yang,'√','×'), ''),
        'D1站回MA5': np.where(d1_exist, np.where(above,'√','×'), ''),
        'D1量比': pool['d1_vrat'].round(2), 'D1涨幅%': (pool['d1_chg']*100).round(1),
        'D1收盘位置': pool['d1_cpos'].round(2),
        'K位置60日': (pool['pos60']*100).round(0).astype('Int64').astype(str).replace('<NA>', '')+'%',
        'D2涨幅%': (pool['d2_chg']*100).round(1), 'D3涨幅%': (pool['d3_chg']*100).round(1),
        'D4涨幅%': (pool['d4_chg']*100).round(1), 'D5涨幅%': (pool['d5_chg']*100).round(1),
        '验证_后续大涨': pool['验证_后续大涨'],
    }).sort_values(['D0日期','信号','优先分'], ascending=[False, True, False])
    out.to_csv(os.path.join(STK, 'watchlist_tracker.csv'), index=False, encoding='utf-8-sig')
    print('候选 %d 条 (D0事件), 覆盖 %s ~ %s' % (len(out), out['D0日期'].min(), out['D0日期'].max()))
    print(out['信号'].value_counts().to_string())
    return out

def _wk(d):
    return ['周一','周二','周三','周四','周五','周六','周日'][datetime.date(*map(int, d.split('-'))).weekday()]

def _sig_color(sig):
    return {'顶格(89%档)':'#c0392b','强确认(86%档)':'#d35400','确认(74%档)':'#b8860b',
            '排雷回避(43%)':'#7f8c8d','待确认(D1未到)':'#1a5276'}.get(sig, '#5d6d7e')

def _chk(v):
    """确认项符号: 达标=绿点, 未达标=红叉, 未走=灰杠"""
    if v == '√': return '<span style="color:#1e8449;font-weight:700;">●</span>'
    if v == '×': return '<span style="color:#c0392b;font-weight:700;">✗</span>'
    return '<span style="color:#bdc3c7;">–</span>'

def _chg_cell(v, bold=False):
    """涨跌幅单元格: 红涨绿跌(A股习惯)"""
    if pd.isna(v) or v == '':
        return '<td style="padding:3px 7px;color:#bdc3c7;">·</td>'
    c = '#c0392b' if v > 0 else ('#1e8449' if v < 0 else '#7f8c8d')
    fw = 'font-weight:700;' if bold or abs(v) >= 6 else ''
    bg = 'background:#fdf0ef;' if v >= 6 else ('background:#eef7f0;' if v <= -6 else '')
    return '<td style="padding:3px 7px;color:%s;%s%s">%+.1f</td>' % (c, fw, bg, v)

def _path_chips(r):
    """确认后D1~D5逐日走势串"""
    chips = []
    for k in range(1, 6):
        v = r.get('D%d涨幅%%' % k)
        if pd.isna(v):
            chips.append('<span class="chip na">D%d</span>' % k)
        elif v >= 6:
            chips.append('<span class="chip big">D%d %+.1f</span>' % (k, v))
        elif v > 0:
            chips.append('<span class="chip up">D%d %+.1f</span>' % (k, v))
        elif v < 0:
            chips.append('<span class="chip dn">D%d %+.1f</span>' % (k, v))
        else:
            chips.append('<span class="chip flat">D%d 0.0</span>' % k)
    return ''.join(chips)

def _row(r, cols):
    tds = []
    for col in cols:
        v = None if col == '确认后逐日' else r[col]
        style = 'padding:3px 7px;white-space:nowrap;'
        if col == '信号':
            tds.append('<td style="%s"><b style="color:%s;">%s</b></td>' % (style, _sig_color(v), v))
        elif col == '优先分':
            tds.append('<td style="%s"><b style="color:#c0392b;">%d★</b></td>' % (style, v))
        elif col == 'D0最低价':
            tds.append('<td style="%s"><b style="color:#c0392b;">%s</b></td>' % (style, v))
        elif col == 'D1最低(现)':
            tds.append('<td style="%s">%s</td>' % (style, v if pd.notna(v) else '·'))
        elif col == 'vs预警线':
            if pd.isna(v):
                tds.append('<td style="%s"><span style="color:#bdc3c7;">–</span></td>' % style)
            elif v <= 0:
                tds.append('<td style="%s;background:#fdedec;"><b style="color:#c0392b;">破线</b></td>' % style)
            else:
                tds.append('<td style="%s"><b style="color:#1e8449;">+%s%%</b></td>' % (style, v))
        elif col in ('收盘站回D0低', 'D1阳线', 'D1站回MA5'):
            tds.append('<td style="%s">%s</td>' % (style, _chk(v)))
        elif col == 'D0跌幅%':
            tds.append(_chg_cell(v, bold=True))
        elif col in ('D1涨幅%', 'D2涨幅%', 'D3涨幅%', 'D4涨幅%', 'D5涨幅%'):
            tds.append(_chg_cell(v))
        elif col == 'D1收盘位置':
            ok = pd.notna(v) and v > 0.75
            tds.append('<td style="%s">%s</td>' % (style,
                '<b style="color:#1e8449;">%s ↑</b>' % v if ok else ('%s' % v if pd.notna(v) else '·')))
        elif col == '确认后逐日':
            tds.append('<td style="padding:3px 7px;white-space:nowrap;">%s</td>' % _path_chips(r))
        elif col == '验证_后续大涨':
            hit = '≥6%' in str(v)
            tds.append('<td style="%s">%s</td>' % (style,
                '<b style="color:#c0392b;">%s</b>' % v if hit else '<span style="color:#95a5a6;">%s</span>' % v))
        else:
            tds.append('<td style="%s">%s</td>' % (style, v))
    sig = r['信号']
    star = r['优先分']
    bg = ''
    if sig == '待确认(D1未到)' and star >= 4: bg = 'background:#fdebd0;'
    elif '排雷' in sig: bg = 'background:#f2f4f4;'
    return '<tr data-star="%d" data-sig="%s"%s>%s</tr>' % (star, sig, (' style="%s"' % bg if bg else ''), ''.join(tds))

def _table(cols, rows_df, bg='#fff'):
    thead = ''.join('<th>%s</th>' % c for c in cols)
    trs = ''.join(_row(r, cols) for _, r in rows_df.iterrows())
    return '<table style="background:%s;"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (bg, thead, trs)

def to_html(out):
    D0_COLS = ['D0日期','代码','名称','优先分','D0收盘','D0最低价','D0跌幅%','涨停距今天数','D0量比','D0_K值','信号']
    D1_COLS = ['D0日期','代码','名称','信号','D1日期','D1最低(现)','vs预警线','收盘站回D0低','D1阳线','D1站回MA5','D1量比','D1涨幅%','D1收盘位置','K位置60日']
    D2_COLS = ['D0日期','代码','名称','信号','D1日期','D1涨幅%','D1收盘位置','D2涨幅%','D3涨幅%','D4涨幅%','D5涨幅%','确认后逐日','验证_后续大涨']

    last = out['D0日期'].max()
    # ① 明日盯盘
    watch = out[(out['D0日期']==last) & (out['信号']=='待确认(D1未到)')].sort_values('优先分', ascending=False)
    watch_top = watch.head(30)
    n4 = int((watch['优先分']>=4).sum()); n3 = int((watch['优先分']==3).sum())
    # ② 今日信号: 最近两个D1日
    d1_dates = sorted(out.loc[out['状态']=='D1已走完', 'D1日期'].dropna().unique())
    sig_recent = out[(out['状态']=='D1已走完') & out['信号'].isin(CONFIRMED) & out['D1日期'].isin(d1_dates[-2:])]
    # ③ 复盘: 最近10个D0日的确认信号
    recent_d0 = sorted(out['D0日期'].unique())[-10:]
    review = out[(out['D0日期'].isin(recent_d0)) & out['信号'].isin(CONFIRMED)].sort_values(['D0日期','信号'], ascending=[False, True])
    # 顶部汇总卡
    conf2 = out[(out['状态']=='D1已走完') & out['信号'].isin(['顶格(89%档)','强确认(86%档)'])]
    hit2 = conf2['验证_后续大涨'].str.contains('≥6%').mean() * 100 if len(conf2) else 0
    confA = out[out['信号'].isin(CONFIRMED)]
    hitA = confA['验证_后续大涨'].str.contains('≥6%').mean() * 100 if len(confA) else 0

    def card(num, label, sub, color):
        return ('<div class="card" style="border-top:3px solid %s;">'
                '<div class="cnum" style="color:%s;">%s</div><div class="clabel">%s</div>'
                '<div class="csub">%s</div></div>') % (color, color, num, label, sub)

    cards = ''.join([
        card('%d' % len(watch), '明日盯盘(急跌入池)', '4★ %d 只 · 3★ %d 只' % (n4, n3), '#1a5276'),
        card('%d' % len(sig_recent), '近两个确认日信号', '顶格%d/强确认%d/确认%d/排雷%d' % (
            (sig_recent['信号']=='顶格(89%档)').sum(), (sig_recent['信号']=='强确认(86%档)').sum(),
            (sig_recent['信号']=='确认(74%档)').sum(), (sig_recent['信号']=='排雷回避(43%)').sum()), '#d35400'),
        card('%.0f%%' % hit2, '顶格+强确认 兑现率', '表内验证 n=%d' % len(conf2), '#c0392b'),
        card('%.0f%%' % hitA, '全部确认信号 兑现率', '表内验证 n=%d' % len(confA), '#b8860b'),
    ])

    html = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>急跌候选池·决策看板</title>
<style>
body{font-family:-apple-system,'PingFang SC',sans-serif;margin:16px 20px;color:#2c3e50;font-size:13px;background:#f4f6f7;}
h2{margin:6px 0 10px;} h3{margin:24px 0 8px;scroll-margin-top:56px;}
.nav{position:sticky;top:0;z-index:99;background:#2c3e50;border-radius:8px;padding:7px 12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0 6px;box-shadow:0 1px 4px rgba(0,0,0,.15);}
.nav b{color:#fff;font-size:13px;}
.zbtn{border:1px solid #5d6d7e;background:#34495e;border-radius:14px;padding:4px 14px;cursor:pointer;font-size:13px;color:#fff;}
.zbtn.on{background:#fff;color:#2c3e50;font-weight:600;border-color:#fff;}
.zbtn.hot{border-color:#e67e22;}
.zbtn.hot.on{background:#e67e22;color:#fff;border-color:#e67e22;}
.nav .tip{color:#aab7b8;font-size:12px;margin-left:auto;}
.zone{margin-bottom:8px;}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0 4px;}
.card{background:#fff;border-radius:8px;padding:10px 18px;box-shadow:0 1px 4px rgba(0,0,0,.08);min-width:170px;}
.cnum{font-size:26px;font-weight:800;line-height:1.2;}
.clabel{font-weight:600;margin-top:2px;}
.csub{color:#7f8c8d;font-size:12px;margin-top:2px;}
table{border-collapse:collapse;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:6px;}
td,th{border:1px solid #d5dbdb;text-align:center;}
th{padding:5px 8px;background:#2c3e50;color:#fff;white-space:nowrap;position:sticky;top:40px;}
tbody tr:hover{background:#ebf5fb !important;}
.note{background:#fef9e7;border:1px solid #f7dc6f;padding:10px 14px;border-radius:6px;margin:8px 0;line-height:1.8;max-width:1000px;}
.sec{color:#fff;padding:4px 10px;border-radius:4px;display:inline-block;}
details{margin:4px 0 14px;} summary{cursor:pointer;color:#2e86c1;font-size:13px;}
.chip{display:inline-block;border-radius:4px;padding:1px 5px;margin:0 2px;font-size:11px;font-weight:600;}
.chip.big{background:#c0392b;color:#fff;} .chip.up{background:#fadbd8;color:#922b21;}
.chip.dn{background:#d5f5e3;color:#186a3b;} .chip.flat{background:#eaeded;color:#566573;}
.chip.na{background:#f4f6f7;color:#bdc3c7;font-weight:400;}
.fbar{display:flex;gap:8px;align-items:center;margin:6px 0 12px;flex-wrap:wrap;}
button{border:1px solid #aab7b8;background:#fff;border-radius:15px;padding:4px 14px;cursor:pointer;font-size:13px;color:#2c3e50;}
button.on{background:#2c3e50;color:#fff;border-color:#2c3e50;}
input#q{border:1px solid #aab7b8;border-radius:15px;padding:5px 14px;font-size:13px;width:200px;outline:none;}
</style></head><body>
<h2>急跌候选池 · 决策看板 <small style="color:#7f8c8d;font-size:13px;">数据截止 __LAST__（__WK__）收盘后生成</small></h2>
<div class="cards">__CARDS__</div>
<div class="nav">
<b>导航:</b>
<button class="zbtn on" onclick="zone('all',this)">全部</button>
<button class="zbtn" onclick="zone('1',this)">① 明日盯盘</button>
<button class="zbtn hot" onclick="zone('2',this)">② 今日信号 ★重点</button>
<button class="zbtn" onclick="zone('3',this)">③ 复盘走势</button>
<button class="zbtn" onclick="zone('4',this)">④ 说明书·胜率速查</button>
<span class="tip">点一下只看该区 · 「全部」看全貌</span>
</div>
<div class="fbar">
<b style="color:#7f8c8d;">筛选:</b>
<button class="fbtn on" onclick="filt('all',this)">全部</button>
<button class="fbtn" onclick="filt('star',this)">3★以上</button>
<button class="fbtn" onclick="filt('顶格(89%档)',this)">顶格</button>
<button class="fbtn" onclick="filt('强确认(86%档)',this)">强确认</button>
<button class="fbtn" onclick="filt('确认(74%档)',this)">确认</button>
<button class="fbtn" onclick="filt('排雷',this)">排雷</button>
<input id="q" placeholder="搜代码 / 名称…" oninput="apply()">
<span id="cnt" style="color:#7f8c8d;"></span>
</div>

<section class="zone" data-z="1">
<h3 id="sec1"><span class="sec" style="background:#1a5276;">① 明日盯盘 __LAST__ 急跌入池 __NWATCH__ 只 → 重点前 __NTOP__</span></h3>
__WATCH__
<details><summary>其余 __NREST__ 只（按优先分已排好，展开查看）</summary>__WATCH_REST__</details>
</section>

<section class="zone" data-z="2">
<h3 id="sec2"><span class="sec" style="background:#b8860b;">② 今日信号：截至 __SIGDATE__ 的确认结果（__NSIG__ 只）—— D1信息在这看</span></h3>
__SIG__
</section>

<section class="zone" data-z="3">
<h3 id="sec3"><span class="sec" style="background:#7f8c8d;">③ 复盘：确认后逐日走势（最近10个急跌日）—— D2~D5信息在这看</span></h3>
__REVIEW__
</section>

<section class="zone" data-z="4">
<h3 id="sec4"><span class="sec" style="background:#5d6d7e;">④ 说明书 · 胜率速查 —— 怎么用、每档什么胜率，都在这</span></h3>
<div class="note">
<b>▎每天怎么用（3步，共约5分钟）</b><br>
<b>收盘后</b>：跑 <code>python daily_tracker.py --update</code> 刷新看板 → 看导航<b>①区</b>今天急跌入池了哪些新票（登记即可，不用操作）。<br>
<b>第二天盘中</b>：中午、14:30 各再跑一次 --update → 看<b>②区</b>「vs预警线」列盯破线（红底=盘中最低已触及D0最低，14:30后看收盘站不站得回来）。<br>
<b>第二天14:30后</b>：核对四项定夺——①收盘站回D0最低价上方（上午碰破不要紧，尾盘还在线下=放弃）②收阳 ③站回MA5 ④量比≥1 → 通过则在 <b>13:00-14:55 买入，次日10:30前卖出</b>。<br>
<b>▎信号档位胜率速查（10日内大涨≥6%）</b><br>
<table style="margin:6px 0;">
<thead><tr><th>档位</th><th>定义</th><th>胜率</th><th>动作</th></tr></thead>
<tbody>
<tr><td>顶格(89%档)</td><td>四项全中 + 收上沿&gt;0.75 + 深急跌≤-6%</td><td style="color:#c0392b;font-weight:700;">89.5%</td><td>重点买入</td></tr>
<tr><td>强确认(86%档)</td><td>四项全中 + 收上沿&gt;0.75</td><td style="color:#d35400;font-weight:700;">86.0%</td><td>买入</td></tr>
<tr><td>确认(74%档)</td><td>四项全中</td><td style="color:#b8860b;font-weight:700;">74.1%</td><td>轻仓或观望</td></tr>
<tr><td>排雷(43%)</td><td>确认但D1涨幅&lt;2%（温和反抽）</td><td style="color:#7f8c8d;">43.0%</td><td>回避</td></tr>
<tr><td>部分确认</td><td>四项没凑齐</td><td>—</td><td>留池观察，哪天凑齐算哪天</td></tr>
</tbody></table>
<b>▎有效期提醒</b>：涨停基因的加成集中在<b>涨停后10个交易日内</b>（10日窗口大涨率48.6% vs 无涨停23.5%），「涨停距今天数」&gt;10 的票自动失效。<br>
<b>▎「K位置60日」（②区）</b>：D0收盘处在最近60日高低区间的百分位。全历史验证：<b>胜率对位置不敏感</b>（确认组低位71.7% ~ 顶部75.6%，高位前半场78.0%/后半场73.3%）——但<b>风险不对称</b>：位置&gt;85%的高位票万一失败，10日内最大回撤中位 <b style="color:#c0392b;">-14.9%</b>（非高位仅-8.7%）。<b>高位票可参与但减半仓，破D1最低价立刻离场</b>。
<b>▎「确认后逐日」</b>（③区）：D1~D5每天涨幅色块 <span class="chip big">≥6%</span><span class="chip up">涨</span><span class="chip dn">跌</span><span class="chip na">未走</span>，一眼看出确认后哪天涨的。
</div>
</section>
<br><i style="color:#7f8c8d;">历史统计口径，含一字板等买不进情形，不构成个人投资建议。</i>
<script>
var cur='all';
function zone(z,btn){
  document.querySelectorAll('.zbtn').forEach(function(b){b.classList.remove('on')});btn.classList.add('on');
  document.querySelectorAll('.zone').forEach(function(s){s.style.display=(z=='all'||s.dataset.z==z)?'':'none';});
  if(z!='all'){window.scrollTo(0,0);}else{document.getElementById('sec1').scrollIntoView();}
}
function filt(s,btn){cur=s;document.querySelectorAll('.fbtn').forEach(function(b){b.classList.remove('on')});btn.classList.add('on');apply();}
function apply(){
  var q=document.getElementById('q').value.trim().toUpperCase();var n=0;
  document.querySelectorAll('tbody tr[data-star]').forEach(function(tr){
    var ok=(cur=='all')||(cur=='star'?+tr.dataset.star>=3:(cur=='排雷'?tr.dataset.sig.indexOf('排雷')>=0:tr.dataset.sig===cur));
    if(ok&&q)ok=tr.textContent.toUpperCase().indexOf(q)>=0;
    tr.style.display=ok?'':'none';if(ok)n++;
  });
  document.getElementById('cnt').textContent='显示 '+n+' 行';
}
apply();
</script>
</body></html>""" \
        .replace('__LAST__', last).replace('__WK__', _wk(last)) \
        .replace('__CARDS__', cards) \
        .replace('__NWATCH__', str(len(watch))).replace('__NTOP__', str(len(watch_top))) \
        .replace('__WATCH_REST__', _table(D0_COLS, watch.iloc[30:], '#fdfefe') if len(watch)>30 else '') \
        .replace('__WATCH__', _table(D0_COLS, watch_top, '#fdfefe')) \
        .replace('__NREST__', str(max(0, len(watch)-len(watch_top)))) \
        .replace('__SIGDATE__', (d1_dates[-1] if d1_dates else '-')) \
        .replace('__NSIG__', str(len(sig_recent))) \
        .replace('__SIG__', _table(D1_COLS, sig_recent, '#fdfefe') if len(sig_recent) else '<p style="color:#7f8c8d;">最近两个确认日无确认信号</p>') \
        .replace('__REVIEW__', _table(D2_COLS, review, '#fdfefe') if len(review) else '')
    open(os.path.join(STK, 'watchlist_tracker.html'), 'w').write(html)
    open(os.path.join(STK, 'index.html'), 'w').write(html)
    print('看板已生成 watchlist_tracker.html')

if __name__ == '__main__':
    if '--update' in sys.argv:
        fetch_update()
    out = build(days=20)
    to_html(out)
