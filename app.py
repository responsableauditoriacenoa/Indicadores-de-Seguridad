from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
import unicodedata

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st


DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1o1CL6U57o9pYOS1ce9vUn7yIm45GIdrH/export?format=csv"
)

MONTHS_ES = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}

BUSINESS_UNITS = {
    "AUTOLUX": "Autolux",
    "AUTOSOL": "Autosol",
    "CIEL": "Ciel",
    "CHANGO": "Chango Truck",
    "KOMPAS": "Kompas",
}

MONTHS_FULL_ES = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}


@dataclass
class DashboardData:
    totals: pd.DataFrame
    services: pd.DataFrame
    subtotals: pd.DataFrame
    savings: pd.DataFrame
    notes: list[str]


def parse_month_label(value: str) -> pd.Timestamp | None:
    if not value:
        return None
    cleaned = str(value).strip().lower().replace(".", "")
    parts = cleaned.replace("-", " ").split()
    if len(parts) != 2 or parts[0] not in MONTHS_ES:
        return None
    month = MONTHS_ES[parts[0]]
    year = int(parts[1])
    year += 2000 if year < 100 else 0
    return pd.Timestamp(year=year, month=month, day=1)


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\ufeff", "").strip()


def normalize_text(value: object) -> str:
    cleaned = clean_text(value)
    normalized = unicodedata.normalize("NFKD", cleaned)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char)).lower()
    return normalized.replace("a3", "o")


