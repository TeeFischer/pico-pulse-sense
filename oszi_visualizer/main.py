import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# Beispielhafte Daten (ersetze dies mit deinen tatsächlichen CSV-Daten)
data = [
    ("2025/11/16 23:44:46::240", 665099440, 273.234),
    ("2025/11/16 23:44:46::240", 665099442, 274.040),
    ("2025/11/16 23:44:46::240", 665099445, 275.652),
    ("2025/11/16 23:44:46::240", 665099448, 274.846),
    ("2025/11/16 23:44:46::240", 665099450, 276.458),
    ("2025/11/16 23:44:46::240", 665099459, 270.010),
    ("2025/11/16 23:44:46::240", 665099461, 267.592),
    ("2025/11/16 23:44:46::240", 665099464, 271.622),
    ("2025/11/16 23:44:46::240", 665099466, 275.652),
    ("2025/11/16 23:44:46::240", 665102403, 232.934),
    ("2025/11/16 23:44:46::240", 665102405, 243.412),
    ("2025/11/16 23:44:46::240", 665102408, 249.860),
    ("2025/11/16 23:44:46::240", 665102411, 257.114),
    ("2025/11/16 23:44:46::240", 665102413, 259.532),
    ("2025/11/16 23:44:46::240", 665102416, 264.368),
    # Beispiel für eine Ausnahme (Nachricht)
    ("2025/11/16 23:44:47::425", "PWM 20% gestartet (100 ms)", 274.846),
    # Weitere normale Zeitpunkte
    ("2025/11/16 23:44:47::425", 666293429, 274.846),
    ("2025/11/16 23:44:47::425", 666293431, 275.652),
    ("2025/11/16 23:44:47::425", 666293434, 274.846),
]

# Umwandlung der Daten in ein pandas DataFrame
# df = pd.DataFrame(data, columns=["RX Date/Time", "Group/Time", "Group/New Plot"])
df = pd.read_csv('2025_Nov_16 23_44_46.csv')

print(df.head())

# Umwandeln der "RX Date/Time" Spalte in datetime-Objekte
df['RX Date/Time'] = pd.to_datetime(df['RX Date/Time'], format='%Y/%m/%d %H:%M:%S::%f')

# Filtern der Zeilen mit Nachrichten (wenn Group/Time mit " beginnt)
messages = df[df['Group/Time'].apply(lambda x: x.startswith('"'))]
# messages = df[df['Group/Time'].apply(lambda x: isinstance(x, str))]

# Anzahl der Datenpunkte ausgeben
print(f"Die Datei enthält {len(df)} Datenpunkte.")
print(f"Anzahl der Nachrichten: {len(messages)}")

# Plotly Visualisierung
fig = go.Figure()

# Plot der Daten ohne Nachrichten (normale Zeitpunkte)
fig.add_trace(go.Scatter(x=df['Group/Time'], y=df['Group/New Plot'], mode='lines+markers', name='Messwerte'))



# Titel und Achsenbeschriftungen hinzufügen
fig.update_layout(
    title='Messwerte mit Ausnahme-Nachrichten',
    xaxis_title='Zeit',
    yaxis_title='Millivolt',
    template='plotly_dark',
    xaxis_rangeslider_visible=True,
    showlegend=True
)

# Interaktive Anzeige
fig.show()
