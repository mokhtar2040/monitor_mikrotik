# app.py
from flask import Flask, request, jsonify, render_template, send_file, make_response
from flask_socketio import SocketIO, emit
from routeros_api import RouterOsApiPool
import logging
import time
import threading
import sqlite3
import io
from weasyprint import HTML

# إعدادات logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إعدادات Flask و SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key' # يجب تغيير هذا المفتاح
socketio = SocketIO(app, async_mode='threading')

# متغيرات عامة
monitor_thread = None
thread_lock = threading.Lock()
LIVE_STATS = {}
DATABASE_NAME = 'performance.db'

# Helper functions (كما هي)
def format_bytes(value):
    try:
        val = float(value)
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if val < 1024:
                return f"{val:.2f} {unit}"
            val /= 1024
        return f"{val:.2f} PB"
    except (ValueError, TypeError):
        return "N/A"

def format_speed(bps):
    try:
        bps = int(bps)
        if bps >= 1_000_000_000:
            return f"{bps / 1_000_000_000:.2f} Gbps"
        elif bps >= 1_000_000:
            return f"{bps / 1_000_000:.2f} Mbps"
        else:
            return f"{bps} bps"
    except (ValueError, TypeError):
        return "N/A"

# SQLite database setup
def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS performance_history (
            timestamp TEXT,
            interface TEXT,
            rx_speed REAL,
            tx_speed REAL,
            rx_total REAL,
            tx_total REAL
        )
    ''')
    conn.commit()
    conn.close()

# The monitor thread class for MikroTik
class MikroTikMonitor(threading.Thread):
    def __init__(self, ip, username, password, interval=3):
        super().__init__()
        self.ip = ip
        self.username = username
        self.password = password
        self.interval = interval
        self.running = True
        self.connection = None
        self.api = None
        self.interfaces_to_monitor = []
        self.previous_stats = {}
        self.last_db_save = 0
        self.save_interval = 60 # Save to DB every 60 seconds

    def connect(self):
        try:
            self.connection = RouterOsApiPool(
                host=self.ip, username=self.username, password=self.password, plaintext_login=True, port=8728
            )
            self.api = self.connection.get_api()
            logger.info("Successfully connected to MikroTik.")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    def disconnect(self):
        if self.connection:
            self.connection.disconnect()
            logger.info("Disconnected from MikroTik.")

    def get_all_interfaces(self):
        try:
            if not self.api:
                if not self.connect():
                    return []
            iface_resource = self.api.get_resource('/interface')
            return [item['name'] for item in iface_resource.get()]
        except Exception as e:
            logger.error(f"Error getting interfaces: {e}")
            return []

    def get_interface_stats(self, interfaces):
        stats = {}
        current_time = time.time()
        try:
            iface_resource = self.api.get_resource('/interface')
            for iface in interfaces:
                rx_speed = tx_speed = 0
                rx_total = tx_total = 0

                try:
                    result = iface_resource.get(name=iface)
                    if result:
                        current_rx_bytes = int(result[0].get('rx-byte', 0))
                        current_tx_bytes = int(result[0].get('tx-byte', 0))
                        rx_total = current_rx_bytes
                        tx_total = current_tx_bytes
                        
                        if iface in self.previous_stats:
                            prev_stats = self.previous_stats[iface]
                            time_diff = current_time - prev_stats['timestamp']
                            if time_diff > 0:
                                rx_bps = (current_rx_bytes - prev_stats['rx_bytes']) / time_diff
                                tx_bps = (current_tx_bytes - prev_stats['tx_bytes']) / time_diff
                                rx_speed = rx_bps * 8
                                tx_speed = tx_bps * 8
                        
                        self.previous_stats[iface] = {
                            'rx_bytes': current_rx_bytes,
                            'tx_bytes': current_tx_bytes,
                            'timestamp': current_time
                        }

                except Exception as e:
                    logger.warning(f"Failed to get stats for {iface}: {e}")

                stats[iface] = {
                    'rx_speed': format_speed(rx_speed),
                    'tx_speed': format_speed(tx_speed),
                    'rx_total': format_bytes(rx_total),
                    'tx_total': format_bytes(tx_total),
                    'timestamp': time.strftime("%H:%M:%S")
                }
        except Exception as e:
            logger.error(f"General error in get_interface_stats: {e}")
        return stats

    def save_to_db(self, stats):
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        for iface, data in stats.items():
            # Extract numerical values from formatted strings
            rx_speed_val = float(data['rx_speed'].split()[0]) if data['rx_speed'] != 'N/A' else 0
            tx_speed_val = float(data['tx_speed'].split()[0]) if data['tx_speed'] != 'N/A' else 0
            rx_total_val = float(data['rx_total'].split()[0]) if data['rx_total'] != 'N/A' else 0
            tx_total_val = float(data['tx_total'].split()[0]) if data['tx_total'] != 'N/A' else 0
            
            cursor.execute('''
                INSERT INTO performance_history (timestamp, interface, rx_speed, tx_speed, rx_total, tx_total)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (time.strftime("%Y-%m-%d %H:%M:%S"), iface, 
                  rx_speed_val, tx_speed_val, 
                  rx_total_val, tx_total_val))
        conn.commit()
        conn.close()

    def run(self):
        while self.running:
            if self.api:
                interfaces = self.interfaces_to_monitor or self.get_all_interfaces()
                stats = self.get_interface_stats(interfaces)
                with thread_lock:
                    global LIVE_STATS
                    LIVE_STATS = stats
                
                socketio.emit('live_stats', {'status': 'success', 'stats': stats})

                if time.time() - self.last_db_save >= self.save_interval:
                    self.save_to_db(stats)
                    self.last_db_save = time.time()
            time.sleep(self.interval)

    def stop(self):
        self.running = False
        self.disconnect()

