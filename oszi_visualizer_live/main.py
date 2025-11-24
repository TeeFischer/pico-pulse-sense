import serial
from serial.tools import list_ports
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import time
import threading
import sys
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLineEdit, QPushButton, QTextEdit, QLabel)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer
from PyQt5.QtGui import QFont

BUFFER_SIZE = 10000
PLOT_INTERVAL = 300  # in ms
PRE_TRIGGER = 100  # number of samples to include before the trigger

timestamps = []
signals = []
pwm = []
running = True
data_lock = threading.Lock()
ser = None

# Tracking for sample rate calculation
processed_since_last_update = 0
last_update_time = time.time()

# Trigger globals
trigger_enabled = False
trigger_threshold = 200.0  # default threshold (mV)
trigger_hold = False  # when True, buffer is not popped/cleared

class SignalEmitter(QObject):
    log_signal = pyqtSignal(str)
    plot_signal = pyqtSignal()
    # Emitted when trigger snapshot is ready: (timestamps_list, signals_list, pwm_list)
    trigger_snapshot = pyqtSignal(object, object, object)

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

def read_serial_data(ser):
    global timestamps, signals, pwm, running, trigger_enabled, trigger_threshold, trigger_hold, processed_since_last_update
    emitter.log_signal.emit("Serial-Lese-Thread gestartet...")
    byte_buf = b''
    processed = 0
    collecting_snapshot = False
    snapshot_ts = []
    snapshot_sig = []
    snapshot_target_len = None

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

                    # Erwartetes Format: "time_ns, signal_mv, pwm"
                    # Entferne Leerzeichen und prüfe, ob die Zeile die richtige Anzahl an Teilen hat
                    parts = line.strip().split(',')
                    if len(parts) != 3:
                        emitter.log_signal.emit(f"⚠ Unparsable Zeile (nicht 3 Teile): '{line}'")
                    else:
                        time_ns_str, signal_mv_str, pwm_str = map(str.strip, parts)
                        try:
                            time_ns = float(time_ns_str)
                            signal_mv = float(signal_mv_str)
                            pwm_val = float(pwm_str)

                            with data_lock:
                                timestamps.append(time_ns)
                                signals.append(signal_mv)
                                pwm.append(pwm_val)
                                # Trigger detection: if enabled and not yet in hold
                                try:
                                    if trigger_enabled and (not trigger_hold) and (signal_mv > float(trigger_threshold)):
                                        trigger_hold = True
                                        # Capture up to PRE_TRIGGER samples before the trigger plus the triggering sample
                                        pre_available = max(0, len(timestamps) - 1)  # exclude current sample at end
                                        pre_count = PRE_TRIGGER if pre_available >= PRE_TRIGGER else pre_available
                                        start_idx = max(0, len(timestamps) - 1 - pre_count)
                                        # slice from start_idx to end to include pre samples and current sample
                                        snapshot_ts = timestamps[start_idx:]
                                        snapshot_sig = signals[start_idx:]
                                        snapshot_pwm = pwm[start_idx:]
                                        # Set target length: pre_count + 1 (trigger) + BUFFER_SIZE (following samples)
                                        snapshot_target_len = len(snapshot_sig) + BUFFER_SIZE
                                        collecting_snapshot = True
                                        emitter.log_signal.emit(f"▶ Trigger ausgelöst bei {signal_mv} (Schwelle {trigger_threshold}) - Snapshot startet mit {len(snapshot_sig)} Pre-Samples; Ziel: {snapshot_target_len} samples")
                                except Exception:
                                    # ignore invalid threshold or other errors
                                    pass

                                # If currently collecting snapshot, append new incoming samples until target length reached
                                if collecting_snapshot:
                                    # snapshot lists already contain current and pre samples; further incoming samples are appended here
                                    if len(snapshot_sig) < snapshot_target_len:
                                        snapshot_ts.append(time_ns)
                                        snapshot_sig.append(signal_mv)
                                        snapshot_pwm.append(pwm_val)

                                # If we've collected enough samples, emit snapshot and stop collecting
                                if collecting_snapshot and snapshot_target_len is not None and len(snapshot_sig) >= snapshot_target_len:
                                    try:
                                        emitter.trigger_snapshot.emit(list(snapshot_ts), list(snapshot_sig), list(snapshot_pwm))
                                        emitter.log_signal.emit(f"▶ Snapshot ready ({len(snapshot_sig)} samples) - Fenster wird geöffnet")
                                    except Exception as e:
                                        emitter.log_signal.emit(f"Fehler beim Senden des Snapshots: {e}")
                                    collecting_snapshot = False
                                    snapshot_target_len = None

                                # Only pop oldest if not in trigger hold
                                if (not trigger_hold) and len(timestamps) > BUFFER_SIZE:
                                    timestamps.pop(0)
                                    signals.pop(0)
                                    # keep pwm buffer in sync
                                    if len(pwm) > 0:
                                        pwm.pop(0)

                            processed += 1
                            processed_since_last_update += 1
                            # if processed % 50 == 0:
                            #     emitter.log_signal.emit(f"✓ Daten verarbeitet: {processed} (puffer: {len(timestamps)})")

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


