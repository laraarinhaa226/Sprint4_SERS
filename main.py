from machine import Pin, ADC, SoftI2C
import time, ujson
import ssd1306
import dht


PONTO          = "AsterPoint Extra 1"
BATERIA_KWH    = 60.0    
SOC_INICIAL    = 10.0     
POT_MAX_KW     = 60.0     
TARIFA_BASE    = 4.20    
DESCONTO_SOLAR = 0.05     
SOLAR_ATIVO    = True     # simula "Energia solar ativa"
ACEL           = 60     


TEMP_REDUCAO   = 45.0     
TEMP_MANUT     = 60.0     
TEMP_RETORNO   = 50.0     

MQTT_ATIVO  = False       # mudar para True para publicar via MQTT
MQTT_BROKER = "broker.hivemq.com"
MQTT_TOPICO = b"astercharge/sessao/ponto1"


TARIFA = TARIFA_BASE * (1 - DESCONTO_SOLAR) if SOLAR_ATIVO else TARIFA_BASE

pot = ADC(Pin(34))
pot.atten(ADC.ATTN_11DB)
btn_ini  = Pin(25, Pin.IN, Pin.PULL_UP)
btn_stop = Pin(26, Pin.IN, Pin.PULL_UP)
btn_man  = Pin(27, Pin.IN, Pin.PULL_UP)
led_g = Pin(16, Pin.OUT)  
led_r = Pin(17, Pin.OUT)  
led_y = Pin(5, Pin.OUT)   

sensor = dht.DHT22(Pin(15))

i2c = SoftI2C(scl=Pin(22), sda=Pin(21))
oled = ssd1306.SSD1306_I2C(128, 64, i2c)

LIVRE, CARREGANDO, MANUTENCAO = "LIVRE", "CARREGANDO", "MANUTENCAO"
estado = LIVRE
kwh = 0.0
decorrido_min = 0.0
soc = SOC_INICIAL
kw = 0.0
restante_min = 0
temp = 25.0
limitada = False
manut_termica = False
_ultimo_btn = {}

# ---------------- MQTT ----------------
cliente = None
if MQTT_ATIVO:
    try:
        import network
        from umqtt.simple import MQTTClient
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        wlan.connect("Wokwi-GUEST", "", channel=6)
        t0 = time.time()
        while not wlan.isconnected() and time.time() - t0 < 10:
            time.sleep(0.2)
        cliente = MQTTClient("astercharge-esp32", MQTT_BROKER)
        cliente.connect()
        print("MQTT conectado")
    except Exception as e:
        print("MQTT indisponivel:", e)
        cliente = None


def publicar():
    dados = {
        "ponto": PONTO, "estado": estado,
        "potencia_kw": round(kw, 1), "energia_kwh": round(kwh, 2),
        "bateria_pct": round(soc), "restante_min": restante_min,
        "temperatura_c": round(temp, 1), "potencia_limitada": limitada,
        "tarifa": round(TARIFA, 2), "total_rs": round(kwh * TARIFA, 2),
    }
    print(ujson.dumps(dados))          # sempre sai na serial
    if cliente:
        try:
            cliente.publish(MQTT_TOPICO, ujson.dumps(dados))
        except Exception as e:
            print("Falha MQTT:", e)


def apertado(pin):
    """Detecta clique com debounce."""
    if pin.value() == 0:
        agora = time.ticks_ms()
        if time.ticks_diff(agora, _ultimo_btn.get(id(pin), 0)) > 300:
            _ultimo_btn[id(pin)] = agora
            return True
    return False


def leds():
    led_g.value(estado == LIVRE)
    led_r.value(estado == CARREGANDO)
    led_y.value(estado == MANUTENCAO)


def tela(linhas):
    oled.fill(0)
    for i, txt in enumerate(linhas):
        oled.text(txt[:16], 0, i * 8)
    oled.show()


def tela_sessao():
    tela([
        "SESSAO ANDAMENTO",
        "Temp: %.1fC%s" % (temp, " LIMIT" if limitada else ""),
        "Energia:%.2fkWh" % kwh,
        "Bateria: %d%%" % round(soc),
        "Potenc.:%.1fkW" % kw,
        "Decorr.: %d min" % int(decorrido_min),
        "Restante:%d min" % restante_min,
        "Total: R$ %.2f" % (kwh * TARIFA),
    ])


