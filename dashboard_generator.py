from __future__ import annotations

import os
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

import pandas as pd


EXCLUDED_ORDER_STATUSES = {
    'выполнен',
    'выпуск',
    'отменен',
    'к закрытию'
}

REQUEST_STATUSES = [
    'Новый',
    'Ком предложение отправлено',
    'Feed-back получен',
    'В работе',
    'Заявка получена'
]

CATEGORY_RULES = {
    'Регулярный': {'risk': 15, 'lost': 25},
    'Регулярные': {'risk': 15, 'lost': 25},
    'Стабильный': {'risk': 30, 'lost': 40},
    'Стабильные': {'risk': 30, 'lost': 40},
    'Нерегулярный': {'risk': 50, 'lost': 60},
    'Нерегулярные': {'risk': 50, 'lost': 60},
}

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR

OUTPUT_DIR = DATA_DIR / 'output'
OUTPUT_DIR.mkdir(exist_ok=True)


def read_excel(path: str) -> pd.DataFrame:
    xls = pd.ExcelFile(path)
    return pd.read_excel(path, sheet_name=xls.sheet_names[0])


def clean_str(x):
    if pd.isna(x):
        return 'Не указан'
    return str(x).strip()


def to_date(s):
    if pd.isna(s):
        return pd.NaT
    return pd.to_datetime(s, errors='coerce')


def safe_num(s):
    return pd.to_numeric(s, errors='coerce').fillna(0)


def fmt_money(v):
    try:
        if pd.isna(v):
            return '0 ₽'
        return f"{float(v):,.0f} ₽".replace(',', ' ')
    except Exception:
        return '0 ₽'


def find_profit_col(df: pd.DataFrame) -> str | None:
    cols = list(df.columns)
    normalized = {str(c).strip().lower(): c for c in cols}

    priority = [
        'план. прибыль без ндс (логистика)',
        'план прибыль без ндс (логистика)',
        'план. прибыль без ндс логистика',
        'план прибыль без ндс логистика',
        'план. прибыль без ндс',
        'план прибыль без ндс',
    ]

    for key in priority:
        if key in normalized:
            return normalized[key]

    for c in cols:
        lc = str(c).strip().lower()
        if 'план' in lc and 'приб' in lc and 'ндс' in lc and 'логист' in lc:
            return c

    for c in cols:
        lc = str(c).strip().lower()
        if 'план' in lc and 'приб' in lc and 'ндс' in lc:
            return c

    return None


def portfolio_status(row, today: datetime):
    category = clean_str(row.get('Признак'))
    rule = CATEGORY_RULES.get(category, {'risk': 30, 'lost': 40})

    last_order = to_date(row.get('Дата последнего заказа'))
    last_req = to_date(row.get('Дата последнего запроса'))

    dates = [d for d in [last_order, last_req] if not pd.isna(d)]

    if not dates:
        days = 999
        last_activity = pd.NaT
    else:
        last_activity = max(dates)
        days = (
            pd.Timestamp(today).normalize()
            - pd.Timestamp(last_activity).normalize()
        ).days

    if days > rule['lost']:
        status = 'LOST'
    elif days >= rule['risk']:
        status = 'РИСК'
    else:
        status = 'АКТИВНЫЙ'

    return status, days, last_activity


def format_cell(v):
    if isinstance(v, pd.Timestamp):
        return '' if pd.isna(v) else v.strftime('%d.%m.%Y')

    if isinstance(v, float):
        if math.isnan(v):
            return ''
        if abs(v - round(v)) < 0.0001:
            return str(int(round(v)))
        return f'{v:.2f}'

    return '' if v is None or str(v) == 'NaT' else str(v)


