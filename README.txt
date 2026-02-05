Permettre le passage de filtres multi-sélection et multi-champs d’un dashboard Tableau Cloud A → dashboard B, sans limite d’URL

Architecture
Tableau Dashboard A
   │
   │ (Extension A)
   │ ── read filters → POST /contexts
   │
   ▼
API (FastAPI)
   │
   ├── write context → Snowflake
   └── return context_id
   │
   ▼
Navigation → Dashboard B?p_context_id=XYZ
   │
   │ (Extension B)
   │ ── read parameter → GET /contexts/{id}
   │ ── apply filters
   ▼
Tableau Dashboard B
