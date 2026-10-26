"""
Balanceamento pelo metodo das 4 corridas (so amplitudes, sem sensor de fase).

SETUP FISICO:
- Massa de desbalanceamento (8g) em posicao FIXA no anel D80 (raio 40mm) -> NAO MEXE
- Massa de teste (7.6g) movel entre os 3 furos D60 (raio 30mm, 120 graus entre si)
- Escolher 1 dos 3 furos D60 como "0 graus". Proximo em sentido horario = 120, ultimo = 240.

EXECUCAO:
    run 0: so com massa de desbalanceamento (sem massa de teste)
    run 1: + massa de teste em 0 graus
    run 2: + massa de teste em 120 graus
    run 3: + massa de teste em 240 graus
Cada corrida faz 3 coletas consecutivas e usa a estatistica configurada
da amplitude 1x (mediana por padrao; media no qcmedia.py).

SAIDA: magnitude e angulo da massa de correcao.
"""

import numpy as np
import serial
import time
import os
from datetime import datetime

# ============================================================
# CONFIGURACAO
# ============================================================

PORTA_SERIAL = "COM3"
BAUDRATE = 115200

RPM = 660               # alvo atual (~11.0 Hz)
MARGEM_HZ = 0.3         # janela de busca do pico 1x: f_rot +/- MARGEM_HZ
N_AMOSTRAS = 1024
TIMEOUT_COLETA = 25.0

N_COLETAS_POR_CORRIDA = 3
MAX_TENTATIVAS_COLETA = 3

MASSA_TESTE_G = 7.6
MASSA_DESBALANCEAMENTO_G = 8.0
RAIO_TESTE_MM = 30.0      # anel D60 (3 furos a 120 graus)
MASSA_TOTAL_ROTOR_G = 328.3  # Massa estimada do disco + eixo, sem a massa de teste temporaria.
G_BALANCEAMENTO_MM_S = 6.3
GRAUS_BALANCEAMENTO_PADRONIZADOS = (
    0.4,
    1.0,
    2.5,
    6.3,
    16.0,
    40.0,
    100.0,
    250.0,
    630.0,
    1600.0,
    4000.0,
)

# Anel externo D80 (16 furos a 22.5 graus) - onde a correcao sera instalada
RAIO_CORRECAO_MM = 40.0
N_FUROS_D80 = 16
RESOLUCAO_D80_DEG = 360.0 / N_FUROS_D80  # 22.5 graus

PASTA_DADOS = globals().get("PASTA_DADOS", "dados_4corridas")
ESTATISTICA_COLETAS = globals().get("ESTATISTICA_COLETAS", "mediana")

G_TO_MM_S2 = 9806.65

LIMITE_FECHAMENTO_RMS_PCT = 20.0
LIMITE_FECHAMENTO_MAX_PCT = 15.0
LIMITE_COSSENO_FECHAMENTO = 1.05
EPS_INTERSECAO_G = 1e-12


def formatar_rpm(rpm):
    rpm = float(rpm)
    if rpm.is_integer():
        return str(int(rpm))
    return f"{rpm:.1f}"


def formatar_numero(valor):
    return f"{float(valor):.3f}".rstrip("0").rstrip(".")


def formatar_grau_balanceamento(grau):
    return formatar_numero(grau).replace(".", ",")


def classe_balanceamento_padronizada(grau_equivalente_mm_s):
    """Retorna a primeira classe G padronizada atendida pelo rotor."""
    grau_equivalente_mm_s = float(grau_equivalente_mm_s)
    if grau_equivalente_mm_s < 0:
        raise ValueError("O grau equivalente nao pode ser negativo.")

    for grau in GRAUS_BALANCEAMENTO_PADRONIZADOS:
        if grau_equivalente_mm_s <= grau:
            return grau
    return None


def estatistica_coletas_nome():
    estatistica = str(ESTATISTICA_COLETAS).strip().lower()
    return "media" if estatistica == "media" else "mediana"


def agregar_coletas(valores):
    valores = np.array(valores, dtype=float)
    if estatistica_coletas_nome() == "media":
        return float(np.mean(valores))
    return float(np.median(valores))


def posicao_angular_mais_proxima(angulo_deg, resolucao_deg, n_posicoes):
    indice = round((angulo_deg % 360.0) / resolucao_deg) % n_posicoes
    angulo_posicao = indice * resolucao_deg
    erro = abs((angulo_deg % 360.0) - angulo_posicao)
    if erro > 180.0:
        erro = 360.0 - erro
    return indice, angulo_posicao, erro


def descrever_furos_adjacentes(angulo_deg, resolucao_deg, n_posicoes):
    angulo_norm = angulo_deg % 360.0
    posicao = angulo_norm / resolucao_deg
    furo_anterior = int(np.floor(posicao)) % n_posicoes
    angulo_anterior = furo_anterior * resolucao_deg
    erro_anterior = angulo_norm - angulo_anterior

    if abs(erro_anterior) < 1e-9:
        return f"{angulo_norm:.1f}° - exatamente no furo #{furo_anterior}"

    furo_posterior = (furo_anterior + 1) % n_posicoes
    angulo_posterior = furo_posterior * resolucao_deg
    return (
        f"{angulo_norm:.1f}° - entre o furo #{furo_anterior} "
        f"({angulo_anterior:.1f}°) e o furo #{furo_posterior} "
        f"({angulo_posterior:.1f}°)"
    )


def escolher_rpm_alvo(rpm_padrao):
    while True:
        texto = input(
            "Espere a bancada estabilizar, confira o tacometro e informe o RPM "
            f"[ENTER = {formatar_rpm(rpm_padrao)}]: "
        ).strip().replace(",", ".")

        if not texto:
            return float(rpm_padrao)

        try:
            rpm = float(texto)
        except ValueError:
            print("RPM invalido. Digite um numero, por exemplo 660 ou 725.5.")
            continue

        if rpm <= 0:
            print("RPM precisa ser maior que zero.")
            continue

        return rpm


def escolher_massa_teste(massa_padrao):
    while True:
        texto = input(
            "Digite a massa de teste em gramas "
            f"[ENTER = {formatar_numero(massa_padrao)}]: "
        ).strip().replace(",", ".")

        if not texto:
            return float(massa_padrao)

        try:
            massa = float(texto)
        except ValueError:
            print("Massa invalida. Digite um numero, por exemplo 7.6 ou 10.")
            continue

        if massa <= 0:
            print("Massa precisa ser maior que zero.")
            continue

        return massa


def escolher_massa_total_rotor(massa_padrao):
    while True:
        texto = input(
            "Digite a massa total do conjunto girante em gramas "
            "(disco + eixo + fixadores; sem a massa de teste temporaria) "
            f"[ENTER = {formatar_numero(massa_padrao)}]: "
        ).strip().replace(",", ".")

        if not texto:
            return float(massa_padrao)

        try:
            massa = float(texto)
        except ValueError:
            print("Massa invalida. Digite um numero em gramas, por exemplo 220 ou 1000.")
            continue

        if massa <= 0:
            print("Massa total do rotor precisa ser maior que zero.")
            continue

        return massa


