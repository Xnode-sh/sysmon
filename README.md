# sysmon — System Health Monitor with Predictive Analytics

Автономный монитор состояния Android-устройства: батарея, CPU, память, сеть, диск + ML-предсказания (время до разряда, тепловой тренд) и алерты. Никаких рутов, работает в фоне через termux-services.

## Метрики (каждые 10с)
| Метрика | Источник |
|---------|----------|
| Батарея: %, °C, мА, мВ, статус | `termux-battery-status` |
| CPU: текущие частоты ядер | `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq` |
| Память: total/available | `/proc/meminfo` |
| Сеть: RX/TX байты | `/proc/net/dev` |
| Load average | `/proc/loadavg` |
| Диск: free space | `df /data` |

## Предсказания (на лету)
- **Battery ETA** — скользящее среднее дропа % за последние 60с → минуты до 0%
- **Thermal trend** — тренд температуры за 60с → rising/stable/critical

## Алерты (авто)
| Триггер | Условие |
|---------|---------|
| `thermal` | > 45°C |
| `battery_low` | < 15% не заряжаясь |
| `battery_drain` | > 3%/10с |
| `memory` | < 10% free RAM |
| `load` | load1 > 6 |

## Использование

```bash
# Зависимости
pkg install python termux-api

# Запуск
python3 ~/sysmon/sysmon.py          # интерактивный дашборд (TTY)
python3 ~/sysmon/sysmon.py history 24   # история за 24ч
python3 ~/sysmon/sysmon.py alerts       # неквитированные алерты

# Автозапуск (termux-services)
ln -sf ~/sysmon ~/.termux/boot/sysmon
```

## Дашборд (интерактивный)
```
╔═══ SYSMON ═══════════════════════════════════════════════════╗
║  🔋  37%  33.0°C  DISCHARGING  ⚡ 1987/2000MHz  📊 2.50  ║
║  🧠 1464MB/7462MB  🌐 ▼19.5KB/s ▲ 9.8KB/s  ║
║  🔮 Battery: ~450min  🌡 Stable  ║
╠════════════════════════════════════════════════════════════════╣
║  ✅ No alerts
╚════════════════════════════════════════════════════════════════╝
```

## Данные
- SQLite: `~/sysmon/sysmon.db` (таблицы `metrics`, `alerts`)
- Логи: `~/sysmon/logs/sysmon.log`
- Экспорт: `python3 -c "import sqlite3; ..."`

## Требования
- Termux + Termux:API
- Python 3.10+
- Android 10+ (thermal zones могут быть недоступны без root)