from __future__ import annotations
from dashboard_parts.logistics import build_logistics_data, render_logistics
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from dashboard_parts.styles import get_styles
from dashboard_parts.scripts import get_scripts


EXCLUDED_ORDER_STATUSES = {
    "выполнен",
    "выпуск",
    "отменен",
    "к закрытию",
}

REQUEST_STATUSES = [
    "Новый",
    "Ком предложение отправлено",
    "Feed-back получен",
    "В работе",
    "Заявка получена",
]

CATEGORY_RULES = {
    "Регулярный": {"risk": 15, "lost": 25},
    "Регулярные": {"risk": 15, "lost": 25},
    "Стабильный": {"risk": 30, "lost": 40},
    "Стабильные": {"risk": 30, "lost": 40},
    "Нерегулярный": {"risk": 50, "lost": 60},
    "Нерегулярные": {"risk": 50, "lost": 60},
}

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


# =====================
# Helpers
# =====================

def read_excel(path: str) -> pd.DataFrame:
    xls = pd.ExcelFile(path)
    return pd.read_excel(path, sheet_name=xls.sheet_names[0])


def clean_str(x: Any) -> str:
    if pd.isna(x):
        return "Не указан"
    value = str(x).strip()
    return value if value else "Не указан"


def to_date(x: Any):
    if pd.isna(x):
        return pd.NaT
    return pd.to_datetime(x, errors="coerce")


def safe_num(x: Any) -> pd.Series:
    return pd.to_numeric(x, errors="coerce").fillna(0)


def fmt_money(v: Any) -> str:
    try:
        if pd.isna(v):
            return "0 ₽"
        return f"{float(v):,.0f} ₽".replace(",", " ")
    except Exception:
        return "0 ₽"


def format_cell(v: Any) -> str:
    if isinstance(v, pd.Timestamp):
        return "" if pd.isna(v) else v.strftime("%d.%m.%Y")

    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if abs(v - round(v)) < 0.0001:
            return str(int(round(v)))
        return f"{v:.2f}"

    if v is None or str(v) == "NaT":
        return ""

    return str(v)


