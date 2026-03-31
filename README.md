# EthMBus SMART – Docker Stack

Kompletný stack pre zber a sprístupnenie dát z EthMBus-XL SMART prevodníka.

```
┌─────────────────────────────────────┐
│         docker-compose stack        │
│                                     │
│  ┌──────────┐    ┌───────────────┐  │
│  │  poller  │───▶│  PostgreSQL   │  │
│  │ (Python) │    │   (port 5432) │  │
│  └──────────┘    └──────┬────────┘  │
│                         │           │
│  ┌──────────┐           │           │
│  │   api    │◀──────────┘           │
│  │(FastAPI) │                       │
│  │ :8000    │                       │
│  └──────────┘                       │
└─────────────────────────────────────┘
         ▲
         │ HTTP /mbus.xml
┌────────┴────────┐
│  EthMBus-XL     │
│  SMART          │
└─────────────────┘
```

## Rýchly štart

```bash
# 1. Skopíruj a uprav konfiguráciu
cp .env.example .env
nano .env          # nastav MBUS_IP a POSTGRES_PASSWORD

# 2. Spusti celý stack
docker compose up -d --build

# 3. Skontroluj logy
docker compose logs -f
```

API bude dostupné na http://localhost:8000
Swagger docs: http://localhost:8000/docs

## Konfigurácia (.env)

| Premenná            | Popis                                  | Default        |
|---------------------|----------------------------------------|----------------|
| `MBUS_IP`           | IP adresa EthMBus prevodníka           | 192.168.1.100  |
| `MBUS_POLL`         | Interval pollingu v sekundách          | 5              |
| `MBUS_TIMEOUT`      | HTTP timeout v sekundách               | 4              |
| `POSTGRES_DB`       | Názov databázy                         | mbus           |
| `POSTGRES_USER`     | DB používateľ                          | mbus           |
| `POSTGRES_PASSWORD` | DB heslo                               | *povinné*      |
| `API_PORT`          | Port API na hostiteľovi                | 8000           |
| `LOG_LEVEL`         | Úroveň logovania (DEBUG/INFO/WARNING)  | INFO           |

## API Endpointy

| Metóda | Endpoint                                          | Popis                               |
|--------|---------------------------------------------------|-------------------------------------|
| GET    | `/`                                               | Status                              |
| GET    | `/health`                                         | Health check (vrátane DB)           |
| GET    | `/latest`                                         | Posledné hodnoty všetkých meračov   |
| GET    | `/converters`                                     | Zoznam prevodníkov                  |
| GET    | `/converters/{id}/stats`                          | Štatistiky prevodníka               |
| GET    | `/converters/{id}/meters`                         | Merače na prevodníku                |
| GET    | `/meters/{id}/readouts`                           | História readoutov                  |
| GET    | `/meters/{id}/values/latest`                      | Posledné hodnoty meradla            |
| GET    | `/meters/{id}/values/history?name=Energy`         | Časový rad jednej veličiny          |
| GET    | `/readouts/{id}`                                  | Konkrétny readout + hodnoty         |

### Príklad – posledné hodnoty

```bash
curl http://localhost:8000/latest | python3 -m json.tool
```

### Príklad – história spotreby energie

```bash
curl "http://localhost:8000/meters/1/values/history?name=Energy&limit=100"
```

## Zastavenie stacku

```bash
docker compose down          # zastaví, dáta ostanú
docker compose down -v       # zastaví + zmaže DB volume
```
