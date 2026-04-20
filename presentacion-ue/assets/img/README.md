# Imágenes para la presentación

Cae aquí las imágenes (JPG/PNG/WebP) y descoméntalas en `index.html` / `handout.html`
(busca `<!-- IMG: ... -->`).

## Imágenes sugeridas (con prompts para nano banana / Gemini)

Estilo global: **dark editorial cinematográfico**, paleta azul UE (#003399) + amarillo
(#FFCC00) + acentos neón, aspecto 16:9, alta calidad, sin texto superpuesto.

| Archivo | Slide | Prompt sugerido |
|---|---|---|
| `portada.jpg` | 1 — Portada | "Cinematic editorial photo of young Spanish students (16-18yo) with backpacks at a European train station at golden hour, stylized EU flag stars glowing in background, dark moody tones, film grain, 16:9" |
| `mapa-ue.png` | 11 — El mapa | "Stylized minimalist map of Europe, 27 EU countries highlighted in deep blue #003399, Schengen-only countries in cyan glow, dark navy background, flat vector illustration, 16:9" |
| `discovereu.jpg` | 21 — DiscoverEU | "Cinematic shot of a modern European passenger train crossing a scenic alpine landscape at sunset, warm golden light, dramatic clouds, editorial photography style, 16:9" |
| `erasmus-fp.jpg` | 22 — Erasmus+ FP | "Young European student/intern (late teens) in a modern Berlin co-working office, laptop open, colleagues in background, warm window light, editorial photography, 16:9" |
| `solidarity.jpg` | 23 — Solidarity Corps | "Diverse group of young European volunteers planting trees in a sunny forest, genuine smiles, wearing outdoor clothing, documentary photography, natural light, 16:9" |
| `boss-bg.jpg` | 25 — Boss fight | "Retro arcade neon gaming aesthetic background, dark purple gradient, pixelated European castle silhouette, pink and cyan glow, 80s synthwave, 16:9" |
| `gracias.jpg` | 30 — Gracias | "Night skyline collage of European capitals (Madrid, Berlin, Paris, Rome) blended with EU flag stars pattern overhead, editorial dark cinematic tone, 16:9" |

## Cómo añadirlas

1. Genera las imágenes.
2. Guárdalas en esta carpeta con el nombre exacto de la tabla.
3. Abre `index.html` y busca los comentarios `<!-- IMG: ... -->`.
4. Descomenta el `<img>` correspondiente.

Cada `<img>` ya tiene la clase CSS lista (`slide-image`, `hero-bg` o `framed`).
