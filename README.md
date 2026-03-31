# EthMBus SMART – Docker Stack

Kompletný stack pre zber a sprístupnenie dát z EthMBus-XL SMART prevodníka v TCP/IP móde (transparentné smerovanie M-Bus pakietov).

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
         │ TCP/IP REQ_UD2 (M-Bus frames)
┌────────┴────────┐
│  EthMBus-XL     │
│  (TCPIP mode)   │
└─────────────────┘
```

## Rýchly štart

```bash
# 1. Uprav konfiguráciu v docker-compose.yml
#    (MBUS_IP, MBUS_ADDRESSES, POSTGRES_PASSWORD)
nano docker-compose.yml

# 2. Spusti celý stack
docker compose up -d --build

# 3. Skontroluj logy
docker compose logs -f
```

API bude dostupné na http://localhost:8000
Swagger docs: http://localhost:8000/docs

## Konfigurácia (docker-compose.yml)

Všetky nastavenia sú priamo v `docker-compose.yml` sekcii `environment:` poddienstva `poller`:

| Premenná            | Popis                                  | Default        |
|---------------------|----------------------------------------|----------------|
| `MBUS_IP`           | IP adresa EthMBus prevodníka           | 192.168.1.154  |
| `MBUS_PORT`         | TCP port prevodníka                    | 9999           |
| `MBUS_ADDRESSES`    | Čiarkou oddelené M-Bus adresy          | 1,2            |
| `MBUS_POLL`         | Interval pollingu v sekundách          | 3              |
| `MBUS_TIMEOUT`      | TCP socket timeout v sekundách        | 5              |
| `POSTGRES_DB`       | Názov databázy                         | mbus           |
| `POSTGRES_USER`     | DB používateľ                          | mbus           |
| `POSTGRES_PASSWORD` | DB heslo                               | *zmeniť!*      |
| `LOG_LEVEL`         | Úroveň logovania (DEBUG/INFO/WARNING)  | INFO           |

### Príklad – zmena konfigurácie

```yaml
environment:
  MBUS_IP:        192.168.1.154
  MBUS_PORT:      9999
  MBUS_ADDRESSES: 1,2,3          # pridať ďalšie adresy
  MBUS_POLL:      3              # poll každé 3 sekundy
  MBUS_TIMEOUT:   5
  DB_DSN:         postgresql://mbus:change_me_please@db:5432/mbus
  LOG_LEVEL:      INFO
```

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

## Grafana integrácia

API je kompatibilné s Grafana HTTP datasource pluginom. Dáta sa aktualizujú každých 3 sekúnd (podľa `MBUS_POLL`).

**Endpoints pre Grafana:**
- **Gauge (posledná hodnota):** `/meters/{id}/values/latest`
- **Time series (história):** `/meters/{id}/values/history?name=Energy&limit=200`
- **Status (statistiky):** `/converters/{id}/stats`