def esc_attr(v: Any) -> str:
    return (
        str(v)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def find_profit_col(df: pd.DataFrame) -> str | None:
    target = "План. прибыль без НДС (Наша логистика)"

    for col in df.columns:
        if str(col).strip() == target:
            return col

    return None


def find_request_number_col(df: pd.DataFrame) -> str | None:
    variants = [
        "Номер запроса",
        "№ запроса",
        "Номер записи",
        "ID",
        "Id",
        "id",
    ]

    normalized = {str(c).strip().lower(): c for c in df.columns}

    for v in variants:
        key = v.strip().lower()
        if key in normalized:
            return normalized[key]

    for col in df.columns:
        lc = str(col).strip().lower()
        if "номер" in lc and "запрос" in lc:
            return col

    return None


def get_col(df: pd.DataFrame, names: list[str], default: str | None = None) -> str | None:
    normalized = {str(c).strip().lower(): c for c in df.columns}

    for name in names:
        key = name.strip().lower()
        if key in normalized:
            return normalized[key]

    return default


def norm_client_name(x: Any) -> str:
    value = str(x).strip().lower()

    replacements = [
        '"',
        "«",
        "»",
        "'",
        "`",
        "ооо ",
        "оoo ",
        "ип ",
        "зао ",
        "ао ",
        "пао ",
        "общество с ограниченной ответственностью ",
        ".",
        ",",
        " ",
        "-",
        "_",
    ]

    for repl in replacements:
        value = value.replace(repl, "")

    return value


def portfolio_status(row: pd.Series, today: datetime):
    category = clean_str(row.get("Признак"))
    rule = CATEGORY_RULES.get(category, {"risk": 30, "lost": 40})

    last_order = to_date(row.get("Дата последнего заказа"))
    last_req = to_date(row.get("Дата последнего запроса"))

    dates = [d for d in [last_order, last_req] if not pd.isna(d)]

    if not dates:
        days = 999
        last_activity = pd.NaT
    else:
        last_activity = max(dates)
        days = (pd.Timestamp(today).normalize() - pd.Timestamp(last_activity).normalize()).days

    if days > rule["lost"]:
        status = "LOST"
    elif days >= rule["risk"]:
        status = "РИСК"
    else:
        status = "АКТИВНЫЙ"

    return status, days, last_activity


# =====================
# Build dashboard
# =====================

def build_dashboard(
    order_path: str,
    requests_path: str,
    portfolio_path: str,
    output_path: str | None = None,
    today: datetime | None = None,
    snoozed_clients: dict | None = None,
) -> Dict[str, Any]:
    today = today or datetime.now()

    orders = read_excel(order_path)
    req = read_excel(requests_path)
    port = read_excel(portfolio_path)

    # =====================
    # ORDERS
    # =====================

    status_col = get_col(orders, ["Статус заказа"], "Статус заказа")
    manager_col = get_col(orders, ["Оперативный менеджер"], "Оперативный менеджер")
    order_num_col = get_col(orders, ["Номер заказа"], "Номер заказа")
    partner_col = get_col(orders, ["Партнер", "Партнёр"], "Партнер")
    units_col = get_col(orders, ["Кол-во грузовых единиц", "Количество грузовых единиц"])
    order_date_col = get_col(orders, ["Дата заказа"], "Дата заказа")

    orders["_status_norm"] = (
        orders[status_col]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    active_orders = orders[~orders["_status_norm"].isin(EXCLUDED_ORDER_STATUSES)].copy()

    profit_col = find_profit_col(orders)
    print(f"Используется колонка прибыли: {profit_col}", flush=True)

    active_orders["_profit"] = safe_num(active_orders[profit_col]) if profit_col else 0
    active_orders["_units"] = safe_num(active_orders[units_col]) if units_col else 0
    active_orders["_order_date"] = pd.to_datetime(active_orders[order_date_col], errors="coerce")

    current_month_start = pd.Timestamp(today).replace(day=1).normalize()
    next_month_start = current_month_start + pd.DateOffset(months=1)

    current_month_orders = active_orders[
        (active_orders["_order_date"] >= current_month_start)
        & (active_orders["_order_date"] < next_month_start)
    ].copy()

    order_mgr_base = (
        active_orders
        .groupby(manager_col, dropna=False)
        .agg(
            orders=(order_num_col, "count"),
            clients=(partner_col, pd.Series.nunique),
            units=("_units", "sum"),
            profit_work=("_profit", "sum"),
        )
        .reset_index()
        .rename(columns={manager_col: "manager"})
    )

    profit_month = (
        current_month_orders
        .groupby(manager_col, dropna=False)["_profit"]
        .sum()
        .reset_index()
        .rename(columns={manager_col: "manager", "_profit": "profit_month"})
    )

    order_mgr = (
        order_mgr_base
        .merge(profit_month, on="manager", how="left")
        .fillna({"profit_month": 0})
        .sort_values("orders", ascending=False)
    )

    # =====================
    # REQUESTS
    # =====================

    req_status_col = get_col(req, ["Статус запроса"], "Статус запроса")
    req_manager_col = get_col(req, ["Владелец записи", "Менеджер"], "Владелец записи")
    req_company_col = get_col(req, ["Компания", "Клиент"], "Компания")
    req_date_col = get_col(req, ["Дата запроса"], "Дата запроса")

    req[req_status_col] = req[req_status_col].apply(clean_str)
    req["_request_date"] = pd.to_datetime(req[req_date_col], errors="coerce") if req_date_col in req.columns else pd.NaT

    current_month_requests = req[
        (req["_request_date"] >= current_month_start)
        & (req["_request_date"] < next_month_start)
    ].copy()

    req_status_counts = {
        s: int((req[req_status_col] == s).sum())
        for s in REQUEST_STATUSES
    }

    req_mgr = (
        req.groupby([req_manager_col, req_status_col])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )

    for s in REQUEST_STATUSES:
        if s not in req_mgr.columns:
            req_mgr[s] = 0

    req_mgr = req_mgr[[req_manager_col] + REQUEST_STATUSES].rename(columns={req_manager_col: "manager"})
    req_mgr["requests_total"] = req_mgr[REQUEST_STATUSES].sum(axis=1)

    due_col = get_col(req, ["Дата выполнения запроса", "Дата выполнения"])

    if due_col:
        req[due_col] = pd.to_datetime(req[due_col], errors="coerce")
        attention_req = req[
            (req[req_status_col] == "Ком предложение отправлено")
            & (req[due_col].notna())
            & (req[due_col] <= pd.Timestamp(today) - pd.Timedelta(days=3))
        ].copy()

        attention_req["_days_overdue"] = (
            pd.Timestamp(today).normalize() - attention_req[due_col].dt.normalize()
        ).dt.days
    else:
        attention_req = req.iloc[0:0].copy()
        attention_req["_days_overdue"] = []

    request_number_col = find_request_number_col(req)
    attention_req["_request_number"] = (
        attention_req[request_number_col].apply(format_cell) if request_number_col else ""
    )

    # =====================
    # PORTFOLIO
    # =====================

    port_manager_col = get_col(port, ["Оперативный менеджер"], "Оперативный менеджер")
    port_name_col = get_col(port, ["Наименование", "Компания", "Клиент"], "Наименование")
    port_group_col = get_col(port, ["Группа"])
    port_sign_col = get_col(port, ["Признак"], "Признак")
    port_orders_count_col = get_col(port, ["Количество заказов", "Кол-во заказов"])

    for col in ["Дата последнего заказа", "Дата последнего запроса"]:
        if col in port.columns:
            port[col] = pd.to_datetime(port[col], errors="coerce")
        else:
            port[col] = pd.NaT

    statuses = []
    days = []
    last_dates = []

    for _, row in port.iterrows():
        st, d, la = portfolio_status(row, today)
        statuses.append(st)
        days.append(d)
        last_dates.append(la)

    port["_status"] = statuses
    port["_days"] = days
    port["_last_activity"] = last_dates

    if port_group_col:
        port["_group"] = port[port_group_col].apply(clean_str)
    else:
        port["_group"] = "Не указана"

    port["_sign"] = port[port_sign_col].apply(clean_str) if port_sign_col else "Не указан"
    port["_orders_count"] = safe_num(port[port_orders_count_col]) if port_orders_count_col else 0

    port_mgr = (
        port
        .groupby(port_manager_col, dropna=False)
        .agg(
            clients=(port_name_col, "count"),
            active=("_status", lambda x: int((x == "АКТИВНЫЙ").sum())),
            risk=("_status", lambda x: int((x == "РИСК").sum())),
            lost=("_status", lambda x: int((x == "LOST").sum())),
        )
        .reset_index()
        .rename(columns={port_manager_col: "manager"})
    )

    a_lost_by_mgr = (
        port[(port["_status"] == "LOST") & (port["_group"].astype(str).str.upper() == "A")]
        .groupby(port_manager_col)
        .size()
        .to_dict()
    )

    port_mgr["a_lost"] = port_mgr["manager"].map(lambda m: int(a_lost_by_mgr.get(m, 0)))
    port_mgr = port_mgr.sort_values(["lost", "risk"], ascending=False)

    snoozed = snoozed_clients or {}
    print(f"SNOOZE IN GENERATOR: {len(snoozed)}", flush=True)

    today_norm = pd.Timestamp(today).normalize()
    snoozed_norm = {norm_client_name(k): v for k, v in snoozed.items()}

    def is_snoozed(client_name: Any) -> bool:
        item = snoozed_norm.get(norm_client_name(client_name))
        if not item:
            return False

        until = pd.to_datetime(item.get("until"), errors="coerce")
        if pd.isna(until):
            return False

        return pd.Timestamp(until).normalize() > today_norm

    priority_all = port[port["_status"].isin(["РИСК", "LOST"])].copy()
    priority_all["_snoozed"] = priority_all[port_name_col].apply(is_snoozed)
    snoozed_active_count = int(priority_all["_snoozed"].sum())

    priority = priority_all[~priority_all["_snoozed"]].copy()

    def priority_score(r: pd.Series) -> float:
        score = float(r["_days"])

        if str(r["_group"]).upper() == "A" and r["_status"] == "LOST":
            score += 10000
        elif str(r["_group"]).upper() == "A":
            score += 5000

        if "Регуляр" in str(r["_sign"]) and r["_status"] == "LOST":
            score += 3000
        elif "Регуляр" in str(r["_sign"]):
            score += 1000

        score += float(r.get("_orders_count", 0)) * 10
        return score

    priority["_score"] = priority.apply(priority_score, axis=1)
    priority = priority.sort_values("_score", ascending=False).head(30)

    managers = sorted(set(
        [str(x) for x in active_orders.get(manager_col, pd.Series(dtype=str)).dropna().unique()]
        + [str(x) for x in req.get(req_manager_col, pd.Series(dtype=str)).dropna().unique()]
        + [str(x) for x in port.get(port_manager_col, pd.Series(dtype=str)).dropna().unique()]
    ))

    data = {
        "generated_at": today.strftime("%d.%m.%Y %H:%M"),
        "profit_col": profit_col or "не найдено",
        "managers": managers,
        "orders": {
            "active_count": int(len(active_orders)),
            "active_clients": int(active_orders[partner_col].nunique()) if partner_col in active_orders else 0,
            "units": int(active_orders["_units"].sum()),
            "profit_work": float(active_orders["_profit"].sum()),
            "profit_month": float(current_month_orders["_profit"].sum()),
            "by_manager": order_mgr.to_dict("records"),
        },
        "requests": {
            "total": int(len(req)),
            "month_total": int(len(current_month_requests)),
            "status_counts": req_status_counts,
            "by_manager": req_mgr.to_dict("records"),
            "attention": attention_req[[
                req_company_col,
                req_manager_col,
                "_request_number",
                req_status_col,
                "_days_overdue",
            ]].head(100).to_dict("records"),
            "cols": {
                "company": req_company_col,
                "manager": req_manager_col,
                "status": req_status_col,
            },
        },
        "portfolio": {
            "total": int(len(port)),
            "active": int((port["_status"] == "АКТИВНЫЙ").sum()),
            "risk": int((port["_status"] == "РИСК").sum()),
            "lost": int((port["_status"] == "LOST").sum()),
            "regular_risk": int(((port["_status"] == "РИСК") & (port["_sign"].str.contains("Регуляр", na=False))).sum()),
            "a_lost": int(((port["_status"] == "LOST") & (port["_group"].astype(str).str.upper() == "A")).sum()),
            "snoozed_active": snoozed_active_count,
            "by_manager": port_mgr.to_dict("records"),
            "attention": priority[[
                port_name_col,
                port_manager_col,
                "_sign",
                "_group",
                "_orders_count",
                "_status",
                "_days",
                "_last_activity",
            ]].to_dict("records"),
            "cols": {
                "name": port_name_col,
                "manager": port_manager_col,
            },
        },
    }

    html = render_html(data)

    output_path = output_path or str(OUTPUT_DIR / "dashboard.html")
    Path(output_path).write_text(html, encoding="utf-8")

    (OUTPUT_DIR / "dashboard_data.json").write_text(
        json.dumps(data, ensure_ascii=False, default=str, indent=2),
        encoding="utf-8",
    )

    return data


# =====================
# Render HTML
# =====================

def render_html(d: Dict[str, Any]) -> str:
    managers_options = "".join(
        f"<option value='{esc_attr(m)}'>{format_cell(m)}</option>"
        for m in d.get("managers", [])
    )

    order_rows = "".join(
        f"<tr data-manager='{esc_attr(r['manager'])}'>"
        f"<td>{format_cell(r['manager'])}</td>"
        f"<td>{int(r.get('orders', 0))}</td>"
        f"<td>{int(r.get('clients', 0))}</td>"
        f"<td>{int(r.get('units', 0))}</td>"
        f"<td>{fmt_money(r.get('profit_work', 0))}</td>"
        f"<td>{fmt_money(r.get('profit_month', 0))}</td>"
        f"</tr>"
        for r in d["orders"]["by_manager"]
    )

    req_mgr_rows = "".join(
        "<tr data-manager='" + esc_attr(r["manager"]) + "'>"
        "<td>" + format_cell(r["manager"]) + "</td>"
        f"<td>{int(r.get('requests_total', 0))}</td>"
        + "".join(f"<td>{int(r.get(s, 0))}</td>" for s in REQUEST_STATUSES)
        + "</tr>"
        for r in d["requests"]["by_manager"]
    )

    # Request attention grouped by client + manager
    attention_grouped: dict[tuple[str, str], dict[str, Any]] = {}
    req_cols = d["requests"]["cols"]

    for r in d["requests"]["attention"]:
        client = format_cell(r.get(req_cols["company"]))
        manager = format_cell(r.get(req_cols["manager"]))
        key = (client, manager)

        if key not in attention_grouped:
            attention_grouped[key] = {
                "client": client,
                "manager": manager,
                "count": 0,
                "max_days": 0,
                "items": [],
            }

        days = int(r.get("_days_overdue") or 0)
        attention_grouped[key]["count"] += 1
        attention_grouped[key]["max_days"] = max(attention_grouped[key]["max_days"], days)
        attention_grouped[key]["items"].append({
            "number": format_cell(r.get("_request_number")),
            "status": format_cell(r.get(req_cols["status"])),
            "days": days,
        })

    req_attention_rows = ""

    for item in attention_grouped.values():
        details_rows = "".join(
            f"<tr class='detail-row'>"
            f"<td></td>"
            f"<td colspan='4'>№ {sub['number']} · {sub['status']} · {sub['days']} дн.</td>"
            f"</tr>"
            for sub in item["items"]
        )

        req_attention_rows += (
            f"<tbody class='attention-group' data-manager='{esc_attr(item['manager'])}'>"
            f"<tr class='attention-main'>"
            f"<td><button class='toggle-details'>▶</button></td>"
            f"<td><b>{item['client']}</b></td>"
            f"<td>{item['manager']}</td>"
            f"<td>{item['count']} запр.</td>"
            f"<td>{item['max_days']} дн.</td>"
            f"</tr>"
            f"{details_rows}"
            f"</tbody>"
        )

    port_mgr_rows = "".join(
        f"<tr data-manager='{esc_attr(r['manager'])}'>"
        f"<td>{format_cell(r['manager'])}</td>"
        f"<td>{int(r.get('clients', 0))}</td>"
        f"<td>{int(r.get('active', 0))}</td>"
        f"<td>{int(r.get('risk', 0))}</td>"
        f"<td>{int(r.get('lost', 0))}</td>"
        f"<td class='hot'>{int(r.get('a_lost', 0))}</td>"
        f"</tr>"
        for r in d["portfolio"]["by_manager"]
    )

    port_cols = d["portfolio"]["cols"]
    att_rows = ""

    for r in d["portfolio"]["attention"]:
        cls = (
            "critical"
            if r["_status"] == "LOST" and str(r["_group"]).upper() == "A"
            else ("lost" if r["_status"] == "LOST" else "risk")
        )

        client_name = format_cell(r.get(port_cols["name"]))
        manager_name = format_cell(r.get(port_cols["manager"]))

        att_rows += (
            f"<tr class='{cls}' data-manager='{esc_attr(manager_name)}'>"
            f"<td>{client_name}</td>"
            f"<td>{manager_name}</td>"
            f"<td>{format_cell(r.get('_sign'))}</td>"
            f"<td>{format_cell(r.get('_group'))}</td>"
            f"<td>{format_cell(r.get('_orders_count'))}</td>"
            f"<td>{format_cell(r.get('_status'))}</td>"
            f"<td>{format_cell(r.get('_days'))}</td>"
            f"<td class='snooze-cell'>"
            f"<select class='snooze-days'>"
            f"<option value=''>Срок</option>"
            f"<option value='7'>7 дней</option>"
            f"<option value='14'>14 дней</option>"
            f"<option value='30'>30 дней</option>"
            f"<option value='60'>60 дней</option>"
            f"<option value='90'>90 дней</option>"
            f"</select> "
            f"<input type='date' class='snooze-date'> "
            f"<select class='snooze-reason'>"
            f"<option value=''>Причина</option>"
            f"<option value='Пауза проекта'>Пауза проекта</option>"
            f"<option value='Сезонность'>Сезонность</option>"
            f"<option value='Ожидаем решение клиента'>Ожидаем решение клиента</option>"
            f"<option value='Нет бюджета'>Нет бюджета</option>"
            f"<option value='Ожидаем документы'>Ожидаем документы</option>"
            f"<option value='Другое'>Другое</option>"
            f"</select> "
            f"<button class='snooze-btn' "
            f"data-client='{esc_attr(client_name)}' "
            f"data-manager='{esc_attr(manager_name)}'>Отложить</button>"
            f"</td>"
            f"</tr>"
        )

    status_cards = "".join(
        f"<div class='stage'><span>{k}</span><b>{v}</b></div>"
        for k, v in d["requests"]["status_counts"].items()
    )

    return f"""
<!doctype html>
<html lang='ru'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Дашборд Инвиктика</title>
{get_styles()}
</head>
<body>
<div class='app-shell'>

<aside class='sidebar'>
    <div class='side-logo'>Дашборд<br>Инвиктика</div>

    <div class='nav-section'>
        <div class='nav-item' data-page='home'>🏠 Главная</div>
    </div>

    <div class='nav-section'>
        <div class='nav-item active' data-page='clients'>👥 Клиентский отдел</div>
        <div class='nav-sub'>
            <a href='#' class='nav-link subtab active-subtab' data-section='orders'>📦 Заказы</a>
            <a href='#' class='nav-link subtab' data-section='requests'>📨 Запросы</a>
            <a href='#' class='nav-link subtab' data-section='portfolio'>👥 Портфель</a>
        </div>
    </div>

    <div class='nav-section'>
        <div class='nav-item' data-page='logistics'>🚢 Логистика</div>
    </div>

    <div class='nav-section'>
        <div class='nav-item' data-page='customs'>📑 Таможня</div>
    </div>

    <div class='nav-section'>
        <div class='nav-item' data-page='sales'>📈 Отдел продаж</div>
    </div>

    <div class='nav-section'>
        <div class='nav-item' data-page='settings'>⚙️ Настройки</div>
    </div>
</aside>

<main class='main-area'>
<div class='wrap'>

<div class='hero'>
    <div>
        <h1>Дашборд Инвиктика</h1>
        <div class='sub'>Обновлено: {d['generated_at']} · источник прибыли: {d['profit_col']}</div>
    </div>
    <div class='badge'>Клиентский отдел</div>
</div>

<div class='page' id='page-home'>
    <div class='placeholder'>
        <h2>Главная</h2>
        <p>Сводная страница компании. Здесь позже появятся ключевые показатели по отделам.</p>
    </div>
</div>

<div class='page active-page' id='page-clients'>

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

<section class='section dashboard-section active-section' id='orders-section'>
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
            <tbody>{order_rows}</tbody>
        </table>
    </div>
</section>

<section class='section dashboard-section' id='requests-section'>
    <h2>2. Запросы</h2>
    <div class='grid two'>
        <div class='card'>
            <h2>Воронка запросов</h2>
            <div class='stage'>
                <span>Итого заведено запросов</span>
                <b>{d['requests']['total']}</b>
            </div>
            {status_cards}
        </div>

        <div class='card'>
            <h2>Без обратной связи</h2>
            <div class='note'>Статус “Ком предложение отправлено” и дата выполнения старше 3 дней.</div>
            <table>
                <thead>
                    <tr>
                        <th></th>
                        <th>Клиент</th>
                        <th>Менеджер</th>
                        <th>Запросов</th>
                        <th>Макс. дней</th>
                    </tr>
                </thead>
                {req_attention_rows}
            </table>
        </div>
    </div>

    <div class='card section'>
        <h2>Запросы по менеджерам</h2>
        <table>
            <thead>
                <tr>
                    <th>Менеджер</th>
                    <th>Итого заведено запросов</th>
                    {''.join(f'<th>{s}</th>' for s in REQUEST_STATUSES)}
                </tr>
            </thead>
            <tbody>{req_mgr_rows}</tbody>
        </table>
    </div>
</section>

<section class='section dashboard-section' id='portfolio-section'>
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
                <tbody>{port_mgr_rows}</tbody>
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
            <div class='stage'>
                <span>Отложены до даты</span>
                <b class='violet'>{d['portfolio']['snoozed_active']}</b>
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
                    <th>Отложить</th>
                </tr>
            </thead>
            <tbody>{att_rows}</tbody>
        </table>
    </div>
</section>

</div>

<div class='page' id='page-logistics'>
    <div class='placeholder'>
        <h2>Логистика</h2>
        <p>Раздел в разработке: этапы перевозок, проблемные заказы, море, ЖД, авто, авиа.</p>
    </div>
</div>

<div class='page' id='page-customs'>
    <div class='placeholder'>
        <h2>Таможня</h2>
        <p>Раздел в разработке: ДТ, выпуски, досмотры, МИДК, IM70 / IM40.</p>
    </div>
</div>

<div class='page' id='page-sales'>
    <div class='placeholder'>
        <h2>Отдел продаж</h2>
        <p>Раздел в разработке: воронка, новые клиенты, КП, конверсия и KPI МОП.</p>
    </div>
</div>

<div class='page' id='page-settings'>
    <div class='placeholder'>
        <h2>Настройки</h2>
        <p>Здесь позже будут настройки дашборда, отложенные клиенты и параметры отображения.</p>
    </div>
</div>

</div>
</main>
</div>

{get_scripts()}
</body>
</html>
"""


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--orders", required=True)
    p.add_argument("--requests", required=True)
    p.add_argument("--portfolio", required=True)
    p.add_argument("--output", default=str(OUTPUT_DIR / "dashboard.html"))

    args = p.parse_args()

    build_dashboard(
        args.orders,
        args.requests,
        args.portfolio,
        args.output,
    )

    print(args.output)