class SnapshotWindow(QMainWindow):
    """Window to display a snapshot (triggering sample + next BUFFER_SIZE samples).
    Now supports plotting both signal (mV) and PWM (secondary subplot).
    """
    def __init__(self, ts_list, sig_list, pwm_list=None):
        super().__init__()
        self.setWindowTitle('Trigger Snapshot')
        self.setGeometry(200, 200, 1000, 700)

        central = QWidget()
        layout = QVBoxLayout()

        self.figure = Figure(figsize=(9, 6), dpi=100)
        self.canvas = FigureCanvas(self.figure)

        # Create two stacked subplots (signal on top, pwm below) sharing x-axis
        self.ax = self.figure.add_subplot(211)
        self.ax2 = self.figure.add_subplot(212, sharex=self.ax)

        # Navigation toolbar for zoom/pan
        try:
            toolbar = NavigationToolbar(self.canvas, self)
            layout.addWidget(toolbar)
        except Exception:
            # If toolbar not available, continue without it
            pass

        layout.addWidget(self.canvas)

        central.setLayout(layout)
        self.setCentralWidget(central)

        # Plot provided data
        try:
            # Top: signal
            self.ax.plot(ts_list, sig_list, 'b-', linewidth=1, label='Signal')
            self.ax.set_title(f'Trigger Snapshot - {len(sig_list)} samples')
            self.ax.set_xlabel('Zeit (ns)')
            self.ax.set_ylabel('Signal (mV)')
            self.ax.grid(True, alpha=0.3)
            if len(ts_list) > 0:
                self.ax.set_xlim([min(ts_list), max(ts_list)])
            if len(sig_list) > 0:
                self.ax.set_ylim([min(sig_list) - 10, max(sig_list) + 10])

            # Bottom: PWM (if provided)
            if pwm_list is not None and len(pwm_list) > 0:
                # Align lengths defensively
                min_len = min(len(ts_list), len(pwm_list))
                ts_plot = ts_list[-min_len:]
                pwm_plot = pwm_list[-min_len:]
                self.ax2.plot(ts_plot, pwm_plot, color='orange', linewidth=1, label='PWM')
                self.ax2.set_xlabel('Zeit (ns)')
                self.ax2.set_ylabel('PWM')
                self.ax2.grid(True, alpha=0.3)
            else:
                # if no pwm data, display a small note
                self.ax2.text(0.5, 0.5, 'Keine PWM-Daten im Snapshot', ha='center', va='center', transform=self.ax2.transAxes)

            self.canvas.draw()
        except Exception as e:
            emitter.log_signal.emit(f"Fehler beim Zeichnen des Snapshots: {e}")

