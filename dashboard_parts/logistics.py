import html

import pandas as pd


LOGISTICS_MANAGERS = [
    "Дмитрий Шеховцов",
    "Осипов Евгений",
    "Вероника Павлова",
    "Чекалов Феликс",
]

WORK_STATUSES = [
    "Новый",
    "Букинг",
    "Море",
    "Порт",
    "Размещение",
    "До границы",
    "После границы",
    "ЖД",
    "ЖД прямое",
    "Ожидание выхода по ЖД",
    "Авто прямое",
    "Автовывоз",
    "Авиа",
    "ПТД",
    "В Работе",
    "В работе",
]

OVERLOAD_LIMIT = 100


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0)


def _safe_text(value) -> str:
    return html.escape(str(value), quote=True)


def build_logistics_data(path, today):
    if not path:
        return None

    df = pd.read_excel(path)

    manager_col = "Менеджер логистики"
    status_col = "Статус заказа"
    units_col = "Кол-во грузовых единиц"
    arrival_col = "Последняя дата прибытия"
    order_col = "Номер заказа"

    required_cols = [
        manager_col,
        status_col,
        units_col,
        arrival_col,
        order_col,
    ]

    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(
            "В файле логистики не найдены колонки: "
            + ", ".join(missing_cols)
        )

    df[manager_col] = (
        df[manager_col]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    df[status_col] = (
        df[status_col]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    df[order_col] = (
        df[order_col]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    df["_units"] = _num(df[units_col])
    df["_arrival"] = pd.to_datetime(df[arrival_col], errors="coerce")

    work = df[
        df[manager_col].isin(LOGISTICS_MANAGERS)
        & df[status_col].isin(WORK_STATUSES)
    ].copy()

    managers_data = []

    for manager in LOGISTICS_MANAGERS:
        manager_df = work[work[manager_col] == manager].copy()

        manager_orders = int(
            manager_df[order_col].replace("", pd.NA).nunique()
        )
        manager_units = int(manager_df["_units"].sum())

        status_counts = {}

        for status in WORK_STATUSES:
            status_df = manager_df[manager_df[status_col] == status]
            status_counts[status] = int(
                status_df[order_col].replace("", pd.NA).nunique()
            )

        managers_data.append({
            "manager": manager,
            "orders": manager_orders,
            "units": manager_units,
            "overloaded": manager_orders > OVERLOAD_LIMIT,
            "statuses": status_counts,
        })

    visible_statuses = [
        status
        for status in WORK_STATUSES
        if any(
            manager["statuses"].get(status, 0) > 0
            for manager in managers_data
        )
    ]

    month_start = pd.Timestamp(today).replace(day=1).normalize()
    next_month = month_start + pd.DateOffset(months=1)

    delivered = df[
        df[manager_col].isin(LOGISTICS_MANAGERS)
        & (df["_arrival"] >= month_start)
        & (df["_arrival"] < next_month)
    ].copy()

    rail_wait = work[
        work[status_col] == "Ожидание выхода по ЖД"
    ].copy()

    totals_by_status = {
        status: sum(
            manager["statuses"].get(status, 0)
            for manager in managers_data
        )
        for status in visible_statuses
    }

    return {
        "orders_work": int(
            work[order_col].replace("", pd.NA).nunique()
        ),
        "units_work": int(work["_units"].sum()),
        "delivered_orders": int(
            delivered[order_col].replace("", pd.NA).nunique()
        ),
        "delivered_units": int(delivered["_units"].sum()),
        "rail_wait_orders": int(
            rail_wait[order_col].replace("", pd.NA).nunique()
        ),
        "rail_wait_units": int(rail_wait["_units"].sum()),
        "managers": managers_data,
        "visible_statuses": visible_statuses,
        "totals_by_status": totals_by_status,
    }


def _manager_card(manager_data: dict) -> str:
    overloaded = bool(manager_data["overloaded"])

    card_style = (
        "border:2px solid #ef4444;"
        "background:linear-gradient(135deg,#fff1f2,#ffe4e6);"
        "box-shadow:0 14px 34px rgba(239,68,68,.20);"
        if overloaded
        else ""
    )

    number_style = "color:#dc2626;" if overloaded else ""

    return f"""
    <div class='card' style='{card_style}'>
        <div class='label'>{_safe_text(manager_data["manager"])}</div>
        <div class='num' style='{number_style}'>
            {int(manager_data["orders"])}
        </div>
        <div class='note'>
            заказов в работе · {int(manager_data["units"])} гр. ед.
        </div>
    </div>
    """


def render_logistics(logistics):
    if not logistics:
        return """
        <div class='page' id='page-logistics'>
            <div class='placeholder'>
                <h2>🚢 Логистика</h2>
                <p>
                    Загрузите файл логистики в бота,
                    чтобы увидеть показатели.
                </p>
            </div>
        </div>
        """

    manager_cards = "".join(
        _manager_card(manager)
        for manager in logistics["managers"]
    )

    status_header = "".join(
        f"<th>{_safe_text(status)}</th>"
        for status in logistics["visible_statuses"]
    )

    status_rows = ""

    for manager in logistics["managers"]:
        overloaded = bool(manager["overloaded"])

        row_style = (
            "background:#fff1f2;color:#991b1b;font-weight:800;"
            if overloaded
            else ""
        )

        order_style = (
            "color:#dc2626;font-size:18px;font-weight:900;"
            if overloaded
            else "font-weight:800;"
        )

        cells = "".join(
            f"<td>{int(manager['statuses'].get(status, 0))}</td>"
            for status in logistics["visible_statuses"]
        )

        status_rows += f"""
        <tr style='{row_style}'>
            <td>{_safe_text(manager["manager"])}</td>
            <td style='{order_style}'>{int(manager["orders"])}</td>
            <td>{int(manager["units"])}</td>
            {cells}
        </tr>
        """

    total_status_cells = "".join(
        f"<td>{int(logistics['totals_by_status'].get(status, 0))}</td>"
        for status in logistics["visible_statuses"]
    )

    totals_row = f"""
    <tr style='font-weight:900;background:#eef2ff;'>
        <td>ИТОГО</td>
        <td>{int(logistics['orders_work'])}</td>
        <td>{int(logistics['units_work'])}</td>
        {total_status_cells}
    </tr>
    """

    return f"""
    <div class='page' id='page-logistics'>
        <section class='section'>
            <h2>🚢 Логистика</h2>

            <div class='grid kpi'>
                <div class='card'>
                    <div class='label'>Заказы в работе</div>
                    <div class='num blue'>
                        {logistics['orders_work']}
                    </div>
                </div>

                <div class='card'>
                    <div class='label'>Грузовых единиц</div>
                    <div class='num violet'>
                        {logistics['units_work']}
                    </div>
                </div>

                <div class='card'>
                    <div class='label'>Доставлено за месяц</div>
                    <div class='num pink'>
                        {logistics['delivered_orders']}
                        |
                        {logistics['delivered_units']}
                    </div>
                    <div class='note'>заказы | гр. ед.</div>
                </div>

                <div class='card'>
                    <div class='label'>Ожидают выхода по ЖД</div>
                    <div class='num red'>
                        {logistics['rail_wait_orders']}
                        |
                        {logistics['rail_wait_units']}
                    </div>
                    <div class='note'>заказы | гр. ед.</div>
                </div>
            </div>

            <div class='grid kpi section'>
                {manager_cards}
            </div>

            <div class='card section'>
                <h2>Заказы по логистам и статусам</h2>

                <div style='overflow-x:auto;'>
                    <table>
                        <thead>
                            <tr>
                                <th>Логист</th>
                                <th>Заказов</th>
                                <th>Гр. ед.</th>
                                {status_header}
                            </tr>
                        </thead>
                        <tbody>
                            {status_rows}
                            {totals_row}
                        </tbody>
                    </table>
                </div>
            </div>
        </section>
    </div>
    """
