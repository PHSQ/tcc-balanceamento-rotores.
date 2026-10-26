# Bancada didática de balanceamento de rotores

Códigos Arduino e Python para aquisição e processamento de sinais de aceleração de uma bancada didática, com aplicação do método das quatro voltas para balanceamento de rotores em um plano.

## Arquivos

| Arquivo | Função |
| --- | --- |
| `quatro_corridas.py` | Aquisição serial, FFT, cálculo da massa corretiva e avaliação da resposta após a correção. |
| `arduino/bancada_eixo_y/bancada_eixo_y.ino` | Leitura do eixo Y do ADXL345 em g. Envia `tempo_ms,accel_y_g`. |
| `arduino/codigo_arduino/codigo_arduino.ino` | Variante que calcula o módulo dos três eixos do ADXL345 em g. Envia `tempo_ms,accel_mag_g`. |
| `requirements.txt` | Bibliotecas Python utilizadas pelo programa. |

As duas variantes do Arduino ficam em pastas separadas. Os arquivos de coleta do ensaio apresentado no TCC possuem o cabeçalho `tempo_ms,accel_y_g`, correspondente à leitura do eixo Y. Isso não confirma, por si só, qual versão está atualmente gravada na placa.

## Arduino

Abra na IDE o arquivo `.ino` correspondente à variante desejada. Ambos utilizam `Wire.h` e a biblioteca Adafruit ADXL345 Unified. As conexões estão descritas nos comentários dos arquivos.

Para identificar a variante que já está na placa, abra o Monitor Serial a **115200 baud**, pressione RESET uma vez e observe o cabeçalho, antes de gravar outro programa.

## Python

Na pasta que contém `quatro_corridas.py`, instale as dependências:

```bat
py -m pip install -r requirements.txt
```

Feche o Monitor Serial da IDE Arduino para liberar a porta e execute:

```bat
py quatro_corridas.py
```

Configurações do programa:

- Porta serial padrão: `COM3`. Ajuste `PORTA_SERIAL` conforme a porta da placa.
- Comunicação serial: 115200 baud.
- Massa inicial fixa: **328,3 g**, estimada para disco e eixo, sem a massa de teste temporária. Definida em `MASSA_TOTAL_ROTOR_G`.
- Massa de teste e rotação: solicitadas durante a execução.

O programa salva as coletas e os resultados na pasta `dados_4corridas`. As estimativas de grau de balanceamento produzidas pelo programa não constituem, isoladamente, comprovação de conformidade normativa.
