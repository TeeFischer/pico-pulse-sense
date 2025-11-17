import serial
from serial.tools import list_ports
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import time
import threading
import sys
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLineEdit, QPushButton, QTextEdit, QLabel)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer
from PyQt5.QtGui import QFont

BUFFER_SIZE = 10000
PLOT_INTERVAL = 300

timestamps = []
signals = []
running = True
data_lock = threading.Lock()
ser = None

# Trigger globals
trigger_enabled = False
trigger_threshold = 200.0  # default threshold (mV)
trigger_hold = False  # when True, buffer is not popped/cleared

class SignalEmitter(QObject):
    log_signal = pyqtSignal(str)
    plot_signal = pyqtSignal()

emitter = SignalEmitter()

def list_serial_ports():
    ports = list_ports.comports()
    if not ports:
        print("Keine seriellen Ports gefunden.")
        return []
    else:
        print("Verfügbare serielle Ports:")
        for i, port in enumerate(ports, 1):
            print(f"{i}. {port.device} - {port.description}")
        return ports

def open_serial_port(com_port, baud_rate):
    try:
        # non-blocking reads; wir handhaben Fragments selbst
        ser = serial.Serial(com_port, baud_rate, timeout=0)
        print(f"Verbindung zu {com_port} hergestellt.")
        return ser
    except serial.SerialException as e:
        print(f"Fehler beim Öffnen des Ports {com_port}: {e}")
        return None
# ...existing code...
def read_serial_data(ser):
    global timestamps, signals, running, trigger_enabled, trigger_threshold, trigger_hold
    emitter.log_signal.emit("Serial-Lese-Thread gestartet...")
    byte_buf = b''
    processed = 0

    while running:
        try:
            if ser and ser.is_open:
                # Lese alle momentan verfügbaren Bytes (non-blocking)
                to_read = ser.in_waiting or 1
                data = ser.read(to_read)
                if not data:
                    time.sleep(0.001)
                    continue

                byte_buf += data

                # Splitte auf '\n' (handle auch '\r\n')
                while b'\n' in byte_buf:
                    line_bytes, byte_buf = byte_buf.split(b'\n', 1)
                    # entferne \r und decode, ersetze invalid bytes
                    line = line_bytes.replace(b'\r', b'').decode('utf-8', errors='replace').strip()
                    if not line:
                        continue

                    try:
                        # Erwartetes Format: "time_ns,signal_mv"
                        time_ns_str, signal_mv_str = map(str.strip, line.split(',', 1))
                        time_ns = float(time_ns_str)
                        signal_mv = float(signal_mv_str)

                        with data_lock:
                            timestamps.append(time_ns)
                            signals.append(signal_mv)
                            # Trigger detection: if enabled and not yet in hold
                            try:
                                if trigger_enabled and (not trigger_hold) and (signal_mv > float(trigger_threshold)):
                                    trigger_hold = True
                                    emitter.log_signal.emit(f"▶ Trigger ausgelöst bei {signal_mv} (Schwelle {trigger_threshold}) - Buffer wird nun nicht mehr geleert")
                            except Exception:
                                # ignore invalid threshold
                                pass

                            # Only pop oldest if not in trigger hold
                            if (not trigger_hold) and len(timestamps) > BUFFER_SIZE:
                                timestamps.pop(0)
                                signals.pop(0)

                        processed += 1
                        if processed % 50 == 0:
                            emitter.log_signal.emit(f"✓ Daten verarbeitet: {processed} (puffer: {len(timestamps)})")

                    except ValueError:
                        # Logge die rohe Zeile zur Analyse, versuche keine weitere Zerlegung hier
                        emitter.log_signal.emit(f"⚠ Unparsable Zeile: '{line}'")
                    except Exception as e:
                        emitter.log_signal.emit(f"Fehler beim Verarbeiten: '{line}' ({e})")
        except Exception as e:
            emitter.log_signal.emit(f"Fehler beim Lesen: {e}")
            break

    emitter.log_signal.emit("Serial-Lese-Thread beendet.")

