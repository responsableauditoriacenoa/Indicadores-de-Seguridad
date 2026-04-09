# Indicadores de Seguridad

App web en Streamlit para visualizar KPIs ejecutivos a partir de la hoja de cálculo de seguridad compartida por Google Sheets.

## Qué muestra

- Facturación real acumulada vs proyección.
- Cumplimiento presupuestario y desvío acumulado.
- Run rate anual estimado.
- Mix de facturación por unidad de negocio.
- Mayores desvíos positivos y negativos por servicio.
- Evolución del ahorro comparado contra el esquema anterior.

## Ejecución local

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Fuente de datos

La app toma por defecto el CSV exportado desde:

`https://docs.google.com/spreadsheets/d/1o1CL6U57o9pYOS1ce9vUn7yIm45GIdrH/export?format=csv`

Si la hoja cambia de pestaña o de formato, se puede reemplazar la URL desde el panel lateral.

## Deploy en Streamlit

1. Subir este proyecto al repositorio `responsableauditoriacenoa/Indicadores-de-Seguridad`.
2. Crear la app en Streamlit Community Cloud.
3. Elegir el repositorio y como archivo principal `app.py`.
4. Confirmar la instalación de `requirements.txt`.

## Observaciones de negocio

- La hoja contiene notas operativas que indican actualización trimestral por inflación.
- También indica descartar enero y febrero 2026 para ciertas lecturas, porque las instalaciones finalizaron el 28/02.