def esc_attr(v):
    return (
        str(v)
        .replace('&', '&amp;')
        .replace('"', '&quot;')
        .replace("'", '&#39;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def build_dashboard(
    order_path: str,
    requests_path: str,
    portfolio_path: str,
    output_path: str | None = None,
    today: datetime | None = None
) -> Dict[str, Any]:

    today = today or datetime.now()

    orders = read_excel(order_path)
    req = read_excel(requests_path)
    port = read_excel(portfolio_path)

    # =====================
    # ORDERS
    # =====================

    orders['_status_norm'] = (
        orders['Статус заказа']
        .fillna('')
        .astype(str)
        .str.strip()
        .str.lower()
    )

    active_orders = orders[
        ~orders['_status_norm'].isin(EXCLUDED_ORDER_STATUSES)
    ].copy()

    profit_col = find_profit_col(orders)

    if profit_col:
        active_orders['_profit'] = safe_num(active_orders[profit_col])
    else:
        active_orders['_profit'] = 0

    if 'Кол-во грузовых единиц' in active_orders:
        active_orders['_units'] = safe_num(active_orders['Кол-во грузовых единиц'])
    else:
        active_orders['_units'] = 0

    active_orders['_order_date'] = pd.to_datetime(active_orders['Дата заказа'], errors='coerce')

    current_month_start = pd.Timestamp(today).replace(day=1).normalize()
    next_month_start = current_month_start + pd.DateOffset(months=1)

    current_month_orders = active_orders[
        (active_orders['_order_date'] >= current_month_start)
        & (active_orders['_order_date'] < next_month_start)
    ].copy()

    order_mgr_base = (
        active_orders
        .groupby('Оперативный менеджер', dropna=False)
        .agg(
            orders=('Номер заказа', 'count'),
            clients=('Партнер', pd.Series.nunique),
            units=('_units', 'sum'),
            profit_work=('_profit', 'sum')
        )
        .reset_index()
        .rename(columns={'Оперативный менеджер': 'manager'})
    )

    profit_month = (
        current_month_orders
        .groupby('Оперативный менеджер', dropna=False)['_profit']
        .sum()
        .reset_index()
        .rename(columns={
            'Оперативный менеджер': 'manager',
            '_profit': 'profit_month'
        })
    )

    order_mgr = (
        order_mgr_base
        .merge(profit_month, on='manager', how='left')
        .fillna({'profit_month': 0})
        .sort_values('orders', ascending=False)
    )

    # =====================
    # REQUESTS
    # =====================
    
    req['Статус запроса'] = req['Статус запроса'].apply(clean_str)
    
    req['_request_date'] = pd.to_datetime(req['Дата запроса'], errors='coerce')

    current_month_start = pd.Timestamp(today).replace(day=1).normalize()
    next_month_start = current_month_start + pd.DateOffset(months=1)
    
    current_month_requests = req[
        (req['_request_date'] >= current_month_start)
        & (req['_request_date'] < next_month_start)
    ].copy()
    
    req_status_counts = {
        s: int((req['Статус запроса'] == s).sum())
        for s in REQUEST_STATUSES
    }
    
    req_mgr = (
        req.groupby(['Владелец записи', 'Статус запроса'])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    
    for s in REQUEST_STATUSES:
        if s not in req_mgr.columns:
            req_mgr[s] = 0
    
    req_mgr = (
        req_mgr[['Владелец записи'] + REQUEST_STATUSES]
        .rename(columns={'Владелец записи': 'manager'})
    )
    
    req_month_by_manager = (
        current_month_requests
        .groupby('Владелец записи', dropna=False)
        .size()
        .reset_index(name='requests_month')
        .rename(columns={'Владелец записи': 'manager'})
    )
    
    req_mgr = (
        req_mgr
        .merge(req_month_by_manager, on='manager', how='left')
        .fillna({'requests_month': 0})
    )
    
    due_col = (
        'Дата выполнения запроса'
        if 'Дата выполнения запроса' in req.columns
        else 'Дата выполнения'
    )
    
    req[due_col] = pd.to_datetime(req[due_col], errors='coerce')
    
    attention_req = req[
        (req['Статус запроса'] == 'Ком предложение отправлено')
        & (req[due_col].notna())
        & (req[due_col] <= pd.Timestamp(today) - pd.Timedelta(days=3))
    ].copy()
    
    attention_req['_days_overdue'] = (
        pd.Timestamp(today).normalize()
        - attention_req[due_col].dt.normalize()
    ).dt.days

    # =====================
    # PORTFOLIO
    # =====================

    for col in ['Дата последнего заказа', 'Дата последнего запроса']:
        port[col] = pd.to_datetime(port[col], errors='coerce')

    statuses = []
    days = []
    last_dates = []

    for _, row in port.iterrows():
        st, d, la = portfolio_status(row, today)
        statuses.append(st)
        days.append(d)
        last_dates.append(la)

    port['_status'] = statuses
    port['_days'] = days
    port['_last_activity'] = last_dates

    port['Группа'] = port['Группа'].apply(clean_str) if 'Группа' in port.columns else 'Не указана'
    port['Признак'] = port['Признак'].apply(clean_str)
    port['Количество заказов'] = safe_num(port['Количество заказов']) if 'Количество заказов' in port.columns else 0

    port_mgr = (
        port
        .groupby('Оперативный менеджер', dropna=False)
        .agg(
            clients=('Наименование', 'count'),
            active=('_status', lambda x: int((x == 'АКТИВНЫЙ').sum())),
            risk=('_status', lambda x: int((x == 'РИСК').sum())),
            lost=('_status', lambda x: int((x == 'LOST').sum())),
            a_lost=('Группа', lambda x: 0)
        )
        .reset_index()
        .rename(columns={'Оперативный менеджер': 'manager'})
    )

    a_lost_by_mgr = (
        port[
            (port['_status'] == 'LOST')
            & (port['Группа'].str.upper() == 'A')
        ]
        .groupby('Оперативный менеджер')
        .size()
        .to_dict()
    )

    port_mgr['a_lost'] = port_mgr['manager'].map(
        lambda m: int(a_lost_by_mgr.get(m, 0))
    )

    port_mgr = port_mgr.sort_values(['lost', 'risk'], ascending=False)

    priority = port[port['_status'].isin(['РИСК', 'LOST'])].copy()

    def priority_score(r):
        score = r['_days']

        if str(r['Группа']).upper() == 'A' and r['_status'] == 'LOST':
            score += 10000
        elif str(r['Группа']).upper() == 'A':
            score += 5000

        if 'Регуляр' in str(r['Признак']) and r['_status'] == 'LOST':
            score += 3000
        elif 'Регуляр' in str(r['Признак']):
            score += 1000

        score += float(r.get('Количество заказов', 0)) * 10

        return score

    priority['_score'] = priority.apply(priority_score, axis=1)
    priority = priority.sort_values('_score', ascending=False).head(30)

    managers = sorted(set(
        [str(x) for x in active_orders.get('Оперативный менеджер', pd.Series(dtype=str)).dropna().unique()]
        + [str(x) for x in req.get('Владелец записи', pd.Series(dtype=str)).dropna().unique()]
        + [str(x) for x in port.get('Оперативный менеджер', pd.Series(dtype=str)).dropna().unique()]
    ))

    data = {
        'generated_at': today.strftime('%d.%m.%Y %H:%M'),
        'profit_col': profit_col or 'не найдено',
        'managers': managers,
        'orders': {
            'active_count': int(len(active_orders)),
            'active_clients': int(active_orders['Партнер'].nunique()) if 'Партнер' in active_orders else 0,
            'units': int(active_orders['_units'].sum()),
            'profit_month': float(current_month_orders['_profit'].sum()),
            'by_manager': order_mgr.to_dict('records'),
        },
        'requests': {
            'total': int(len(req)),
            'month_total': int(len(current_month_requests)),
            'status_counts': req_status_counts,
            'by_manager': req_mgr.to_dict('records'),
            'attention': attention_req[
                ['Компания', 'Владелец записи', 'Статус запроса', due_col, '_days_overdue']
            ].head(30).to_dict('records')
        },
        'portfolio': {
            'total': int(len(port)),
            'active': int((port['_status'] == 'АКТИВНЫЙ').sum()),
            'risk': int((port['_status'] == 'РИСК').sum()),
            'lost': int((port['_status'] == 'LOST').sum()),
            'regular_risk': int(
                ((port['_status'] == 'РИСК') & (port['Признак'].str.contains('Регуляр', na=False))).sum()
            ),
            'a_lost': int(
                ((port['_status'] == 'LOST') & (port['Группа'].str.upper() == 'A')).sum()
            ),
            'by_manager': port_mgr.to_dict('records'),
            'attention': priority[
                ['Наименование', 'Оперативный менеджер', 'Признак', 'Группа',
                 'Количество заказов', '_status', '_days', '_last_activity']
            ].to_dict('records')
        }
    }

    html = render_html(data)

    output_path = output_path or str(OUTPUT_DIR / 'dashboard.html')
    Path(output_path).write_text(html, encoding='utf-8')

    (OUTPUT_DIR / 'dashboard_data.json').write_text(
        json.dumps(data, ensure_ascii=False, default=str, indent=2),
        encoding='utf-8'
    )

    return data


def render_html(d: Dict[str, Any]) -> str:
    managers_options = ''.join(
        f"<option value='{esc_attr(m)}'>{format_cell(m)}</option>"
        for m in d.get('managers', [])
    )

    order_rows = ''.join(
        f"<tr data-manager='{esc_attr(r['manager'])}'>"
        f"<td>{r['manager']}</td>"
        f"<td>{r['orders']}</td>"
        f"<td>{r['clients']}</td>"
        f"<td>{int(r['units'])}</td>"
        f"<td>{fmt_money(r.get('profit_work', 0))}</td>"
        f"<td>{fmt_money(r.get('profit_month', 0))}</td>"
        f"</tr>"
        for r in d['orders']['by_manager']
    )

    req_mgr_rows = ''.join(
        "<tr data-manager='" + esc_attr(r['manager']) + "'>"
        "<td>" + str(r['manager']) + "</td>"
        + ''.join(f"<td>{int(r.get(s, 0))}</td>" for s in REQUEST_STATUSES)
        + "</tr>"
        for r in d['requests']['by_manager']
    )

    req_attention_rows = ''.join(
        f"<tr data-manager='{esc_attr(r.get('Владелец записи'))}'>"
        f"<td>{format_cell(r.get('Компания'))}</td>"
        f"<td>{format_cell(r.get('Владелец записи'))}</td>"
        f"<td>{format_cell(r.get('_days_overdue'))}</td>"
        f"</tr>"
        for r in d['requests']['attention']
    )

    port_mgr_rows = ''.join(
        f"<tr data-manager='{esc_attr(r['manager'])}'>"
        f"<td>{r['manager']}</td>"
        f"<td>{r['clients']}</td>"
        f"<td>{r['active']}</td>"
        f"<td>{r['risk']}</td>"
        f"<td>{r['lost']}</td>"
        f"<td class='hot'>{r['a_lost']}</td>"
        f"</tr>"
        for r in d['portfolio']['by_manager']
    )

    att_rows = ''

    for r in d['portfolio']['attention']:
        cls = (
            'critical'
            if r['_status'] == 'LOST' and str(r['Группа']).upper() == 'A'
            else ('lost' if r['_status'] == 'LOST' else 'risk')
        )

        att_rows += (
            f"<tr class='{cls}' data-manager='{esc_attr(r['Оперативный менеджер'])}'>"
            f"<td>{format_cell(r['Наименование'])}</td>"
            f"<td>{format_cell(r['Оперативный менеджер'])}</td>"
            f"<td>{format_cell(r['Признак'])}</td>"
            f"<td>{format_cell(r['Группа'])}</td>"
            f"<td>{format_cell(r['Количество заказов'])}</td>"
            f"<td>{format_cell(r['_status'])}</td>"
            f"<td>{format_cell(r['_days'])}</td>"
            f"</tr>"
        )

    status_cards = ''.join(
        f"<div class='stage'><span>{k}</span><b>{v}</b></div>"
        for k, v in d['requests']['status_counts'].items()
    )

    return f"""
<!doctype html>
<html lang='ru'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Дашборд клиентского портфеля</title>
<style>
:root {{
    --blue:#5bc7f2;
    --pink:#ff5fa2;
    --violet:#8b5cf6;
    --orange:#ff9f43;
    --bg:#f7f9ff;
    --dark:#172033;
    --muted:#667085;
    --card:#fff;
    --red:#ff3b5f;
    --yellow:#ffbf47;
    --green:#20c997;
}}

* {{
    box-sizing:border-box;
}}

body {{
    margin:0;
    font-family:Inter,Arial,sans-serif;
    background:
        radial-gradient(circle at top left,#e6f8ff,transparent 32%),
        radial-gradient(circle at top right,#ffe6f2,transparent 28%),
        var(--bg);
    color:var(--dark);
}}

.wrap {{
    max-width:1440px;
    margin:0 auto;
    padding:28px;
}}

.hero {{
    display:flex;
    justify-content:space-between;
    gap:16px;
    align-items:center;
    margin-bottom:20px;
}}

h1 {{
    margin:0;
    font-size:34px;
    letter-spacing:-.04em;
}}

.sub {{
    color:var(--muted);
    margin-top:8px;
}}

.badge {{
    padding:10px 14px;
    border-radius:999px;
    background:#fff;
    border:1px solid #e9ecf5;
    box-shadow:0 10px 30px rgba(43,55,90,.08);
}}

.toolbar {{
    display:flex;
    gap:12px;
    align-items:center;
    justify-content:space-between;
    margin:16px 0 20px;
    padding:14px 16px;
    border-radius:22px;
    background:rgba(255,255,255,.78);
    border:1px solid #edf0fa;
    box-shadow:0 14px 40px rgba(42,56,100,.07);
}}

.toolbar label {{
    font-size:13px;
    color:var(--muted);
    text-transform:uppercase;
    letter-spacing:.06em;
}}

select {{
    border:1px solid #dde5f3;
    border-radius:14px;
    padding:11px 14px;
    background:white;
    color:var(--dark);
    font-weight:700;
    min-width:260px;
}}

.filter-note {{
    font-size:13px;
    color:var(--muted);
}}

.grid {{
    display:grid;
    gap:16px;
}}

.kpi {{
    grid-template-columns:repeat(4,1fr);
    margin-bottom:16px;
}}

.three-kpi {{
    grid-template-columns:repeat(3,1fr);
}}

.card {{
    background:rgba(255,255,255,.86);
    backdrop-filter:blur(8px);
    border:1px solid #edf0fa;
    border-radius:24px;
    padding:20px;
    box-shadow:0 18px 50px rgba(42,56,100,.08);
}}

.label {{
    font-size:13px;
    color:var(--muted);
    text-transform:uppercase;
    letter-spacing:.06em;
}}

.num {{
    font-size:34px;
    font-weight:850;
    margin-top:8px;
}}

.pink {{ color:var(--pink); }}
.blue {{ color:var(--blue); }}
.violet {{ color:var(--violet); }}
.red {{ color:var(--red); }}

.section {{
    margin-top:24px;
}}

h2 {{
    font-size:24px;
    margin:0 0 14px;
}}

.two {{
    grid-template-columns:1.1fr .9fr;
}}

table {{
    width:100%;
    border-collapse:collapse;
    font-size:14px;
}}

th,td {{
    text-align:left;
    padding:12px;
    border-bottom:1px solid #edf0f7;
}}

th {{
    color:var(--muted);
    font-size:12px;
    text-transform:uppercase;
    letter-spacing:.06em;
}}

.stage {{
    display:flex;
    justify-content:space-between;
    align-items:center;
    padding:16px;
    margin-bottom:10px;
    border-radius:18px;
    background:linear-gradient(90deg,#fff,#f7fbff);
    border:1px solid #eef2fb;
}}

.stage b {{
    font-size:26px;
}}

.hot {{
    font-weight:800;
    color:var(--red);
}}

.critical td {{
    background:#fff0f3!important;
    color:#9f1239;
    font-weight:700;
}}

.lost td {{
    background:#fff7f8;
}}

.risk td {{
    background:#fffaf0;
}}

.note {{
    font-size:12px;
    color:var(--muted);
    margin-top:8px;
}}

.hidden-by-filter {{
    display:none!important;
}}

@media(max-width:900px) {{
    .kpi,.three-kpi,.two {{
        grid-template-columns:1fr;
    }}

    .hero,.toolbar {{
        display:block;
    }}

    select {{
        width:100%;
        margin-top:8px;
    }}
}}
</style>
</head>
<body>
<div class='wrap'>

<div class='hero'>
    <div>
        <h1>Клиентский портфель · live dashboard</h1>
        <div class='sub'>Обновлено: {d['generated_at']} · источник прибыли: {d['profit_col']}</div>
    </div>
    <div class='badge'>INVCTC · Client Control Center</div>
</div>

<div class='toolbar'>
    <div>
        <label for='managerFilter'>Фильтр по менеджеру</label><br>
        <select id='managerFilter'>
            <option value='__all__'>Все менеджеры</option>
            {managers_options}
        </select>
    </div>
    <div class='filter-note'>
        Фильтр применяется к таблицам: заказы, запросы, портфель и блок “Требует внимания”.
        KPI сверху показывают общую картину.
    </div>
</div>

<section class='section'>
    <h2>1. Заказы</h2>
    <div class='grid kpi three-kpi'>
        <div class='card'>
            <div class='label'>Заказов в работе</div>
            <div class='num blue'>{d['orders']['active_count']}</div>
        </div>
        <div class='card'>
            <div class='label'>Клиентов с заказами</div>
            <div class='num violet'>{d['orders']['active_clients']}</div>
        </div>
        <div class='card'>
            <div class='label'>Грузовых единиц</div>
            <div class='num pink'>{d['orders']['units']}</div>
        </div>
    </div>

    <div class='card'>
        <h2>Заказы по менеджерам</h2>
        <table>
            <thead>
                <tr>
                    <th>Менеджер</th>
                    <th>Заказы</th>
                    <th>Клиенты</th>
                    <th>Гр. ед.</th>
                    <th>План прибыль по заказам в работе</th>
                    <th>План прибыль текущего месяца</th>
                </tr>
            </thead>
            <tbody>
                {order_rows}
            </tbody>
        </table>
    </div>
</section>

<section class='section'>
    <h2>2. Запросы</h2>
    <div class='grid two'>
        <div class='card'>
            <h2>Воронка запросов</h2>

    <div class='stage'>
        <span>Итого заведено запросов</span>
        <b>{d['requests']['total']}</b>
    </div>

    <div class='stage'>
        <span>Запросов текущего месяца</span>
        <b>{d['requests']['month_total']}</b>
    </div>

    {status_cards}
    </div>

        <div class='card'>
            <h2>Без обратной связи</h2>
            <div class='note'>Статус “Ком предложение отправлено” и дата выполнения старше 3 дней.</div>
            <table>
                <thead>
                    <tr>
                        <th>Клиент</th>
                        <th>Менеджер</th>
                        <th>Дней</th>
                    </tr>
                </thead>
                <tbody>
                    {req_attention_rows}
                </tbody>
            </table>
        </div>
    </div>

    <div class='card section'>
        <h2>Запросы по менеджерам</h2>
        <table>
            <thead>
                <tr>
                    <th>Менеджер</th>
                    {''.join(f'<th>{s}</th>' for s in REQUEST_STATUSES)}
                </tr>
            </thead>
            <tbody>
                {req_mgr_rows}
            </tbody>
        </table>
    </div>
</section>

<section class='section'>
    <h2>3. Клиентский портфель</h2>

    <div class='grid kpi'>
        <div class='card'>
            <div class='label'>Всего клиентов</div>
            <div class='num'>{d['portfolio']['total']}</div>
        </div>
        <div class='card'>
            <div class='label'>Активные</div>
            <div class='num blue'>{d['portfolio']['active']}</div>
        </div>
        <div class='card'>
            <div class='label'>Риск</div>
            <div class='num pink'>{d['portfolio']['risk']}</div>
        </div>
        <div class='card'>
            <div class='label'>LOST</div>
            <div class='num red'>{d['portfolio']['lost']}</div>
        </div>
    </div>

    <div class='grid two'>
        <div class='card'>
            <h2>Портфель по менеджерам</h2>
            <table>
                <thead>
                    <tr>
                        <th>Менеджер</th>
                        <th>Всего</th>
                        <th>Активные</th>
                        <th>Риск</th>
                        <th>Lost</th>
                        <th>A Lost</th>
                    </tr>
                </thead>
                <tbody>
                    {port_mgr_rows}
                </tbody>
            </table>
        </div>

        <div class='card'>
            <h2>Сигналы для руководителя</h2>
            <div class='stage'>
                <span>Регулярные в риске</span>
                <b class='pink'>{d['portfolio']['regular_risk']}</b>
            </div>
            <div class='stage'>
                <span>Клиенты A в LOST</span>
                <b class='red'>{d['portfolio']['a_lost']}</b>
            </div>
        </div>
    </div>

    <div class='card section'>
        <h2>Требует внимания</h2>
        <table>
            <thead>
                <tr>
                    <th>Клиент</th>
                    <th>Менеджер</th>
                    <th>Признак</th>
                    <th>Группа</th>
                    <th>Заказов</th>
                    <th>Статус</th>
                    <th>Дней без активности</th>
                </tr>
            </thead>
            <tbody>
                {att_rows}
            </tbody>
        </table>
    </div>
</section>

</div>

<script>
const filter = document.getElementById('managerFilter');

function applyManagerFilter() {{
    const value = filter.value;

    document.querySelectorAll('tr[data-manager]').forEach(row => {{
        const manager = row.getAttribute('data-manager') || '';
        row.classList.toggle(
            'hidden-by-filter',
            value !== '__all__' && manager !== value
        );
    }});
}}

filter.addEventListener('change', applyManagerFilter);
</script>

</body>
</html>
"""


if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument('--orders', required=True)
    p.add_argument('--requests', required=True)
    p.add_argument('--portfolio', required=True)
    p.add_argument('--output', default=str(OUTPUT_DIR / 'dashboard.html'))

    args = p.parse_args()

    build_dashboard(
        args.orders,
        args.requests,
        args.portfolio,
        args.output
    )

    print(args.output)
