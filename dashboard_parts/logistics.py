import pandas as pd


LOGISTICS_MANAGERS_WORK = [
    "Валерия Конакова",
    "Вероника Павлова",
    "Дмитрий Шеховцов",
    "Осипов Евгений",
    "Терешкина Анастасия",
    "Чекалов Феликс",
]

LOGISTICS_MANAGERS_TABLE = [
    "Вероника Павлова",
    "Дмитрий Шеховцов",
    "Осипов Евгений",
    "Терешкина Анастасия",
    "Чекалов Феликс",
]

WORK_STATUSES = [
    "Авто прямое",
    "Автовывоз",
    "Букинг",
    "В Работе",
    "В работе",
    "До границы",
    "ЖД",
    "ЖД прямое",
    "Море",
    "Новый",
    "Ожидание выхода по ЖД",
    "ПТД",
    "Порт",
    "После границы",
    "Размещение",
    "Авиа",
]


def _num(series):
    return pd.to_numeric(series, errors="coerce").fillna(0)


def build_logistics_data(path, today):
    df = pd.read_excel(path)

    manager_col = "Менеджер логистики"
    status_col = "Статус заказа"
    units_col = "Кол-во грузовых единиц"
    arrival_col = "Последняя дата прибытия"
    order_col = "Номер заказа"

    df[manager_col] = df[manager_col].fillna("").astype(str).str.strip()
    df[status_col] = df[status_col].fillna("").astype(str).str.strip()
    df["_units"] = _num(df[units_col])
    df["_arrival"] = pd.to_datetime(df[arrival_col], errors="coerce")

    work = df[
        df[manager_col].isin(LOGISTICS_MANAGERS_WORK)
        & df[status_col].isin(WORK_STATUSES)
    ].copy()

    by_manager = (
        work[work[manager_col].isin(LOGISTICS_MANAGERS_TABLE)]
        .groupby(manager_col, dropna=False)
        .agg(
            orders=(order_col, "count"),
            units=("_units", "sum"),
        )
        .reset_index()
        .rename(columns={manager_col: "manager"})
        .sort_values("orders", ascending=False)
        .to_dict("records")
    )

    month_start = pd.Timestamp(today).replace(day=1).normalize()
    next_month = month_start + pd.DateOffset(months=1)

    delivered = df[
        (df["_arrival"] >= month_start)
        & (df["_arrival"] < next_month)
    ].copy()

    rail_wait = df[
        df[status_col] == "Ожидание выхода по ЖД"
    ].copy()

    return {
        "orders_work": int(len(work)),
        "units_work": int(work["_units"].sum()),
        "delivered_orders": int(len(delivered)),
        "delivered_units": int(delivered["_units"].sum()),
        "rail_wait_orders": int(len(rail_wait)),
        "rail_wait_units": int(rail_wait["_units"].sum()),
        "by_manager": by_manager,
    }


def render_logistics(logistics):
    if not logistics:
        return """
        <div class='page' id='page-logistics'>
            <div class='placeholder'>
                <h2>Логистика</h2>
                <p>Загрузите файл логистики в бота, чтобы увидеть показатели.</p>
            </div>
        </div>
        """

    rows = "".join(
        f"<tr>"
        f"<td>{r['manager']}</td>"
        f"<td>{int(r['orders'])}</td>"
        f"<td>{int(r['units'])}</td>"
        f"</tr>"
        for r in logistics["by_manager"]
    )

    return f"""
    <div class='page' id='page-logistics'>
        <section class='section'>
            <h2>🚢 Логистика</h2>

            <div class='grid kpi'>
                <div class='card'>
                    <div class='label'>Заказы в работе</div>
                    <div class='num blue'>{logistics['orders_work']}</div>
                </div>

                <div class='card'>
                    <div class='label'>Грузовых единиц</div>
                    <div class='num violet'>{logistics['units_work']}</div>
                </div>

                <div class='card'>
                    <div class='label'>Доставлено за месяц</div>
                    <div class='num pink'>{logistics['delivered_orders']} | {logistics['delivered_units']}</div>
                    <div class='note'>заказы | гр. ед.</div>
                </div>

                <div class='card'>
                    <div class='label'>Ожидают выхода по ЖД</div>
                    <div class='num red'>{logistics['rail_wait_orders']} | {logistics['rail_wait_units']}</div>
                    <div class='note'>заказы | гр. ед.</div>
                </div>
            </div>

            <div class='card section'>
                <h2>Нагрузка по логистам</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Логист</th>
                            <th>Заказов в работе</th>
                            <th>Грузовых единиц</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
        </section>
    </div>
    """
