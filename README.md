# 🌤️ Poly_Weather — Polymarket Weather Mispricing Bot

> **Hybrid weather + copy-trade bot** para Polymarket. Detecta mispricing en mercados de clima usando datos meteorológicos reales (Open-Meteo GFS ensemble + históricos) vs precios de Polymarket. Validación IA opcional con Ollama local.

## ✨ Features

- **Weather Strategy** — Calcula probabilidades reales de eventos climáticos con modelos GFS ensemble + 5 años de datos históricos
- **15 ciudades target** — Madrid, Berlín, Sydney, Singapur, CDMX, Varsovia, Atenas, Lisboa, Estambul, Buenos Aires, Praga, Budapest, Viena, Dublín, Helsinki
- **Detección de mispricing** — Solo opera cuando la diferencia real vs Polymarket es >15%
- **IA Validator** — Ollama (phi3:mini / qwen2:7b) valida cada señal antes de operar
- **Paper Trading** — Modo simulación seguro por defecto con PnL tracking
- **Shadow Mode** — Registra TODAS las señales en CSV para análisis post-hoc
- **Risk Manager** — Max 3% capital/trade, min $30K liquidez, min 24h resolución
- **Half-Kelly Sizing** — Tamaño óptimo de posición con Kelly criterion
- **Analysis Script** — Sharpe ratio, max drawdown, calibración, breakdown por ciudad

## 🚀 Quick Start (Paper Trading)

### 1. Clonar e instalar

```bash
git clone https://github.com/anderr04/Poly_Weather.git
cd Poly_Weather

# Crear virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Instalar dependencias
pip install -r requirements.txt
```

### 2. Configurar

```bash
# Copiar template de env
cp .env.example .env

# Editar con tu configuración (paper mode por defecto)
# No necesitas API keys para paper trading
```

### 3. Verificar APIs

```bash
python main.py --dry-run
```

Esto verifica:
- ✅ Open-Meteo Ensemble API
- ✅ Open-Meteo Historical API
- ✅ Polymarket Gamma API
- ✅ Ollama (si `VALIDATOR_ENABLED=true`)

### 4. Ejecutar

```bash
# Single scan (para probar)
python main.py --once

# Loop continuo (escanea cada 30 min)
python main.py

# Custom interval (cada 60 min)
python main.py --interval 60
```

### 5. Analizar resultados

```bash
# Ver reporte
python analysis.py

# Con plots
python analysis.py --plot

# Esperar al menos 50 trades
python analysis.py --min-trades 50
```

## 🧠 IA Validator (Ollama)

### Instalar Ollama

```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows: descargar de https://ollama.com/download
```

### Pull modelo

```bash
# Ligero (3.8 GB, bueno para VMs)
ollama pull phi3:mini

# Más potente (4.4 GB, mejor resultados)
ollama pull qwen2:7b
```

### Activar en .env

```env
VALIDATOR_ENABLED=true
VALIDATOR_MODEL=phi3:mini
VALIDATOR_OLLAMA_URL=http://localhost:11434
```

## 📊 Shadow Mode

Todos los signals se guardan en `data/shadow_trades.csv` con:
- Fuente (weather / whale_copy)
- Ciudad
- Probabilidad del forecast vs precio Polymarket
- Resultado del validador IA
- Side (BUY_YES / BUY_NO / SKIP)
- Kelly size, liquidez, resolución
- Ensemble members, base rate histórico
- Latencia

### Columnas para tracking/ajuste:

| Columna | Descripción |
|---------|------------|
| `forecast_probability` | Nuestra probabilidad calculada |
| `poly_price` | Precio en Polymarket |
| `mispricing` | forecast - poly (>0.15 = oportunidad) |
| `model_probability` | Opinión del validador IA |
| `model_confidence` | Confianza del modelo (0-100) |
| `kelly_size` | Tamaño óptimo de posición |
| `ensemble_members_yes` | Miembros del ensemble a favor |
| `historical_base_rate` | Tasa base en últimos 5 años |

## 🔒 Safeguards

| Control | Valor por defecto |
|---------|-------------------|
| Max capital por trade | 3% |
| Liquidez mínima | $30,000 |
| Tiempo mínimo a resolución | 24 horas |
| Max pérdida diaria | 5% |
| Max exposición total | 30% |
| Max posiciones abiertas | 10 |
| Kelly fraction | 0.5 (half-Kelly) |

## 📁 Estructura del Proyecto

```
Poly_Weather/
├── main.py                      # Entry point
├── config.py                    # Configuración central
├── analysis.py                  # Análisis de resultados
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── DEPLOY_GCLOUD.md             # Deploy en Google Cloud
├── data/                        # CSVs y logs (gitignored)
├── src/
│   ├── polymarket_api.py        # Cliente Polymarket
│   ├── paper_trader.py          # Simulador paper trading
│   ├── probability_validator.py # Validador IA (Ollama)
│   ├── risk_manager.py          # Safeguards
│   └── shadow_logger.py         # CSV logger
└── strategies/
    └── weather/
        ├── weather_strategy.py  # Estrategia principal
        ├── open_meteo.py        # Cliente Open-Meteo
        └── market_scanner.py    # Scanner de mercados
```

## ⚙️ Variables de Entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `PAPER_MODE` | `true` | Paper trading (safe) |
| `INITIAL_CAPITAL` | `100` | Capital inicial ($) |
| `VALIDATOR_ENABLED` | `true` | Toggle validador IA |
| `VALIDATOR_MODEL` | `phi3:mini` | Modelo Ollama |
| `WEATHER_MISPRICING_THRESHOLD` | `0.15` | Umbral mispricing |
| `MAX_CAPITAL_PER_TRADE_PCT` | `0.03` | Max % capital/trade |
| `MIN_LIQUIDITY_USD` | `30000` | Liquidez mínima |

## 📈 Métricas de Rendimiento

El script `analysis.py` calcula:
- **Win Rate** — con y sin filtro IA
- **PnL total** — simulado
- **Sharpe Ratio** — annualizado
- **Max Drawdown** — peak-to-trough
- **Profit Factor** — gross profit / gross loss
- **Calibration** — probabilidad predicha vs outcome real
- **City breakdown** — rendimiento por ciudad
- **% Losses blocked** — pérdidas evitadas por el validador IA

## 📄 License

MIT — úsalo bajo tu propia responsabilidad. Este bot es para fines educativos y de investigación. No es asesoramiento financiero.
