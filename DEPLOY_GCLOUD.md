# 🚀 Deploy Poly_Weather en Google Cloud

## Opción 1: VM e2-medium (4 GB RAM) — Sin Ollama

Más económica. El bot opera solo con pronósticos meteorológicos.

```bash
# 1. Crear VM
gcloud compute instances create poly-weather-bot \
    --machine-type=e2-medium \
    --zone=us-central1-a \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=30GB \
    --tags=poly-weather

# 2. SSH a la VM
gcloud compute ssh poly-weather-bot --zone=us-central1-a
```

### En la VM:

```bash
# Actualizar sistema
sudo apt update && sudo apt upgrade -y

# Instalar Python 3.11+
sudo apt install -y python3.11 python3.11-venv python3-pip git tmux

# Clonar repo
git clone https://github.com/anderr04/Poly_Weather.git
cd Poly_Weather

# Virtual environment
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configurar .env
cp .env.example .env
nano .env
# CAMBIAR:
#   PAPER_MODE=true
#   INITIAL_CAPITAL=100
#   VALIDATOR_ENABLED=false   ← desactivar IA en e2-medium

# Dry run
python main.py --dry-run

# Ejecutar en tmux
tmux new -s polyweather
python main.py
# Ctrl+B, D para detach

# Reconectar
tmux attach -t polyweather
```

---

## Opción 2: VM e2-standard-2 (8 GB RAM) — Con Ollama

Más potente. Incluye validador IA con Ollama.

```bash
# 1. Crear VM
gcloud compute instances create poly-weather-bot \
    --machine-type=e2-standard-2 \
    --zone=us-central1-a \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=50GB \
    --tags=poly-weather

# 2. SSH
gcloud compute ssh poly-weather-bot --zone=us-central1-a
```

### En la VM:

```bash
# Actualizar sistema
sudo apt update && sudo apt upgrade -y

# Instalar Python 3.11+ y dependencias
sudo apt install -y python3.11 python3.11-venv python3-pip git tmux curl

# ── Instalar Ollama ──
curl -fsSL https://ollama.com/install.sh | sh

# Arrancar Ollama en background
ollama serve &
sleep 5

# Pull modelo (phi3:mini = 3.8 GB, ~3 min de descarga)
ollama pull phi3:mini

# Verificar
ollama list
# Debería mostrar: phi3:mini

# ── Instalar Bot ──
git clone https://github.com/anderr04/Poly_Weather.git
cd Poly_Weather

python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configurar .env
cp .env.example .env
nano .env
# CAMBIAR:
#   PAPER_MODE=true
#   INITIAL_CAPITAL=100
#   VALIDATOR_ENABLED=true    ← activar IA
#   VALIDATOR_MODEL=phi3:mini

# Dry run
python main.py --dry-run

# Ejecutar en tmux (dos ventanas: ollama + bot)
tmux new -s polyweather

# Ventana 1: Ollama
ollama serve

# Ctrl+B, C para nueva ventana
# Ventana 2: Bot
cd ~/Poly_Weather
source venv/bin/activate
python main.py

# Ctrl+B, D para detach
```

---

## Opción 3: Systemd Services (Producción)

Para que el bot se reinicie automáticamente si se cae.

### Ollama service (ya se instala automáticamente)

```bash
# Verificar que ollama está como servicio
sudo systemctl status ollama
sudo systemctl enable ollama
```

### Bot service

```bash
# Crear service file
sudo tee /etc/systemd/system/poly-weather.service << 'EOF'
[Unit]
Description=Poly_Weather Polymarket Bot
After=network.target ollama.service
Wants=ollama.service

[Service]
Type=simple
User=your_username
WorkingDirectory=/home/your_username/Poly_Weather
Environment=PATH=/home/your_username/Poly_Weather/venv/bin:/usr/bin
ExecStart=/home/your_username/Poly_Weather/venv/bin/python main.py
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF

# Reemplazar 'your_username' con tu usuario
sudo sed -i "s/your_username/$USER/g" /etc/systemd/system/poly-weather.service

# Activar
sudo systemctl daemon-reload
sudo systemctl enable poly-weather
sudo systemctl start poly-weather

# Ver logs
sudo journalctl -u poly-weather -f
```

---

## Monitoreo

```bash
# Estado del bot
sudo systemctl status poly-weather

# Logs en tiempo real
sudo journalctl -u poly-weather -f

# Shadow trades
cat ~/Poly_Weather/data/shadow_trades.csv | wc -l

# Análisis rápido
cd ~/Poly_Weather
source venv/bin/activate
python analysis.py

# Análisis con plots
python analysis.py --plot

# Ver log del bot
tail -f data/bot.log
```

## Costes estimados

| VM | RAM | CPU | Coste/mes | Ollama | Uso |
|---|---|---|---|---|---|
| e2-medium | 4 GB | 1-2 vCPU | ~$25/mes | ❌ | Solo weather |
| e2-standard-2 | 8 GB | 2 vCPU | ~$50/mes | ✅ | Weather + IA |

> **Tip**: Puedes empezar con e2-medium sin Ollama para validar que el bot genera señales rentables, y luego upgrade a e2-standard-2 para añadir el validador IA.

## Actualizar

```bash
cd ~/Poly_Weather
git pull origin main
pip install -r requirements.txt
sudo systemctl restart poly-weather
```
