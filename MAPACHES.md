# Guía de mantenimiento: Mapaches

## Corregir foto o casa de un mapache

Editar los 3 archivos siguientes, luego `make deploy`.

### 1. `src/knowledge_base/avatars.json`
Agregar o corregir la key con el nombre completo en minúsculas (con y sin tilde si aplica):
```json
"brissy cáceres": "brissy.jpg",
"brissy caceres": "brissy.jpg"
```
El archivo de foto debe existir en `frontend/mapache-fotos/`.

### 2. `src/rag.py` — dict `_CASAS`
Agregar el nombre (lowercase, con y sin tilde) → casa:
```python
"brissy cáceres": "Chavin", "brissy caceres": "Chavin",
```
Casas válidas: `Chavin`, `Wari`, `Moche`, `Nazca`.

### 3. `scripts/generate_ranking.py` — dict `_CASAS`
Mismo cambio que en `rag.py` (los dos dicts deben estar sincronizados).

### 4. Deploy
```bash
make deploy
```
El deploy regenera `frontend/ranking.json` automáticamente antes de subir.

---

## Agregar un mapache nuevo al CSV

Editar `Mapaches-badges.csv` (raíz del repo) y también `src/knowledge_base/mapaches_badges.csv` (copia bundleada con el Lambda). Luego seguir los pasos de foto y casa de arriba.

> **Nota:** `src/knowledge_base/mapaches_badges.csv` es la copia que usa el Lambda. Si solo editas la raíz, el chatbot no verá el cambio hasta que copies el archivo y hagas deploy.

---

## Archivos involucrados (referencia rápida)

| Archivo | Propósito |
|---|---|
| `Mapaches-badges.csv` | Fuente original de badges (editar aquí primero) |
| `src/knowledge_base/mapaches_badges.csv` | Copia bundleada con Lambda (mantener en sync) |
| `src/knowledge_base/avatars.json` | Nombre → archivo de foto |
| `src/knowledge_base/badge_name_map.json` | Equivalencias de nombres CSV → KB |
| `src/rag.py` | `_CASAS` (casa por mapache) y lógica del chatbot |
| `scripts/generate_ranking.py` | `_CASAS` (igual que rag.py) + generación de ranking.json |
| `frontend/mapache-fotos/` | Fotos de mapaches (JPG/PNG) |
| `frontend/ranking.json` | Generado automáticamente por `make deploy` — no editar a mano |