# Flask routes and SocketIO events
@app.route('/')
def index():
    return render_template('monitor.html')

@socketio.on('connect_mikrotik')
def connect_mikrotik_via_socket(data):
    global monitor_thread
    with thread_lock:
        if monitor_thread and monitor_thread.is_alive():
            monitor_thread.stop()
            monitor_thread.join()
        
        ip = data.get('ip')
        username = data.get('username')
        password = data.get('password')
        interfaces = data.get('interfaces')

        monitor_thread = MikroTikMonitor(ip, username, password)
        monitor_thread.interfaces_to_monitor = interfaces
        
        if monitor_thread.connect():
            monitor_thread.daemon = True
            monitor_thread.start()
            emit('connection_status', {'status': 'success', 'message': 'Monitoring started'})
        else:
            emit('connection_status', {'status': 'error', 'message': 'Failed to connect to MikroTik'})

@socketio.on('disconnect_mikrotik')
def disconnect_mikrotik():
    global monitor_thread
    with thread_lock:
        if monitor_thread and monitor_thread.is_alive():
            monitor_thread.stop()
            monitor_thread.join()
            emit('connection_status', {'status': 'success', 'message': 'Monitoring stopped'})

@app.route('/api/get-report-data', methods=['POST'])
def get_report_data():
    data = request.get_json()
    start_date = data.get('startDate')
    end_date = data.get('endDate')

    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM performance_history
        WHERE timestamp BETWEEN ? AND ?
        ORDER BY timestamp
    ''', (start_date, end_date))
    report_data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({"status": "success", "data": report_data})


@app.route('/api/download-report-pdf', methods=['POST'])
def download_report_pdf():
    data = request.get_json()
    start_date = data.get('startDate')
    end_date = data.get('endDate')
    
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM performance_history
        WHERE timestamp BETWEEN ? AND ?
        ORDER BY timestamp
    ''', (start_date, end_date))
    report_data = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not report_data:
        return "لا توجد بيانات متاحة لإنشاء تقرير.", 404
        
    html_content = f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700&display=swap');
            body {{ font-family: 'Tajawal', sans-serif; direction: rtl; }}
            h1, h2 {{ text-align: center; color: #2c3e50; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: right; }}
            th {{ background-color: #3498db; color: white; }}
            tr:nth-child(even) {{ background-color: #f2f2f2; }}
            .footer-banner {{
                margin-top: 40px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 20px;
                border-radius: 8px;
                text-align: center;
                position: fixed;
                bottom: 0;
                width: 100%;
                left: 0;
            }}
            .social-links {{
                margin-top: 15px;
                font-size: 24px;
            }}
            .social-links a {{
                color: white;
                margin: 0 10px;
                text-decoration: none;
            }}
        </style>
    </head>
    <body>
        <h1>MikroTik Speed Monitor Report</h1>
        <h2>الفترة الزمنية: من {start_date} إلى {end_date}</h2>
        <table>
            <thead>
                <tr>
                    <th>الوقت</th>
                    <th>الواجهة</th>
                    <th>سرعة RX</th>
                    <th>سرعة TX</th>
                    <th>إجمالي RX</th>
                    <th>إجمالي TX</th>
                </tr>
            </thead>
            <tbody>
    """
    for row in report_data:
        html_content += f"""
        <tr>
            <td>{row['timestamp']}</td>
            <td>{row['interface']}</td>
            <td>{row['rx_speed']}</td>
            <td>{row['tx_speed']}</td>
            <td>{row['rx_total']}</td>
            <td>{row['tx_total']}</td>
        </tr>
        """
    
    html_content += f"""
            </tbody>
        </table>
        <div class="footer-banner">
            <p><i class="fas fa-code"></i> تصميم وبرمجة المهندس / مختار الحماي</p>
            <p><i class="fas fa-mobile-alt"></i> الهاتف: <strong>967-773324122</strong></p>
            <div class="social-links">
                <a href="https://x.com/M_Alhamadee" target="_blank"><i class="fab fa-twitter"></i></a>
                <a href="https://t.me/mokhtar_tech" target="_blank"><i class="fab fa-telegram-plane"></i></a>
                <a href="https://www.youtube.com/@mkh20" target="_blank"><i class="fab fa-youtube"></i></a>
            </div>
            <p>&copy; 2025 MokhtarTech. جميع الحقوق محفوظة.</p>
        </div>
    </body>
    </html>
    """
    
    pdf_buffer = io.BytesIO()
    HTML(string=html_content).write_pdf(pdf_buffer)
    pdf_buffer.seek(0)
    
    response = make_response(pdf_buffer.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=mikrotik_report.pdf'
    response.headers['Content-Type'] = 'application/pdf'
    return response


if __name__ == '__main__':
    init_db()
    socketio.run(app, debug=True, host='127.0.0.1', port=5000)