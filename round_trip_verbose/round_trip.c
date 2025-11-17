#define timestamping true

#include <stdio.h>
#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/adc.h"
#include "hardware/pwm.h" // PWM-Header hinzufügen
#include "pico/time.h" // Zeitfunktionen hinzufügen

#define NUM_SAMPLES 300
#define THRESHOLD 200 // Schwellwert für Flankenerkennung 4096 enspricht 3.3V
// 600mV entsprechen 744 ADC-Wert bei 12 Bit Auflösung
#define VperDev 0.806

#define pwm_min 0.05f // Untere Grenze für PWM
#define pwm_max 0.95f // Obere Grenze für PWM
#define pwm_step 0.05f // Schrittweite für PWM-Anpassung

#define lower_avg_threshold 300.0f // Untere Grenze für PWM-Regelung
#define upper_avg_threshold 550.0f // Obere Grenze für PWM-Regelung

#define PWM_GPIO 15     // Wähle einen freien GPIO, z.B. GPIO15
#define PWM_WRAP 4095   // 12 Bit PWM-Auflösung
//#define PWM_LEVEL 480  // Duty Cycle (30/255)
#define PWM_LEVEL 1606  // Duty Cycle (100/255)

// Funktionsprototyp einfügen
void startmessung(void);


int main(void) {
    stdio_init_all();
    adc_init();
    adc_gpio_init(26); // GPIO26 = ADC0
    gpio_set_pulls(26,0,1);  // input Pulldown
    adc_select_input(0);

    // PWM initialisieren
    gpio_set_function(PWM_GPIO, GPIO_FUNC_PWM);
    uint slice_num = pwm_gpio_to_slice_num(PWM_GPIO);

    // PWM konfigurieren neu
    // Systemtakt normalerweise 125 MHz
    const float sys_clk = 125000000;
    float freq = 4000.0f;       // Gewünschte Frequenz
    
    float pwm = 0.3f;                    // Start Duty Cycle (30%)

    // Clock divider (CLKDIV)
    // Wir wählen z.B. CLKDIV = 125, um Zählerfrequenz 1 MHz zu erhalten
    float clkdiv = 125.0f;

    // Wrap Wert berechnen: Wrap = (PWM_Takt / freq) - 1
    uint16_t wrap = (uint16_t)((sys_clk / clkdiv) / freq) - 1;

    pwm_set_clkdiv(slice_num, clkdiv);
    pwm_set_wrap(slice_num, wrap);

    // Duty Cycle einstellen (50%)
    pwm_set_gpio_level(PWM_GPIO, (uint16_t)(wrap * pwm));
    pwm_set_enabled(slice_num, false);

    sleep_ms(1000); // Warten bis USB-Serial bereit

    uint16_t samples[NUM_SAMPLES];
#if timestamping
    uint32_t timestamps[NUM_SAMPLES];
#endif

    pwm_set_enabled(slice_num, true);
    while (1) {   // Dauerschleife
        //pwm_set_enabled(slice_num, false);
        //sleep_ms(100); // Warten bis Laser sicher aus ist

        //startmessung();
        //pwm_set_enabled(slice_num, true);

        // Messungenen durchführen
        for (int i = 0; i < NUM_SAMPLES; i++) {
#if timestamping
            timestamps[i] = time_us_32();
#endif
            samples[i] = adc_read();
        }

        // PWM-Analyse: Puls suchen
        int pulse_start = -1, pulse_end = -1;
        uint16_t min_val = 4095, max_val = 0;

        // Suche steigende Flanke
        for (int i = 1; i < NUM_SAMPLES; i++) {
            if (samples[i-1] < THRESHOLD && samples[i] >= THRESHOLD) {
                pulse_start = i;
                break;
            }
        }
        // Suche fallende Flanke nach steigender
        if (pulse_start != -1) {
            for (int i = pulse_start + 1; i < NUM_SAMPLES; i++) {
                if (samples[i-1] >= THRESHOLD && samples[i] < THRESHOLD) {
                    pulse_end = i;
                    break;
                }
            }
        }

        float avg_an = 0.0f;

        // Wenn Puls erkannt, Durchschnittswerte berechnen
        if (pulse_start != -1 && pulse_end != -1) {
        #if timestamping
            uint32_t pulse_time_us = timestamps[pulse_end] - timestamps[pulse_start];
        #endif

            // Berechnung der durchschnittlichen Amplitude
            uint32_t sum_an = 0;
            int count_an = pulse_end - pulse_start;
            for (int i = pulse_start; i < pulse_end; i++) {
                sum_an += samples[i];
            }
            avg_an = count_an > 0 ? (float)sum_an / count_an * VperDev : 0.0f;

            // Berechnung der durchschnittlichen Amplitude nach Pulsende
            uint32_t sum_aus = 0;
            int count_aus = NUM_SAMPLES - pulse_end;
            for (int i = pulse_end; i < NUM_SAMPLES; i++) {
                sum_aus += samples[i];
            }
            float avg_aus = count_aus > 0 ? (float)sum_aus / count_aus * VperDev : 0.0f;

            // Ausgabe der Ergebnisse
        #if timestamping
            // printf("Berechnungszeit: %.2f us\n",
            //     time_us_32() - timestamps[NUM_SAMPLES-1]);

            printf("%.2f, %d, %d, %d, %.2f, %.2f, %lu\n",
                pwm, pulse_start, pulse_end, pulse_end - pulse_start, avg_an, avg_aus, pulse_time_us);
        #else
            printf("%.2f, %d, %d, %d, %.2f, %.2f\n",
                pwm, pulse_start, pulse_end, pulse_end - pulse_start, avg_an, avg_aus);
        #endif
        } else {
            printf("%.2f, Kein Puls erkannt, 0, 0, 0, 0, 0\n", pwm);
            avg_an = 0.0f;  // Für Regelung weiter unten
            
        }
        

        // === Regelung: Wenn Puls zu schwach oder nicht erkannt, erhöhe PWM ===
        if (avg_an < lower_avg_threshold) {
            pwm += pwm_step;
            if (pwm > pwm_max) pwm = pwm_max;
            uint16_t new_level = (uint16_t)(wrap * pwm);
            pwm_set_gpio_level(PWM_GPIO, new_level);

            printf("%.2f, PWM erhöht\n", pwm);
        }
        else if (avg_an > upper_avg_threshold) {
            pwm -= pwm_step;
            if (pwm < pwm_min) pwm = pwm_min;
            uint16_t new_level = (uint16_t)(wrap * pwm);
            pwm_set_gpio_level(PWM_GPIO, new_level);

            printf("%.2f, PWM verringert\n", pwm);
        } else {
            printf("%.2f, PWM bleibt\n", pwm);
        }
    }
}

void startmessung(void) {
    printf("Startmessung %lu\n");

    uint32_t start_time = time_us_32();

    uint16_t start_samples[NUM_SAMPLES];
    // 100 Messungen durchführen
    for (int i = 0; i < NUM_SAMPLES; i++) {
        start_samples[i] = adc_read();
    }

    // Durchschnitt der Startmessung berechnen
    uint32_t summe = 0;
    for (int i = 0; i < NUM_SAMPLES; i++) {
        summe += start_samples[i];
    }
    float avg = summe / NUM_SAMPLES * VperDev;

    printf("Durchschnitt: %.2f mV, Zeit: %lu us\n", avg, time_us_32()- start_time);
}