def send_command_to_pico(command):
    global ser
    if ser and ser.is_open:
        try:
            ser.write((command + '\n').encode('utf-8'))
            emitter.log_signal.emit(f">>> Befehl gesendet: {command}")
        except Exception as e:
            emitter.log_signal.emit(f"Fehler beim Senden: {e}")
    else:
        emitter.log_signal.emit("Fehler: Serieller Port nicht geöffnet!")

class PicoVisualizerApp(QMainWindow):
    def __init__(self, ser_port):
        super().__init__()
        self.ser = ser_port
        self.update_counter = 0  # Counter für update_plot Aufrufe
        self.initUI()
        self.setup_plot_update()
        
    def initUI(self):
        self.setWindowTitle('Pico Pulse Sense - Live Visualizer')
        self.setGeometry(100, 100, 1400, 800)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout()
        
        left_layout = QVBoxLayout()
        
        self.figure = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_title('Live Messdaten')
        self.ax.set_xlabel('Zeit (ns)')
        self.ax.set_ylabel('Signal (mV)')
        self.ax.grid(True, alpha=0.3)
        
        left_layout.addWidget(self.canvas)
        
        right_layout = QVBoxLayout()
        
        log_label = QLabel('Log / Empfangene Daten:')
        log_label.setFont(QFont('Arial', 10, QFont.Bold))
        right_layout.addWidget(log_label)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(400)
        right_layout.addWidget(self.log_text)
        
        cmd_label = QLabel('Befehl an Pico:')
        cmd_label.setFont(QFont('Arial', 10, QFont.Bold))
        right_layout.addWidget(cmd_label)
        
        cmd_layout = QHBoxLayout()
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText('Geben Sie einen Befehl ein und drücken Sie Enter...')
        self.cmd_input.returnPressed.connect(self.send_command)
        cmd_layout.addWidget(self.cmd_input)
        
        send_btn = QPushButton('Senden')
        send_btn.clicked.connect(self.send_command)
        cmd_layout.addWidget(send_btn)
        
        right_layout.addLayout(cmd_layout)
        
        predef_label = QLabel('Vordefinierte Befehle:')
        predef_label.setFont(QFont('Arial', 10, QFont.Bold))
        right_layout.addWidget(predef_label)
        
        btn_layout = QVBoxLayout()
        
        btn1 = QPushButton('Enter')
        btn1.clicked.connect(lambda: self.send_predefined_command(' '))
        btn_layout.addWidget(btn1)
        
        btn2 = QPushButton('20')
        btn2.clicked.connect(lambda: self.send_predefined_command('20'))
        btn_layout.addWidget(btn2)
        
        btn3 = QPushButton('60')
        btn3.clicked.connect(lambda: self.send_predefined_command('60'))
        btn_layout.addWidget(btn3)
        
        right_layout.addLayout(btn_layout)

        # Trigger controls
        trig_hbox = QHBoxLayout()
        trig_label = QLabel('Trigger Schwelle (mV):')
        trig_label.setFont(QFont('Arial', 9))
        trig_hbox.addWidget(trig_label)

        self.trig_input = QLineEdit()
        self.trig_input.setText(str(trigger_threshold))
        self.trig_input.setFixedWidth(80)
        trig_hbox.addWidget(self.trig_input)

        self.trig_btn = QPushButton('Trigger: OFF')
        self.trig_btn.setCheckable(True)
        self.trig_btn.clicked.connect(self.toggle_trigger)
        trig_hbox.addWidget(self.trig_btn)

        self.trig_release_btn = QPushButton('Release Hold')
        self.trig_release_btn.clicked.connect(self.release_trigger)
        trig_hbox.addWidget(self.trig_release_btn)

        right_layout.addLayout(trig_hbox)
        right_layout.addStretch()
        
        main_layout.addLayout(left_layout, 2)
        main_layout.addLayout(right_layout, 1)
        
        central_widget.setLayout(main_layout)
        
        emitter.log_signal.connect(self.append_log)
        emitter.plot_signal.connect(self.update_plot)
    
    def send_command(self):
        command = self.cmd_input.text().strip()
        if command:
            send_command_to_pico(command)
            self.cmd_input.clear()
    
    def send_predefined_command(self, command):
        send_command_to_pico(command)

    def toggle_trigger(self):
        """Toggle trigger enabled/disabled and set threshold from input."""
        global trigger_enabled, trigger_threshold, trigger_hold
        if self.trig_btn.isChecked():
            # Enable trigger
            try:
                val = float(self.trig_input.text())
                trigger_threshold = val
            except Exception:
                emitter.log_signal.emit(f"Ungültige Schwelle: '{self.trig_input.text()}'")
                # reset button
                self.trig_btn.setChecked(False)
                return

            trigger_enabled = True
            trigger_hold = False
            self.trig_btn.setText('Trigger: ON')
            emitter.log_signal.emit(f"Trigger aktiviert. Schwelle = {trigger_threshold} mV")
        else:
            # Disable trigger and clear hold
            trigger_enabled = False
            trigger_hold = False
            self.trig_btn.setText('Trigger: OFF')
            emitter.log_signal.emit("Trigger deaktiviert.")

    def release_trigger(self):
        """Release trigger hold so buffer popping resumes."""
        global trigger_hold
        if trigger_hold:
            trigger_hold = False
            emitter.log_signal.emit("Trigger-Hold freigegeben. Buffer wird wieder normal geleert.")
        else:
            emitter.log_signal.emit("Trigger-Hold ist nicht aktiv.")
    
    def append_log(self, message):
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )
    
    def update_plot(self):
        self.update_counter += 1
        with data_lock:
            if len(timestamps) > 1 and len(signals) > 1:
                self.ax.clear()
                self.ax.plot(timestamps, signals, 'b-', linewidth=1, label='Messwerte')
                self.ax.set_xlabel('Zeit (ns)')
                self.ax.set_ylabel('Signal (mV)')
                self.ax.set_title(f'Live Messdaten - {len(timestamps)} Punkte (Update #{self.update_counter})')
                self.ax.grid(True, alpha=0.3)
                self.ax.legend()
                
                if len(timestamps) > 0:
                    self.ax.set_xlim([min(timestamps), max(timestamps)])
                if len(signals) > 0:
                    self.ax.set_ylim([min(signals) - 10, max(signals) + 10])
                
                self.canvas.draw()
            else:
                # Warten auf Daten
                if len(timestamps) == 0:
                    self.ax.clear()
                    self.ax.text(0.5, 0.5, f'Warten auf Daten...\nUpdate #{self.update_counter}', 
                                ha='center', va='center', transform=self.ax.transAxes)
                    self.ax.set_xlim([0, 1])
                    self.ax.set_ylim([0, 1])
                    self.canvas.draw()
    
    def setup_plot_update(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(PLOT_INTERVAL)
    
    def closeEvent(self, event):
        global running, ser
        running = False
        time.sleep(0.5)
        if ser and ser.is_open:
            ser.close()
        event.accept()

if __name__ == '__main__':
    ports = list_serial_ports()
    
    com_port = None
    
    if ports:
        for port in ports:
            if "Pico" in port.description:
                com_port = port.device
                print(f"Pico-Port gefunden: {com_port} ({port.description})")
                break
        
        if com_port is None:
            try:
                choice = int(input("Kein Pico gefunden. Wählen Sie einen Port (1, 2, 3, ...): "))
                if 1 <= choice <= len(ports):
                    com_port = ports[choice - 1].device
                else:
                    print("Ungültige Auswahl.")
                    sys.exit(1)
            except ValueError:
                print("Ungültige Eingabe.")
                sys.exit(1)
        
        baud_rate = 115200
        ser = open_serial_port(com_port, baud_rate)
        
        if ser and ser.is_open:
            app = QApplication(sys.argv)
            window = PicoVisualizerApp(ser)
            window.show()
            
            print("Starte Serial-Lese-Thread...")
            serial_thread = threading.Thread(target=read_serial_data, args=(ser,), daemon=True)
            serial_thread.start()
            
            sys.exit(app.exec_())
        else:
            print("Fehler beim Öffnen des seriellen Ports.")
    else:
        print("Keine seriellen Ports gefunden.")
