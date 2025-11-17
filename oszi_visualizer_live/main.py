import serial
from serial.tools import list_ports
import plotly.graph_objects as go
import time

BUFFER_SIZE = 1000  # oder ein anderer Wert je nach Bedarf

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
        baud_rate = 9600  # Beispiel-Baudrate
        ser = open_serial_port(com_port, baud_rate)

# Initialisierung der Plotly-Figur
fig = go.Figure()

# X- und Y-Datenpuffer
timestamps = []
signals = []

# Plot-Layout anpassen
fig.update_layout(
    title='Live Messdaten',
    xaxis_title='Zeit (ns)',
    yaxis_title='Signal (mV)',
    template='plotly_dark',
    xaxis_rangeslider_visible=True,
    showlegend=True
)

# Plotly-Trace für die Daten
trace = go.Scatter(x=timestamps, y=signals, mode='lines+markers', name='Messwerte')
fig.add_trace(trace)

# Plot anzeigen
fig.show()

# Live-Datenempfang und Plot-Aktualisierung
try:
    while True:
        # Serielle Daten lesen (Datenformat: "Zeit in ns, Signal in mV")
        line = ser.readline().decode('utf-8').strip()
        
        if line:  # Wenn eine Zeile empfangen wurde
            try:
                # Daten in Zeit und Signal trennen
                time_ns, signal_mv = map(float, line.split(','))
                
                # Daten in Puffer hinzufügen
                timestamps.append(time_ns)
                signals.append(signal_mv)
                
                # Wenn der Puffer voll ist, älteste Daten löschen
                if len(timestamps) > BUFFER_SIZE:
                    timestamps.pop(0)
                    signals.pop(0)
                
                # Die Daten im Plot aktualisieren
                fig.data[0].x = timestamps
                fig.data[0].y = signals
                fig.update_layout(
                    xaxis=dict(range=[min(timestamps), max(timestamps)]),  # X-Achse automatisch anpassen
                    yaxis=dict(range=[min(signals), max(signals)])  # Y-Achse automatisch anpassen
                )
                fig.update()
                
                time.sleep(0.1)  # kurze Pause, um Daten nicht zu schnell zu empfangen
            except ValueError:
                # Fehler, wenn die Zeile keine gültigen Daten enthält
                print(f"Ungültige Zeile empfangen: {line}")
        else:
            time.sleep(0.1)  # Falls keine Daten empfangen werden, kurze Pause
except KeyboardInterrupt:
    print("Live-Datenvisualisierung beendet.")

finally:
    # Seriellen Port schließen
    ser.close()