def g_pico_para_mms_rms(amp_g_pico, f_hz):
    """Converte amplitude de aceleracao (g pico) em velocidade (mm/s RMS)
    para um componente harmonico de frequencia f_hz. Padrao da ISO 20816."""
    if f_hz <= 0:
        return 0.0
    return amp_g_pico * G_TO_MM_S2 / (2 * np.pi * f_hz) / np.sqrt(2)


def verificar_balanceamento_iso_21940_11(
    amplitude_residual_1x_g,
    resposta_massa_teste_g,
    massa_teste_g,
    raio_teste_mm,
    rpm,
    massa_total_rotor_g,
    grau_balanceamento_mm_s=G_BALANCEAMENTO_MM_S,
    massa_correcao_adicionada_g=0.0,
):
    """Estima Ures, o grau G equivalente e a classe padronizada atendida."""
    amplitude_residual_1x_g = float(amplitude_residual_1x_g)
    resposta_massa_teste_g = float(resposta_massa_teste_g)
    massa_teste_g = float(massa_teste_g)
    raio_teste_mm = float(raio_teste_mm)
    rpm = float(rpm)
    massa_total_rotor_g = float(massa_total_rotor_g)
    massa_correcao_adicionada_g = float(massa_correcao_adicionada_g)
    grau_balanceamento_mm_s = float(grau_balanceamento_mm_s)

    if amplitude_residual_1x_g < 0:
        raise ValueError("A amplitude residual em 1x nao pode ser negativa.")
    if resposta_massa_teste_g <= 0:
        raise ValueError("A resposta T da massa de teste precisa ser maior que zero.")
    if massa_teste_g <= 0:
        raise ValueError("A massa de teste precisa ser maior que zero.")
    if raio_teste_mm <= 0:
        raise ValueError("O raio da massa de teste precisa ser maior que zero.")
    if rpm <= 0:
        raise ValueError("O RPM do tacometro precisa ser maior que zero.")
    if massa_total_rotor_g <= 0:
        raise ValueError("A massa total do rotor precisa ser maior que zero.")
    if massa_correcao_adicionada_g < 0:
        raise ValueError("A massa de correcao adicionada nao pode ser negativa.")
    if grau_balanceamento_mm_s <= 0:
        raise ValueError("O grau de balanceamento precisa ser maior que zero.")

    omega_rad_s = 2.0 * np.pi * rpm / 60.0
    eper_mm = grau_balanceamento_mm_s / omega_rad_s
    uteste_gmm = massa_teste_g * raio_teste_mm
    ures_gmm = (amplitude_residual_1x_g / resposta_massa_teste_g) * uteste_gmm
    massa_total_rotor_final_g = massa_total_rotor_g + massa_correcao_adicionada_g
    uper_gmm = eper_mm * massa_total_rotor_final_g
    percentual_limite = ures_gmm / uper_gmm * 100.0
    dentro_limite = ures_gmm <= uper_gmm
    grau_equivalente_mm_s = (
        ures_gmm / massa_total_rotor_final_g
    ) * omega_rad_s
    classe_padronizada = classe_balanceamento_padronizada(grau_equivalente_mm_s)
    if classe_padronizada is None:
        status = "QUALIDADE ESTIMADA ACIMA DE G 4000"
    else:
        status = (
            "CLASSE ISO ESTIMADA ATENDIDA: G "
            f"{formatar_grau_balanceamento(classe_padronizada)}"
        )

    return {
        "grau_balanceamento_mm_s": grau_balanceamento_mm_s,
        "grau_equivalente_mm_s": grau_equivalente_mm_s,
        "classe_padronizada_atendida": classe_padronizada,
        "omega_rad_s": omega_rad_s,
        "eper_mm": eper_mm,
        "uteste_gmm": uteste_gmm,
        "ures_gmm": ures_gmm,
        "uper_gmm": uper_gmm,
        "massa_total_rotor_inicial_g": massa_total_rotor_g,
        "massa_correcao_adicionada_g": massa_correcao_adicionada_g,
        "massa_total_rotor_final_g": massa_total_rotor_final_g,
        "percentual_limite": percentual_limite,
        "dentro_limite": dentro_limite,
        "status": status,
    }


# ============================================================
# COLETA SERIAL (mesma logica do coletar_e_fft.py)
# ============================================================

def coletar(porta, baudrate, n, timeout_total):
    """Aceita qualquer header que comece com 'tempo_ms,accel' OU,
    se sketch ja esta rodando (sem reset), aceita primeira linha
    CSV valida e usa header padrao.
    Retorna (tempos, valores, header_recebido)."""
    ser = serial.Serial(porta, baudrate, timeout=2)
    time.sleep(0.5)
    ser.reset_input_buffer()

    def _eh_csv_valido(s):
        if "," not in s:
            return False
        partes = s.split(",")
        if len(partes) != 2:
            return False
        try:
            float(partes[0]); float(partes[1])
            return True
        except ValueError:
            return False

    t_inicio = time.time()
    header_recebido = None
    primeira_csv = None
    while time.time() - t_inicio < timeout_total:
        linha = ser.readline().decode("utf-8", errors="ignore").strip()
        if not linha:
            continue
        if linha.startswith("tempo_ms,accel"):
            header_recebido = linha
            break
        if _eh_csv_valido(linha):
            primeira_csv = linha
            header_recebido = "tempo_ms,accel_g"
            break

    if header_recebido is None:
        ser.close()
        raise RuntimeError("Header/dados nao encontrados dentro do timeout")

    tempos, valores = [], []
    last_t = -1.0
    if primeira_csv:
        t_ms_str, a_y = primeira_csv.split(",")
        t_ms = float(t_ms_str)
        tempos.append(t_ms)
        valores.append(float(a_y))
        last_t = t_ms

    while len(valores) < n and time.time() - t_inicio < timeout_total:
        linha = ser.readline().decode("utf-8", errors="ignore").strip()
        if not linha or not _eh_csv_valido(linha):
            continue
        t_ms_str, a_y = linha.split(",")
        t_ms = float(t_ms_str)
        if t_ms <= last_t:
            continue
        tempos.append(t_ms)
        valores.append(float(a_y))
        last_t = t_ms

    ser.close()
    if len(valores) < n:
        raise RuntimeError(f"Timeout: coletadas {len(valores)}/{n} amostras")
    return np.array(tempos), np.array(valores), header_recebido


# ============================================================
# FFT
# ============================================================

def fft_amplitude_1x(sinal, fs, f_rotacao, margem_hz=5.0):
    N = len(sinal)
    sinal = sinal - np.mean(sinal)
    janela = np.hanning(N)
    espectro = np.fft.rfft(sinal * janela)
    freqs = np.fft.rfftfreq(N, 1 / fs)
    magnitudes = 2.0 * np.abs(espectro) / np.sum(janela)

    mascara = (freqs >= f_rotacao - margem_hz) & (freqs <= f_rotacao + margem_hz)
    idx = np.where(mascara)[0]
    if len(idx) == 0:
        return 0.0, freqs[np.argmax(magnitudes)]
    idx_1x = idx[np.argmax(magnitudes[idx])]
    return float(magnitudes[idx_1x]), float(freqs[idx_1x])


