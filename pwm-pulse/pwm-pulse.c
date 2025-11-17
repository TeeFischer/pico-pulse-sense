#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pico/stdlib.h"

#define PULSE_PIN 15     // GPIO-Pin für den Puls
#define DEFAULT_PULSE_MS 100 // Standard-Pulsdauer in Millisekunden

int main() {
    stdio_init_all();

    gpio_init(PULSE_PIN);
    gpio_set_dir(PULSE_PIN, GPIO_OUT);
    gpio_put(PULSE_PIN, 0); // Pin auf LOW initialisieren

    int pulse_ms = DEFAULT_PULSE_MS;
    char input_buffer[32];
    int input_index = 0;

    printf("Bereit! Gib eine Pulsdauer in ms ein (z.B. 40) und drücke Enter.\n");
    printf("Nur Enter = Wiederhole letzten Puls (%d ms)\n", pulse_ms);

    while (true) {
        int c = getchar_timeout_us(0);  // Eingabe prüfen (nicht blockierend)

        if (c != PICO_ERROR_TIMEOUT) {
            if (c == '\r' || c == '\n') {  // Enter gedrückt
                input_buffer[input_index] = '\0'; // String beenden

                if (input_index > 0) {
                    int new_value = atoi(input_buffer);
                    if (new_value > 0) {
                        pulse_ms = new_value;
                        printf("Neue Pulsdauer: %d ms\n", pulse_ms);
                    } else {
                        printf("Ungültige Eingabe. Verwende letzten Wert: %d ms\n", pulse_ms);
                    }
                    input_index = 0;
                } else {
                    printf("Wiederhole letzten Puls (%d ms)\n", pulse_ms);
                }

                // Puls auslösen
                printf("Puls!\n");
                gpio_put(PULSE_PIN, 1);
                sleep_ms(pulse_ms);
                gpio_put(PULSE_PIN, 0);
                printf("Fertig.\n");

            } else if (c >= '0' && c <= '9' && input_index < (int)sizeof(input_buffer) - 1) {
                // Ziffer zur Eingabe hinzufügen
                input_buffer[input_index++] = (char)c;
            } else if (c == '\b' && input_index > 0) {
                // Backspace verarbeiten
                input_index--;
            }
        }

        sleep_ms(10);
    }
}
