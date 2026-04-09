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
    pivot = (
        totals.pivot_table(index="month", columns="metric", values="value", aggfunc="sum")
        .reset_index()
        .fillna(0)
    )
    pivot["label"] = pivot["month"].dt.strftime("%b-%y")
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=pivot["label"],
            y=pivot["Proyectado"] if "Proyectado" in pivot.columns else 0,
            name="Proyectado",
            marker_color="#A0AEC0",
        )
    )
    figure.add_trace(
        go.Bar(
            x=pivot["label"],
            y=pivot["Facturado"] if "Facturado" in pivot.columns else 0,
            name="Facturado",
            marker_color="#0F766E",
        )
    )
    if "Diferencia (+/-)" in pivot.columns:
        figure.add_trace(
            go.Scatter(
                x=pivot["label"],
                y=pivot["Diferencia (+/-)"],
                name="Desvio",
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
        yaxis2=dict(overlaying="y", side="right", title="Desvio"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
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


def main() -> None:
    st.set_page_config(page_title="Indicadores de Seguridad", page_icon=":bar_chart:", layout="wide")
    st.markdown(
        """
        <style>
            .stApp {
                background:
                    radial-gradient(circle at top left, rgba(15,118,110,0.12), transparent 25%),
                    radial-gradient(circle at top right, rgba(194,65,12,0.08), transparent 20%),
                    linear-gradient(180deg, #F8FAFC 0%, #EEF2F7 100%);
            }
            .metric-card {
                background: rgba(255,255,255,0.82);
                border: 1px solid rgba(148,163,184,0.25);
                border-radius: 18px;
                padding: 1rem 1.1rem;
                box-shadow: 0 18px 40px rgba(15, 23, 42, 0.06);
                min-height: 138px;
            }
            .metric-title {
                color: #475569;
                font-size: 0.95rem;
                margin-bottom: 0.7rem;
            }
            .metric-value {
                color: #0F172A;
                font-size: 1.85rem;
                font-weight: 700;
                margin-bottom: 0.35rem;
            }
            .metric-caption {
                color: #64748B;
                font-size: 0.92rem;
            }
            .block-title {
                font-size: 1.15rem;
                font-weight: 700;
                color: #0F172A;
                margin-top: 0.4rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    data = load_dashboard_data(DEFAULT_SHEET_URL)
    snapshot = kpi_snapshot(data)
    available_months = sorted(data.services["month"].dropna().unique().tolist())
    default_month = snapshot["latest_actual"] if snapshot["latest_actual"] in available_months else available_months[-1]

    st.sidebar.title("Filtros")
    selected_month = st.sidebar.selectbox(
        "Mes de analisis",
        options=available_months,
        index=available_months.index(default_month),
        format_func=lambda value: value.strftime("%B %Y").title(),
    )
    business_unit_options = sorted(data.services["business_unit"].dropna().unique().tolist())
    selected_units = st.sidebar.multiselect(
        "Unidad de negocio",
        options=business_unit_options,
        default=business_unit_options,
    )
    service_type_options = sorted(data.services["service_type"].dropna().unique().tolist())
    selected_service_types = st.sidebar.multiselect(
        "Tipo de servicio",
        options=service_type_options,
        default=service_type_options,
    )

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
    selected_month_label = selected_month.strftime("%B %Y").title()

    st.title("Indicadores de Seguridad")
    st.caption(
        "Dashboard ejecutivo para accionistas con foco en facturacion, desvios operativos, mix por unidad y ahorro estimado."
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_metric_card("Facturacion filtrada", format_currency(selected_actual), f"Facturado en {selected_month_label}")
    with col2:
        render_metric_card(
            "Cumplimiento vs proyeccion",
            f"{selected_attainment:.1%}",
            f"Desvio del mes {format_delta(selected_variance)}",
        )
    with col3:
        render_metric_card(
            "Proyeccion filtrada",
            format_currency(selected_projected),
            f"Presupuesto para {selected_month_label}",
        )
    with col4:
        render_metric_card(
            "Ahorro esperado",
            format_currency(snapshot["latest_savings_value"]),
            f"Ultimo dato de ahorro: {snapshot['latest_savings_month'].strftime('%b-%y')}",
        )

    left, right = st.columns([1.65, 1])
    with left:
        st.markdown('<div class="block-title">Evolucion mensual</div>', unsafe_allow_html=True)
        st.plotly_chart(build_monthly_chart(filtered_totals), use_container_width=True)
    with right:
        st.markdown('<div class="block-title">Mix por unidad de negocio</div>', unsafe_allow_html=True)
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

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="block-title">Mayores desvios negativos</div>', unsafe_allow_html=True)
        st.dataframe(negative, use_container_width=True, hide_index=True)
    with c2:
        st.markdown('<div class="block-title">Mayores desvios positivos</div>', unsafe_allow_html=True)
        st.dataframe(positive, use_container_width=True, hide_index=True)

    lower_left, lower_right = st.columns([1.15, 1])
    with lower_left:
        st.markdown('<div class="block-title">Ahorro vs esquema anterior</div>', unsafe_allow_html=True)
        st.plotly_chart(build_savings_chart(data.savings), use_container_width=True)
    with lower_right:
        st.markdown('<div class="block-title">Lecturas para accionistas</div>', unsafe_allow_html=True)
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
        bullets = [
            (
                f"En {selected_month_label}, la unidad con mayor facturacion fue {strongest_unit['business_unit']} "
                f"con {format_currency(strongest_unit['value'])}."
                if strongest_unit is not None
                else "No hay facturacion disponible para los filtros seleccionados."
            ),
            (
                f"El mayor desvio negativo del mes fue {weakest_service['concept']} "
                f"con {format_delta(weakest_service['Diferencia (+/-)'])}."
                if weakest_service is not None
                else "No hay desvios disponibles para los filtros seleccionados."
            ),
            (
                f"El principal desvio positivo fue {best_service['concept']} "
                f"con {format_delta(best_service['Diferencia (+/-)'])}."
                if best_service is not None
                else "No hay desvios positivos disponibles para los filtros seleccionados."
            ),
            "El calculo de ahorro toma el bloque comparativo provisto en la hoja y conviene leerlo desde marzo 2026 por la nota operativa de cierre de instalaciones.",
        ]
        for bullet in bullets:
            st.markdown(f"- {bullet}")
        if data.notes:
            st.markdown("**Notas de origen**")
            for note in data.notes:
                st.caption(note)


if __name__ == "__main__":
    main()
