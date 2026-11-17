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
    """增量抓取最近10个交易日日线, 合并进 kline_all.csv (16线程并发)"""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    uni = pd.read_csv(os.path.join(STK, 'universe.csv'))
    codes = uni['code'].tolist()
    old = pd.read_csv(CSV)
    have = set(map(tuple, old[['code','date']].values))
    rows = []

    def fetch_one(code):
        for host in ('https://web.ifzq.gtimg.cn', 'https://proxy.finance.qq.com'):
            try:
                url = f'{host}/appstock/app/fqkline/get?param={code},day,,,10,'
                with urllib.request.urlopen(url, timeout=6) as r:
                    data = json.loads(r.read().decode())
                d = data['data'][code]
                k = d.get('qfqday') or d.get('day') or []
                if k:
                    return k
            except Exception:
                continue
        return None

    from datetime import datetime
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    after_close = now.hour >= 15   # 15点前的盘中K线是半根, 只进today_live.csv不污染历史
    live_rows = []
    ok = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        for i, k in enumerate(ex.map(fetch_one, codes)):
            if k:
                ok += 1
                code = codes[i]
                for bar in k:
                    dt = bar[0].replace('-', '')
                    row = [code, bar[0], float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]), float(bar[5])]
                    if bar[0] == today and not after_close:
                        live_rows.append(row)
                    elif (code, dt) not in have:
                        rows.append(row)
            if i % 500 == 499:
                print('  fetched %d/%d (ok=%d)' % (i+1, len(codes), ok), flush=True)
    print('抓取成功 %d/%d 只' % (ok, len(codes)), flush=True)
    if rows:
        new = pd.DataFrame(rows, columns=['code','date','open','close','high','low','volume'])
        old = pd.concat([old, new]).drop_duplicates(['code','date']).sort_values(['code','date'])
        old.to_csv(CSV, index=False)
        print('新增 %d 条K线, 数据截止 %s' % (len(rows), old['date'].max()))
    else:
        print('无新增数据')
    if live_rows:
        pd.DataFrame(live_rows, columns=['code','date','open','close','high','low','volume']).to_csv(
            os.path.join(STK, 'today_live.csv'), index=False)
        print('盘中实时数据 %d 条 -> today_live.csv (仅渲染用, 不写入历史)' % len(live_rows))
    else:
        _lp = os.path.join(STK, 'today_live.csv')
        if after_close and os.path.exists(_lp):
            os.remove(_lp)

