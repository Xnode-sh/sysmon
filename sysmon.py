#!/data/data/com.termux/files/usr/bin/python3
import sqlite3
import subprocess
import time
import json
import os
import sys
import statistics
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque

DB_PATH = Path.home() / "sysmon" / "sysmon.db"
INTERVAL = 10

def read_battery():
    try:
        out = subprocess.run(["termux-battery-status"], capture_output=True, text=True, timeout=5)
        return json.loads(out.stdout)
    except Exception:
        return {}

def read_cpu_freq():
    freqs = {}
    try:
        base = Path("/sys/devices/system/cpu")
        for cpu_dir in base.glob("cpu[0-9]*"):
            cur = cpu_dir / "cpufreq" / "scaling_cur_freq"
            if cur.exists():
                freqs[cpu_dir.name] = int(cur.read_text().strip()) // 1000
    except Exception:
        pass
    return freqs

def read_meminfo():
    mem = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if ':' in line:
                    k, v = line.split(':', 1)
                    mem[k.strip()] = int(v.strip().split()[0])
    except Exception:
        pass
    return mem

def read_net_dev():
    stats = {"rx": 0, "tx": 0}
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if ':' in line:
                    parts = line.split()
                    if len(parts) >= 10:
                        stats["rx"] += int(parts[1])
                        stats["tx"] += int(parts[9])
    except Exception:
        pass
    return stats

def read_loadavg():
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            return {"load1": float(parts[0]), "load5": float(parts[1]), "load15": float(parts[2])}
    except Exception:
        return {}