def fft_completa(sinal, fs):
    N = len(sinal)
    sinal = sinal - np.mean(sinal)
    janela = np.hanning(N)
    espectro = np.fft.rfft(sinal * janela)
    freqs = np.fft.rfftfreq(N, 1 / fs)
    magnitudes = 2.0 * np.abs(espectro) / np.sum(janela)
    return freqs, magnitudes


# ============================================================
# CORRIDA (3 coletas + estatistica configurada)
# ============================================================

def medir_corrida(nome, f_rot_esperada, iniciar_automatico=False):
    if iniciar_automatico:
        print(f"Iniciando {N_COLETAS_POR_CORRIDA} coletas...")
    else:
        input(f"Bancada GIRANDO (espere estabilizar). ENTER para iniciar {N_COLETAS_POR_CORRIDA} coletas...")

    amplitudes, frequencias, arquivos = [], [], []
    for i in range(N_COLETAS_POR_CORRIDA):
        print(f"\n  Coleta {i + 1}/{N_COLETAS_POR_CORRIDA}...")

        for tentativa in range(1, MAX_TENTATIVAS_COLETA + 1):
            try:
                if tentativa > 1:
                    print(f"    Tentativa {tentativa}/{MAX_TENTATIVAS_COLETA}...")
                    time.sleep(1.0)
                tempos, sinal, header = coletar(PORTA_SERIAL, BAUDRATE, N_AMOSTRAS, TIMEOUT_COLETA)
                break
            except RuntimeError as e:
                print(f"    Falha na coleta: {e}")
                if tentativa == MAX_TENTATIVAS_COLETA:
                    input("    Verifique cabo/Arduino/Serial Monitor e pressione ENTER para tentar novamente...")
                    tempos, sinal, header = coletar(PORTA_SERIAL, BAUDRATE, N_AMOSTRAS, TIMEOUT_COLETA)

        dt = np.diff(tempos)
        fs_real = 1000.0 / np.mean(dt)
        amp, freq = fft_amplitude_1x(sinal, fs_real, f_rot_esperada, MARGEM_HZ)
        amplitudes.append(amp)
        frequencias.append(freq)
        amp_mms = g_pico_para_mms_rms(amp, freq)
        print(f"    1x: {amp_mms:.4f} mm/s RMS @ {freq:.2f} Hz")

        os.makedirs(PASTA_DADOS, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        arq = os.path.join(PASTA_DADOS, f"{nome}_coleta{i + 1}_{ts}.csv")
        with open(arq, "w") as f:
            f.write(f"{header}\n")
            for t, v in zip(tempos, sinal):
                f.write(f"{t:.0f},{v:.4f}\n")
        arquivos.append(arq)

    nome_estatistica = estatistica_coletas_nome()
    amp_med = agregar_coletas(amplitudes)
    freq_med = agregar_coletas(frequencias)
    amp_med_mms = g_pico_para_mms_rms(amp_med, freq_med)
    print(f"\n  {nome_estatistica.upper()}: {amp_med_mms:.4f} mm/s RMS @ {freq_med:.2f} Hz")
    return amp_med, amplitudes, frequencias, arquivos


# ============================================================
# RESOLUCAO DO BALANCEAMENTO
# ============================================================

def resolver_4corridas(A0, A1, A2, A3):
    """Retorna (T, theta_deg, razao_massa, angulo_correcao_deg).
       T = amplitude de resposta da massa de teste.
       theta = angulo do desbalanceamento original (0° = posicao de A1).
       razao_massa = m_correcao / m_teste (mesmo raio).
       angulo_correcao = theta + 180° (oposto ao desbalanceamento)."""
    A0, A1, A2, A3 = map(float, (A0, A1, A2, A3))
    if A0 <= 0:
        raise ValueError("A0 precisa ser maior que zero para resolver o metodo das 4 corridas.")
    if min(A1, A2, A3) < 0:
        raise ValueError("As amplitudes A1, A2 e A3 nao podem ser negativas.")

    T2 = (A1 ** 2 + A2 ** 2 + A3 ** 2) / 3.0 - A0 ** 2
    if T2 <= 0:
        raise ValueError(
            f"T² = {T2:.6f} (<=0). A massa de teste nao gerou mudanca mensuravel "
            f"nas amplitudes — aumentar massa de teste ou reduzir ruido."
        )
    T = np.sqrt(T2)

    c1 = np.clip((A1 ** 2 - A0 ** 2 - T ** 2) / (2 * A0 * T), -1, 1)
    c2 = np.clip((A2 ** 2 - A0 ** 2 - T ** 2) / (2 * A0 * T), -1, 1)
    sin_theta = (2 * c2 + c1) / np.sqrt(3)
    sin_theta = np.clip(sin_theta, -1, 1)
    theta = float(np.degrees(np.arctan2(sin_theta, c1)))

    razao = A0 / T
    angulo_correcao = (theta + 180.0) % 360.0
    return T, theta, razao, angulo_correcao


def _amplitude_prevista(A0, T, theta_deg, angulo_teste_deg):
    """Amplitude prevista pela soma vetorial A0 + T no angulo de teste."""
    termo = (
        A0 ** 2
        + T ** 2
        + 2 * A0 * T * np.cos(np.deg2rad(theta_deg - angulo_teste_deg))
    )
    return float(np.sqrt(max(termo, 0.0)))


def _intersecoes_circulos(c0, r0, c1, r1):
    """Retorna os pontos de intersecao entre dois circulos."""
    x0, y0 = c0
    x1, y1 = c1
    dx = x1 - x0
    dy = y1 - y0
    d = float(np.hypot(dx, dy))

    if d <= EPS_INTERSECAO_G:
        return []
    if d > r0 + r1 + EPS_INTERSECAO_G:
        return []
    if d < abs(r0 - r1) - EPS_INTERSECAO_G:
        return []

    a = (r0 ** 2 - r1 ** 2 + d ** 2) / (2.0 * d)
    h2 = r0 ** 2 - a ** 2
    if h2 < 0 and h2 > -EPS_INTERSECAO_G:
        h2 = 0.0
    if h2 < 0:
        return []

    h = float(np.sqrt(h2))
    xm = x0 + a * dx / d
    ym = y0 + a * dy / d
    rx = -dy * h / d
    ry = dx * h / d

    if h <= EPS_INTERSECAO_G:
        return [(xm, ym)]
    return [(xm + rx, ym + ry), (xm - rx, ym - ry)]


def _gap_intersecao_circulos(c0, r0, c1, r1):
    """Distancia minima que falta para dois circulos se tocarem/cruzarem."""
    d = float(np.hypot(c1[0] - c0[0], c1[1] - c0[1]))
    if d > r0 + r1:
        return d - (r0 + r1)
    if d < abs(r0 - r1):
        return abs(r0 - r1) - d
    return 0.0


def avaliar_intersecao_circunferencias(A0, A1, A2, A3, T, theta):
    """Avalia se as 3 circunferencias de teste se cruzam num ponto comum."""
    centros = [ang_to_xy(ang, A0) for ang in (0.0, 120.0, 240.0)]
    raios = [float(A1), float(A2), float(A3)]
    pares = [(0, 1, "A1-A2"), (0, 2, "A1-A3"), (1, 2, "A2-A3")]
    escala = max(float(np.mean(raios)), 1e-12)

    intersecoes_por_par = []
    pares_sem_intersecao = []
    gaps = []
    for i, j, nome in pares:
        pontos = _intersecoes_circulos(centros[i], raios[i], centros[j], raios[j])
        gap = _gap_intersecao_circulos(centros[i], raios[i], centros[j], raios[j])
        if gap <= EPS_INTERSECAO_G:
            gap = 0.0
        gaps.append(gap)
        intersecoes_por_par.append(pontos)
        if not pontos:
            pares_sem_intersecao.append({
                "par": nome,
                "gap_abs": gap,
                "gap_pct": gap / escala * 100.0,
            })

    cluster_abs = None
    if not pares_sem_intersecao:
        melhor = None
        for p12 in intersecoes_por_par[0]:
            for p13 in intersecoes_por_par[1]:
                for p23 in intersecoes_por_par[2]:
                    distancias = [
                        float(np.hypot(p12[0] - p13[0], p12[1] - p13[1])),
                        float(np.hypot(p12[0] - p23[0], p12[1] - p23[1])),
                        float(np.hypot(p13[0] - p23[0], p13[1] - p23[1])),
                    ]
                    espalhamento = max(distancias)
                    if melhor is None or espalhamento < melhor:
                        melhor = espalhamento
        cluster_abs = melhor

    ponto_ajuste = ang_to_xy((theta + 180.0) % 360.0, T)
    erros_ponto = [
        abs(float(np.hypot(ponto_ajuste[0] - c[0], ponto_ajuste[1] - c[1])) - r)
        for c, r in zip(centros, raios)
    ]

    cluster_pct = None if cluster_abs is None else cluster_abs / escala * 100.0
    return {
        "pares_sem_intersecao": pares_sem_intersecao,
        "pares_ok": not pares_sem_intersecao,
        "max_gap_abs": max(gaps) if gaps else 0.0,
        "max_gap_pct": (max(gaps) / escala * 100.0) if gaps else 0.0,
        "cluster_abs": cluster_abs,
        "cluster_pct": cluster_pct,
        "erros_ponto_abs": erros_ponto,
        "max_erro_ponto_abs": max(erros_ponto) if erros_ponto else 0.0,
        "max_erro_ponto_pct": (
            max(erros_ponto) / escala * 100.0 if erros_ponto else 0.0
        ),
    }


def coef_variacao_pct(valores):
    valores = np.array(valores, dtype=float)
    media = float(np.mean(np.abs(valores))) if len(valores) else 0.0
    if len(valores) < 2 or media <= 0:
        return 0.0
    return float(np.std(valores, ddof=1) / media * 100.0)


def diagnosticar_repetibilidade(corridas):
    """Avalia a dispersao das 3 coletas de cada corrida."""
    cvs = {nome: coef_variacao_pct(valores) for nome, valores in corridas.items()}
    max_cv = max(cvs.values()) if cvs else 0.0

    if max_cv <= 10.0:
        qualidade = "boa"
    elif max_cv <= 20.0:
        qualidade = "aceitavel"
    else:
        qualidade = "ruim"

    avisos = []
    if max_cv > 20.0:
        pior = max(cvs, key=cvs.get)
        avisos.append(
            f"Repetibilidade ruim em {pior} (CV={cvs[pior]:.1f}%). "
            "Repetir essa corrida pode mudar massa e angulo."
        )

    return {
        "cvs_pct": cvs,
        "max_cv_pct": max_cv,
        "qualidade": qualidade,
        "confiavel": max_cv <= 20.0,
        "avisos": avisos,
    }


def diagnosticar_4corridas(A0, A1, A2, A3, T, theta):
    """Compara as amplitudes medidas com o modelo teorico das 4 corridas."""
    medidas = np.array([A1, A2, A3], dtype=float)
    previstas = np.array([
        _amplitude_prevista(A0, T, theta, 0.0),
        _amplitude_prevista(A0, T, theta, 120.0),
        _amplitude_prevista(A0, T, theta, 240.0),
    ])
    residuais = medidas - previstas
    escala = max(float(np.mean(medidas)), 1e-12)
    rms = float(np.sqrt(np.mean(residuais ** 2)))
    max_abs = float(np.max(np.abs(residuais)))
    rms_pct = rms / escala * 100.0
    max_pct = max_abs / escala * 100.0
    resposta_rel_pct = (T / A0 * 100.0) if A0 > 0 else 0.0

    den = 2 * A0 * T
    cossenos_brutos = [
        (A1 ** 2 - A0 ** 2 - T ** 2) / den,
        (A2 ** 2 - A0 ** 2 - T ** 2) / den,
        (A3 ** 2 - A0 ** 2 - T ** 2) / den,
    ] if den > 0 else [0.0, 0.0, 0.0]
    intersecao = avaliar_intersecao_circunferencias(A0, A1, A2, A3, T, theta)

    if rms_pct <= 5.0:
        qualidade = "excelente"
    elif rms_pct <= 10.0:
        qualidade = "boa"
    elif rms_pct <= 20.0:
        qualidade = "aceitavel"
    else:
        qualidade = "ruim"

    avisos = []
    if resposta_rel_pct < 30.0:
        msg = (
            f"Resposta da massa de teste baixa ({resposta_rel_pct:.1f}% de A0); "
            "o angulo fica mais sensivel ao ruido."
        )
        avisos.append(msg)
    if rms_pct > 20.0:
        msg = (
            f"Fechamento ruim do modelo (erro RMS {rms_pct:.1f}%); "
            "confira RPM, posicoes 0/120/240 e estabilidade da bancada."
        )
        avisos.append(msg)
    elif rms_pct > 10.0:
        msg = (
            f"Fechamento apenas aceitavel (erro RMS {rms_pct:.1f}%); "
            "use a recomendacao com cautela."
        )
        avisos.append(msg)
    if max_pct > 15.0:
        msg = (
            f"Maior erro individual das circunferencias = {max_pct:.1f}% da media; "
            "resultado sensivel ao ruido."
        )
        avisos.append(msg)
    if max(abs(c) for c in cossenos_brutos) > 1.05:
        msg = (
            "As amplitudes nao fecham bem como soma vetorial ideal; "
            "ha sinal de ruido, massa de teste fraca ou posicao angular incorreta."
        )
        avisos.append(msg)
    if intersecao["pares_sem_intersecao"]:
        pares = ", ".join(
            f"{p['par']} gap {p['gap_pct']:.1f}%"
            for p in intersecao["pares_sem_intersecao"]
        )
        avisos.append(
            "As circunferencias de teste nao tem intersecao geometrica comum "
            f"({pares}); a correcao calculada e apenas um melhor ajuste."
        )

    return {
        "medidas": medidas,
        "previstas": previstas,
        "residuais": residuais,
        "rms_residual": rms,
        "max_residual": max_abs,
        "rms_residual_pct": rms_pct,
        "max_residual_pct": max_pct,
        "resposta_teste_rel_pct": resposta_rel_pct,
        "cossenos_brutos": cossenos_brutos,
        "intersecao": intersecao,
        "qualidade": qualidade,
        "avisos": avisos,
    }


def circunferencias_fecham(diag_modelo):
    """Gera circunferencias quando A1, A2 e A3 se cruzam par-a-par."""
    intersecao = diag_modelo.get("intersecao")
    if intersecao is None:
        return True
    return bool(intersecao.get("pares_ok", False))


def motivo_nao_fechamento(diag_modelo):
    intersecao = diag_modelo.get("intersecao", {})
    pares_sem_intersecao = intersecao.get("pares_sem_intersecao", [])
    if pares_sem_intersecao:
        detalhes = ", ".join(
            f"{p['par']} gap {p['gap_pct']:.1f}%"
            for p in pares_sem_intersecao
        )
        return f"pares sem cruzamento: {detalhes}"

    return "A1, A2 e A3 sem cruzamento par-a-par"


def descrever_intersecao_circunferencias(intersecao):
    pares_sem_intersecao = intersecao.get("pares_sem_intersecao", [])
    if pares_sem_intersecao:
        detalhes = ", ".join(
            f"{p['par']} gap {p['gap_pct']:.1f}%"
            for p in pares_sem_intersecao
        )
        return f"incompleta ({detalhes})"

    cluster_pct = intersecao.get("cluster_pct")
    if cluster_pct is None:
        return "indisponivel"

    return f"pares cruzam; dispersao comum {cluster_pct:.1f}%"


def ang_to_xy(ang_deg, raio):
    """0 graus no topo, angulos positivos no sentido horario."""
    theta = np.deg2rad(90.0 - ang_deg)
    return raio * np.cos(theta), raio * np.sin(theta)


def carregar_csv_medicao(csv_path):
    dados = np.genfromtxt(csv_path, delimiter=",", skip_header=1)
    return dados[:, 0], dados[:, 1]


def plotar_circulo(ax, centro, raio, cor, label):
    from matplotlib.patches import Circle

    circ = Circle(centro, raio, fill=False, color=cor, linewidth=2.0, label=label)
    ax.add_patch(circ)


def _texto_correcao_grafico(
    massa_correcao_d80=None,
    ang_correcao=None,
    furo_correcao_idx=None,
    angulo_furo_correcao=None,
    tentativa=False,
):
    prefixo = "Correcao D80"
    if massa_correcao_d80 is None:
        return ""

    if furo_correcao_idx is not None and angulo_furo_correcao is not None:
        texto = (
            f"{prefixo}: {massa_correcao_d80:.2f} g @ furo "
            f"#{furo_correcao_idx} ({angulo_furo_correcao:.1f} deg)"
        )
    elif ang_correcao is not None:
        texto = f"{prefixo}: {massa_correcao_d80:.2f} g @ {ang_correcao:.1f} deg"
    else:
        texto = f"{prefixo}: {massa_correcao_d80:.2f} g"

    return texto


def resumo_reducao_correcao(A0, A_correcao, f_hz):
    if A_correcao is None or A0 <= 0:
        return None

    A0_mms = g_pico_para_mms_rms(A0, f_hz)
    A_correcao_mms = g_pico_para_mms_rms(A_correcao, f_hz)
    delta_mms = A0_mms - A_correcao_mms
    delta_g = A0 - A_correcao
    reducao_pct = delta_g / A0 * 100.0
    acao = "diminuiu" if delta_g >= 0 else "aumentou"

    nome_variacao = "Reducao" if delta_g >= 0 else "Aumento"

    return {
        "A0_mms": A0_mms,
        "A_correcao_mms": A_correcao_mms,
        "delta_mms": delta_mms,
        "delta_g": delta_g,
        "reducao_pct": reducao_pct,
        "texto": (
            f"{nome_variacao} A0 -> A_final: {A0_mms:.4f} para {A_correcao_mms:.4f} mm/s RMS "
            f"({acao} {abs(delta_mms):.4f} mm/s RMS, {abs(reducao_pct):.1f}%) | "
            f"{A0:.5f} para {A_correcao:.5f} g pico ({acao} {abs(delta_g):.5f} g)"
        ),
    }


def gerar_grafico_4corridas(
    A0,
    A1,
    A2,
    A3,
    T,
    theta,
    ang_correcao,
    arquivos_por_corrida,
    massa_correcao_d80=None,
    furo_correcao_idx=None,
    angulo_furo_correcao=None,
    A_correcao=None,
):
    """Gera figura com circunferencias do metodo e FFT media de cada corrida."""
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    os.makedirs(PASTA_DADOS, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    saida = os.path.join(PASTA_DADOS, f"grafico_4corridas_{ts}.png")

    runs = [
        ("A0 baseline", "run0_baseline", A0, None, "#c81d32"),
        ("A1 teste 0 deg", "run1_0deg", A1, 0.0, "#f28e1c"),
        ("A2 teste 120 deg", "run2_120deg", A2, 120.0, "#009b4e"),
        ("A3 teste 240 deg", "run3_240deg", A3, 240.0, "#27348b"),
    ]
    runs_fft = list(runs)
    if A_correcao is not None:
        runs_fft.append(("A_final", "run4_correcao", A_correcao, None, "#1f77b4"))

    n_linhas = max(4, len(runs_fft))
    fig = plt.figure(figsize=(15, 9 + max(0, n_linhas - 4) * 1.8))
    gs = GridSpec(n_linhas, 2, figure=fig, width_ratios=[1.15, 1.0], wspace=0.25, hspace=0.38)
    ax_c = fig.add_subplot(gs[:, 0])

    # Construcao grafica classica:
    # A0 fica centrada na origem. Os centros de A1, A2 e A3 ficam sobre a
    # circunferencia de A0, respectivamente em 0, 120 e 240 graus.
    centros = [(0.0, 0.0)]
    for _, _, _, ang_teste, _ in runs[1:]:
        centros.append(ang_to_xy(ang_teste, A0))

    for (label, _, amp, _, cor), centro in zip(runs, centros):
        plotar_circulo(ax_c, centro, amp, cor, f"{label} ({amp:.5f} g)")

    # O ponto comum das circunferencias de teste representa a direcao de correcao.
    cx, cy = ang_to_xy(ang_correcao, T)
    ax_c.plot([0, cx], [0, cy], color="black", linewidth=2.0)
    ax_c.scatter([cx], [cy], s=60, color="black", zorder=6)
    if massa_correcao_d80 is not None:
        texto_correcao = f"correcao\n{massa_correcao_d80:.2f} g\n{ang_correcao:.1f} deg"
    else:
        texto_correcao = f"correcao\n{ang_correcao:.1f} deg"
    ax_c.annotate(
        texto_correcao,
        xy=(cx, cy),
        xytext=(cx * 1.45 + 0.001, cy * 1.45 + 0.001),
        arrowprops=dict(arrowstyle="->", color="black", lw=1.0),
        fontsize=9,
    )

    for (label, _, _, ang_teste, cor), centro in zip(runs, centros):
        if ang_teste is not None:
            ax_c.annotate(
                f"{ang_teste:.0f} deg",
                xy=centro,
                xytext=(centro[0] * 1.08, centro[1] * 1.08),
                ha="center",
                va="center",
                fontsize=8,
                color=cor,
            )
        ax_c.plot([centro[0], cx], [centro[1], cy], color=cor, linestyle="--", linewidth=0.9, alpha=0.75)
        ax_c.scatter([centro[0]], [centro[1]], color=cor, s=25, zorder=4)

    ax_c.axhline(0, color="#bbb", linewidth=0.7)
    ax_c.axvline(0, color="#bbb", linewidth=0.7)
    ax_c.set_aspect("equal", adjustable="box")
    ax_c.grid(True, alpha=0.25)
    ax_c.set_title("Metodo das 4 corridas - circunferencias", fontsize=12, fontweight="bold")
    ax_c.set_xlabel("g pico")
    ax_c.set_ylabel("g pico")
    ax_c.legend(loc="upper right", fontsize=8)

    limite = max(
        max(abs(x) + r for (x, _), (_, _, r, _, _) in zip(centros, runs)),
        max(abs(y) + r for (_, y), (_, _, r, _, _) in zip(centros, runs)),
        A0 + T,
    ) * 1.15
    ax_c.set_xlim(-limite, limite)
    ax_c.set_ylim(-limite, limite)

    for idx, (label, prefixo, _, _, cor) in enumerate(runs_fft):
        ax = fig.add_subplot(gs[idx, 1])
        arquivos = arquivos_por_corrida.get(prefixo, [])
        mags_todas = []
        freqs_ref = None
        picos = []

        for arq in arquivos:
            tempos, sinal = carregar_csv_medicao(arq)
            fs = 1000.0 / np.mean(np.diff(tempos))
            freqs, mags = fft_completa(sinal, fs)
            amp_1x, freq_1x = fft_amplitude_1x(sinal, fs, RPM / 60.0, MARGEM_HZ)
            mags_todas.append(mags)
            picos.append((amp_1x, freq_1x))
            if freqs_ref is None:
                freqs_ref = freqs

        if mags_todas and freqs_ref is not None:
            mags_media = np.mean(np.array(mags_todas), axis=0)
            ax.plot(freqs_ref, mags_media, color=cor, linewidth=1.2, label="media dos espectros")
            amp_med = agregar_coletas([p[0] for p in picos])
            freq_med = agregar_coletas([p[1] for p in picos])
            ax.plot(freq_med, amp_med, "o", color=cor, markersize=5, label="pico 1x")

        f_rot = RPM / 60.0
        ax.axvspan(f_rot - MARGEM_HZ, f_rot + MARGEM_HZ, color="#ffd166", alpha=0.25)
        ax.axvline(f_rot, color="red", linestyle="--", linewidth=1.0, alpha=0.7)
        ax.set_xlim(0, 45)
        ax.grid(True, alpha=0.25)
        ax.set_ylabel("|FFT| g")
        ax.set_title(f"{label} - FFT media 3 coletas", fontsize=9)
        if mags_todas:
            ax.legend(fontsize=7, loc="upper right")
        if idx == len(runs_fft) - 1:
            ax.set_xlabel("frequencia (Hz)")

    if A_correcao is not None:
        titulo = f"Metodo das 4 corridas + A_final | RPM alvo {formatar_rpm(RPM)} ({RPM / 60.0:.2f} Hz)"
    else:
        titulo = (
            f"4 corridas | RPM alvo {formatar_rpm(RPM)} "
            f"({RPM / 60.0:.2f} Hz), janela +/- {MARGEM_HZ:.1f} Hz"
        )
    texto_aplicacao = _texto_correcao_grafico(
        massa_correcao_d80,
        ang_correcao,
        furo_correcao_idx,
        angulo_furo_correcao,
    )
    if texto_aplicacao:
        titulo += "\n" + texto_aplicacao
    fig.suptitle(titulo, fontsize=13)
    resumo = resumo_reducao_correcao(A0, A_correcao, RPM / 60.0)
    if resumo is not None:
        fig.text(0.5, 0.018, resumo["texto"], ha="center", fontsize=10, fontweight="bold")
        fig.subplots_adjust(bottom=0.075, top=0.90)
    fig.savefig(saida, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return saida


def gerar_grafico_fft_corridas(
    A0,
    A1,
    A2,
    A3,
    arquivos_por_corrida,
    motivo=None,
    massa_correcao_d80=None,
    ang_correcao=None,
    furo_correcao_idx=None,
    angulo_furo_correcao=None,
    A_correcao=None,
):
    """Gera figura apenas com as FFTs medias das 4 corridas."""
    import matplotlib.pyplot as plt

    os.makedirs(PASTA_DADOS, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    saida = os.path.join(PASTA_DADOS, f"grafico_fft_4corridas_{ts}.png")

    runs = [
        ("A0 baseline", "run0_baseline", A0, "#c81d32"),
        ("A1 teste 0 deg", "run1_0deg", A1, "#f28e1c"),
        ("A2 teste 120 deg", "run2_120deg", A2, "#009b4e"),
        ("A3 teste 240 deg", "run3_240deg", A3, "#27348b"),
    ]
    if A_correcao is not None:
        runs.append(("A_final", "run4_correcao", A_correcao, "#1f77b4"))

    n_linhas = int(np.ceil(len(runs) / 2.0))
    fig, axes = plt.subplots(n_linhas, 2, figsize=(13, 4 * n_linhas), sharex=True)
    axes = axes.ravel()
    f_rot = RPM / 60.0
    grade_freq = np.linspace(0, 45, 900)

    for ax, (label, prefixo, amp_medida, cor) in zip(axes, runs):
        arquivos = arquivos_por_corrida.get(prefixo, [])
        espectros = []
        picos = []

        for arq in arquivos:
            tempos, sinal = carregar_csv_medicao(arq)
            fs = 1000.0 / np.mean(np.diff(tempos))
            freqs, mags = fft_completa(sinal, fs)
            amp_1x, freq_1x = fft_amplitude_1x(sinal, fs, f_rot, MARGEM_HZ)
            espectros.append(np.interp(grade_freq, freqs, mags))
            picos.append((amp_1x, freq_1x))

        if espectros:
            mags_media = np.mean(np.array(espectros), axis=0)
            ax.plot(grade_freq, mags_media, color=cor, linewidth=1.2, label="media dos espectros")
            amp_med = agregar_coletas([p[0] for p in picos])
            freq_med = agregar_coletas([p[1] for p in picos])
            ax.plot(freq_med, amp_med, "o", color=cor, markersize=5, label="pico 1x")
        else:
            ax.text(0.5, 0.5, "sem CSVs", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10)

        ax.axvspan(f_rot - MARGEM_HZ, f_rot + MARGEM_HZ, color="#ffd166", alpha=0.25)
        ax.axvline(f_rot, color="red", linestyle="--", linewidth=1.0, alpha=0.7)
        ax.set_xlim(0, 45)
        ax.grid(True, alpha=0.25)
        ax.set_title(f"{label} | {estatistica_coletas_nome()} {amp_medida:.5f} g", fontsize=10)
        ax.set_ylabel("|FFT| g")
        if espectros:
            ax.legend(fontsize=7, loc="upper right")

    for ax in axes[-2:]:
        ax.set_xlabel("frequencia (Hz)")
    for ax in axes[len(runs):]:
        fig.delaxes(ax)

    if A_correcao is not None:
        titulo = f"FFT das 4 corridas + A_final | RPM alvo {formatar_rpm(RPM)} ({f_rot:.2f} Hz)"
    else:
        titulo = f"FFT das 4 corridas | RPM alvo {formatar_rpm(RPM)} ({f_rot:.2f} Hz)"
    texto_aplicacao = _texto_correcao_grafico(
        massa_correcao_d80,
        ang_correcao,
        furo_correcao_idx,
        angulo_furo_correcao,
        tentativa=True,
    )
    if texto_aplicacao:
        titulo += "\n" + texto_aplicacao
    fig.suptitle(titulo, fontsize=11)
    resumo = resumo_reducao_correcao(A0, A_correcao, f_rot)
    if resumo is not None:
        fig.text(0.5, 0.018, resumo["texto"], ha="center", fontsize=10, fontweight="bold")
        fig.tight_layout(rect=(0, 0.055, 1, 0.93))
    else:
        fig.tight_layout()
    fig.savefig(saida, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return saida


# ============================================================
# EXECUCAO
# ============================================================

if __name__ == "__main__":
    f_rot = RPM / 60.0
    nome_estatistica = estatistica_coletas_nome()
    titulo_metodo = "METODO DAS 4 CORRIDAS"
    if nome_estatistica == "media":
        titulo_metodo += " - MEDIA"

    print("=" * 60)
    print(titulo_metodo)
    print("=" * 60)
    print(f"RPM padrao atual: {formatar_rpm(RPM)} RPM ({f_rot:.2f} Hz)")
    print(f"Massa de teste padrao: {formatar_numero(MASSA_TESTE_G)}g @ raio {RAIO_TESTE_MM}mm")
    print()

    # Corrida 0: baseline (so desbalanceamento)
    print(
        "Massa inicial do conjunto girante: "
        f"{formatar_numero(MASSA_TOTAL_ROTOR_G)} g"
    )
    MASSA_TESTE_G = escolher_massa_teste(MASSA_TESTE_G)
    print(f"Massa de teste informada: {formatar_numero(MASSA_TESTE_G)}g")
    RPM = escolher_rpm_alvo(RPM)
    f_rot = RPM / 60.0
    print("\n" + "=" * 60)
    print(f"RPM ALVO PARA AS 4 CORRIDAS: {formatar_rpm(RPM)} RPM ({f_rot:.2f} Hz)")
    print("RPM informado. Iniciando a CORRIDA 0.")
    print("=" * 60)
    A0, amps0, fr0, arqs0 = medir_corrida("run0_baseline", f_rot, iniciar_automatico=True)

    # Corrida 1
    print("\n>>> Coloque a massa de teste em 0 graus (furo de referencia) <<<")
    A1, amps1, fr1, arqs1 = medir_corrida("run1_0deg", f_rot)

    # Corrida 2
    print("\n>>> Mova a massa de teste para 120 graus <<<")
    A2, amps2, fr2, arqs2 = medir_corrida("run2_120deg", f_rot)

    # Corrida 3
    print("\n>>> Mova a massa de teste para 240 graus <<<")
    A3, amps3, fr3, arqs3 = medir_corrida("run3_240deg", f_rot)

    # Conversao pra mm/s RMS (padrao do site das 4 voltas e da ISO 20816)
    A0_mms = g_pico_para_mms_rms(A0, f_rot)
    A1_mms = g_pico_para_mms_rms(A1, f_rot)
    A2_mms = g_pico_para_mms_rms(A2, f_rot)
    A3_mms = g_pico_para_mms_rms(A3, f_rot)

    # Resultado
    print("\n" + "=" * 60)
    print("AMPLITUDES")
    print("=" * 60)
    print(f"                  mm/s RMS")
    print(f"A0 baseline       {A0_mms:.4f}  (com desbalanceamento)")
    print(f"A1 (teste em 0°)  {A1_mms:.4f}")
    print(f"A2 (teste em 120°){A2_mms:.4f}")
    print(f"A3 (teste em 240°){A3_mms:.4f}")

    arquivos_por_corrida = {
        "run0_baseline": arqs0,
        "run1_0deg": arqs1,
        "run2_120deg": arqs2,
        "run3_240deg": arqs3,
    }

    try:
        T, theta, razao, ang_correcao = resolver_4corridas(A0, A1, A2, A3)
        massa_correcao_g = razao * MASSA_TESTE_G
        diag_modelo = diagnosticar_4corridas(A0, A1, A2, A3, T, theta)
        fecharam = circunferencias_fecham(diag_modelo)

        print("\n" + "=" * 60)
        print("CORRECAO CALCULADA")
        print("=" * 60)
        # Conversao pro anel D80 (16 furos), onde a correcao sera instalada
        massa_correcao_D80 = massa_correcao_g * (RAIO_TESTE_MM / RAIO_CORRECAO_MM)
        verificacao_iso = None
        furo_mais_proximo_idx, angulo_furo, erro_angular = posicao_angular_mais_proxima(
            ang_correcao,
            RESOLUCAO_D80_DEG,
            N_FUROS_D80,
        )
        posicao_real_d80 = descrever_furos_adjacentes(
            ang_correcao,
            RESOLUCAO_D80_DEG,
            N_FUROS_D80,
        )

        print("\n--- RESULTADOS ---")
        print(f"  Massa calculada no raio {RAIO_TESTE_MM}mm: {massa_correcao_g:.2f} g")
        print(f"  Massa ajustada no raio {RAIO_CORRECAO_MM}mm: {massa_correcao_D80:.2f} g")
        print(f"  Ângulo calculado: {posicao_real_d80}")
        A_correcao, amps_correcao, fr_correcao, arqs_correcao = None, [], [], []
        print(
            f"\n>>> Para adicionar a FFT com correcao no grafico, instale "
            f"{massa_correcao_D80:.2f}g no furo #{furo_mais_proximo_idx} "
            f"({angulo_furo:.1f} graus) <<<"
        )
        resp_correcao = input(
            "Com a massa de correcao instalada e a bancada estabilizada GIRANDO, "
            "pressione ENTER para executar a volta de verificacao (ou digite n para pular): "
        ).strip().lower()
        if not resp_correcao.startswith("n"):
            A_correcao, amps_correcao, fr_correcao, arqs_correcao = medir_corrida(
                "run4_correcao",
                f_rot,
                iniciar_automatico=True,
            )
            arquivos_por_corrida["run4_correcao"] = arqs_correcao
            verificacao_iso = verificar_balanceamento_iso_21940_11(
                A_correcao,
                T,
                MASSA_TESTE_G,
                RAIO_TESTE_MM,
                RPM,
                MASSA_TOTAL_ROTOR_G,
                massa_correcao_adicionada_g=massa_correcao_D80,
            )
        A_correcao_mms = (
            g_pico_para_mms_rms(A_correcao, f_rot)
            if A_correcao is not None
            else None
        )
        resumo_correcao = resumo_reducao_correcao(A0, A_correcao, f_rot)
        reducao_vibracao_pct = None
        if resumo_correcao is not None:
            reducao_vibracao_pct = resumo_correcao["reducao_pct"]
            print("\n--- RESULTADO APOS CORRECAO ---")
            print(f"  A0 baseline:  {A0_mms:.4f} mm/s RMS")
            print(f"  A_correcao:   {A_correcao_mms:.4f} mm/s RMS")
            print(f"  Reducao da vibracao 1x: {reducao_vibracao_pct:.1f}%")
            print("\n--- QUALIDADE DE BALANCEAMENTO ESTIMADA ---")
            print(
                f"  Vibracao residual em 1x: {A_correcao_mms:.4f} mm/s RMS "
                f"({A_correcao:.5f} g pico)"
            )
            print(
                "  Massa inicial do conjunto girante: "
                f"{verificacao_iso['massa_total_rotor_inicial_g']:.2f} g"
            )
            print(
                "  Massa de correcao adicionada: "
                f"{verificacao_iso['massa_correcao_adicionada_g']:.2f} g"
            )
            print(
                "  Massa final usada no calculo: "
                f"{verificacao_iso['massa_total_rotor_final_g']:.2f} g"
            )
            print(f"  Ures estimado: {verificacao_iso['ures_gmm']:.3f} g·mm")
            print(
                "  Grau equivalente estimado: G "
                f"{formatar_grau_balanceamento(verificacao_iso['grau_equivalente_mm_s'])}"
            )
            print(f"  {verificacao_iso['status']}")
            print("  Resultado estimado pela resposta vibratoria da bancada.")

        if fecharam:
            grafico = gerar_grafico_4corridas(
                A0,
                A1,
                A2,
                A3,
                T,
                theta,
                ang_correcao,
                arquivos_por_corrida,
                massa_correcao_D80,
                furo_mais_proximo_idx,
                angulo_furo,
                A_correcao,
            )
            tipo_grafico = "circunferencias + FFT"
            if A_correcao is not None:
                tipo_grafico += " + A_final"
            print(f"\nGrafico 4 corridas salvo: {grafico}")
        else:
            grafico = gerar_grafico_fft_corridas(
                A0,
                A1,
                A2,
                A3,
                arquivos_por_corrida,
                None,
                massa_correcao_D80,
                ang_correcao,
                furo_mais_proximo_idx,
                angulo_furo,
                A_correcao,
            )
            tipo_grafico = "apenas FFT"
            if A_correcao is not None:
                tipo_grafico += " + A_final"
            print(f"Grafico FFT salvo: {grafico}")

        relatorio = os.path.join(
            PASTA_DADOS,
            f"resultado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
        with open(relatorio, "w", encoding="utf-8") as f:
            f.write(f"Metodo das 4 corridas - {datetime.now().isoformat()}\n")
            f.write(f"RPM alvo: {formatar_rpm(RPM)} | Massa teste: {MASSA_TESTE_G}g @ {RAIO_TESTE_MM}mm\n\n")
            f.write(
                "Massa inicial do conjunto girante: "
                f"{MASSA_TOTAL_ROTOR_G} g (sem massa de teste temporaria)\n\n"
            )
            f.write(f"A0 = {A0_mms:.4f} mm/s RMS ({A0:.5f} g pico) | coletas g: {amps0} | freqs: {fr0}\n")
            f.write(f"A1 = {A1_mms:.4f} mm/s RMS ({A1:.5f} g pico) | coletas g: {amps1} | freqs: {fr1}\n")
            f.write(f"A2 = {A2_mms:.4f} mm/s RMS ({A2:.5f} g pico) | coletas g: {amps2} | freqs: {fr2}\n")
            f.write(f"A3 = {A3_mms:.4f} mm/s RMS ({A3:.5f} g pico) | coletas g: {amps3} | freqs: {fr3}\n\n")
            if A_correcao is not None:
                f.write(
                    f"A_final = {A_correcao_mms:.4f} mm/s RMS ({A_correcao:.5f} g pico) "
                    f"| coletas g: {amps_correcao} | freqs: {fr_correcao} "
                    "[volta de correcao]\n\n"
                )
                f.write(
                    f"{resumo_correcao['texto']}\n\n"
                )
            f.write(f"\n--- CORRECAO ---\n")
            f.write(f"Correcao equivalente (D60) = {massa_correcao_g:.2f} g @ r={RAIO_TESTE_MM}mm\n")
            f.write(f"Correcao pratica (D80) = {massa_correcao_D80:.2f} g @ furo #{furo_mais_proximo_idx} "
                    f"({angulo_furo:.1f}°) @ r={RAIO_CORRECAO_MM}mm\n")
            f.write(f"Ângulo calculado = {posicao_real_d80}\n")
            f.write(f"Erro angular por arredondamento = {erro_angular:.1f} graus\n")
            if verificacao_iso is not None:
                f.write("\n--- QUALIDADE DE BALANCEAMENTO ESTIMADA ---\n")
                f.write(
                    f"Vibracao residual em 1x = {A_correcao_mms:.4f} mm/s RMS "
                    f"({A_correcao:.5f} g pico)\n"
                )
                f.write(
                    "Massa inicial do conjunto girante = "
                    f"{verificacao_iso['massa_total_rotor_inicial_g']:.2f} g\n"
                )
                f.write(
                    "Massa de correcao adicionada = "
                    f"{verificacao_iso['massa_correcao_adicionada_g']:.2f} g\n"
                )
                f.write(
                    "Massa final usada no calculo = "
                    f"{verificacao_iso['massa_total_rotor_final_g']:.2f} g\n"
                )
                f.write(f"Ures estimado = {verificacao_iso['ures_gmm']:.3f} g·mm\n")
                f.write(
                    "Grau equivalente estimado = G "
                    f"{formatar_grau_balanceamento(verificacao_iso['grau_equivalente_mm_s'])}\n"
                )
                f.write(f"{verificacao_iso['status']}\n")
                f.write("Resultado estimado pela resposta vibratoria da bancada.\n")
            f.write(f"Grafico gerado ({tipo_grafico}) = {grafico}\n")
            if A_correcao is not None:
                f.write("Grafico inclui a FFT media da volta de correcao (A_final/run4_correcao).\n")
        print(f"\nRelatorio salvo: {relatorio}")

    except ValueError as e:
        print(f"\nERRO: {e}")
        grafico = gerar_grafico_fft_corridas(
            A0, A1, A2, A3, arquivos_por_corrida, None
        )
        print(f"Grafico FFT salvo: {grafico}")