def build(days=20):
    df = pd.read_csv(CSV)
    # 叠加盘中实时半根K线(仅本次渲染, 不回写历史CSV)
    _live_p = os.path.join(STK, 'today_live.csv')
    if os.path.exists(_live_p):
        try:
            _live = pd.read_csv(_live_p)
            _live = _live[_live['date'] > df['date'].max()]
            if len(_live):
                df = pd.concat([df, _live], ignore_index=True)
                print('已叠加盘中实时数据 %d 条 (截至 %s)' % (len(_live), _live['date'].max()))
        except Exception:
            pass
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
    df['ma4_d0'] = g['close'].transform(lambda s: s.rolling(4, min_periods=4).mean())
    df['vol_base'] = g['volume'].transform(lambda s: s.rolling(5, min_periods=5).mean())
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

    # 滚动核对日: 每只票在df中的最新一行(含盘中实时半根K线), 未确认票自动核对到最新交易日
    _last = df.groupby('code', sort=False).tail(1)
    _last = _last[['_i', 'code', 'date', 'open', 'close', 'low', 'vrat', 'chg', 'cpos', 'ma5']].rename(columns={
        '_i': '_i_last', 'date': 'chk_date', 'open': 'chk_open', 'close': 'chk_close', 'low': 'chk_low',
        'vrat': 'chk_vrat', 'chg': 'chk_chg', 'cpos': 'chk_cpos', 'ma5': 'chk_ma5'})
    pool = pool.merge(_last, on='code', how='left')
    pool['chk_k'] = pool['_i_last'] - pool['_i']   # 0=最新行就是D0(核对日未到), 1=D1, 2=D2...

    nnl = pool['d1_close'] > pool['low']   # 放宽口径: D1收盘站回D0最低价上方
    yang = pool['d1_close'] > pool['d1_open']
    above = pool['d1_close'] > pool['d1_ma5']
    vok = pool['d1_vrat'] >= 1
    combo = nnl & yang & above & vok
    strong = combo & (pool['d1_cpos'] > 0.75)
    top = strong & (pool['chg'] <= -0.06)
    weak = combo & (pool['d1_chg'] < 0.02)
    d1_exist = pool['d1_date'].notna()
    # 滚动核对(核对日在D2~D5): 四项在最新交易日重新核对
    nnl_c = pool['chk_close'] > pool['low']
    yang_c = pool['chk_close'] > pool['chk_open']
    above_c = pool['chk_close'] > pool['chk_ma5']
    vok_c = pool['chk_vrat'] >= 1
    combo_c = nnl_c & yang_c & above_c & vok_c
    strong_c = combo_c & (pool['chk_cpos'] > 0.75)
    top_c = strong_c & (pool['chg'] <= -0.06)
    weak_c = combo_c & (pool['chk_chg'] < 0.02)
    late_ok = (pool['chk_k'] >= 2) & (pool['chk_k'] <= 5) & d1_exist & (~combo)
    pool['信号'] = np.select(
        [~d1_exist,
         d1_exist & weak, d1_exist & top, d1_exist & strong, d1_exist & combo,
         late_ok & weak_c, late_ok & top_c, late_ok & strong_c, late_ok & combo_c],
        ['待确认(D1未到)',
         '排雷回避(43%)', '顶格(89%档)', '强确认(86%档)', '确认(74%档)',
         '排雷回避(43%)', '顶格(89%档)', '强确认(86%档)', '确认(74%档)'],
        default='部分确认,继续观察')
    # 核对日: 已确认票冻结在确认日; 未确认票滚动到最新交易日
    use_d1 = d1_exist & combo
    _d1s = pool['d1_date'].fillna('').astype(str)
    _cks = pool['chk_date'].fillna('').astype(str)
    _ckn = pool['chk_k'].fillna(0).astype(int)
    pool['核对日期'] = np.where(use_d1, _d1s, np.where(_ckn >= 1, _cks, ''))
    pool['核对日'] = np.where(use_d1, 'D1·' + _d1s.str[5:],
                     np.where(_ckn >= 1, 'D' + _ckn.astype(str) + '·' + _cks.str[5:], ''))
    _chk_low = np.where(use_d1, pool['d1_low'], pool['chk_low'])
    _chk_vrat = np.where(use_d1, pool['d1_vrat'], pool['chk_vrat'])
    _chk_chg = np.where(use_d1, pool['d1_chg'], pool['chk_chg'])
    _chk_cpos = np.where(use_d1, pool['d1_cpos'], pool['chk_cpos'])
    _m_nnl = np.where(use_d1, np.where(nnl, '√', '×'), np.where(_ckn >= 1, np.where(nnl_c, '√', '×'), '–'))
    _m_yang = np.where(use_d1, np.where(yang, '√', '×'), np.where(_ckn >= 1, np.where(yang_c, '√', '×'), '–'))
    _m_above = np.where(use_d1, np.where(above, '√', '×'), np.where(_ckn >= 1, np.where(above_c, '√', '×'), '–'))
    _m_vok = np.where(use_d1, np.where(vok, '√', '×'), np.where(_ckn >= 1, np.where(vok_c, '√', '×'), '–'))

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
    # D0性质判别(全历史n=26608回测): 放量急跌+涨停新鲜=有承接(确认率15.5%), 缩量阴跌=无承接(确认率3.0%)
    pool['D0性质'] = np.select(
        [(pool['vrat'] >= 1.2) & (pool['lu_ago'] <= 2),
         (pool['vrat'] < 0.7)],
        ['偏洗盘', '偏出货'], default='中性')
    out = pd.DataFrame({
        'D0日期': pool['date'], '代码': pool['code'], '名称': pool['name'],
        '优先分': pool['优先分'],
        'D0收盘': pool['close'].round(2), 'D0最低价': pool['low'].round(2), 'D0跌幅%': (pool['chg']*100).round(1),
        '涨停距今天数': pool['lu_ago'].astype(int), 'D0量比': pool['vrat'].round(2),
        'D0性质': pool['D0性质'],
        '明日MA5参考': pool['ma4_d0'].round(2), '明日量比基准(手)': pool['vol_base'].round(0),
        '连跌天数': pool['dn_run'].astype(int), 'D0_K值': pool['K'].round(0),
        '状态': pool['状态'], '信号': pool['信号'],
        '核对日': pool['核对日'], '核对日期': pool['核对日期'],
        'D1日期': pool['d1_date'],
        '今日最低(现)': pd.Series(_chk_low, index=pool.index).round(2), 'vs预警线': ((pd.Series(_chk_low, index=pool.index) / pool['low'] - 1) * 100).round(1),
        '①站回D0低': _m_nnl,
        '②阳线': _m_yang,
        '③站回MA5': _m_above,
        '④量比≥1': _m_vok,
        '核对量比': pd.Series(_chk_vrat, index=pool.index).round(2), '核对日涨幅%': (pd.Series(_chk_chg, index=pool.index)*100).round(1),
        '核对日收盘位置': pd.Series(_chk_cpos, index=pool.index).round(2),
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

def _row(r, cols, live=False):
    tds = []
    for col in cols:
        v = None if col == '确认后逐日' else r[col]
        style = 'padding:3px 7px;white-space:nowrap;'
        if col == '信号':
            tds.append('<td style="%s"><b style="color:%s;">%s</b></td>' % (style, _sig_color(v), v))
        elif col == '优先分':
            tds.append('<td style="%s"><b style="color:#c0392b;">%d★</b></td>' % (style, v))
        elif col == 'D0性质':
            m = {'偏洗盘': ('#1e8449', '#eafaf1', '● 偏洗盘'),
                 '偏出货': ('#c0392b', '#fdedec', '▼ 偏出货')}
            if v in m:
                c, bgc, txt = m[v]
                tds.append('<td class="d0px" style="%s"><span style="color:%s;background:%s;border-radius:3px;padding:1px 5px;font-weight:700;font-size:11px;">%s</span></td>' % (style, c, bgc, txt))
            else:
                tds.append('<td class="d0px" style="%s"><span style="color:#7f8c8d;font-size:11px;">中性</span></td>' % style)
        elif col == 'D0最低价':
            tds.append('<td style="%s"><b style="color:#c0392b;">%s</b></td>' % (style, v))
        elif col == '今日最低(现)':
            tds.append('<td class="lv-low" style="%s">%s</td>' % (style, v if pd.notna(v) else '·'))
        elif col == 'vs预警线':
            if pd.isna(v):
                tds.append('<td class="lv-vs" style="%s"><span style="color:#bdc3c7;">–</span></td>' % style)
            elif v <= 0:
                tds.append('<td class="lv-vs" style="%s;background:#fdedec;"><b style="color:#c0392b;">破线</b></td>' % style)
            else:
                tds.append('<td class="lv-vs" style="%s"><b style="color:#1e8449;">+%s%%</b></td>' % (style, v))
        elif col in ('①站回D0低', '②阳线', '③站回MA5', '④量比≥1'):
            tds.append('<td style="%s">%s</td>' % (style, _chk(v)))
        elif col == '核对日':
            tds.append('<td style="%s"><b style="color:#1a5276;">%s</b></td>' % (style, v if v else '–'))
        elif col == 'D0跌幅%':
            tds.append(_chg_cell(v, bold=True))
        elif col in ('核对日涨幅%', 'D2涨幅%', 'D3涨幅%', 'D4涨幅%', 'D5涨幅%'):
            tds.append(_chg_cell(v))
        elif col == '核对日收盘位置':
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
    if live:
        tds.append('<td class="lq" style="padding:3px 7px;white-space:nowrap;color:#95a5a6;">…</td>')
    attrs = ''
    if live:
        attrs = ' data-code="%s"' % r['代码']
        base = r['D0最低价'] if 'D0最低价' in r.index else None
        if pd.notna(base):
            attrs += ' data-d0low="%.2f"' % base
        if live == 'd1':
            _cd = str(r.get('核对日期') or '')
            d1d = _cd.replace('-', '') if _cd else ''
            attrs += ' data-d1date="%s"' % d1d
        attrs += ' data-name="%s"' % str(r['名称']).replace('"', '')
    pxv = str(r['D0性质']) if 'D0性质' in r.index else ''
    return '<tr data-star="%d" data-sig="%s" data-px="%s"%s%s>%s</tr>' % (star, sig, pxv, attrs, (' style="%s"' % bg if bg else ''), ''.join(tds))

def _table(cols, rows_df, bg='#fff', live=False):
    thead = ''.join('<th>%s</th>' % c for c in cols)
    if live:
        thead += '<th style="background:#1e8449;">实时价</th>'
    trs = ''.join(_row(r, cols, live=live) for _, r in rows_df.iterrows())
    return '<div class="tw"><table style="background:%s;"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (bg, thead, trs)

def to_html(out):
    D0_COLS = ['D0日期','代码','名称','优先分','D0收盘','D0最低价','D0跌幅%','涨停距今天数','D0量比','D0性质','D0_K值','明日MA5参考','明日量比基准(手)','信号']
    D1_COLS = ['D0日期','代码','名称','信号','核对日','D0最低价','今日最低(现)','vs预警线','①站回D0低','②阳线','③站回MA5','④量比≥1','核对量比','核对日涨幅%','核对日收盘位置','K位置60日']
    D2_COLS = ['D0日期','代码','名称','信号','核对日','核对日涨幅%','核对日收盘位置','D2涨幅%','D3涨幅%','D4涨幅%','D5涨幅%','确认后逐日','验证_后续大涨']

    last = out['D0日期'].max()
    import time as _t
    _today = _t.strftime('%Y-%m-%d')
    _live_note = ' · <b style="color:#0e6251;">含今日盘中实时</b>' if (last == _today and _t.localtime().tm_hour < 15) else ''
    # ① 明日盯盘
    watch = out[(out['D0日期']==last) & (out['信号']=='待确认(D1未到)')].sort_values('优先分', ascending=False)
    watch_top = watch.head(30)
    n4 = int((watch['优先分']>=4).sum()); n3 = int((watch['优先分']==3).sum())
    # ② 今日信号: 最近两个核对日(D1或滚动D2~D5)的确认结果
    _cd_series = out.loc[out['信号'].isin(CONFIRMED), '核对日期'].replace('', np.nan)
    chk_dates = sorted(_cd_series.dropna().unique())
    sig_recent = out[out['信号'].isin(CONFIRMED) & out['核对日期'].isin(chk_dates[-2:])]
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

    html = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=5"><title>急跌候选池·决策看板</title>
<style>
body{font-family:-apple-system,'PingFang SC',sans-serif;margin:16px 20px;color:#2c3e50;font-size:13px;background:#f4f6f7;}
h2{margin:6px 0 10px;} h3{margin:24px 0 8px;scroll-margin-top:calc(var(--hd,76px) + 8px);}
.headbar{position:sticky;top:0;z-index:98;margin:8px 0 6px;}
.nav{background:#2c3e50;border-radius:8px;padding:7px 12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;box-shadow:0 1px 4px rgba(0,0,0,.15);margin-top:6px;}
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
th{padding:5px 8px;background:#2c3e50;color:#fff;white-space:nowrap;position:sticky;top:var(--hd,76px);}
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
.livebar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#0e6251;color:#fff;border-radius:8px;padding:6px 14px;font-size:13px;box-shadow:0 1px 5px rgba(0,0,0,.2);}
.livebar b{color:#fff;}
.livebar .dot{width:9px;height:9px;border-radius:50%;background:#2ecc71;display:inline-block;animation:pl 1.6s infinite;}
.livebar .dot.off{background:#f39c12;animation:none;}
@keyframes pl{0%{opacity:1}50%{opacity:.25}100%{opacity:1}}
.livebar button{border:1px solid #7fb3a8;background:#148f77;color:#fff;border-radius:12px;padding:2px 12px;font-size:12px;cursor:pointer;}
.livebar .lvnote{color:#a2d9ce;font-size:12px;margin-left:auto;}
.brk{background:#fdedec;color:#c0392b;font-size:11px;border-radius:3px;padding:0 4px;font-weight:700;}
.nowtag{display:inline-block;font-size:10px;color:#0e6251;background:#d1f2eb;border-radius:3px;padding:0 3px;font-weight:700;margin-left:2px;}
tr.brkrow{background:#fff2f0 !important;}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%;}
@media (min-width:821px){.tw{overflow-x:visible;}}
@media (max-width:820px){
  body{padding:8px 8px 40px;}
  h2{font-size:16px;line-height:1.4;}
  h2 small{display:block;font-size:11px;margin-top:2px;}
  h3{font-size:13px;margin:14px 0 6px;}
  .sec{padding:3px 7px;font-size:12px;}
  .headbar{position:static;}
  .livebar{font-size:12px;gap:6px;padding:6px 10px;}
  .livebar .lvnote{display:none;}
  .nav{font-size:12px;gap:6px;padding:6px 8px;}
  .nav .tip{display:none;}
  .zbtn{padding:4px 10px;font-size:12px;}
  .cards{gap:6px;}
  .card{min-width:0;flex:1 1 45%;padding:7px 10px;}
  .cnum{font-size:20px;} .clabel{font-size:12px;} .csub{font-size:10px;}
  .note{font-size:12px;line-height:1.65;padding:8px 10px;}
  .fbar{gap:5px;}
  .fbtn,button{font-size:12px;padding:3px 10px;}
  input#q{width:130px;font-size:12px;}
  table{margin-bottom:8px;}
  th,td{font-size:11px;padding:3px 5px;}
  th{position:static;}
  .chip{font-size:10px;padding:1px 4px;margin:0 1px;}
  .lq,.lv-low,.lv-vs{font-size:11px;}
}
</style></head><body>
<h2>急跌候选池 · 决策看板 <small style="color:#7f8c8d;font-size:13px;">数据截止 __LAST__（__WK__）__LIVENOTE__</small></h2>
<div class="cards">__CARDS__</div>
<div class="headbar" id="headbar">
<div class="livebar" id="livebar">
<span class="dot" id="lvdot"></span>
<b>实时行情</b>
<span id="lvmsg">启动中…</span>
<span style="color:#a2d9ce;">|</span>
<span>①区破预警线 <b id="lvbrk1" style="color:#ff7675;">–</b> 只</span>
<span style="color:#a2d9ce;">|</span>
<span>②区破线 <b id="lvbrk2" style="color:#ff7675;">–</b> 只</span>
<button id="lvbtn" onclick="lvToggle()">暂停</button>
<span class="lvnote">价格/涨跌幅/当日最低 = 腾讯实时 · 每20秒(休市120秒)</span>
</div>
<div class="nav">
<b>导航:</b>
<button class="zbtn on" onclick="zone('all',this)">全部</button>
<button class="zbtn" onclick="zone('1',this)">① 明日盯盘</button>
<button class="zbtn hot" onclick="zone('2',this)">② 今日信号 ★重点</button>
<button class="zbtn" onclick="zone('3',this)">③ 复盘走势</button>
<button class="zbtn" onclick="zone('4',this)">④ 说明书·胜率速查</button>
<span class="tip">点一下只看该区 · 「全部」看全貌</span>
</div>
</div>
<div class="fbar">
<b style="color:#7f8c8d;">筛选:</b>
<button class="fbtn on" onclick="filt('all',this)">全部</button>
<button class="fbtn" onclick="filt('star',this)">3★以上</button>
<button class="fbtn" onclick="filt('顶格(89%档)',this)">顶格</button>
<button class="fbtn" onclick="filt('强确认(86%档)',this)">强确认</button>
<button class="fbtn" onclick="filt('确认(74%档)',this)">确认</button>
<button class="fbtn" onclick="filt('排雷',this)">排雷</button>
<button class="fbtn" onclick="filt('px',this)">偏洗盘</button>
<input id="q" placeholder="搜代码 / 名称…" oninput="apply()">
<span id="cnt" style="color:#7f8c8d;"></span>
</div>

<section class="zone" data-z="1">
<h3 id="sec1"><span class="sec" style="background:#1a5276;">① 明日盯盘 __LAST__ 急跌入池 __NWATCH__ 只 → 重点前 __NTOP__</span></h3>
<div class="note" style="margin:0 0 8px;">
<b>明日(D1)四项核对基准，盘中 14:30 逐项比对：</b>① <b>预警线=D0最低价</b>（收盘须在其上方，盘中碰破等拉回）② <b>收阳</b>（收盘&gt;开盘，只能盘中看）③ <b>收盘 &gt; 「明日MA5参考」</b>（该值=近4日收盘均价，明日收盘价站上它即站回MA5）④ <b>成交量 ≥ 「明日量比基准(手)」</b>（该值=近5日均量，达到即量比≥1）。四项全中=确认，再看「核对日收盘位置」和「D0跌幅%」定档位。<br>
<b>滚动核对：</b>没确认的票不用只盯一天——看板每天自动拿<b>最新交易日</b>重新核对四项（②区「核对日」列显示核到第几天，如 D3·11-13），四项哪天全中、哪天就是确认日；最多盯到 D5，「涨停距今天数」&gt;10 自动失效。<br>
<b>「D0性质」怎么用：</b><span class="chip up">● 偏洗盘</span>放量+涨停新鲜，次日确认率15.5%，<b>重点盯</b>；<span class="chip dn">▼ 偏出货</span>缩量无承接，确认率仅3.0%，<b>直接跳过</b>；中性正常走流程。「筛选→偏洗盘」可一键只看重点。详见④说明书。</div>
__WATCH__
<details><summary>其余 __NREST__ 只（按优先分已排好，展开查看）</summary>__WATCH_REST__</details>
</section>

<section class="zone" data-z="2">
<h3 id="sec2"><span class="sec" style="background:#b8860b;">② 今日信号：最近两个核对日的确认结果（__NSIG__ 只）—— 四项逐项打勾在这看</span></h3>
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
<b>收盘后</b>：跑 <code>python daily_tracker.py --update</code> 刷新看板（<b>盘中跑也安全</b>：实时半根K线只用于显示，不会污染历史数据，收盘后正式数据照常入库）→ 看导航<b>①区</b>今天急跌入池了哪些新票（登记即可，不用操作）。<br>
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
<tr><td>部分确认</td><td>四项没凑齐</td><td>—</td><td>留池观察，看板自动滚动核对，哪天凑齐算哪天（最多D5）</td></tr>
</tbody></table>
<b>▎有效期提醒</b>：涨停基因的加成集中在<b>涨停后10个交易日内</b>（10日窗口大涨率48.6% vs 无涨停23.5%），「涨停距今天数」&gt;10 的票自动失效。<br>
<b>▎「核对日」与四项逐项打勾（②区）</b><br>
每只票四个信号 <span style="color:#1e8449;font-weight:700;">●</span>到位 / <span style="color:#c0392b;font-weight:700;">✗</span>未到位 / <span style="color:#bdc3c7;">–</span>还没轮到，逐项标在②区表格里。「核对日」列显示当前核到第几天：D1 没凑齐的票会<b>自动滚动到最新交易日继续核</b>（近一年回测：27% 的票要等 D2~D5 才确认，且晚确认质量不打折——D1 43.6% / D2 47.3% / D5 48.7%）。四项全中的当天就是<b>确认日</b>，当天 13:00-14:55 买入、次日卖；超过 5 天还没凑齐或「涨停距今天数」&gt;10，放弃。<br>
<b>▎「D0最低价」vs「今日最低(现)」（②区两列并排）</b>：左列 = <b>D0预警线锚</b>（固定不变，整个 D1~D5 窗口都比它），右列 = <b>核对日当天最低价</b>（盘中每20秒实时刷新，带 <span class="nowtag">今</span> 标 = 正在用当日实时数据）；<b>右列低于左列 = 盘中破线</b>（红底「破线」），14:30 后看收盘能否站回来定①项成不成立——两列放一起就是为了不用来回翻①区找锚价。
<b>▎「D0性质」—— 今天这根急跌是洗盘还是出货？（全历史 n=26608 回测）</b><br>
<span class="chip up">● 偏洗盘</span> = <b>放量急跌(量比≥1.2) + 涨停1~2天内</b>：下跌有承接、分歧换手充分，次日反抽概率最高，<b>确认率 15.5%</b>（基线7.3%的2倍+），优先盯。<br>
<span class="chip dn">▼ 偏出货</span> = <b>缩量阴跌(量比&lt;0.7)</b>：没有资金接、阴跌磨人，次日确认率仅 <b>3.0%</b>；其中缩量+涨停1~2天的最坑，确认后次日卖出均值 <b>-2.29%</b>（全表最差），直接跳过、别浪费精力。<br>
<span class="chip flat">中性</span> = 其余组合，按四项确认正常流程走。<br>
<font color="#7f8c8d">⚠️ 反直觉提醒：教科书说"缩量回调是洗盘"，在这套体系里<b>恰恰相反</b>——缩量急跌+涨停1~2天内的票，次日卖出均值 <b>-2.29%</b> 是全表最差（缩量=没人接盘，不是筹码稳定）；真正的好信号是<b>放量+涨停新鲜</b>。注意：偏洗盘≠能买，D0当天仍然禁止买入（负期望），它只决定"明天值不值得重点盯"。</font><br>
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
    var ok=(cur=='all')||(cur=='star'?+tr.dataset.star>=3:(cur=='排雷'?tr.dataset.sig.indexOf('排雷')>=0:(cur=='px'?tr.dataset.px==='偏洗盘':tr.dataset.sig===cur)));
    if(ok&&q)ok=tr.textContent.toUpperCase().indexOf(q)>=0;
    tr.style.display=ok?'':'none';if(ok)n++;
  });
  document.getElementById('cnt').textContent='显示 '+n+' 行';
}
apply();

/* ================= 吸附头高度动态测量（防错位） ================= */
function hdFix(){
  var h=document.getElementById('headbar');
  if(h)document.documentElement.style.setProperty('--hd',h.offsetHeight+'px');
}
window.addEventListener('resize',hdFix);
window.addEventListener('load',hdFix);
hdFix();

/* ================= 实时行情刷新（腾讯行情 · 跨域 script 注入） ================= */
var lvOn=true, lvTimer=null, lvCodes=[], lvQuotes={};
function lvToday(){var d=new Date();return ''+d.getFullYear()+('0'+(d.getMonth()+1)).slice(-2)+('0'+d.getDate()).slice(-2);}
function lvInSession(){
  var d=new Date(),w=d.getDay();if(w===0||w===6)return false;
  var m=d.getHours()*100+d.getMinutes();
  return (m>=925&&m<=1132)||(m>=1258&&m<=1502);
}
function lvCollect(){
  var seen={},arr=[];
  document.querySelectorAll('tr[data-code]').forEach(function(tr){
    var c=tr.dataset.code;if(c&&!seen[c]){seen[c]=1;arr.push(c);}
  });
  lvCodes=arr;return arr;
}
function lvLoad(codes,cb){
  var s=document.createElement('script');s.charset='GBK';
  s.src='https://qt.gtimg.cn/q='+codes.join(',')+'&_='+Date.now();
  var done=false;
  s.onload=s.onerror=function(){if(done)return;done=true;try{s.remove();}catch(e){}cb();};
  document.head.appendChild(s);
}
function lvApply(){
  var tdy=lvToday(),okSet={},b1=0,b2=0;
  document.querySelectorAll('tr[data-code]').forEach(function(tr){
    var code=tr.dataset.code,s=window['v_'+code];
    if(!s)return;
    var p=s.split('~');
    if(p.length<35)return;
    var price=parseFloat(p[3]),pct=parseFloat(p[32]),low=parseFloat(p[34]);
    if(!isFinite(price)||price<=0)return;
    okSet[code]=1;
    var col = pct>0.001?'#c0392b':(pct<-0.001?'#1e8449':'#566573');
    var sign = pct>0.001?'+':'';
    var d0=parseFloat(tr.dataset.d0low||'');
    var d1d=tr.dataset.d1date||'';
    var lq=tr.querySelector('.lq');
    if(lq){
      var html='<b style="color:'+col+';">'+price.toFixed(2)+'</b> <span style="font-size:11px;color:'+col+';">'+sign+pct.toFixed(2)+'%</span>';
      if(!d1d&&isFinite(d0)&&d0>0&&price<d0){html+=' <span class="brk">破线</span>';b1++;}
      lq.innerHTML=html;lq.style.color='';
    }
    if(isFinite(d0)&&d0>0&&isFinite(low)&&low>0&&(!d1d||d1d===tdy)){
      var cl=tr.querySelector('.lv-low');if(cl)cl.innerHTML=low.toFixed(2)+' <span class="nowtag">今</span>';
      var vs=tr.querySelector('.lv-vs');
      if(vs){
        var g=(low-d0)/d0*100;
        if(g<=0.001){vs.style.background='#fdedec';vs.innerHTML='<b style="color:#c0392b;">破线</b>';b2++;tr.classList.add('brkrow');}
        else{vs.style.background='';vs.innerHTML='<b style="color:'+(g<1?'#d35400':'#1e8449')+';">+'+g.toFixed(1)+'%</b>';tr.classList.remove('brkrow');}
      }
    }
  });
  return {ok:Object.keys(okSet).length,b1:b1,b2:b2};
}
function lvDone(){
  var r=lvApply();
  var d=new Date(),pad=function(n){return ('0'+n).slice(-2);};
  var hh=pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());
  var sess=lvInSession();
  document.getElementById('lvmsg').innerHTML=(sess?'<b style="color:#7dcea0;">盘中实时</b>':'<span style="color:#f8c471;">非交易时段·显示最近价</span>')
    +' · 更新 '+hh+' · 覆盖 '+r.ok+'/'+lvCodes.length+' 只';
  document.getElementById('lvbrk1').textContent=r.b1;
  document.getElementById('lvbrk2').textContent=r.b2;
  hdFix();
  var dot=document.getElementById('lvdot');
  dot.className='dot'+(lvOn?'':(sess?'':' off'));
  clearTimeout(lvTimer);
  if(lvOn)lvTimer=setTimeout(lvRefresh,sess?20000:120000);
}
function lvRefresh(){
  if(!lvOn)return;
  var codes=lvCollect();if(!codes.length)return;
  var B=90,bs=[],i;
  for(i=0;i<codes.length;i+=B)bs.push(codes.slice(i,i+B));
  var k=0;
  (function next(){
    if(k>=bs.length){lvDone();return;}
    lvLoad(bs[k++],function(){setTimeout(next,50);});
  })();
}
function lvToggle(){
  lvOn=!lvOn;
  document.getElementById('lvbtn').textContent=lvOn?'暂停':'继续';
  document.getElementById('lvmsg').innerHTML=lvOn?'继续刷新中…':'已暂停（点「继续」恢复）';
  hdFix();
  clearTimeout(lvTimer);
  if(lvOn)lvRefresh();
}
setTimeout(lvRefresh,600);
</script>
</body></html>""" \
        .replace('__LAST__', last).replace('__WK__', _wk(last)).replace('__LIVENOTE__', _live_note) \
        .replace('__CARDS__', cards) \
        .replace('__NWATCH__', str(len(watch))).replace('__NTOP__', str(len(watch_top))) \
        .replace('__WATCH_REST__', _table(D0_COLS, watch.iloc[30:], '#fdfefe', live='price') if len(watch)>30 else '') \
        .replace('__WATCH__', _table(D0_COLS, watch_top, '#fdfefe', live='price')) \
        .replace('__NREST__', str(max(0, len(watch)-len(watch_top)))) \
        .replace('__SIGDATE__', (chk_dates[-1] if chk_dates else '-')) \
        .replace('__NSIG__', str(len(sig_recent))) \
        .replace('__SIG__', _table(D1_COLS, sig_recent, '#fdfefe', live='d1') if len(sig_recent) else '<p style="color:#7f8c8d;">最近两个确认日无确认信号</p>') \
        .replace('__REVIEW__', _table(D2_COLS, review, '#fdfefe') if len(review) else '')
    open(os.path.join(STK, 'watchlist_tracker.html'), 'w').write(html)
    open(os.path.join(STK, 'index.html'), 'w').write(html)
    print('看板已生成 watchlist_tracker.html')

if __name__ == '__main__':
    if '--update' in sys.argv:
        fetch_update()
    out = build(days=20)
    to_html(out)