def tela_estado():
    if estado == MANUTENCAO and manut_termica:
        tela(["AsterCharge", "", PONTO[:16], "", "MANUTENCAO", "SUPERAQUECIMENTO", "Temp: %.1fC" % temp])
    elif estado == MANUTENCAO:
        tela(["AsterCharge", "", PONTO[:16], "", "MANUTENCAO", "", "Aguardando", "operador"])
    else:
        tela(["AsterCharge", "", PONTO[:16], "", "LIVRE", "", "Aperte VERDE p/", "iniciar recarga"])


def iniciar():
    global estado, kwh, decorrido_min, soc
    kwh, decorrido_min, soc = 0.0, 0.0, SOC_INICIAL
    estado = CARREGANDO
    print(">> Recarga iniciada | tarifa R$ %.2f/kWh" % TARIFA)


def faturar(motivo="Interrompido"):
    global estado
    total = kwh * TARIFA
    print(">> %s | %.2f kWh | R$ %.2f" % (motivo, kwh, total))
    tela(["FATURA FECHADA", "", "Energia:%.2fkWh" % kwh, "Bateria: %d%%" % round(soc),
          "Tempo: %d min" % int(decorrido_min), "Tarifa:%.2f/kWh" % TARIFA,
          "TOTAL: R$ %.2f" % total, ""])
    publicar()
    time.sleep(4)
    estado = LIVRE


# ---------------- LOOP PRINCIPAL ----------------
leds(); tela_estado()
ultimo = time.ticks_ms()
ultimo_pub = ultimo
ultima_temp = time.ticks_add(ultimo, -3000)

while True:
    agora = time.ticks_ms()
    dt = time.ticks_diff(agora, ultimo) / 1000
    ultimo = agora

    
    if time.ticks_diff(agora, ultima_temp) > 2000:
        ultima_temp = agora
        try:
            sensor.measure()
            temp = sensor.temperature()
        except Exception as e:
            print("Falha DHT22:", e)

    
    if temp >= TEMP_MANUT and estado != MANUTENCAO:
        if estado == CARREGANDO:
            faturar("Superaquecimento")
        estado = MANUTENCAO
        manut_termica = True
        print(">> MANUTENCAO automatica | temp %.1fC" % temp)
    elif estado == MANUTENCAO and manut_termica and temp <= TEMP_RETORNO:
        estado = LIVRE
        manut_termica = False
        print(">> Temperatura normalizada | ponto LIVRE")

    
    if apertado(btn_ini) and estado == LIVRE and temp < TEMP_MANUT:
        iniciar()

    
    if apertado(btn_stop) and estado == CARREGANDO:
        faturar("Interrompido")

    
    if apertado(btn_man):
        if estado == CARREGANDO:
            faturar("Manutencao durante sessao")
            estado = MANUTENCAO
            manut_termica = False
        elif estado == MANUTENCAO:
            if temp >= TEMP_MANUT:
                print(">> Nao liberado: temperatura ainda alta (%.1fC)" % temp)
            else:
                estado = LIVRE
                manut_termica = False
        else:
            estado = MANUTENCAO
            manut_termica = False
        print(">> Estado:", estado)

    if estado == CARREGANDO:
        kw = pot.read() / 4095 * POT_MAX_KW
        limitada = temp >= TEMP_REDUCAO
        if limitada:
            kw *= 0.5          # reducao de potencia por temperatura
        horas_sim = dt * ACEL / 3600
        kwh += kw * horas_sim
        decorrido_min += dt * ACEL / 60
        soc = SOC_INICIAL + kwh / BATERIA_KWH * 100
        falta_kwh = (100 - soc) / 100 * BATERIA_KWH
        restante_min = int(falta_kwh / kw * 60) if kw > 0.5 else 0
        if soc >= 100:
            soc = 100
            faturar("Bateria completa")
        else:
            tela_sessao()
    else:
        kw = 0.0
        limitada = False
        restante_min = 0
        tela_estado()

    leds()

    if time.ticks_diff(agora, ultimo_pub) > 2000:
        ultimo_pub = agora
        publicar()

    time.sleep(0.2)