def read_disk():
    try:
        out = subprocess.run(["df", "-k", "/data"], capture_output=True, text=True, timeout=5)
        for line in out.stdout.strip().split('\n')[1:]:
            parts = line.split()
            if len(parts) >= 4:
                return {"total_kb": int(parts[1]), "used_kb": int(parts[2]), "avail_kb": int(parts[3])}
    except Exception:
        pass
    return {}

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                bat_pct INTEGER,
                bat_temp REAL,
                bat_current INTEGER,
                bat_voltage INTEGER,
                bat_status TEXT,
                cpu_avg_mhz REAL,
                cpu_max_mhz INTEGER,
                mem_total_kb INTEGER,
                mem_avail_kb INTEGER,
                net_rx_bytes INTEGER,
                net_tx_bytes INTEGER,
                load1 REAL,
                load5 REAL,
                load15 REAL,
                disk_avail_kb INTEGER
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(timestamp)")

        con.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                acknowledged INTEGER DEFAULT 0
            )
        """)

def save_metrics(data):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            INSERT INTO metrics (timestamp, bat_pct, bat_temp, bat_current, bat_voltage, bat_status,
                                 cpu_avg_mhz, cpu_max_mhz, mem_total_kb, mem_avail_kb,
                                 net_rx_bytes, net_tx_bytes, load1, load5, load15, disk_avail_kb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            data.get("bat_pct"), data.get("bat_temp"), data.get("bat_current"), data.get("bat_voltage"), data.get("bat_status"),
            data.get("cpu_avg_mhz"), data.get("cpu_max_mhz"),
            data.get("mem_total_kb"), data.get("mem_avail_kb"),
            data.get("net_rx"), data.get("net_tx"),
            data.get("load1"), data.get("load5"), data.get("load15"),
            data.get("disk_avail_kb")
        ))

def check_alerts(data, prev_data):
    alerts = []
    now = datetime.now().isoformat()

    if data.get("bat_temp", 0) > 45:
        alerts.append(("thermal", f"Battery temp {data['bat_temp']}°C"))
    if data.get("bat_pct", 100) < 15 and data.get("bat_status") != "CHARGING":
        alerts.append(("battery_low", f"Battery {data['bat_pct']}%"))
    if data.get("mem_avail_kb", 1) / data.get("mem_total_kb", 1) < 0.1:
        alerts.append(("memory", f"Low memory: {data['mem_avail_kb']//1024}MB free"))
    if data.get("load1") is not None and data["load1"] > 6:
        alerts.append(("load", f"High load: {data['load1']:.2f}"))

    if prev_data:
        if data.get("bat_pct") is not None and prev_data.get("bat_pct") is not None:
            drop = prev_data["bat_pct"] - data["bat_pct"]
            if drop > 3 and data.get("bat_status") != "CHARGING":
                alerts.append(("battery_drain", f"Fast drain: -{drop}% in {INTERVAL}s"))

    with sqlite3.connect(DB_PATH) as con:
        for atype, msg in alerts:
            con.execute("INSERT INTO alerts (timestamp, type, message) VALUES (?, ?, ?)", (now, atype, msg))

    return alerts

def predict_battery(history):
    if len(history) < 6:
        return None
    pcts = [h["bat_pct"] for h in history if h.get("bat_pct") is not None]
    if len(pcts) < 6:
        return None
    recent = pcts[-6:]
    deltas = [recent[i] - recent[i+1] for i in range(len(recent)-1)]
    avg_drop = statistics.mean(deltas)
    if avg_drop <= 0:
        return None
    current = pcts[-1]
    minutes = (current / avg_drop) * INTERVAL * 60 / 60
    return minutes

def predict_thermal(history):
    if len(history) < 6:
        return None
    temps = [h["bat_temp"] for h in history if h.get("bat_temp") is not None]
    if len(temps) < 6:
        return None
    recent = temps[-6:]
    trend = recent[-1] - recent[0]
    if trend > 2:
        return f"Rising {trend:.1f}°C/10s"
    return "Stable"

def fmt_bytes(b):
    for u in ['B','KB','MB','GB']:
        if b < 1024: return f"{b:.1f}{u}"
        b /= 1024
    return f"{b:.1f}TB"

def fmt_speed(bps):
    return fmt_bytes(bps) + "/s"

def get_history(limit=60):
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM metrics ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

def get_recent_alerts(limit=10):
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM alerts WHERE acknowledged=0 ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

def print_dashboard(data, prev_data, history, alerts):
    os.system('clear')
    bat = data.get("bat_pct", "?")
    bat_t = data.get("bat_temp", "?")
    bat_s = data.get("bat_status", "?")
    cpu_avg = data.get("cpu_avg_mhz", 0)
    cpu_max = data.get("cpu_max_mhz", 0)
    mem_avail = data.get("mem_avail_kb", 0) // 1024
    mem_total = data.get("mem_total_kb", 0) // 1024
    load1 = data.get("load1", 0)
    net_rx = data.get("net_rx", 0)
    net_tx = data.get("net_tx", 0)
    prev_rx = prev_data.get("net_rx", 0) if prev_data else 0
    prev_tx = prev_data.get("net_tx", 0) if prev_data else 0

    rx_speed = (net_rx - prev_rx) / INTERVAL
    tx_speed = (net_tx - prev_tx) / INTERVAL

    bat_pred = predict_battery(history)
    therm_pred = predict_thermal(history)

    print(f"╔═══ SYSMON ═══════════════════════════════════════════════════╗")
    print(f"║  🔋 {bat:>3}%  {bat_t:>4.1f}°C  {bat_s:<10}  ⚡ {cpu_avg:>4.0f}/{cpu_max}MHz  📊 {load1:.2f}  ║")
    print(f"║  🧠 {mem_avail:>4}MB/{mem_total}MB  🌐 ▼{fmt_speed(rx_speed):>8} ▲{fmt_speed(tx_speed):>8}  ║")
    if bat_pred:
        print(f"║  🔮 Battery: ~{bat_pred:.0f}min  🌡 {therm_pred}  ║")
    print(f"╠════════════════════════════════════════════════════════════════╣")

    if alerts:
        print(f"║  ⚠️  ALERTS:")
        for a in alerts[:3]:
            print(f"║     [{a['type']}] {a['message']}")
    else:
        print(f"║  ✅ No alerts")

    print(f"╚════════════════════════════════════════════════════════════════╝")

def daemon_mode():
    init_db()
    prev = None
    print("sysmon daemon started. Ctrl+C to stop.")
    try:
        while True:
            bat = read_battery()
            cpu = read_cpu_freq()
            mem = read_meminfo()
            net = read_net_dev()
            load = read_loadavg()
            disk = read_disk()

            data = {
                "bat_pct": bat.get("percentage"),
                "bat_temp": bat.get("temperature"),
                "bat_current": bat.get("current"),
                "bat_voltage": bat.get("voltage"),
                "bat_status": bat.get("status"),
                "cpu_avg_mhz": statistics.mean(cpu.values()) if cpu else 0,
                "cpu_max_mhz": max(cpu.values()) if cpu else 0,
                "mem_total_kb": mem.get("MemTotal"),
                "mem_avail_kb": mem.get("MemAvailable"),
                "net_rx": net["rx"],
                "net_tx": net["tx"],
                "load1": load.get("load1"),
                "load5": load.get("load5"),
                "load15": load.get("load15"),
                "disk_avail_kb": disk.get("avail_kb")
            }

            hist = get_history()
            alerts = check_alerts(data, prev)
            save_metrics(data)
            prev = data
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\nStopped.")

def interactive_mode():
    init_db()
    prev = None
    try:
        while True:
            bat = read_battery()
            cpu = read_cpu_freq()
            mem = read_meminfo()
            net = read_net_dev()
            load = read_loadavg()
            disk = read_disk()

            data = {
                "bat_pct": bat.get("percentage"),
                "bat_temp": bat.get("temperature"),
                "bat_current": bat.get("current"),
                "bat_voltage": bat.get("voltage"),
                "bat_status": bat.get("status"),
                "cpu_avg_mhz": statistics.mean(cpu.values()) if cpu else 0,
                "cpu_max_mhz": max(cpu.values()) if cpu else 0,
                "mem_total_kb": mem.get("MemTotal"),
                "mem_avail_kb": mem.get("MemAvailable"),
                "net_rx": net["rx"],
                "net_tx": net["tx"],
                "load1": load.get("load1"),
                "load5": load.get("load5"),
                "load15": load.get("load15"),
                "disk_avail_kb": disk.get("avail_kb")
            }

            hist = get_history()
            alerts = check_alerts(data, prev)
            save_metrics(data)
            print_dashboard(data, prev, hist, alerts)
            prev = data
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\nStopped.")

def show_history(hours=24):
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = con.execute("SELECT * FROM metrics WHERE timestamp > ? ORDER BY timestamp DESC LIMIT 100", (since,)).fetchall()
        for r in rows:
            load_str = f"{r['load1']:.2f}" if r['load1'] is not None else "N/A"
            print(f"  {r['timestamp'][:19]}  🔋{r['bat_pct']}% {r['bat_temp']}°C  ⚡{r['cpu_avg_mhz']:.0f}MHz  🧠{r['mem_avail_kb']//1024}MB  🌐{fmt_bytes(r['net_rx_bytes'])}/{fmt_bytes(r['net_tx_bytes'])}  📈{load_str}")

def show_alerts():
    alerts = get_recent_alerts(20)
    for a in alerts:
        print(f"  {a['timestamp'][:19]}  [{a['type']}] {a['message']}")

def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == 'history':
            hrs = int(sys.argv[2]) if len(sys.argv) > 2 else 24
            show_history(hrs)
            return
        elif sys.argv[1] == 'alerts':
            show_alerts()
            return

    if os.isatty(0):
        interactive_mode()
    else:
        daemon_mode()

if __name__ == "__main__":
    main()