import serial
from serial.tools import list_ports
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import time
import threading

BUFFER_SIZE = 10000  # oder ein anderer Wert je nach Bedarf
plot_interval = 300  # in Millisekunden

# Globale Variablen für Threading
timestamps = []
signals = []
running = True
data_lock = threading.Lock()

# Funktion, um alle verfügbaren COM-Ports anzuzeigen
def list_serial_ports():
    ports = list_ports.comports()  # Liste der verfügbaren Ports
    if not ports:
        print("Keine seriellen Ports gefunden.")
        return []
    else:
        print("Verfügbare serielle Ports:")
        # Liste der Ports ausgeben
        for i, port in enumerate(ports, 1):  # enumerate mit 1 beginnen für Benutzerfreundlichkeit
            print(f"{i}. {port.device} - {port.description}")
        return ports

# Funktion, um den gewählten Port zu öffnen
def open_serial_port(com_port, baud_rate):
    try:
        ser = serial.Serial(com_port, baud_rate, timeout=1)
        print(f"Verbindung zu {com_port} hergestellt.")
        return ser
    except serial.SerialException as e:
        print(f"Fehler beim Öffnen des Ports {com_port}: {e}")
        return None

# Funktion zum kontinuierlichen Lesen der seriellen Daten
def read_serial_data(ser):
    global timestamps, signals, running
    print("Serial-Lese-Thread gestartet...")
    
    while running:
        try:
            if ser and ser.is_open:
                line = ser.readline().decode('utf-8').strip()
                
                if line:  # Wenn eine Zeile empfangen wurde
                    try:
                        # Daten in Zeit und Signal trennen
                        time_ns, signal_mv = map(float, line.split(','))
                        
                        # Daten thread-safe in Puffer hinzufügen
                        with data_lock:
                            timestamps.append(time_ns)
                            signals.append(signal_mv)
                            
                            # Wenn der Puffer voll ist, älteste Daten löschen
                            if len(timestamps) > BUFFER_SIZE:
                                timestamps.pop(0)
                                signals.pop(0)
                    
                    except Exception as e:
                        print(f"Ungültige Zeile empfangen: {line} ({e})")
        except Exception as e:
            print(f"Fehler beim Lesen: {e}")
            break
    
    print("Serial-Lese-Thread beendet.")

# Funktion zum Aktualisieren des Plots mit Animation
def update_plot_animation(frame):
    global timestamps, signals
    
    with data_lock:
        if timestamps and signals:
            # Leere den Plot
            ax.clear()
            
            # Zeichne die neuen Daten
            ax.plot(timestamps, signals, 'b-', linewidth=1, label='Messwerte')
            ax.set_xlabel('Zeit (ns)')
            ax.set_ylabel('Signal (mV)')
            ax.set_title('Live Messdaten')
            ax.grid(True, alpha=0.3)
            ax.legend()
            
            # X- und Y-Achse anpassen
            if len(timestamps) > 0:
                ax.set_xlim([min(timestamps), max(timestamps)])
            if len(signals) > 0:
                ax.set_ylim([min(signals) - 10, max(signals) + 10])
            
            print(f"Plot aktualisiert - Datenpunkte: {len(timestamps)}")

# Alle verfügbaren Ports auflisten
ports = list_serial_ports()

com_port = None
ser = None

if ports:
    # Automatisch nach "Pico" suchen
    for port in ports:
        if "Pico" in port.description:
            com_port = port.device
            print(f"Pico-Port gefunden: {com_port} ({port.description})")
            break
    
    # Falls kein Pico gefunden wurde, Benutzer nach Auswahl fragen
    if com_port is None:
        try:
            choice = int(input("Kein Pico gefunden. Wählen Sie einen Port (1, 2, 3, ...): "))
            if 1 <= choice <= len(ports):
                com_port = ports[choice - 1].device
            else:
                print("Ungültige Auswahl. Bitte wählen Sie eine gültige Nummer.")
        except ValueError:
            print("Ungültige Eingabe. Bitte geben Sie eine Zahl ein.")
    
    # Port öffnen, falls gefunden
    if com_port:
        baud_rate = 115200  # Beispiel-Baudrate
        ser = open_serial_port(com_port, baud_rate)

# Initialisierung der Matplotlib-Figur
fig, ax = plt.subplots(figsize=(12, 6))

# X- und Y-Datenpuffer (bereits oben definiert als globale Variablen)

# Plot-Layout anpassen
ax.set_title('Live Messdaten')
ax.set_xlabel('Zeit (ns)')
ax.set_ylabel('Signal (mV)')
ax.grid(True, alpha=0.3)

# Threads starten
print("Starte Datenerfassung und Plot-Aktualisierung...")
serial_thread = threading.Thread(target=read_serial_data, args=(ser,), daemon=True)
serial_thread.start()

# Matplotlib Animation für Plot-Updates (alle 1000ms = 1 Sekunde)
ani = animation.FuncAnimation(fig, update_plot_animation, interval=plot_interval, blit=False)

# Zeige den Plot
plt.show()

# Hauptschleife - warte auf Benutzer-Eingabe zum Beenden
try:
    while running:
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nBeende Anwendung...")
    running = False
    time.sleep(0.5)  # Gebe den Threads Zeit zum Beenden
    print("Anwendung beendet.")

finally:
    # Seriellen Port schließen
    running = False
    if ser and ser.is_open:
        ser.close()
    print("Serial-Port geschlossen.")