def parse_number(value: object) -> float | None:
    text = clean_text(value)
    if not text:
        return None
    text = text.replace("%", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def identify_unit(concept: str) -> str:
    upper = concept.upper()
    for key, label in BUSINESS_UNITS.items():
        if key in upper:
            return label
    return "Otros"


def identify_service_type(concept: str, sample_label: str) -> str:
    sample = sample_label.lower()
    concept_lower = concept.lower()
    if "monitoreo" in sample:
        return "Monitoreo"
    if "alarma" in concept_lower:
        return "Alarmas"
    if "instalacion" in concept_lower:
        return "Instalaciones"
    if "evento" in concept_lower:
        return "Eventos"
    return "Servicios"


def build_column_specs(rows: list[list[str]]) -> list[dict[str, object]]:
    month_row = rows[1]
    metric_row = rows[3]
    field_row = rows[4]
    specs: list[dict[str, object]] = []
    current_month = None
    current_metric = ""
    for index in range(1, max(len(month_row), len(metric_row), len(field_row))):
        month_label = clean_text(month_row[index]) if index < len(month_row) else ""
        if month_label:
            current_month = parse_month_label(month_label)
        metric_label = clean_text(metric_row[index]) if index < len(metric_row) else ""
        if metric_label:
            current_metric = metric_label
        field_label = clean_text(field_row[index]) if index < len(field_row) else ""
        specs.append(
            {
                "column": index,
                "month": current_month,
                "metric": current_metric,
                "field": field_label,
            }
        )
    return specs


def section_index(rows: list[list[str]], label: str) -> int:
    expected = normalize_text(label)
    for index, row in enumerate(rows):
        current = normalize_text(row[0])
        if current == expected:
            return index
    raise ValueError(f"No se encontro la seccion {label!r}.")


def extract_totals(rows: list[list[str]], specs: list[dict[str, object]]) -> pd.DataFrame:
    total_row = rows[2]
    records = []
    for spec in specs:
        month = spec["month"]
        metric = clean_text(spec["metric"])
        field = clean_text(spec["field"])
        if month is None or not metric or field != "Importe":
            continue
        value = parse_number(total_row[spec["column"]]) if spec["column"] < len(total_row) else None
        if value is None:
            continue
        records.append({"month": month, "metric": metric, "value": value})
    return pd.DataFrame(records).sort_values(["month", "metric"]).reset_index(drop=True)


def extract_services(rows: list[list[str]], specs: list[dict[str, object]]) -> pd.DataFrame:
    start = section_index(rows, "Servicios ProyecciÃ³n") + 2
    end = section_index(rows, "Subtotales")
    records = []
    for row in rows[start:end]:
        concept = clean_text(row[0])
        if not concept:
            continue
        business_unit = identify_unit(concept)
        sample_label = clean_text(row[2]) if len(row) > 2 else ""
        service_type = identify_service_type(concept, sample_label)
        for spec in specs:
            month = spec["month"]
            metric = clean_text(spec["metric"])
            field = clean_text(spec["field"])
            if month is None or not metric or not field or spec["column"] >= len(row):
                continue
            raw_value = row[spec["column"]]
            if field == "Importe":
                value = parse_number(raw_value)
            else:
                parsed = parse_number(raw_value)
                value = parsed if parsed is not None else clean_text(raw_value)
            if value in (None, ""):
                continue
            records.append(
                {
                    "concept": concept,
                    "business_unit": business_unit,
                    "service_type": service_type,
                    "month": month,
                    "metric": metric,
                    "field": field,
                    "value": value,
                }
            )
    return pd.DataFrame(records)


def extract_subtotals(rows: list[list[str]], specs: list[dict[str, object]]) -> pd.DataFrame:
    start = section_index(rows, "Subtotales") + 1
    end = section_index(rows, "Diferencias (+/-)")
    records = []
    for row in rows[start:end]:
        unit_name = clean_text(row[0])
        if not unit_name:
            continue
        for spec in specs:
            month = spec["month"]
            metric = clean_text(spec["metric"])
            field = clean_text(spec["field"])
            if month is None or not metric or field != "Importe" or spec["column"] >= len(row):
                continue
            value = parse_number(row[spec["column"]])
            if value is None:
                continue
            records.append(
                {
                    "business_unit": unit_name.title(),
                    "month": month,
                    "metric": metric,
                    "value": value,
                }
            )
    return pd.DataFrame(records)


def extract_savings(rows: list[list[str]]) -> pd.DataFrame:
    start = section_index(rows, "Diferencias (+/-)") + 2
    header = rows[start]
    records = []
    for row in rows[start + 1 :]:
        concept = clean_text(row[0])
        if not concept:
            break
        for index in range(1, len(header)):
            month = parse_month_label(clean_text(header[index]))
            if month is None or index >= len(row):
                continue
            value = parse_number(row[index])
            if value is None:
                continue
            records.append({"concept": concept, "month": month, "value": value})
    return pd.DataFrame(records)


def extract_notes(rows: list[list[str]]) -> list[str]:
    notes = []
    capture = False
    for row in rows:
        label = clean_text(row[0])
        if label == "Datos:":
            capture = True
            continue
        if capture and label:
            notes.append(label)
    return notes


@st.cache_data(show_spinner=False, ttl=1800)
def load_dashboard_data(sheet_url: str) -> DashboardData:
    response = requests.get(sheet_url, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"
    rows = pd.read_csv(StringIO(response.text), header=None).fillna("").values.tolist()
    specs = build_column_specs(rows)
    return DashboardData(
        totals=extract_totals(rows, specs),
        services=extract_services(rows, specs),
        subtotals=extract_subtotals(rows, specs),
        savings=extract_savings(rows),
        notes=extract_notes(rows),
    )


def format_currency(value: float) -> str:
    return f"$ {value:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_delta(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}$ {abs(value):,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_month_spanish(value: pd.Timestamp) -> str:
    return f"{MONTHS_FULL_ES[value.month]} {value.year}"


def kpi_snapshot(data: DashboardData) -> dict[str, object]:
    projected = data.totals[data.totals["metric"] == "Proyectado"].copy()
    actual = data.totals[data.totals["metric"] == "Facturado"].copy()
    merged = projected.merge(actual, on="month", suffixes=("_projected", "_actual"))
    latest_actual = merged["month"].max()
    actual_ytd = merged["value_actual"].sum()
    projected_ytd = merged["value_projected"].sum()
    variance = actual_ytd - projected_ytd
    latest_savings_month = data.savings["month"].max()
    latest_savings_value = (
        data.savings[
            (data.savings["concept"] == "Ahorro +/-") & (data.savings["month"] == latest_savings_month)
        ]["value"].sum()
        if not data.savings.empty
        else 0
    )
    return {
        "latest_actual": latest_actual,
        "actual_ytd": actual_ytd,
        "projected_ytd": projected_ytd,
        "variance": variance,
        "latest_savings_month": latest_savings_month,
        "latest_savings_value": latest_savings_value,
    }


def aggregate_filtered_totals(services: pd.DataFrame) -> pd.DataFrame:
    amount_rows = services[services["field"] == "Importe"].copy()
    amount_rows["value"] = pd.to_numeric(amount_rows["value"], errors="coerce")
    amount_rows = amount_rows.dropna(subset=["value"])
    return (
        amount_rows.groupby(["month", "metric"], as_index=False)["value"]
        .sum()
        .sort_values(["month", "metric"])
    )


def build_monthly_chart(totals: pd.DataFrame) -> go.Figure:
    if totals.empty:
        figure = go.Figure()
        figure.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        figure.add_annotation(
            text="No hay datos para los filtros seleccionados.",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(size=16, color="#64748B"),
        )
        return figure
    pivot = (
        totals.pivot_table(index="month", columns="metric", values="value", aggfunc="sum")
        .reset_index()
        .fillna(0)
    )
    pivot["label"] = pivot["month"].map(lambda value: value.strftime("%m-%y"))
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=pivot["label"],
            y=pivot["Proyectado"] if "Proyectado" in pivot.columns else [0] * len(pivot),
            name="Proyectado",
            marker_color="#A0AEC0",
        )
    )
    figure.add_trace(
        go.Bar(
            x=pivot["label"],
            y=pivot["Facturado"] if "Facturado" in pivot.columns else [0] * len(pivot),
            name="Facturado",
            marker_color="#0F766E",
        )
    )
    if "Diferencia (+/-)" in pivot.columns:
        figure.add_trace(
            go.Scatter(
                x=pivot["label"],
                y=pivot["Diferencia (+/-)"],
                name="Desv\u00edo",
                mode="lines+markers",
                marker_color="#C2410C",
                yaxis="y2",
            )
        )
    figure.update_layout(
        barmode="group",
        margin=dict(l=20, r=20, t=20, b=20),
        legend=dict(orientation="h", y=1.08),
        yaxis_title="Ingresos",
        yaxis2=dict(overlaying="y", side="right", title="Desv\u00edo"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Aptos, Segoe UI, sans-serif", color="#243447"),
    )
    return figure


def build_filtered_unit_chart(services: pd.DataFrame, month: pd.Timestamp) -> go.Figure:
    amount_rows = services[
        (services["field"] == "Importe") & (services["month"] == month) & (services["metric"] == "Facturado")
    ].copy()
    amount_rows["value"] = pd.to_numeric(amount_rows["value"], errors="coerce")
    latest = (
        amount_rows.dropna(subset=["value"])
        .groupby("business_unit", as_index=False)["value"]
        .sum()
        .sort_values("value", ascending=True)
    )
    if latest.empty:
        figure = go.Figure()
        figure.update_layout(
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        figure.add_annotation(
            text="Sin facturación para mostrar.",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(size=15, color="#64748B"),
        )
        return figure
    figure = px.bar(
        latest,
        x="value",
        y="business_unit",
        orientation="h",
        color="value",
        color_continuous_scale=["#D6E4F0", "#0F766E"],
        labels={"value": "Facturado", "business_unit": "Unidad"},
    )
    figure.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        coloraxis_showscale=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Aptos, Segoe UI, sans-serif", color="#243447"),
    )
    return figure


def build_service_variance_table(services: pd.DataFrame, month: pd.Timestamp) -> pd.DataFrame:
    amount_rows = services[(services["field"] == "Importe") & (services["month"] == month)].copy()
    amount_rows["value"] = pd.to_numeric(amount_rows["value"], errors="coerce")
    amount_rows = amount_rows.dropna(subset=["value"])
    pivot = amount_rows.pivot_table(
        index=["concept", "business_unit", "service_type"],
        columns="metric",
        values="value",
        aggfunc="sum",
    ).reset_index()
    for column in ["Proyectado", "Facturado", "Diferencia (+/-)"]:
        if column not in pivot.columns:
            pivot[column] = 0.0
        pivot[column] = pd.to_numeric(pivot[column], errors="coerce")
    pivot["Diferencia (+/-)"] = pivot["Diferencia (+/-)"].fillna(pivot["Facturado"] - pivot["Proyectado"])
    pivot["cumplimiento"] = pivot.apply(
        lambda row: row["Facturado"] / row["Proyectado"] if pd.notna(row["Proyectado"]) and row["Proyectado"] else None,
        axis=1,
    )
    return pivot.sort_values("Diferencia (+/-)")


def build_savings_chart(savings: pd.DataFrame) -> go.Figure:
    pivot = savings.pivot(index="month", columns="concept", values="value").reset_index()
    pivot["label"] = pivot["month"].dt.strftime("%b-%y")
    figure = go.Figure()
    for concept, color in [
        ("Servicio 2025", "#94A3B8"),
        ("Servicio 2026", "#0F766E"),
        ("Inversiones", "#F59E0B"),
        ("Ahorro +/-", "#B91C1C"),
    ]:
        if concept in pivot.columns:
            figure.add_trace(
                go.Scatter(
                    x=pivot["label"],
                    y=pivot[concept],
                    name=concept,
                    mode="lines+markers",
                    marker_color=color,
                )
            )
    figure.update_layout(
        margin=dict(l=20, r=20, t=20, b=20),
        legend=dict(orientation="h", y=1.1),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Aptos, Segoe UI, sans-serif", color="#243447"),
    )
    return figure


def render_metric_card(title: str, value: str, caption: str) -> None:
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-title">{title}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-caption">{caption}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_hero(selected_month_label: str, selected_units: list[str], selected_service_types: list[str]) -> None:
    unit_text = "todas las unidades" if not selected_units else f"{len(selected_units)} unidades"
    service_text = "todos los servicios" if not selected_service_types else f"{len(selected_service_types)} tipos de servicio"
    st.markdown(
        f"""
        <section class="hero-card">
            <p class="eyebrow">Panel ejecutivo de seguridad</p>
            <h1>Costos, desv\u00edos y ahorro a la vista</h1>
            <p class="hero-copy">
                Lectura de {selected_month_label}, con {unit_text} y {service_text}. 
                La idea es mirar r\u00e1pido d\u00f3nde estamos bien, d\u00f3nde se movi\u00f3 el gasto y qu\u00e9 conviene revisar.
            </p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_takeaway(title: str, text: str) -> None:
    st.markdown(
        f"""
        <div class="takeaway-card">
            <div class="takeaway-title">{title}</div>
            <div class="takeaway-text">{text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_checkbox_filter(title: str, options: list[str], key_prefix: str) -> list[str]:
    with st.sidebar.expander(title, expanded=False):
        select_all_key = f"{key_prefix}_all"
        manual_key = f"{key_prefix}_manual"
        use_all = st.checkbox("Incluir todo", value=True, key=select_all_key)
        if use_all:
            st.caption("Se incluyen todas las opciones.")
            return options

        st.caption("Marcá únicamente lo que querés ver.")
        selected = []
        for option in options:
            if st.checkbox(option, value=False, key=f"{manual_key}_{option}"):
                selected.append(option)
    return selected


def main() -> None:
    st.set_page_config(page_title="Indicadores de Seguridad", page_icon=":bar_chart:", layout="wide")
    st.markdown(
        """
        <style>
            .stApp {
                background:
                    radial-gradient(circle at top left, rgba(25,94,88,0.16), transparent 28%),
                    radial-gradient(circle at top right, rgba(190,124,62,0.12), transparent 24%),
                    linear-gradient(180deg, #F7F3EA 0%, #EDF3EF 100%);
                color: #243447;
                font-family: Aptos, Segoe UI, sans-serif;
            }
            [data-testid="stSidebar"] {
                background: linear-gradient(180deg, #E7EFEA 0%, #DCE7E1 100%);
                border-right: 1px solid rgba(36, 52, 71, 0.08);
            }
            [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
                color: #193D39;
            }
            .hero-card {
                background:
                    linear-gradient(135deg, rgba(24,74,68,0.96), rgba(40,103,91,0.9)),
                    radial-gradient(circle at top right, rgba(255,255,255,0.16), transparent 28%);
                border-radius: 28px;
                padding: 2rem 2.2rem;
                margin: 0.2rem 0 1.25rem 0;
                color: #F8FAFC;
                box-shadow: 0 24px 70px rgba(24, 74, 68, 0.22);
            }
            .hero-card h1 {
                font-size: clamp(2rem, 4vw, 3.6rem);
                line-height: 0.96;
                letter-spacing: -0.05em;
                margin: 0.1rem 0 0.85rem 0;
                color: #FFF8E8;
            }
            .hero-copy {
                max-width: 760px;
                color: #DCEDE6;
                font-size: 1.05rem;
                line-height: 1.6;
                margin: 0;
            }
            .eyebrow {
                text-transform: uppercase;
                letter-spacing: 0.18em;
                font-size: 0.78rem;
                color: #F2C97D;
                font-weight: 700;
                margin: 0;
            }
            .metric-card {
                background: rgba(255,252,245,0.88);
                border: 1px solid rgba(101,88,72,0.12);
                border-radius: 22px;
                padding: 1rem 1.1rem;
                box-shadow: 0 18px 40px rgba(36, 52, 71, 0.08);
                min-height: 138px;
            }
            .metric-title {
                color: #5F6F68;
                font-size: 0.95rem;
                margin-bottom: 0.7rem;
            }
            .metric-value {
                color: #193D39;
                font-size: 1.85rem;
                font-weight: 700;
                margin-bottom: 0.35rem;
            }
            .metric-caption {
                color: #6C7D76;
                font-size: 0.92rem;
            }
            .block-title {
                font-size: 1.15rem;
                font-weight: 700;
                color: #193D39;
                margin-top: 0.4rem;
            }
            .takeaway-card {
                background: rgba(255,252,245,0.88);
                border: 1px solid rgba(101,88,72,0.12);
                border-radius: 22px;
                padding: 1.15rem 1.25rem;
                box-shadow: 0 18px 40px rgba(36, 52, 71, 0.08);
                margin-bottom: 0.8rem;
            }
            .takeaway-title {
                color: #193D39;
                font-weight: 800;
                margin-bottom: 0.45rem;
            }
            .takeaway-text {
                color: #52655E;
                line-height: 1.55;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    data = load_dashboard_data(DEFAULT_SHEET_URL)
    snapshot = kpi_snapshot(data)
    available_months = sorted(data.services["month"].dropna().unique().tolist())
    default_month = snapshot["latest_actual"] if snapshot["latest_actual"] in available_months else available_months[-1]

    st.sidebar.title("Ajustar vista")
    selected_month = st.sidebar.selectbox(
        "Período",
        options=available_months,
        index=available_months.index(default_month),
        format_func=format_month_spanish,
    )
    business_unit_options = sorted(data.services["business_unit"].dropna().unique().tolist())
    selected_units = render_checkbox_filter("Empresas", business_unit_options, "unidad")
    service_type_options = sorted(data.services["service_type"].dropna().unique().tolist())
    selected_service_types = render_checkbox_filter("Servicios", service_type_options, "servicio")

    st.sidebar.caption(f"Empresas activas: {len(selected_units)}")
    st.sidebar.caption(f"Servicios activos: {len(selected_service_types)}")

    filtered_services = data.services[
        data.services["business_unit"].isin(selected_units) & data.services["service_type"].isin(selected_service_types)
    ].copy()
    filtered_totals = aggregate_filtered_totals(filtered_services)
    variance_table = build_service_variance_table(filtered_services, selected_month)

    month_totals = (
        filtered_totals[filtered_totals["month"] == selected_month].set_index("metric")["value"].to_dict()
        if not filtered_totals.empty
        else {}
    )
    selected_projected = float(month_totals.get("Proyectado", 0.0))
    selected_actual = float(month_totals.get("Facturado", 0.0))
    selected_variance = float(month_totals.get("Diferencia (+/-)", selected_actual - selected_projected))
    selected_attainment = (selected_actual / selected_projected) if selected_projected else 0.0
    selected_month_label = format_month_spanish(selected_month)

    render_hero(selected_month_label, selected_units, selected_service_types)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_metric_card("Facturación del mes", format_currency(selected_actual), f"Registrado en {selected_month_label}")
    with col2:
        render_metric_card(
            "Contra lo previsto",
            f"{selected_attainment:.1%}",
            f"Diferencia del mes {format_delta(selected_variance)}",
        )
    with col3:
        render_metric_card(
            "Plan del mes",
            format_currency(selected_projected),
            f"Presupuesto para {selected_month_label}",
        )
    with col4:
        render_metric_card(
            "Ahorro esperado",
            format_currency(snapshot["latest_savings_value"]),
            f"Último dato: {snapshot['latest_savings_month'].strftime('%m-%y')}",
        )

    left, right = st.columns([1.65, 1])
    with left:
        st.markdown('<div class="block-title">Cómo viene evolucionando</div>', unsafe_allow_html=True)
        st.plotly_chart(build_monthly_chart(filtered_totals), use_container_width=True)
    with right:
        st.markdown('<div class="block-title">Dónde se concentra el gasto</div>', unsafe_allow_html=True)
        st.plotly_chart(build_filtered_unit_chart(filtered_services, selected_month), use_container_width=True)

    negative = variance_table.nsmallest(8, "Diferencia (+/-)")[
        ["concept", "business_unit", "service_type", "Proyectado", "Facturado", "Diferencia (+/-)", "cumplimiento"]
    ].copy()
    positive = variance_table.nlargest(8, "Diferencia (+/-)")[
        ["concept", "business_unit", "service_type", "Proyectado", "Facturado", "Diferencia (+/-)", "cumplimiento"]
    ].copy()

    for frame in (negative, positive):
        frame["Proyectado"] = frame["Proyectado"].map(format_currency)
        frame["Facturado"] = frame["Facturado"].map(format_currency)
        frame["Diferencia (+/-)"] = frame["Diferencia (+/-)"].map(format_delta)
        frame["cumplimiento"] = frame["cumplimiento"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "-")
    display_columns = {
        "concept": "Servicio",
        "business_unit": "Empresa",
        "service_type": "Tipo",
        "cumplimiento": "Cumplimiento",
    }
    negative = negative.rename(columns=display_columns)
    positive = positive.rename(columns=display_columns)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="block-title">Para revisar</div>', unsafe_allow_html=True)
        st.dataframe(negative, use_container_width=True, hide_index=True)
    with c2:
        st.markdown('<div class="block-title">A favor del resultado</div>', unsafe_allow_html=True)
        st.dataframe(positive, use_container_width=True, hide_index=True)

    lower_left, lower_right = st.columns([1.15, 1])
    with lower_left:
        st.markdown('<div class="block-title">Ahorro vs esquema anterior</div>', unsafe_allow_html=True)
        st.plotly_chart(build_savings_chart(data.savings), use_container_width=True)
    with lower_right:
        st.markdown('<div class="block-title">Lectura rápida</div>', unsafe_allow_html=True)
        strongest_units = (
            filtered_services[
                (filtered_services["field"] == "Importe")
                & (filtered_services["metric"] == "Facturado")
                & (filtered_services["month"] == selected_month)
            ]
            .assign(value=lambda frame: pd.to_numeric(frame["value"], errors="coerce"))
            .dropna(subset=["value"])
            .groupby("business_unit", as_index=False)["value"]
            .sum()
            .sort_values("value", ascending=False)
        )
        strongest_unit = strongest_units.iloc[0] if not strongest_units.empty else None
        weakest_service = variance_table.iloc[0] if not variance_table.empty else None
        best_service = variance_table.iloc[-1] if not variance_table.empty else None
        render_takeaway(
            "Mayor peso del mes",
            (
                f"{strongest_unit['business_unit']} concentra la mayor facturación de {selected_month_label}: "
                f"{format_currency(strongest_unit['value'])}."
                if strongest_unit is not None
                else "No hay facturación disponible para los filtros seleccionados."
            ),
        )
        render_takeaway(
            "Atención recomendada",
            (
                f"El desvío más sensible aparece en {weakest_service['concept']}, "
                f"con {format_delta(weakest_service['Diferencia (+/-)'])}."
                if weakest_service is not None
                else "No hay desvíos disponibles para los filtros seleccionados."
            ),
        )
        render_takeaway(
            "Punto a favor",
            (
                f"El mejor desvío del mes viene de {best_service['concept']}, "
                f"con {format_delta(best_service['Diferencia (+/-)'])}."
                if best_service is not None
                else "No hay desvíos positivos disponibles para los filtros seleccionados."
            ),
        )
        st.caption(
            "Nota: el ahorro se lee desde el bloque comparativo de la hoja. La planilla indica revisar la serie desde marzo 2026 por el cierre de instalaciones."
        )
        if data.notes:
            with st.expander("Supuestos de lectura"):
                for note in data.notes:
                    st.caption(note)


if __name__ == "__main__":
    main()
