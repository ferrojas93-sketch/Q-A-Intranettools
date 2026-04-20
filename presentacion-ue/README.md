# Presentación: La UE para jóvenes

Presentación HTML de 20 minutos dirigida a alumnos de FP Grado Medio (16–18 años) sobre la Unión Europea y las oportunidades para jóvenes (DiscoverEU, Erasmus+ FP, European Solidarity Corps).

Stack: **reveal.js 5** + Chart.js + CSS custom (dark mode, paleta UE azul/amarillo + acentos gaming).

## Cómo presentar

1. Abre `index.html` en Chrome o Firefox (doble clic o servir con `python3 -m http.server`).
2. Navega con `→ / ← / Espacio`. Tecla `F` para fullscreen.
3. Tecla `S` abre el **panel de speaker notes** (ventana aparte).
4. El quiz es clicable: pulsa la respuesta y se revela la correcta + confetti si aciertas.

## Contenido — 30 slides, 20 min

| # | Bloque | Slides |
|---|---|---|
| 0 | Portada + plan | 1–2 |
| 1 | Quiz rompehielo | 3–8 |
| 2 | Qué es la UE | 9–12 |
| 3 | Quién paga la UE | 13–14 |
| 4 | Beneficios para TI | 15–17 |
| 5 | Instituciones + cómo se hace una ley | 18–19 |
| 6 | Oportunidades (DiscoverEU, Erasmus+, Solidarity) | 20–24 |
| 7 | Boss fight quiz + cierre | 25–30 |

## Exportar a PDF

### Opción A — desde el navegador (recomendado)
1. Abre `index.html?print-pdf` en Chrome.
2. `Ctrl+P` → "Guardar como PDF" → orientación **horizontal** → márgenes `Ninguno` → activa "Gráficos de fondo".

### Opción B — con decktape (CLI)
```bash
./export-pdf.sh
```
Requiere Node.js. Descarga decktape vía `npx`.

## Handout

`handout.html` es una hoja A4 imprimible (resumen + QRs) para que los alumnos se lleven. Imprime con Ctrl+P desde el navegador.

## Estructura

```
presentacion-ue/
├── index.html              # Presentación principal
├── handout.html            # Hoja A4 imprimible
├── styles/
│   ├── custom.css          # Tokens de diseño UE
│   └── handout.css         # Estilos print A4
├── scripts/
│   ├── quiz.js             # Lógica quiz interactivo + confetti
│   └── charts.js           # Chart.js (donut presupuesto UE)
├── assets/
│   ├── qr/                 # QRs a DiscoverEU, Erasmus+, Solidarity, Youth Portal
│   ├── flags/              # (placeholder — SVGs de banderas si hacen falta)
│   └── icons/              # (placeholder — iconos Lucide SVG)
├── export-pdf.sh           # Helper export a PDF
└── README.md               # Este archivo
```

## Créditos

- Contenido oficial UE: <https://op.europa.eu/webpub/com/short-guide-eu/en/about-eu.html>
- DiscoverEU: <https://youth.europa.eu/discovereu_en>
- Erasmus+ FP: <https://erasmus-plus.ec.europa.eu>
- European Solidarity Corps: <https://youth.europa.eu/solidarity_en>