class PicoVisualizerApp(QMainWindow):
    def __init__(self, ser_port):
        super().__init__()
        self.ser = ser_port
        # Keep references to any snapshot windows to avoid GC closing them
        self.snapshot_windows = []
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

        btn4 = QPushButton('1')
        btn4.clicked.connect(lambda: self.send_predefined_command('1'))
        btn_layout.addWidget(btn4)

        btn5 = QPushButton('10')
        btn5.clicked.connect(lambda: self.send_predefined_command('10'))
        btn_layout.addWidget(btn5)
        
        btn2 = QPushButton('20')
        btn2.clicked.connect(lambda: self.send_predefined_command('20'))
        btn_layout.addWidget(btn2)
        
        btn3 = QPushButton('60')
        btn3.clicked.connect(lambda: self.send_predefined_command('60'))
        btn_layout.addWidget(btn3)

        btn6 = QPushButton('an')
        btn6.clicked.connect(lambda: self.send_predefined_command('an'))
        btn_layout.addWidget(btn6)

        btn7 = QPushButton('aus')
        btn7.clicked.connect(lambda: self.send_predefined_command('aus'))
        btn_layout.addWidget(btn7)
        
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
        # Buffer size controls (adjustable + reset)
        buf_hbox = QHBoxLayout()
        buf_label = QLabel('BufferSize:')
        buf_label.setFont(QFont('Arial', 9))
        buf_hbox.addWidget(buf_label)

        self.buf_input = QLineEdit()
        self.buf_input.setText(str(BUFFER_SIZE))
        self.buf_input.setFixedWidth(80)
        buf_hbox.addWidget(self.buf_input)

        buf_set_btn = QPushButton('Set BufferSize')
        buf_set_btn.clicked.connect(self.set_buffer_size)
        buf_hbox.addWidget(buf_set_btn)

        buf_reset_btn = QPushButton('Reset Buffer')
        buf_reset_btn.clicked.connect(self.reset_buffer)
        buf_hbox.addWidget(buf_reset_btn)

        right_layout.addLayout(buf_hbox)
        right_layout.addStretch()
        
        main_layout.addLayout(left_layout, 2)
        main_layout.addLayout(right_layout, 1)
        
        central_widget.setLayout(main_layout)
        
        emitter.log_signal.connect(self.append_log)
        emitter.plot_signal.connect(self.update_plot)
        emitter.trigger_snapshot.connect(self.open_snapshot_window)

    def open_snapshot_window(self, ts_list, sig_list, pwm_list=None):
        """Open a new window displaying the given timestamp/signal/pwm snapshot."""
        try:
            win = SnapshotWindow(ts_list, sig_list, pwm_list)
            self.snapshot_windows.append(win)
            win.show()
            emitter.log_signal.emit("Snapshot-Fenster geöffnet.")
        except Exception as e:
            emitter.log_signal.emit(f"Fehler beim Öffnen des Snapshot-Fensters: {e}")
    
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

    def set_buffer_size(self):
        """Set BUFFER_SIZE from the UI input. Trim buffers if necessary."""
        global BUFFER_SIZE, trigger_hold
        txt = self.buf_input.text().strip()
        try:
            val = int(txt)
            if val < 1:
                raise ValueError("must be >=1")
        except Exception:
            emitter.log_signal.emit(f"Ungültige BufferSize: '{txt}'")
            return

        with data_lock:
            old = BUFFER_SIZE
            BUFFER_SIZE = val
            # If not in trigger_hold, trim to new size
            if (not trigger_hold) and len(timestamps) > BUFFER_SIZE:
                excess = len(timestamps) - BUFFER_SIZE
                del timestamps[0:excess]
                del signals[0:excess]
                # keep pwm buffer in sync
                if len(pwm) > excess:
                    del pwm[0:excess]
                else:
                    pwm.clear()

        emitter.log_signal.emit(f"BufferSize geändert: {old} -> {BUFFER_SIZE}")

    def reset_buffer(self):
        """Clear the current timestamp/signal buffers."""
        with data_lock:
            timestamps.clear()
            signals.clear()
            pwm.clear()
        emitter.log_signal.emit("Buffer geleert.")
    
    def append_log(self, message):
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )
    
    def update_plot(self):
        global processed_since_last_update, last_update_time
        
        # Calculate sample rate
        current_time = time.time()
        time_elapsed = current_time - last_update_time
        if time_elapsed > 0:
            sample_rate = processed_since_last_update / time_elapsed
        else:
            sample_rate = 0
        
        # Reset counter and time for next interval
        processed_since_last_update = 0
        last_update_time = current_time
        
        self.update_counter += 1
        with data_lock:
            if len(timestamps) > 1 and len(signals) > 1:
                # draw fresh figure each update to avoid stale axes/labels
                self.figure.clear()
                ax = self.figure.add_subplot(111)

                # Align lengths defensively
                min_len = min(len(timestamps), len(signals), len(pwm)) if len(pwm) > 0 else min(len(timestamps), len(signals))
                ts_plot = timestamps[-min_len:]
                sig_plot = signals[-min_len:]

                # Primary axis: signal
                line1, = ax.plot(ts_plot, sig_plot, 'b-', linewidth=1, label='Signal (mV)')
                ax.set_xlabel('Zeit (ns)')
                ax.set_ylabel('Signal (mV)')
                ax.set_title(f'Live Messdaten - {len(timestamps)} Punkte | {sample_rate:.1f} Mus/s (Update #{self.update_counter})')
                ax.grid(True, alpha=0.3)

                # Enforce fixed y-axis for the measured signal
                try:
                    ax.set_ylim([0, 800])
                except Exception:
                    # fallback to autoscale if something goes wrong
                    pass

                # Secondary axis: PWM (if present)
                if len(pwm) > 0:
                    pwm_plot = pwm[-min_len:]
                    ax2 = ax.twinx()
                    line2, = ax2.plot(ts_plot, pwm_plot, color='orange', linewidth=1, label='PWM')
                    ax2.set_ylabel('PWM')
                    # Enforce fixed PWM axis between 0 and 1
                    try:
                        ax2.set_ylim([0, 1])
                    except Exception:
                        pass
                    # combine legends
                    lines = [line1, line2]
                    labels = [l.get_label() for l in lines]
                    ax.legend(lines, labels, loc='upper right')
                else:
                    ax.legend(loc='upper right')

                if len(ts_plot) > 0:
                    ax.set_xlim([min(ts_plot), max(ts_plot)])

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
